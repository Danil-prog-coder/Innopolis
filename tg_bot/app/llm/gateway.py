"""Граница между ботом и ИИ-частью проекта.

Бот ничего не знает о том, как устроен ИИ: он вызывает методы ``LLMGateway``.
Сейчас в DI зарегистрирован ``StubLLMGateway``. Реальную реализацию пишет
отвечающий за ИИ: наследник ``LLMGateway`` подменяется в ``get_obj_llm_gateway``
(app/dependencies.py), остальной код бота не меняется.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.enums import DocsMode

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AskResult:
    found: bool
    answer: str | None


@dataclass(frozen=True, slots=True)
class DocFile:
    filename: str
    content: bytes


class LLMUnavailableError(Exception):
    """ИИ-часть недоступна (сеть, таймаут, ошибка сервиса). Бот просит повторить позже."""


class LLMGateway(ABC):
    """Что бот ожидает от ИИ-части. Каждый вызов получает project_id:
    поиск и знания строго ограничены документацией этого проекта."""

    @abstractmethod
    async def ask(self, project_id: int, question: str) -> AskResult:
        """Ответить на вопрос по документации проекта.

        ``found=False`` — в документации ответа нет, бот передаст вопрос админу.
        ``answer`` — в Markdown (GFM: заголовки, списки, таблицы, блоки кода): бот отправит
        его Rich Message, а если Telegram не примет разметку — как обычный текст.
        """

    @abstractmethod
    async def ask_image(self, project_id: int, image: bytes, caption: str | None) -> AskResult:
        """Ответить по скриншоту (с необязательной подписью пользователя)."""

    @abstractmethod
    async def upload_documents(self, project_id: int, mode: DocsMode, files: list[DocFile]) -> str:
        """Проиндексировать документацию проекта.

        ``APPEND`` — добавить файлы к уже загруженным, ``REPLACE`` — заменить всю
        документацию. Вернуть краткую сводку изменений для кнопки «Что изменилось»
        (в Markdown, как ответ в ``ask``).
        """

    @abstractmethod
    async def save_knowledge(self, project_id: int, question: str, answer: str) -> None:
        """Сохранить проверенный админом ответ в базу знаний проекта."""

    async def close(self) -> None:  # noqa: B027 — необязательный хук, не абстрактный
        """Освободить ресурсы при остановке бота."""


class StubLLMGateway(LLMGateway):
    """Заглушка без ИИ: на вопросы ответа нет, документация не индексируется."""

    async def ask(self, project_id: int, question: str) -> AskResult:
        return AskResult(found=False, answer=None)

    async def ask_image(self, project_id: int, image: bytes, caption: str | None) -> AskResult:
        return AskResult(found=False, answer=None)

    async def upload_documents(self, project_id: int, mode: DocsMode, files: list[DocFile]) -> str:
        action = "заменена" if mode == DocsMode.REPLACE else "дополнена"
        names = ", ".join(f.filename for f in files)
        return f"Документация {action}. Файлы: {names}"

    async def save_knowledge(self, project_id: int, question: str, answer: str) -> None:
        logger.info("StubLLMGateway: save_knowledge skipped for project %s", project_id)
