from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser

from app.core.depends import DependsResolver
from app.dependencies import get_obj_user_work


class UserMiddleware(BaseMiddleware):
    """Outer-middleware: регистрирует/обновляет отправителя и кладёт его в data["db_user"]."""

    def __init__(self, resolver: DependsResolver) -> None:
        self._resolver = resolver

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user: TgUser | None = data.get("event_from_user")
        if tg_user is not None and not tg_user.is_bot:
            user_work = await self._resolver.resolve(get_obj_user_work)
            data["db_user"] = await user_work.upsert_from_telegram(
                tg_user.id, tg_user.username, tg_user.full_name
            )
        return await handler(event, data)
