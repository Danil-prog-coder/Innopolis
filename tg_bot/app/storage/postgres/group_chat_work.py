from app.storage.postgres.client import PostgresClient
from app.storage.postgres.models import GroupChat


class PostgresGroupChatWork:
    def __init__(self, client: PostgresClient) -> None:
        self._client = client

    async def bind(self, chat_id: int, project_id: int, bound_by_telegram_id: int) -> GroupChat:
        """Привязать чат к проекту. Повторная привязка переносит чат в другой проект."""
        return await self._client.upsert(
            GroupChat,
            {"chat_id": chat_id},
            project_id=project_id,
            bound_by_telegram_id=bound_by_telegram_id,
        )

    async def get_by_chat_id(self, chat_id: int) -> GroupChat | None:
        return await self._client.get_one(GroupChat, chat_id=chat_id)
