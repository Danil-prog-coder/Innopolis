import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SendReport:
    sent: int
    failed: int


class Notifier:
    """Рассылка сообщений с учётом лимитов Telegram (~30 сообщений/сек на бота).

    Работает на asyncio: при перезапуске бота незавершённая рассылка теряется.
    Когда это станет важно, рассылку стоит перенести в очередь задач (taskiq/arq).
    """

    def __init__(self, bot: Bot, rate_per_second: float = 25) -> None:
        self._bot = bot
        self._delay = 1 / rate_per_second
        self._tasks: set[asyncio.Task[SendReport]] = set()
        self._scheduled: set[asyncio.Task[SendReport]] = set()

    async def send_many(self, chat_ids: list[int], text: str, **kwargs: Any) -> SendReport:
        return await self._deliver(
            chat_ids, lambda chat_id: self._bot.send_message(chat_id, text, **kwargs)
        )

    async def send_photo_many(
        self, chat_ids: list[int], photo: str, caption: str, **kwargs: Any
    ) -> SendReport:
        return await self._deliver(
            chat_ids,
            lambda chat_id: self._bot.send_photo(chat_id, photo, caption=caption, **kwargs),
        )

    def send_many_in_background(
        self, chat_ids: list[int], text: str, **kwargs: Any
    ) -> asyncio.Task[SendReport]:
        task = asyncio.create_task(self.send_many(chat_ids, text, **kwargs))
        self._tasks.add(task)  # ссылка, чтобы задачу не собрал GC
        task.add_done_callback(self._tasks.discard)
        return task

    def remove_markup_later(self, chat_id: int, message_id: int, delay_seconds: float) -> None:
        """Снять кнопки с сообщения через delay_seconds (например, кнопку копирования пароля).

        На asyncio: если бот перезапустится раньше, кнопки останутся.
        """

        async def remove() -> SendReport:
            await asyncio.sleep(delay_seconds)
            try:
                await self._bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=message_id, reply_markup=None
                )
            except TelegramBadRequest as error:
                logger.info("Cannot remove markup from %s: %s", message_id, error.message)
                return SendReport(sent=0, failed=1)
            return SendReport(sent=1, failed=0)

        task = asyncio.create_task(remove())
        self._scheduled.add(task)
        task.add_done_callback(self._scheduled.discard)

    async def wait_background(self) -> None:
        """Дождаться фоновых рассылок (остановка бота и тесты)."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def shutdown(self) -> None:
        """Остановка бота: рассылки дослать, отложенные действия отменить."""
        await self.wait_background()
        for task in self._scheduled:
            task.cancel()
        if self._scheduled:
            await asyncio.gather(*self._scheduled, return_exceptions=True)

    async def _deliver(
        self, chat_ids: list[int], send: Callable[[int], Awaitable[Any]]
    ) -> SendReport:
        sent = failed = 0
        for chat_id in dict.fromkeys(chat_ids):  # без дублей, порядок сохраняется
            if await self._send_one(chat_id, send):
                sent += 1
            else:
                failed += 1
            await asyncio.sleep(self._delay)
        return SendReport(sent=sent, failed=failed)

    async def _send_one(self, chat_id: int, send: Callable[[int], Awaitable[Any]]) -> bool:
        for attempt in range(2):
            try:
                await send(chat_id)
                return True
            except TelegramRetryAfter as error:
                if attempt == 1:
                    break
                await asyncio.sleep(error.retry_after)
            except (TelegramForbiddenError, TelegramBadRequest) as error:
                logger.info("Notifier: cannot deliver to %s: %s", chat_id, error.message)
                return False
        logger.warning("Notifier: flood limit for %s, message dropped", chat_id)
        return False
