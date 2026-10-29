from app.storage.postgres.client import PostgresClient
from app.storage.postgres.models import ProjectBan


class PostgresProjectBanWork:
    def __init__(self, client: PostgresClient) -> None:
        self._client = client

    async def add(self, user_id: int, project_id: int, last_invite_id: int) -> ProjectBan:
        """Запретить возврат по приглашениям с id не больше ``last_invite_id``."""
        return await self._client.upsert(
            ProjectBan,
            {"user_id": user_id, "project_id": project_id},
            last_invite_id=last_invite_id,
        )

    async def get(self, user_id: int, project_id: int) -> ProjectBan | None:
        return await self._client.get_one(ProjectBan, user_id=user_id, project_id=project_id)

    async def remove(self, user_id: int, project_id: int) -> None:
        await self._client.delete(ProjectBan, user_id=user_id, project_id=project_id)
