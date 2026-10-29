from redis.asyncio import Redis


class RedisClient:
    """Подключение к Redis и универсальные операции с ключами. О сущностях не знает."""

    def __init__(self, url: str, redis: Redis | None = None) -> None:
        self._url = url
        self._redis = redis

    @property
    def redis(self) -> Redis:
        if self._redis is None:
            raise RuntimeError("RedisClient is not connected")
        return self._redis

    async def connect(self) -> None:
        if self._redis is None:
            self._redis = Redis.from_url(self._url, decode_responses=True)
        await self._redis.ping()

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def get(self, key: str) -> str | None:
        return await self.redis.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        await self.redis.set(key, value, ex=ttl_seconds)

    async def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        """Атомарно создать ключ, только если его нет (SET NX EX)."""
        return bool(await self.redis.set(key, value, ex=ttl_seconds, nx=True))

    async def delete(self, key: str) -> int:
        return await self.redis.delete(key)

    async def incr(self, key: str, ttl_seconds: int) -> int:
        """Увеличить счётчик. TTL ставится только при создании ключа (окно не сдвигается)."""
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, ttl_seconds, nx=True)
            value, _ = await pipe.execute()
        return int(value)

    async def list_push(self, key: str, value: str, ttl_seconds: int) -> int:
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.rpush(key, value)
            pipe.expire(key, ttl_seconds)
            length, _ = await pipe.execute()
        return int(length)

    async def list_remove(self, key: str, value: str) -> int:
        """Удалить одно (последнее) вхождение значения из списка."""
        return await self.redis.lrem(key, -1, value)

    async def list_all(self, key: str) -> list[str]:
        return await self.redis.lrange(key, 0, -1)
