from pydantic import BaseModel

from app.storage.enums import RedisKey
from app.storage.redis.client import RedisClient


class DraftFile(BaseModel):
    file_id: str
    filename: str
    size: int


class RedisDocsDraftWork:
    """Файлы документации, которые админ прислал, но ещё не отправил кнопкой «Готово».

    Список Redis вместо данных FSM: файлы альбома приходят параллельными апдейтами,
    и read-modify-write в FSM терял бы часть из них. RPUSH атомарен.
    """

    def __init__(self, client: RedisClient, ttl_seconds: int = 3600) -> None:
        self._client = client
        self._ttl = ttl_seconds

    @staticmethod
    def _key(telegram_id: int, project_id: int) -> str:
        return RedisKey.DOCS_DRAFT.build(telegram_id=telegram_id, project_id=project_id)

    async def add(self, telegram_id: int, project_id: int, file: DraftFile, max_files: int) -> int:
        """Добавить файл. Вернуть число файлов в черновике или -1, если лимит превышен."""
        key = self._key(telegram_id, project_id)
        value = file.model_dump_json()
        length = await self._client.list_push(key, value, self._ttl)
        if length > max_files:
            await self._client.list_remove(key, value)
            return -1
        return length

    async def list(self, telegram_id: int, project_id: int) -> list[DraftFile]:
        raw = await self._client.list_all(self._key(telegram_id, project_id))
        return [DraftFile.model_validate_json(item) for item in raw]

    async def clear(self, telegram_id: int, project_id: int) -> None:
        await self._client.delete(self._key(telegram_id, project_id))

    async def try_lock(self, telegram_id: int, project_id: int, ttl_seconds: int) -> bool:
        """Захватить отправку черновика. False — его уже отправляет параллельный апдейт."""
        key = RedisKey.DOCS_UPLOAD_LOCK.build(telegram_id=telegram_id, project_id=project_id)
        return await self._client.set_if_absent(key, "1", ttl_seconds)

    async def unlock(self, telegram_id: int, project_id: int) -> None:
        key = RedisKey.DOCS_UPLOAD_LOCK.build(telegram_id=telegram_id, project_id=project_id)
        await self._client.delete(key)
