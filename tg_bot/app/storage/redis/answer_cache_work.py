import hashlib

from app.storage.enums import RedisKey
from app.storage.redis.client import RedisClient


class RedisAnswerCacheWork:
    """Кеш готовых ответов. Ключ включает namespace, project и knowledge_version.

    Хешируется полный уже провалидированный текст без схлопывания внутренних
    пробелов/переносов и без casefold: иначе код с другой индентацией или
    регистрозависимым идентификатором получил бы чужой ответ. Trim на границах
    делает вызывающий слой (``QuestionService._validate``). Старые значения
    ``cache:answer:*`` не читаются.
    """

    def __init__(self, client: RedisClient, ttl_seconds: int) -> None:
        self._client = client
        self._ttl = ttl_seconds

    @staticmethod
    def _question_hash(question: str) -> str:
        return hashlib.sha256(question.encode()).hexdigest()

    def _key(self, project_id: int, knowledge_version: int, question: str) -> str:
        return RedisKey.ANSWER_CACHE.build(
            project_id=project_id,
            knowledge_version=knowledge_version,
            question_hash=self._question_hash(question),
        )

    async def get(self, project_id: int, knowledge_version: int, question: str) -> str | None:
        return await self._client.get(self._key(project_id, knowledge_version, question))

    async def set(self, project_id: int, knowledge_version: int, question: str, value: str) -> None:
        await self._client.set(self._key(project_id, knowledge_version, question), value, self._ttl)
