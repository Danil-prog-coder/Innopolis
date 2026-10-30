"""Повторяющиеся экраны бота."""

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InputRichMessage, Message, ReplyParameters

from app.bot import keyboards, texts
from app.core.errors import (
    CannotRemoveCreator,
    CannotRemoveSelf,
    DocUpdateNotFound,
    DomainError,
    EmptyQuestion,
    FileTooLarge,
    GroupRebindForbidden,
    InvalidPassword,
    InvalidProjectName,
    InviteExpired,
    InviteNotFound,
    InviteRevoked,
    NoFilesToUpload,
    NotProjectAdmin,
    NotProjectMember,
    ProjectNotFound,
    QuestionAlreadyAnswered,
    QuestionNotAnswered,
    QuestionNotFound,
    QuestionTooLong,
    RateLimited,
    RemovedFromProject,
    TooManyAttempts,
)
from app.core.security import ProjectPasswordManager
from app.services.access_service import CurrentProject

logger = logging.getLogger(__name__)

ERROR_TEXTS: dict[type[DomainError], str] = {
    NotProjectMember: texts.NOT_MEMBER,
    NotProjectAdmin: texts.NO_RIGHTS,
    ProjectNotFound: texts.NOT_MEMBER,
    InvalidProjectName: texts.INVALID_PROJECT_NAME,
    InvalidPassword: texts.INVALID_PASSWORD,
    TooManyAttempts: texts.TOO_MANY_ATTEMPTS,
    InviteNotFound: texts.INVITE_NOT_FOUND,
    InviteExpired: texts.INVITE_EXPIRED,
    InviteRevoked: texts.INVITE_REVOKED,
    CannotRemoveCreator: texts.CANNOT_REMOVE_CREATOR,
    CannotRemoveSelf: texts.CANNOT_REMOVE_SELF,
    RateLimited: texts.RATE_LIMITED,
    QuestionTooLong: texts.QUESTION_TOO_LONG,
    EmptyQuestion: texts.EMPTY_TEXT,
    QuestionNotFound: texts.QUESTION_NOT_FOUND,
    QuestionAlreadyAnswered: texts.ALREADY_ANSWERED,
    QuestionNotAnswered: texts.QUESTION_NOT_ANSWERED,
    FileTooLarge: texts.FILE_TOO_LARGE,
    NoFilesToUpload: texts.NO_FILES,
    DocUpdateNotFound: texts.CHANGES_NOT_FOUND,
    RemovedFromProject: texts.REMOVED_FROM_PROJECT_BEFORE,
    GroupRebindForbidden: texts.GROUP_REBIND_FORBIDDEN,
}


def error_text(error: DomainError) -> str:
    return ERROR_TEXTS.get(type(error), texts.NO_RIGHTS)


async def alert(callback: CallbackQuery, error: DomainError) -> None:
    """Ошибка на нажатие кнопки — во всплывающем окне."""
    await callback.answer(error_text(error), show_alert=True)


async def send_project_home(bot: Bot, chat_id: int, current: CurrentProject) -> None:
    """Сообщение, с которого начинается работа в проекте (README, «Работа внутри проекта»)."""
    await bot.send_message(
        chat_id,
        texts.joined_project(current.project.name, current.role),
        reply_markup=keyboards.project_reply_kb(current),
    )
    await bot.send_message(
        chat_id, texts.main_menu(current.project.name), reply_markup=keyboards.main_menu_kb(current)
    )


async def send_rich(
    bot: Bot,
    chat_id: int,
    rich: InputRichMessage,
    fallback: str,
    reply_parameters: ReplyParameters | None = None,
) -> None:
    """Отправить Rich Message (заголовки, списки, таблицы, код — Bot API 10.1+).

    Если текст длиннее лимита или Telegram не принял разметку (её пишет ИИ, она может быть
    битой), отправляем ``fallback`` обычным текстом без parse_mode, разбив на части.
    """
    if len(rich.markdown or rich.html or "") <= texts.TELEGRAM_RICH_MESSAGE_LIMIT:
        try:
            await bot.send_rich_message(chat_id, rich, reply_parameters=reply_parameters)
            return
        except TelegramBadRequest as error:
            logger.warning("Rich message rejected in chat %s: %s", chat_id, error.message)
    for index, part in enumerate(texts.split_message(fallback)):
        reply = reply_parameters if index == 0 else None
        await bot.send_message(chat_id, part, parse_mode=None, reply_parameters=reply)


async def bot_username(bot: Bot) -> str:
    return (await bot.me()).username or ""


async def delete_secret_message(message: Message) -> bool:
    """Пароль не должен оставаться в истории переписки. False — удалить не удалось."""
    try:
        await message.delete()
    except TelegramBadRequest as error:
        # В группе без прав администратора бот не может удалять чужие сообщения
        logger.warning("Cannot delete secret message %s: %s", message.message_id, error.message)
        return False
    return True


async def refuse_password_as_question(message: Message, text: str | None) -> bool:
    """Если в тексте пароль проекта — удалить сообщение и не пускать его дальше (в ИИ и админам)."""
    if not text or not ProjectPasswordManager.looks_like_password(text):
        return False
    await delete_secret_message(message)
    await message.answer(texts.PASSWORD_NOT_A_QUESTION)
    return True
