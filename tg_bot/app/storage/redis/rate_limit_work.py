from app.core.enums import RateLimitScope
from app.storage.enums import RedisKey
from app.storage.redis.client import RedisClient


class RedisRateLimitWork:
    """Ограничение частоты: фиксированное окно на счётчике INCR + EXPIRE."""

    def __init__(self, client: RedisClient) -> None:
        self._client = client

    async def hit(
        self, scope: RateLimitScope, subject_id: int, limit: int, window_seconds: int
    ) -> bool:
        """Учесть попытку. True — попытка разрешена, False — лимит исчерпан."""
        key = RedisKey.RATE_LIMIT.build(scope=scope, subject_id=subject_id)
        return await self._client.incr(key, window_seconds) <= limit

    async def reset(self, scope: RateLimitScope, subject_id: int) -> None:
        await self._client.delete(RedisKey.RATE_LIMIT.build(scope=scope, subject_id=subject_id))
