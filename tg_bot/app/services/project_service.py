from app.config import Settings
from app.core.enums import MemberRole, RateLimitScope
from app.core.errors import (
    InvalidPassword,
    InvalidProjectName,
    NotProjectAdmin,
    ProjectNotFound,
    RemovedFromProject,
    TooManyAttempts,
)
from app.core.security import ProjectPasswordManager
from app.services.access_service import AccessGuard, CurrentProject
from app.storage.postgres.membership_work import PostgresMembershipWork
from app.storage.postgres.models import Membership, Project, User
from app.storage.postgres.project_ban_work import PostgresProjectBanWork
from app.storage.postgres.project_work import PostgresProjectWork
from app.storage.postgres.user_work import PostgresUserWork
from app.storage.redis.password_reveal_work import RedisPasswordRevealWork
from app.storage.redis.rate_limit_work import RedisRateLimitWork

PROJECT_NAME_MAX_LEN = 64
_PUBLIC_ID_ATTEMPTS = 3


class ProjectService:
    def __init__(
        self,
        project_work: PostgresProjectWork,
        membership_work: PostgresMembershipWork,
        user_work: PostgresUserWork,
        project_ban_work: PostgresProjectBanWork,
        password_manager: ProjectPasswordManager,
        password_reveal_work: RedisPasswordRevealWork,
        rate_limit_work: RedisRateLimitWork,
        access_guard: AccessGuard,
        settings: Settings,
    ) -> None:
        self._project_work = project_work
        self._membership_work = membership_work
        self._user_work = user_work
        self._project_ban_work = project_ban_work
        self._password_manager = password_manager
        self._password_reveal_work = password_reveal_work
        self._rate_limit_work = rate_limit_work
        self._access = access_guard
        self._settings = settings

    async def create_project(self, creator: User, name: str) -> Project:
        name = " ".join(name.split())
        if not name or len(name) > PROJECT_NAME_MAX_LEN:
            raise InvalidProjectName

        project: Project | None = None
        for _ in range(_PUBLIC_ID_ATTEMPTS):
            public_id = self._password_manager.new_public_id()
            password = self._password_manager.generate(public_id)
            password_hash = await self._password_manager.hash(password)
            project = await self._project_work.create(name, public_id, password_hash, creator.id)
            if project is not None:
                break
            # Коллизия public_id (2^32 вариантов) крайне маловероятна — пробуем ещё раз
        if project is None:
            raise RuntimeError("Could not allocate a unique project public_id")

        await self._membership_work.add_or_upgrade(creator.id, project.id, MemberRole.ADMIN)
        await self._set_current(creator, project.id)
        await self._password_reveal_work.put(project.id, password)
        return project

    async def reveal_password(self, user: User, project_id: int) -> str | None:
        """Пароль для всплывающего окна. Только создателю и только пока не истёк TTL."""
        project = await self._project_work.get_by_id(project_id)
        if project is None or project.creator_id != user.id:
            raise NotProjectAdmin
        return await self._password_reveal_work.get(project_id)

    async def authenticate_by_password(self, user: User, password: str) -> Project:
        """Проект по секретному паролю — с лимитом попыток и запретом для удалённых."""
        await self._spend_password_attempt(user)
        project = await self._find_project_by_password(password)
        if project is None:
            raise InvalidPassword
        if await self._project_ban_work.get(user.id, project.id) is not None:
            # Удалённый админ знает пароль, но вернуться может только по новому приглашению
            raise RemovedFromProject
        await self._rate_limit_work.reset(RateLimitScope.PASSWORD, user.telegram_id)
        return project

    async def join_as_admin(self, user: User, password: str) -> CurrentProject:
        project = await self.authenticate_by_password(user, password)
        await self._membership_work.add_or_upgrade(user.id, project.id, MemberRole.ADMIN)
        await self._set_current(user, project.id)
        return CurrentProject(project=project, role=MemberRole.ADMIN)

    async def check_password(self, user: User, project_id: int, password: str) -> None:
        """Подтверждение опасного действия (замена документации) паролем проекта."""
        await self._access.require_admin(user.id, project_id)
        await self._spend_password_attempt(user)
        project = await self._find_project_by_password(password)
        if project is None or project.id != project_id:
            raise InvalidPassword
        await self._rate_limit_work.reset(RateLimitScope.PASSWORD, user.telegram_id)

    async def get_current(self, user: User) -> CurrentProject | None:
        project_id = user.current_project_id
        if project_id is None:
            return None
        membership = await self._membership_work.get(user.id, project_id)
        project = await self._project_work.get_by_id(project_id) if membership else None
        if membership is None or project is None:
            # Пользователя удалили из проекта — текущего проекта больше нет
            await self._user_work.reset_current_project_if(user.id, project_id)
            user.current_project_id = None
            return None
        return CurrentProject(project=project, role=membership.role)

    async def list_user_projects(self, user: User) -> list[Membership]:
        return await self._membership_work.get_user_memberships(user.id)

    async def switch_project(self, user: User, project_id: int) -> CurrentProject:
        membership = await self._access.require_member(user.id, project_id)
        project = await self._project_work.get_by_id(project_id)
        if project is None:
            raise ProjectNotFound
        await self._set_current(user, project_id)
        return CurrentProject(project=project, role=membership.role)

    async def _spend_password_attempt(self, user: User) -> None:
        allowed = await self._rate_limit_work.hit(
            RateLimitScope.PASSWORD,
            user.telegram_id,
            self._settings.password_attempts_limit,
            self._settings.password_attempts_window_seconds,
        )
        if not allowed:
            raise TooManyAttempts

    async def _find_project_by_password(self, password: str) -> Project | None:
        password = password.strip()
        public_id = self._password_manager.extract_public_id(password)
        if public_id is None:
            return None
        project = await self._project_work.get_by_public_id(public_id)
        if project is None or not await self._password_manager.verify(
            project.password_hash, password
        ):
            return None
        return project

    async def _set_current(self, user: User, project_id: int) -> None:
        await self._user_work.set_current_project(user.id, project_id)
        user.current_project_id = project_id
