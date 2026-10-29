"""Бот в рабочем чате команды: /join@бот <пароль>, /ask@бот <вопрос> и @бот <вопрос>."""

import re
from typing import Any

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from app.bot import texts, views
from app.bot.notifier import Notifier
from app.bot.routers.questions import deliver_outcome
from app.core.depends import Depends
from app.core.enums import RateLimitScope
from app.core.errors import DomainError
from app.dependencies import get_obj_group_chat_service, get_obj_notifier, get_obj_question_service
from app.llm.gateway import LLMUnavailableError
from app.services.group_chat_service import GroupChatService
from app.services.question_service import Asker, QuestionService
from app.storage.postgres.models import User

GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}


async def bot_mentioned(message: Message, bot: Bot) -> dict[str, Any] | bool:
    """Фильтр: в сообщении есть упоминание бота. Отдаёт текст вопроса без упоминания."""
    username = await views.bot_username(bot)
    mention = f"@{username}".casefold()
    mentioned = any(
        entity.type == "mention" and entity.extract_from(message.text).casefold() == mention
        for entity in message.entities or []
    )
    if not mentioned:
        return False
    question = re.sub(rf"@{re.escape(username)}\b", "", message.text, flags=re.IGNORECASE)
    return {"question_text": " ".join(question.split())}


async def join_group(
    message: Message,
    command: CommandObject,
    db_user: User,
    group_chat_service: GroupChatService = Depends(get_obj_group_chat_service),
) -> None:
    password = (command.args or "").strip()
    if not password:
        await message.reply(texts.JOIN_USAGE)
        return
    # Пароль не должен оставаться в группе. Удалить чужое сообщение бот может,
    # только если он администратор группы — иначе просим удалить вручную.
    deleted = await views.delete_secret_message(message)
    try:
        project = await group_chat_service.bind(message.chat.id, password, db_user)
    except DomainError as error:
        text = views.error_text(error)
    else:
        text = texts.group_bound(project.name)
    if not deleted:
        text = f"{text}\n\n{texts.DELETE_PASSWORD_MANUALLY}"
    await message.answer(text)


async def _ask(
    message: Message,
    bot: Bot,
    question_text: str,
    group_chat_service: GroupChatService,
    question_service: QuestionService,
    notifier: Notifier,
) -> None:
    if not question_text:
        await message.reply(texts.ASK_USAGE)
        return
    if await views.refuse_password_as_question(message, question_text):
        return
    project = await group_chat_service.get_bound_project(message.chat.id)
    if project is None:
        await message.reply(texts.group_not_bound(await views.bot_username(bot)))
        return
    asker = Asker(message.from_user.id, message.chat.id, message.message_id)
    try:
        outcome = await question_service.ask_text(
            project.id, question_text, asker, RateLimitScope.GROUP_QUESTION
        )
    except DomainError as error:
        await message.reply(views.error_text(error))
        return
    except LLMUnavailableError:
        await message.reply(texts.LLM_UNAVAILABLE)
        return
    await deliver_outcome(message, project, outcome, question_service, notifier)


async def ask_command(
    message: Message,
    command: CommandObject,
    bot: Bot,
    group_chat_service: GroupChatService = Depends(get_obj_group_chat_service),
    question_service: QuestionService = Depends(get_obj_question_service),
    notifier: Notifier = Depends(get_obj_notifier),
) -> None:
    await _ask(
        message, bot, (command.args or "").strip(), group_chat_service, question_service, notifier
    )


async def ask_mention(
    message: Message,
    bot: Bot,
    question_text: str,
    group_chat_service: GroupChatService = Depends(get_obj_group_chat_service),
    question_service: QuestionService = Depends(get_obj_question_service),
    notifier: Notifier = Depends(get_obj_notifier),
) -> None:
    await _ask(message, bot, question_text, group_chat_service, question_service, notifier)


def create_router() -> Router:
    router = Router(name="group")
    router.message.filter(F.chat.type.in_(GROUP_TYPES))
    router.message.register(join_group, Command("join"))
    router.message.register(ask_command, Command("ask"))
    router.message.register(ask_mention, F.text, bot_mentioned)
    return router
