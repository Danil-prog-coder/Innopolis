import re
import secrets
from datetime import timedelta

from app.core.clock import Clock, utc_now
from app.core.enums import MemberRole
from app.core.errors import (
    InviteExpired,
    InviteNotFound,
    InviteRevoked,
    ProjectNotFound,
    RemovedFromProject,
)
from app.services.access_service import AccessGuard, CurrentProject
from app.storage.postgres.invite_work import PostgresInviteWork
from app.storage.postgres.membership_work import PostgresMembershipWork
from app.storage.postgres.models import Invite, User
from app.storage.postgres.project_ban_work import PostgresProjectBanWork
from app.storage.postgres.project_work import PostgresProjectWork
from app.storage.postgres.user_work import PostgresUserWork

INVITE_TTL_DAYS = (1, 7, 30)

_CODE = r"[A-Za-z0-9_-]{8,64}"
_LINK_RE = re.compile(
    rf"(?:https?://)?t\.me/[A-Za-z0-9_]{{3,64}}\?start=({_CODE})(?![A-Za-z0-9_-])"
)
_CODE_RE = re.compile(_CODE)


class InviteService:
    def __init__(
        self,
        invite_work: PostgresInviteWork,
        membership_work: PostgresMembershipWork,
        user_work: PostgresUserWork,
        project_work: PostgresProjectWork,
        project_ban_work: PostgresProjectBanWork,
        access_guard: AccessGuard,
        clock: Clock = utc_now,
    ) -> None:
        self._invite_work = invite_work
        self._membership_work = membership_work
        self._user_work = user_work
        self._project_work = project_work
        self._project_ban_work = project_ban_work
        self._access = access_guard
        self._clock = clock

    async def create_invite(self, admin: User, project_id: int, ttl_days: int) -> Invite:
        if ttl_days not in INVITE_TTL_DAYS:
            raise ValueError(f"ttl_days must be one of {INVITE_TTL_DAYS}")
        await self._access.require_admin(admin.id, project_id)
        return await self._invite_work.create(
            code=secrets.token_urlsafe(16),
            project_id=project_id,
            created_by_id=admin.id,
            expires_at=self._clock() + timedelta(days=ttl_days),
        )

    async def list_active(self, admin: User, project_id: int) -> list[Invite]:
        await self._access.require_admin(admin.id, project_id)
        return await self._invite_work.get_active_for_project(project_id, self._clock())

    async def revoke(self, admin: User, project_id: int, invite_id: int) -> bool:
        await self._access.require_admin(admin.id, project_id)
        return await self._invite_work.revoke(invite_id, project_id, self._clock())

    @staticmethod
    def extract_code(text: str) -> str | None:
        """Код из ссылки ``t.me/<бот>?start=<код>`` или из голого кода."""
        text = text.strip()
        if match := _LINK_RE.search(text):
            return match.group(1)
        if _CODE_RE.fullmatch(text):
            return text
        return None

    async def get_valid_invite(self, code: str) -> Invite:
        invite = await self._invite_work.get_by_code(code)
        if invite is None:
            raise InviteNotFound
        if invite.revoked_at is not None:
            raise InviteRevoked
        if invite.expires_at <= self._clock():
            raise InviteExpired
        return invite

    async def redeem(self, user: User, code: str) -> CurrentProject:
        invite = await self.get_valid_invite(code)
        project = await self._project_work.get_by_id(invite.project_id)
        if project is None:
            raise ProjectNotFound
        ban = await self._project_ban_work.get(user.id, project.id)
        if ban is not None:
            if invite.id <= ban.last_invite_id:
                # Ссылка выпущена до удаления — удалённый участник не может ей вернуться
                raise RemovedFromProject
            # Новое приглашение после удаления: админ осознанно зовёт человека обратно
            await self._project_ban_work.remove(user.id, project.id)
        membership = await self._membership_work.add_or_upgrade(
            user.id, project.id, MemberRole.USER
        )
        await self._user_work.set_current_project(user.id, project.id)
        user.current_project_id = project.id
        return CurrentProject(project=project, role=membership.role)
