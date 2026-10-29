import asyncio
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class ProjectPasswordManager:
    """Секретный пароль проекта вида ``<public_id>-<секрет>``.

    По ``public_id`` бот находит проект: искать по самому паролю нельзя, в базе только
    хеш argon2. Хеширование нагружает CPU, поэтому выполняется в отдельном потоке.
    """

    _PUBLIC_ID_RE = re.compile(r"^[0-9a-f]{8}$")
    # Ровно формат generate(): 8 hex, дефис, token_urlsafe(18) = 24 символа
    _PASSWORD_RE = re.compile(r"[0-9a-f]{8}-[A-Za-z0-9_-]{24}")

    def __init__(self) -> None:
        self._hasher = PasswordHasher()

    @staticmethod
    def new_public_id() -> str:
        return secrets.token_hex(4)

    @staticmethod
    def generate(public_id: str) -> str:
        return f"{public_id}-{secrets.token_urlsafe(18)}"

    @classmethod
    def looks_like_password(cls, text: str) -> bool:
        """Текст — пароль проекта (по формату). Такой текст нельзя пересылать как вопрос."""
        return any(cls._PASSWORD_RE.fullmatch(word) for word in text.split())

    @classmethod
    def extract_public_id(cls, password: str) -> str | None:
        public_id, sep, secret = password.strip().partition("-")
        if not sep or not secret or not cls._PUBLIC_ID_RE.fullmatch(public_id):
            return None
        return public_id

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password_hash: str, password: str) -> bool:
        try:
            return await asyncio.to_thread(self._hasher.verify, password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
