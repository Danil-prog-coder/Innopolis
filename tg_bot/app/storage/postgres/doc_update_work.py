from app.core.enums import DocsMode
from app.storage.postgres.client import PostgresClient
from app.storage.postgres.models import DocUpdate


class PostgresDocUpdateWork:
    def __init__(self, client: PostgresClient) -> None:
        self._client = client

    async def create(
        self, project_id: int, created_by_id: int, mode: DocsMode, summary: str
    ) -> DocUpdate:
        return await self._client.create(
            DocUpdate,
            project_id=project_id,
            created_by_id=created_by_id,
            mode=mode,
            summary=summary,
        )

    async def get_by_id(self, update_id: int) -> DocUpdate | None:
        return await self._client.get_one(DocUpdate, id=update_id)
