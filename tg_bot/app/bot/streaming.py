"""Постепенный показ ответа, как в ChatGPT/Claude.

Пока ИИ думает, пользователь видит «Thinking…» (в личке — черновик Bot API, в группе —
сообщение-заглушка). Когда ответ готов, он «допечатывается» несколькими кадрами и
отправляется обычным Rich Message — черновик только превью, в истории остаётся финал.

Сейчас ``LLMGateway.ask`` отдаёт ответ целиком, поэтому кадры — проигрывание готового
текста (``replay_frames``). Когда ИИ-часть начнёт отдавать ответ потоком, кадрами станут
настоящие куски ответа, а превью (``AnswerPreview``) останутся теми же.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InputRichMessage, Message

from app.bot import texts
from app.config import Settings
from app.services.question_service import AskOutcome

logger = logging.getLogger(__name__)

ELLIPSIS = "…"

# --- Разметка недописанного ответа ---

# Построчные шаблоны: пробелы только [ \t] — \s захватил бы перевод строки и склеил строки
_FENCE_LINE = re.compile(r"^[ \t]*```.*$", re.MULTILINE)
_FENCE_LINE_WITH_BREAK = re.compile(r"^[ \t]*```.*(?:\n|$)", re.MULTILINE)
_LONE_BACKTICK = re.compile(r"(?<!`)`(?!`)")
_HEADING = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_BULLET = re.compile(r"^([ \t]*)[-*+][ \t]+", re.MULTILINE)
_QUOTE = re.compile(r"^[ \t]*>[ \t]?", re.MULTILINE)
_TABLE_SEPARATOR = re.compile(
    r"^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)*\|?[ \t]*(?:\n|$)", re.MULTILINE
)
_TABLE_ROW = re.compile(r"^[ \t]*\|(.*?)\|?[ \t]*$", re.MULTILINE)
_LINK = re.compile(r"\[([^\]\n]+)\]\([^)\s]*\)")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)
_UNDERSCORE_BOLD = re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", re.DOTALL)
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.DOTALL)
_CODE = re.compile(r"`([^`\n]+)`")
_MANY_NEWLINES = re.compile(r"\n{3,}")


def balance_markdown(markdown: str) -> str:
    """Сделать оборванный на полуслове Markdown пригодным для Rich-черновика.

    Незакрытый блок кода закрываем, непарный ``**``/``~~``/`` ` `` убираем — иначе разметка
    «протекает» на весь остальной текст. Финальный ответ это не затрагивает.
    """
    fences = len(_FENCE_LINE.findall(markdown))
    if fences % 2:
        # Внутри блока кода инлайн-разметки нет — достаточно закрыть блок
        return f"{markdown}\n```"
    for marker in ("**", "~~"):
        if markdown.count(marker) % 2:
            index = markdown.rfind(marker)
            markdown = markdown[:index] + markdown[index + len(marker) :]
    lone = list(_LONE_BACKTICK.finditer(markdown))
    if len(lone) % 2:
        index = lone[-1].start()
        markdown = markdown[:index] + markdown[index + 1 :]
    return markdown


def plain_preview(markdown: str) -> str:
    """Markdown → читаемый обычный текст: без ``**``, ``#``, ``|---|`` и прочих служебных знаков.

    Нужен там, где Rich Message недоступен: редактирование сообщения в группе и запасной
    вариант черновика, если Telegram не принял разметку.
    """
    text = _FENCE_LINE_WITH_BREAK.sub("", markdown)
    text = _TABLE_SEPARATOR.sub("", text)
    text = _TABLE_ROW.sub(lambda m: " │ ".join(cell.strip() for cell in m[1].split("|")), text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub(r"\1• ", text)
    text = _QUOTE.sub("", text)
    text = _LINK.sub(r"\1", text)
    for pattern in (_BOLD, _UNDERSCORE_BOLD, _STRIKE, _CODE):
        text = pattern.sub(r"\1", text)
    # Хвост оборванной разметки: «**жирн» без пары
    text = text.replace("**", "").replace("~~", "")
    text = _LONE_BACKTICK.sub("", text)
    return _MANY_NEWLINES.sub("\n\n", text).strip()


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len(ELLIPSIS)].rstrip() + ELLIPSIS


def replay_frames(text: str, count: int) -> list[str]:
    """Растущие префиксы ответа (``count - 1`` штук), обрезанные по границе слова.

    Полного текста среди кадров нет: его сразу показывает финальное сообщение, лишний кадр —
    лишний запрос к Telegram. ``count < 2`` — анимации нет.
    """
    if count < 2 or not text:
        return []
    frames: list[str] = []
    for step in range(1, count):
        cut = len(text) * step // count
        space = max(text.rfind(" ", 0, cut + 1), text.rfind("\n", 0, cut + 1))
        prefix = text[: space if space > 0 else cut].rstrip()
        if prefix and prefix != text and (not frames or prefix != frames[-1]):
            frames.append(prefix)
    return frames


# --- Куда показываем недописанный ответ ---


class AnswerPreview(Protocol):
    """Превью ответа. Сбой превью никогда не роняет ответ: методы глотают ошибки Telegram."""

    async def start(self) -> None:
        """Показать «думаю…»."""

    async def keepalive(self) -> None:
        """Не дать превью погаснуть, пока ИИ долго думает."""

    async def show(self, markdown: str) -> None:
        """Показать недописанный ответ."""

    async def finish(self) -> None:
        """Убрать следы превью перед финальным сообщением."""


class PrivateDraftPreview:
    """Черновик в личке (sendRichMessageDraft / sendMessageDraft) с кнопкой «Стоп».

    Черновик живёт ~30 секунд и исчезает сам, когда бот отправляет сообщение, — убирать его
    не нужно. ``draft_id`` — id сообщения с вопросом: он ненулевой и уникален в чате.
    """

    def __init__(self, bot: Bot, chat_id: int, draft_id: int, thread_id: int | None) -> None:
        self._bot = bot
        self._chat_id = chat_id
        self._draft_id = draft_id
        self._thread_id = thread_id
        self._enabled = True
        self._rich = True
        self._shown: str | None = None

    async def start(self) -> None:
        # Пустой текст Telegram показывает как «Thinking…»
        await self._plain("")

    async def keepalive(self) -> None:
        if self._shown is None:
            await self._plain("")
        else:
            await self.show(self._shown)

    async def show(self, markdown: str) -> None:
        if not self._enabled:
            return  # Telegram отказал в черновиках — не долбим его каждым кадром
        self._shown = markdown
        if self._rich:
            rich = InputRichMessage(
                markdown=balance_markdown(truncate(markdown, texts.TELEGRAM_RICH_MESSAGE_LIMIT))
            )
            try:
                await self._bot.send_rich_message_draft(
                    self._chat_id,
                    self._draft_id,
                    rich,
                    message_thread_id=self._thread_id,
                    can_stop=True,
                )
                return
            except TelegramRetryAfter:
                return  # кадр пропускаем: следующий всё равно покажет больше текста
            except TelegramBadRequest as error:
                # Битая разметка от ИИ — дальше показываем обычным текстом
                logger.info("Rich draft rejected in chat %s: %s", self._chat_id, error.message)
                self._rich = False
            except TelegramAPIError as error:
                logger.warning("Rich draft failed in chat %s: %s", self._chat_id, error)
                return
        await self._plain(truncate(plain_preview(markdown), texts.TELEGRAM_MESSAGE_LIMIT))

    async def finish(self) -> None:
        return None

    async def _plain(self, text: str) -> None:
        if not self._enabled:
            return
        try:
            await self._bot.send_message_draft(
                self._chat_id,
                self._draft_id,
                message_thread_id=self._thread_id,
                text=text,
                parse_mode=None,
                can_stop=True,
            )
        except TelegramRetryAfter:
            return
        except TelegramBadRequest as error:
            # Например, черновики недоступны этому боту — отвечаем как раньше, без превью
            logger.warning("Message draft rejected in chat %s: %s", self._chat_id, error.message)
            self._enabled = False
        except TelegramAPIError as error:
            logger.warning("Message draft failed in chat %s: %s", self._chat_id, error)


class GroupEditPreview:
    """В группах черновиков нет: шлём заглушку ответом на вопрос и редактируем её.

    Rich Message редактировать нельзя, поэтому в заглушке — текст без разметки, а перед
    финальным Rich-ответом заглушка удаляется.
    """

    def __init__(self, message: Message) -> None:
        self._message = message
        self._placeholder: Message | None = None
        self._shown: str | None = None

    async def start(self) -> None:
        try:
            self._placeholder = await self._message.reply(texts.ANSWER_PLACEHOLDER, parse_mode=None)
        except TelegramAPIError as error:
            logger.warning("Cannot send placeholder to chat %s: %s", self._message.chat.id, error)

    async def keepalive(self) -> None:
        return None  # заглушка — обычное сообщение, сама не гаснет

    async def show(self, markdown: str) -> None:
        if self._placeholder is None:
            return
        text = truncate(plain_preview(markdown), texts.TELEGRAM_MESSAGE_LIMIT)
        if not text or text == self._shown:
            return
        try:
            await self._message.bot.edit_message_text(
                text,
                chat_id=self._placeholder.chat.id,
                message_id=self._placeholder.message_id,
                parse_mode=None,
            )
            self._shown = text
        except TelegramRetryAfter:
            return
        except TelegramAPIError as error:
            logger.warning("Cannot edit placeholder in chat %s: %s", self._message.chat.id, error)

    async def finish(self) -> None:
        if self._placeholder is None:
            return
        placeholder, self._placeholder = self._placeholder, None
        try:
            await self._message.bot.delete_message(placeholder.chat.id, placeholder.message_id)
        except TelegramAPIError as error:
            logger.warning("Cannot delete placeholder in chat %s: %s", self._message.chat.id, error)


# --- Остановка генерации ---


class GenerationRegistry:
    """Идущие генерации ответов: по нажатию «Стоп» соответствующая задача отменяется.

    Хранится в памяти процесса — бот работает одним инстансом (long polling). Если
    инстансов станет несколько, апдейт «Стоп» может прийти не в тот процесс: тогда нужен
    общий флаг остановки в Redis.
    """

    def __init__(self) -> None:
        self._tasks: dict[tuple[int, int], asyncio.Task[AskOutcome]] = {}

    def add(self, chat_id: int, draft_id: int, task: asyncio.Task[AskOutcome]) -> None:
        self._tasks[(chat_id, draft_id)] = task

    def discard(self, chat_id: int, draft_id: int, task: asyncio.Task[AskOutcome]) -> None:
        if self._tasks.get((chat_id, draft_id)) is task:
            del self._tasks[(chat_id, draft_id)]

    def stop(self, chat_id: int, draft_id: int) -> bool:
        """``False`` — такой генерации нет или она уже закончилась."""
        task = self._tasks.get((chat_id, draft_id))
        if task is None or task.done():
            return False
        task.cancel()
        return True

    def __len__(self) -> int:
        return len(self._tasks)


# --- Сборка ---


@dataclass(frozen=True, slots=True)
class StreamTiming:
    frame_interval: float
    replay_seconds: float
    keepalive_seconds: float

    @classmethod
    def private(cls, settings: Settings) -> "StreamTiming":
        return cls(
            settings.answer_stream_draft_interval_seconds,
            settings.answer_stream_replay_seconds,
            settings.answer_stream_keepalive_seconds,
        )

    @classmethod
    def group(cls, settings: Settings) -> "StreamTiming":
        # Редактирования в группе Telegram ограничивает строже, чем черновики в личке
        return cls(
            settings.answer_stream_edit_interval_seconds,
            settings.answer_stream_replay_seconds,
            settings.answer_stream_keepalive_seconds,
        )

    @property
    def frame_count(self) -> int:
        return round(self.replay_seconds / self.frame_interval)


AskCall = Callable[[], Awaitable[AskOutcome]]


async def _wait_answer(preview: AnswerPreview, ask: AskCall, timing: StreamTiming) -> AskOutcome:
    task = asyncio.ensure_future(ask())
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=timing.keepalive_seconds)
            if done:
                return task.result()
            await preview.keepalive()
    finally:
        if not task.done():
            # Генерацию остановили — бросаем и запрос к ИИ, чтобы не тратить его впустую
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task


async def _generate(
    preview: AnswerPreview,
    ask: AskCall,
    timing: StreamTiming,
    sleep: Callable[[float], Awaitable[None]],
) -> AskOutcome:
    await preview.start()
    outcome = await _wait_answer(preview, ask, timing)
    if outcome.answer is not None:
        # Пауза и после последнего кадра — иначе его сразу перекроет финальное сообщение
        for frame in replay_frames(outcome.answer, timing.frame_count):
            await preview.show(frame)
            await sleep(timing.frame_interval)
    return outcome


async def stream_answer(
    preview: AnswerPreview,
    ask: AskCall,
    timing: StreamTiming,
    registry: GenerationRegistry | None = None,
    stop_key: tuple[int, int] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AskOutcome | None:
    """Получить ответ, показывая его по ходу. ``None`` — пользователь нажал «Стоп».

    Ошибки ``ask`` (``DomainError``, ``LLMUnavailableError``) пробрасываются как есть, превью
    к этому моменту уже убрано.
    """
    task = asyncio.ensure_future(_generate(preview, ask, timing, sleep))
    if registry is not None and stop_key is not None:
        registry.add(*stop_key, task)
    try:
        await asyncio.wait({task})
    except asyncio.CancelledError:
        task.cancel()  # остановка бота: не оставляем генерацию висеть
        raise
    finally:
        if registry is not None and stop_key is not None:
            registry.discard(*stop_key, task)
        await preview.finish()
    if task.cancelled():
        return None
    return task.result()
