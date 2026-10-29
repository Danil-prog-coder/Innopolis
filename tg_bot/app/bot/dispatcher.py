import logging

from aiogram import Dispatcher
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.base import BaseStorage
from aiogram.types import ErrorEvent

from app.bot import texts
from app.bot.middlewares import UserMiddleware
from app.bot.routers import setup_routers
from app.core.depends import DependsMiddleware, DependsResolver

logger = logging.getLogger(__name__)


async def on_error(event: ErrorEvent) -> bool:
    """Непойманная ошибка: в лог с трейсбеком, пользователю — короткое сообщение вместо тишины."""
    logger.exception(
        "Unhandled error in update %s", event.update.update_id, exc_info=event.exception
    )
    update = event.update
    try:
        if update.callback_query is not None:
            await update.callback_query.answer(texts.INTERNAL_ERROR, show_alert=True)
        elif update.message is not None and update.message.chat.type == ChatType.PRIVATE:
            await update.message.answer(texts.INTERNAL_ERROR)
    except TelegramAPIError:
        logger.warning("Cannot report error to user for update %s", update.update_id)
    return True


def build_dispatcher(
    storage: BaseStorage, resolver: DependsResolver, catch_errors: bool = True
) -> Dispatcher:
    """``catch_errors=False`` — для тестов: исключение хендлера роняет тест, а не прячется."""
    dp = Dispatcher(storage=storage)
    user_middleware = UserMiddleware(resolver)
    depends_middleware = DependsMiddleware(resolver)
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(user_middleware)
        observer.middleware(depends_middleware)
    dp.include_router(setup_routers())
    if catch_errors:
        dp.errors.register(on_error)
    return dp
