from datetime import datetime

from app.storage.enums import RedisKey
from app.storage.redis.client import RedisClient


class RedisSchedulerWork:
    """Отметки планировщика: срабатывание задания уже взято в работу или отработано."""

    def __init__(self, client: RedisClient) -> None:
        self._client = client

    async def claim(self, job: str, due: datetime, ttl_seconds: int) -> bool:
        """Взять срабатывание. True — только у первого, кто успел (SET NX)."""
        return await self._client.set_if_absent(self._key(job, due), "1", ttl_seconds)

    async def release(self, job: str, due: datetime) -> None:
        await self._client.delete(self._key(job, due))

    @staticmethod
    def _key(job: str, due: datetime) -> str:
        return RedisKey.SCHEDULER_CLAIM.build(job=job, due=int(due.timestamp()))
