"""Safe Telegram delivery for an ``AskEvent`` stream.

Drafts are private-chat only; a group receives the final message only. Drafts
are coalesced and throttled, are visibly preliminary, defang URLs, are never
persisted, and a retry repeats only the Telegram API call — it never restarts
retrieval or generation. This module is not wired into the router until the
published-snapshot publisher exists.
"""

import asyncio
import hashlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Protocol

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import InputRichMessage, Message, ReplyParameters

from app.llm.gateway import AskEvent, AskEventType, AskResult

logger = logging.getLogger(__name__)

DRAFT_TEXT_LIMIT = 4096
RICH_MESSAGE_LIMIT = 32768
DRAFT_PREFIX = "⏳ Предварительный ответ\n\n"
_URL_PATTERN = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9+.-]*://|www\.|t\.me/)\S*",
    re.IGNORECASE,
)
# Only these server messages justify a plain-text fallback. A generic 400 may
# mean missing rights, a bad chat/thread or invalid context and must propagate.
_FORMAT_ERROR_MARKERS = (
    "can't parse entities",
    "can't find end of the entity",
    "unsupported start tag",
    "unclosed",
    "rich message",
    "message is too long",
    "text is too long",
)


class _DeliveryStopped(Exception):
    """Stop won before the final message was delivered."""


def draft_id_for(chat_id: int, request_id: str) -> int:
    """Stable positive draft id for one request in one chat."""
    digest = hashlib.sha256(f"{chat_id}:{request_id}".encode()).digest()
    value = int.from_bytes(digest[:4], "big") & 0x7FFFFFFF
    return value or 1


class DeliveryBot(Protocol):
    async def send_rich_message_draft(
        self,
        chat_id: int,
        draft_id: int,
        rich_message: InputRichMessage,
        *,
        message_thread_id: int | None = None,
        can_stop: bool | None = None,
        keep_on_stop: bool | None = None,
    ) -> bool: ...

    async def send_rich_message(
        self,
        chat_id: int,
        rich_message: InputRichMessage,
        *,
        message_thread_id: int | None = None,
        reply_parameters: ReplyParameters | None = None,
    ) -> Message: ...

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: None = None,
        message_thread_id: int | None = None,
        reply_parameters: ReplyParameters | None = None,
    ) -> Message: ...


@dataclass(frozen=True, slots=True)
class DraftPolicy:
    min_interval_seconds: float = 0.7
    max_draft_chars: int = DRAFT_TEXT_LIMIT
    max_send_attempts: int = 2

    def __post_init__(self) -> None:
        if self.min_interval_seconds < 0:
            raise ValueError("min_interval_seconds должен быть неотрицательным")
        if self.max_draft_chars < 1:
            raise ValueError("max_draft_chars должен быть положительным")
        if self.max_send_attempts < 0:
            raise ValueError("max_send_attempts должен быть неотрицательным")


@dataclass(frozen=True, slots=True)
class DeliveryOutcome:
    terminal: AskEvent | None
    drafts_sent: int
    final_sent: bool
    aborted_by_stop: bool = False


RenderFinal = Callable[[AskResult], InputRichMessage]


class _TerminalState:
    """Serialize the one terminal decision; stop wins over final."""

    def __init__(self, stop_event: asyncio.Event | None) -> None:
        self._stop = stop_event
        self._lock = asyncio.Lock()
        self._decided = False
        self._event: AskEvent | None = None

    async def decide(self, event: AskEvent | None) -> AskEvent | None:
        async with self._lock:
            if self._decided:
                return self._event
            self._decided = True
            if event is not None and self._stop is not None and self._stop.is_set():
                self._event = None
            else:
                self._event = event
            return self._event

    async def abort(self) -> None:
        async with self._lock:
            self._decided = True
            self._event = None


class TelegramStreamDelivery:
    """Consume typed events and render draft/final Telegram messages."""

    def __init__(
        self,
        bot: DeliveryBot,
        chat_id: int,
        *,
        draft_id: int | None = None,
        message_thread_id: int | None = None,
        reply_parameters: ReplyParameters | None = None,
        policy: DraftPolicy | None = None,
    ) -> None:
        if draft_id is not None and draft_id < 1:
            raise ValueError("draft_id must be positive")
        self._bot = bot
        self._chat_id = chat_id
        self._draft_id = draft_id
        self._thread_id = message_thread_id
        self._reply_parameters = reply_parameters
        self._policy = policy or DraftPolicy()

    async def deliver(
        self,
        events: AsyncIterator[AskEvent],
        request_id: str,
        *,
        render_final: RenderFinal,
        render_fallback: Callable[[AskResult], str] | None = None,
        group: bool = False,
        stop_event: asyncio.Event | None = None,
    ) -> DeliveryOutcome:
        draft_id = (
            self._draft_id
            if self._draft_id is not None
            else draft_id_for(self._chat_id, request_id)
        )
        loop = asyncio.get_running_loop()
        parts: list[str] = []
        drafts_sent = 0
        last_draft_at: float | None = None
        terminal: AskEvent | None = None

        async with aclosing(events) as stream:
            async for event in stream:
                if stop_event is not None and stop_event.is_set():
                    break
                if event.type is AskEventType.DELTA:
                    if event.text:
                        parts.append(event.text)
                    text = "".join(parts)
                    if (
                        not group
                        and text
                        and (
                            last_draft_at is None
                            or loop.time() - last_draft_at >= self._policy.min_interval_seconds
                        )
                    ):
                        # Best-effort: a failed draft must not break the final.
                        if await self._send_draft(draft_id, text):
                            last_draft_at = loop.time()
                            drafts_sent += 1
                    continue
                if event.type is AskEventType.FINAL:
                    terminal = event
                    break
                if event.type in {AskEventType.ERROR, AskEventType.CANCELLED}:
                    # Partial text is never sent as a final answer.
                    terminal = event
                    break

        state = _TerminalState(stop_event)
        terminal = await state.decide(terminal)
        final_sent = False
        aborted = terminal is None and stop_event is not None and stop_event.is_set()
        if terminal is not None and terminal.type is AskEventType.FINAL:
            result = terminal.result
            if result is not None and result.found and result.answer:
                fallback = render_fallback(result) if render_fallback is not None else None
                if await self._send_final(render_final(result), fallback, stop_event):
                    final_sent = True
                else:
                    # A stop during the Telegram call/sleep wins: no final state.
                    await state.abort()
                    terminal = None
                    aborted = True
        return DeliveryOutcome(
            terminal=terminal,
            drafts_sent=drafts_sent,
            final_sent=final_sent,
            aborted_by_stop=aborted,
        )

    async def _send_draft(self, draft_id: int, text: str) -> bool:
        preview = _draft_preview(text, self._policy.max_draft_chars)
        rich = InputRichMessage(markdown=preview, skip_entity_detection=True)
        try:
            await self._bot.send_rich_message_draft(
                self._chat_id,
                draft_id,
                rich,
                message_thread_id=self._thread_id,
                can_stop=True,
                keep_on_stop=False,
            )
        except TelegramBadRequest as error:
            # Unsupported draft in this chat or rejected markup: final still wins.
            logger.warning("Draft rejected in chat %s: %s", self._chat_id, error.message)
            return False
        except (TelegramRetryAfter, TelegramNetworkError) as error:
            # Do not delay the stream for an ephemeral preview.
            logger.warning("Draft not sent in chat %s: %s", self._chat_id, type(error).__name__)
            return False
        return True

    async def _send_final(
        self,
        rich: InputRichMessage,
        fallback: str | None,
        stop_event: asyncio.Event | None,
    ) -> bool:
        plain = fallback if fallback is not None else (rich.markdown or rich.html or "")
        try:
            if len(rich.markdown or rich.html or "") > RICH_MESSAGE_LIMIT:
                await self._send_plain(plain, stop_event)
                return True
            try:
                await self._send_with_retry(
                    lambda: self._bot.send_rich_message(
                        self._chat_id,
                        rich,
                        message_thread_id=self._thread_id,
                        reply_parameters=self._reply_parameters,
                    ),
                    stop_event,
                )
                return True
            except TelegramBadRequest as error:
                if fallback is None or not _is_rich_format_error(error):
                    raise
                logger.warning("Rich message rejected in chat %s: %s", self._chat_id, error.message)
            await self._send_plain(plain, stop_event)
            return True
        except _DeliveryStopped:
            return False

    async def _send_plain(self, text: str, stop_event: asyncio.Event | None) -> None:
        for index, part in enumerate(_split_text(text)):
            reply = self._reply_parameters if index == 0 else None
            await self._send_with_retry(
                lambda part=part, reply=reply: self._bot.send_message(
                    self._chat_id,
                    part,
                    parse_mode=None,
                    message_thread_id=self._thread_id,
                    reply_parameters=reply,
                ),
                stop_event,
            )

    async def _send_with_retry(
        self,
        operation: Callable[[], Awaitable[object]],
        stop_event: asyncio.Event | None,
    ) -> None:
        attempt = 0
        while True:
            try:
                await self._run_or_stop(operation, stop_event)
                return
            except TelegramRetryAfter as error:
                # 429 means the message was not accepted. Timeouts are ambiguous
                # and are deliberately not retried.
                if attempt >= self._policy.max_send_attempts:
                    raise
                attempt += 1
                await self._sleep_or_stop(float(error.retry_after), stop_event)

    @staticmethod
    async def _run_or_stop(
        operation: Callable[[], Awaitable[object]],
        stop_event: asyncio.Event | None,
    ) -> object:
        if stop_event is None:
            return await operation()
        if stop_event.is_set():
            raise _DeliveryStopped
        operation_task = asyncio.ensure_future(operation())
        stop_task = asyncio.ensure_future(stop_event.wait())
        try:
            await asyncio.wait({operation_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if operation_task.done():
                # The API call completed first. It may already be delivered; a
                # later stop cannot un-send it.
                return operation_task.result()
            operation_task.cancel()
            await asyncio.gather(operation_task, return_exceptions=True)
            raise _DeliveryStopped
        finally:
            for task in (operation_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(operation_task, stop_task, return_exceptions=True)

    @staticmethod
    async def _sleep_or_stop(seconds: float, stop_event: asyncio.Event | None) -> None:
        if stop_event is None:
            await asyncio.sleep(seconds)
            return
        if stop_event.is_set():
            raise _DeliveryStopped
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=seconds)
        except TimeoutError:
            return
        raise _DeliveryStopped


def _draft_preview(text: str, limit: int) -> str:
    """Preliminary, URL-free preview; never a claim of a final answer."""
    safe = _URL_PATTERN.sub("[ссылка скрыта]", text)
    room = max(1, limit - len(DRAFT_PREFIX))
    return (DRAFT_PREFIX + safe[-room:])[:limit]


def _is_rich_format_error(error: TelegramBadRequest) -> bool:
    message = (error.message or "").casefold()
    return any(marker in message for marker in _FORMAT_ERROR_MARKERS)


def _split_text(text: str, limit: int = 4096) -> list[str]:
    if not text:
        return []
    return [text[index : index + limit] for index in range(0, len(text), limit)]
