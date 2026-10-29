from app.storage.postgres.client import PostgresClient
from app.storage.postgres.models import User


class PostgresUserWork:
    def __init__(self, client: PostgresClient) -> None:
        self._client = client

    async def upsert_from_telegram(
        self, telegram_id: int, username: str | None, full_name: str
    ) -> User:
        """Вызывается на каждый апдейт: если данные не изменились — один SELECT без записи."""
        full_name = full_name[:256]
        user = await self.get_by_telegram_id(telegram_id)
        if user is not None and user.username == username and user.full_name == full_name:
            return user
        return await self._client.upsert(
            User, {"telegram_id": telegram_id}, username=username, full_name=full_name
        )

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return await self._client.get_one(User, telegram_id=telegram_id)

    async def get_by_id(self, user_id: int) -> User | None:
        return await self._client.get_one(User, id=user_id)

    async def set_current_project(self, user_id: int, project_id: int | None) -> None:
        await self._client.update(User, {"id": user_id}, current_project_id=project_id)

    async def reset_current_project_if(self, user_id: int, project_id: int) -> None:
        """Сбросить текущий проект пользователя, если это именно ``project_id``."""
        await self._client.update(
            User, {"id": user_id, "current_project_id": project_id}, current_project_id=None
        )
