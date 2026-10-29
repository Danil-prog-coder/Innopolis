import hashlib

from app.storage.enums import RedisKey
from app.storage.redis.client import RedisClient


class RedisAnswerCacheWork:
    """Кеш ответов на вопросы. Ключ включает версию документации проекта,
    поэтому после обновления документации старые ответы просто перестают находиться."""

    def __init__(self, client: RedisClient, ttl_seconds: int) -> None:
        self._client = client
        self._ttl = ttl_seconds

    @staticmethod
    def _question_hash(question: str) -> str:
        normalized = " ".join(question.split()).casefold()
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _key(self, project_id: int, docs_version: int, question: str) -> str:
        return RedisKey.ANSWER_CACHE.build(
            project_id=project_id,
            docs_version=docs_version,
            question_hash=self._question_hash(question),
        )

    async def get(self, project_id: int, docs_version: int, question: str) -> str | None:
        return await self._client.get(self._key(project_id, docs_version, question))

    async def set(self, project_id: int, docs_version: int, question: str, answer: str) -> None:
        await self._client.set(self._key(project_id, docs_version, question), answer, self._ttl)
