"""Последний роутер: всё, что не поймали остальные, получает понятный ответ, а не тишину."""

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import texts


async def unhandled_message(message: Message, state: FSMContext) -> None:
    if await state.get_state() is not None:
        await message.answer(texts.WAITING_OTHER_INPUT)
    elif message.text and message.text.startswith("/"):
        await message.answer(texts.UNKNOWN_COMMAND)
    else:
        await message.answer(texts.UNSUPPORTED_MESSAGE)


async def expired_button(callback: CallbackQuery) -> None:
    """Кнопка, у которой нет обработчика в текущем состоянии (например, «Готово» после отмены)."""
    await callback.answer(texts.BUTTON_EXPIRED, show_alert=True)


def create_router() -> Router:
    router = Router(name="fallback")
    # В группах бот молчит на всё, что не адресовано ему явно
    router.message.register(unhandled_message, F.chat.type == ChatType.PRIVATE)
    router.callback_query.register(expired_button)
    return router
