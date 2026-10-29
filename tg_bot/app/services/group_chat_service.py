from app.core.errors import GroupRebindForbidden, NotProjectAdmin
from app.services.access_service import AccessGuard
from app.services.project_service import ProjectService
from app.storage.postgres.group_chat_work import PostgresGroupChatWork
from app.storage.postgres.models import Project, User
from app.storage.postgres.project_work import PostgresProjectWork


class GroupChatService:
    def __init__(
        self,
        group_chat_work: PostgresGroupChatWork,
        project_service: ProjectService,
        project_work: PostgresProjectWork,
        access_guard: AccessGuard,
    ) -> None:
        self._group_chat_work = group_chat_work
        self._project_service = project_service
        self._project_work = project_work
        self._access = access_guard

    async def bind(self, chat_id: int, password: str, by_user: User) -> Project:
        """Привязать групповой чат к проекту по секретному паролю проекта.

        Привязать может любой, кто знает пароль. Перенести уже привязанный чат в другой
        проект может только админ текущего проекта: иначе посторонний в чате увёл бы
        вопросы команды в свой проект и отвечал бы «от имени админа».
        """
        project = await self._project_service.authenticate_by_password(by_user, password)
        bound = await self._group_chat_work.get_by_chat_id(chat_id)
        if bound is not None and bound.project_id != project.id:
            try:
                await self._access.require_admin(by_user.id, bound.project_id)
            except NotProjectAdmin:
                raise GroupRebindForbidden from None
        await self._group_chat_work.bind(chat_id, project.id, by_user.telegram_id)
        return project

    async def get_bound_project(self, chat_id: int) -> Project | None:
        group_chat = await self._group_chat_work.get_by_chat_id(chat_id)
        if group_chat is None:
            return None
        return await self._project_work.get_by_id(group_chat.project_id)
