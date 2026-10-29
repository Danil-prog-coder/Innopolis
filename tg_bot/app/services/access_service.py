from dataclasses import dataclass

from app.core.enums import MemberRole
from app.core.errors import NotProjectAdmin, NotProjectMember
from app.storage.postgres.membership_work import PostgresMembershipWork
from app.storage.postgres.models import Membership, Project


@dataclass(frozen=True, slots=True)
class CurrentProject:
    project: Project
    role: MemberRole


class AccessGuard:
    """Проверка прав на проект. Сервисы вызывают её на каждое действие,
    поэтому устаревшая кнопка с чужим project_id ничего не сделает."""

    def __init__(self, membership_work: PostgresMembershipWork) -> None:
        self._membership_work = membership_work

    async def require_member(self, user_id: int, project_id: int) -> Membership:
        membership = await self._membership_work.get(user_id, project_id)
        if membership is None:
            raise NotProjectMember
        return membership

    async def require_admin(self, user_id: int, project_id: int) -> Membership:
        membership = await self._membership_work.get(user_id, project_id)
        if membership is None or membership.role != MemberRole.ADMIN:
            raise NotProjectAdmin
        return membership
