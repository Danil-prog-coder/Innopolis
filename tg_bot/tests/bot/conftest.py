"""Обвязка для тестов хендлеров: настоящий Dispatcher, вызовы Bot API пишутся в MockedSession."""

import itertools
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.methods import GetFile, GetMe, TelegramMethod
from aiogram.types import (
    CallbackQuery,
    Chat,
    Document,
    File,
    InlineKeyboardMarkup,
    Message,
    MessageEntity,
    PhotoSize,
    Update,
)
from aiogram.types import User as TgUser

from app import dependencies
from app.bot.dispatcher import build_dispatcher
from app.bot.notifier import Notifier
from app.core.depends import DependsResolver

BOT_ID = 42
BOT_USERNAME = "test_bot"

ADMIN_TG = TgUser(id=1001, is_bot=False, first_name="Admin", username="admin")
USER_TG = TgUser(id=1002, is_bot=False, first_name="User", username="user")
SECOND_ADMIN_TG = TgUser(id=1003, is_bot=False, first_name="Second Admin", username="admin2")
OUTSIDER_TG = TgUser(id=1004, is_bot=False, first_name="Outsider", username="outsider")
FRESH_TG = TgUser(id=2001, is_bot=False, first_name="Fresh")
BOT_TG = TgUser(id=BOT_ID, is_bot=True, first_name="Bot", username=BOT_USERNAME)

_MESSAGE_RETURNING = {
    "SendMessage",
    "SendRichMessage",
    "SendPhoto",
    "EditMessageText",
    "SendDocument",
}


class MockedSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self._ids = itertools.count(5000)
        self.file_content = b"file-bytes"
        # Имена методов Bot API, которые должны падать с TelegramBadRequest
        self.failing_methods: set[str] = set()

    # Сигнатуры make_request/stream_content задаёт BaseSession aiogram
    async def make_request(self, bot: Bot, method: TelegramMethod[Any], timeout: int | None = None):
        self.requests.append(method)
        name = type(method).__name__
        if name in self.failing_methods:
            raise TelegramBadRequest(method=method, message="not enough rights")
        if isinstance(method, GetMe):
            return BOT_TG
        if isinstance(method, GetFile):
            return File(file_id=method.file_id, file_unique_id="u", file_path=f"f/{method.file_id}")
        if name in _MESSAGE_RETURNING:
            chat_id = int(getattr(method, "chat_id", 0) or 0)
            return Message(
                message_id=next(self._ids),
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="private"),
                from_user=BOT_TG,
                text=getattr(method, "text", None),
            )
        return True

    async def stream_content(
        self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True
    ):
        yield self.file_content

    async def close(self) -> None:
        pass

    # --- Помощники для проверок ---

    def all(self, method_type: type) -> list[Any]:
        return [r for r in self.requests if isinstance(r, method_type)]

    def last(self, method_type: type) -> Any:
        found = self.all(method_type)
        calls = [type(r).__name__ for r in self.requests]
        assert found, f"{method_type.__name__} was not called; calls: {calls}"
        return found[-1]

    def buttons(self) -> list[Any]:
        result = []
        for request in self.requests:
            markup = getattr(request, "reply_markup", None)
            if isinstance(markup, InlineKeyboardMarkup):
                result.extend(b for row in markup.inline_keyboard for b in row)
        return result

    def clear(self) -> None:
        self.requests.clear()


class Feeder:
    """Подаёт апдейты в диспетчер так, будто они пришли от Telegram."""

    def __init__(self, bot: Bot, dp) -> None:
        self._bot = bot
        self._dp = dp
        self._ids = itertools.count(1)

    def _message(self, from_user: TgUser, chat_id: int | None, chat_type: str, **fields) -> Message:
        chat_id = from_user.id if chat_id is None else chat_id
        return Message(
            message_id=next(self._ids),
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=from_user,
            **fields,
        )

    async def message(
        self,
        text: str | None = None,
        *,
        from_user: TgUser = USER_TG,
        chat_id: int | None = None,
        chat_type: str = "private",
        entities: list[dict] | None = None,
        document: dict | None = None,
        photo: bool = False,
        caption: str | None = None,
    ) -> Message:
        fields: dict[str, Any] = {}
        if text is not None:
            fields["text"] = text
        if entities:
            fields["entities"] = [MessageEntity(**e) for e in entities]
        if document:
            fields["document"] = Document(**document)
        if photo:
            fields["photo"] = [
                PhotoSize(file_id="small", file_unique_id="s", width=90, height=90),
                PhotoSize(file_id="big", file_unique_id="b", width=1280, height=720),
            ]
        if caption is not None:
            fields["caption"] = caption
        message = self._message(from_user, chat_id, chat_type, **fields)
        await self._dp.feed_update(self._bot, Update(update_id=next(self._ids), message=message))
        return message

    async def callback(self, data: str, *, from_user: TgUser = USER_TG) -> CallbackQuery:
        message = self._message(BOT_TG, from_user.id, "private", text="menu")
        query = CallbackQuery(
            id=str(next(self._ids)),
            from_user=from_user,
            chat_instance="ci",
            data=data,
            message=message,
        )
        await self._dp.feed_update(
            self._bot, Update(update_id=next(self._ids), callback_query=query)
        )
        return query


@pytest.fixture
def session() -> MockedSession:
    return MockedSession()


@pytest.fixture
def bot(session) -> Bot:
    return Bot("42:TEST", session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


@pytest.fixture
def notifier(bot) -> Notifier:
    return Notifier(bot, rate_per_second=10_000)


@pytest.fixture
def resolver(pg_client, redis_client, fake_llm, settings, bot, notifier) -> DependsResolver:
    resolver = DependsResolver()
    resolver.overrides.update(
        {
            dependencies.get_obj_postgres_client: lambda: pg_client,
            dependencies.get_obj_redis_client: lambda: redis_client,
            dependencies.get_obj_llm_gateway: lambda: fake_llm,
            dependencies.get_obj_settings: lambda: settings,
            dependencies.get_obj_bot: lambda: bot,
            dependencies.get_obj_notifier: lambda: notifier,
        }
    )
    return resolver


@pytest.fixture
async def dp(redis_client, resolver) -> AsyncGenerator[Any]:
    yield build_dispatcher(RedisStorage(redis=redis_client.redis), resolver, catch_errors=False)


@pytest.fixture
def feed(bot, dp) -> Feeder:
    return Feeder(bot, dp)
