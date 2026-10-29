from app.storage.enums import RedisKey
from app.storage.redis.client import RedisClient


class RedisPasswordRevealWork:
    """Пароль только что созданного проекта для кнопки «Показать пароль».

    Пароль хранится в открытом виде, но только до истечения TTL: этого достаточно,
    чтобы создатель его сохранил. В Postgres лежит лишь хеш.
    """

    def __init__(self, client: RedisClient, ttl_seconds: int) -> None:
        self._client = client
        self._ttl = ttl_seconds

    async def put(self, project_id: int, password: str) -> None:
        await self._client.set(
            RedisKey.PASSWORD_REVEAL.build(project_id=project_id), password, self._ttl
        )

    async def get(self, project_id: int) -> str | None:
        return await self._client.get(RedisKey.PASSWORD_REVEAL.build(project_id=project_id))
