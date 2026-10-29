from tortoise.exceptions import IntegrityError

from app.core.enums import MemberRole
from app.storage.postgres.client import PostgresClient
from app.storage.postgres.models import Membership


class PostgresMembershipWork:
    def __init__(self, client: PostgresClient) -> None:
        self._client = client

    async def add_or_upgrade(self, user_id: int, project_id: int, role: MemberRole) -> Membership:
        """Добавить участника или повысить его до админа. Админа никогда не понижает."""
        membership = await self.get(user_id, project_id)
        if membership is None:
            try:
                return await self._client.create(
                    Membership, user_id=user_id, project_id=project_id, role=role
                )
            except IntegrityError:
                # Параллельный запрос успел создать членство первым
                membership = await self.get(user_id, project_id)
                if membership is None:
                    raise
        if role == MemberRole.ADMIN and membership.role != MemberRole.ADMIN:
            await self._client.update(Membership, {"id": membership.id}, role=MemberRole.ADMIN)
            membership.role = MemberRole.ADMIN
        return membership

    async def get(self, user_id: int, project_id: int) -> Membership | None:
        return await self._client.get_one(Membership, user_id=user_id, project_id=project_id)

    async def get_user_memberships(self, user_id: int) -> list[Membership]:
        return await self._client.get_many(
            Membership, user_id=user_id, prefetch=("project",), order_by=("project__name", "id")
        )

    async def get_project_members(
        self, project_id: int, offset: int, limit: int
    ) -> list[Membership]:
        return await self._client.get_many(
            Membership,
            project_id=project_id,
            prefetch=("user",),
            order_by=("created_at", "id"),
            offset=offset,
            limit=limit,
        )

    async def count_project_members(self, project_id: int) -> int:
        return await self._client.count(Membership, project_id=project_id)

    async def get_member_telegram_ids(
        self, project_id: int, role: MemberRole | None = None
    ) -> list[int]:
        filters: dict[str, object] = {"project_id": project_id}
        if role is not None:
            filters["role"] = role
        return await self._client.get_values(Membership, "user__telegram_id", **filters)

    async def remove(self, user_id: int, project_id: int) -> bool:
        return await self._client.delete(Membership, user_id=user_id, project_id=project_id) > 0
