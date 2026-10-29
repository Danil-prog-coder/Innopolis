import math
from dataclasses import dataclass

from app.core.errors import CannotRemoveCreator, CannotRemoveSelf, NotProjectMember, ProjectNotFound
from app.services.access_service import AccessGuard
from app.storage.postgres.invite_work import PostgresInviteWork
from app.storage.postgres.membership_work import PostgresMembershipWork
from app.storage.postgres.models import Membership, User
from app.storage.postgres.project_ban_work import PostgresProjectBanWork
from app.storage.postgres.project_work import PostgresProjectWork
from app.storage.postgres.user_work import PostgresUserWork


@dataclass(frozen=True, slots=True)
class MembersPage:
    items: list[Membership]
    total: int
    page: int
    pages: int


class MembershipService:
    PAGE_SIZE = 10

    def __init__(
        self,
        membership_work: PostgresMembershipWork,
        user_work: PostgresUserWork,
        project_work: PostgresProjectWork,
        project_ban_work: PostgresProjectBanWork,
        invite_work: PostgresInviteWork,
        access_guard: AccessGuard,
    ) -> None:
        self._membership_work = membership_work
        self._user_work = user_work
        self._project_work = project_work
        self._project_ban_work = project_ban_work
        self._invite_work = invite_work
        self._access = access_guard

    async def list_members(self, admin: User, project_id: int, page: int) -> MembersPage:
        await self._access.require_admin(admin.id, project_id)
        total = await self._membership_work.count_project_members(project_id)
        pages = max(1, math.ceil(total / self.PAGE_SIZE))
        page = min(max(page, 0), pages - 1)
        items = await self._membership_work.get_project_members(
            project_id, offset=page * self.PAGE_SIZE, limit=self.PAGE_SIZE
        )
        return MembersPage(items=items, total=total, page=page, pages=pages)

    async def get_member(self, admin: User, project_id: int, target_user_id: int) -> User:
        """Участник проекта для экрана подтверждения удаления. Чужих пользователей не отдаёт."""
        await self._access.require_admin(admin.id, project_id)
        await self._access.require_member(target_user_id, project_id)
        target = await self._user_work.get_by_id(target_user_id)
        if target is None:
            raise NotProjectMember
        return target

    async def remove_member(self, admin: User, project_id: int, target_user_id: int) -> User:
        """Удалить участника и запретить ему вернуться по старым ссылкам и паролю.

        Вернуть удалённого пользователя (чтобы его уведомить)."""
        await self._access.require_admin(admin.id, project_id)
        project = await self._project_work.get_by_id(project_id)
        if project is None:
            raise ProjectNotFound
        if target_user_id == project.creator_id:
            raise CannotRemoveCreator
        if target_user_id == admin.id:
            raise CannotRemoveSelf
        target = await self._user_work.get_by_id(target_user_id)
        if target is None or not await self._membership_work.remove(target_user_id, project_id):
            raise NotProjectMember
        last_invite_id = await self._invite_work.get_last_id(project_id)
        await self._project_ban_work.add(target_user_id, project_id, last_invite_id)
        await self._user_work.reset_current_project_if(target_user_id, project_id)
        return target
