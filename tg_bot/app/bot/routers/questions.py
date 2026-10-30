"""Вопросы по документации в личке. Подключается последним: ловит любой текст без состояния."""

import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.types import InputRichMessage, Message, ReplyParameters

from app.bot import keyboards, texts, views
from app.bot.notifier import Notifier
from app.core.depends import Depends
from app.core.enums import RateLimitScope
from app.core.errors import DomainError, FileTooLarge
from app.dependencies import get_obj_notifier, get_obj_project_service, get_obj_question_service
from app.llm.gateway import LLMUnavailableError
from app.services.docs_service import DocsService
from app.services.project_service import ProjectService
from app.services.question_service import Asker, AskOutcome, QuestionService
from app.storage.postgres.models import Project, User

logger = logging.getLogger(__name__)


async def deliver_outcome(
    message: Message,
    project: Project,
    outcome: AskOutcome,
    question_service: QuestionService,
    notifier: Notifier,
) -> None:
    """Ответ — автору в тот же чат; нет ответа — сообщить автору и передать вопрос админам."""
    if outcome.answer is not None:
        reply = ReplyParameters(message_id=message.message_id, allow_sending_without_reply=True)
        # Ответ ИИ — в Markdown: заголовки, списки, таблицы и код Telegram покажет как есть
        await views.send_rich(
            message.bot,
            message.chat.id,
            InputRichMessage(markdown=outcome.answer),
            fallback=outcome.answer,
            reply_parameters=reply,
        )
        return

    question = outcome.question
    await message.answer(
        texts.NOT_FOUND_FORWARDED,
        reply_parameters=ReplyParameters(
            message_id=message.message_id, allow_sending_without_reply=True
        ),
    )
    admin_ids = await question_service.get_admin_chat_ids(project.id)
    markup = keyboards.unanswered_question_kb(question.id, project.id)
    if question.image_file_id:
        caption = texts.admin_unanswered(project.name, question.text, texts.CAPTION_QUESTION_LEN)
        await notifier.send_photo_many(
            admin_ids, question.image_file_id, caption, reply_markup=markup
        )
    else:
        await notifier.send_many(
            admin_ids, texts.admin_unanswered(project.name, question.text), reply_markup=markup
        )


async def _current_project(
    message: Message, db_user: User, project_service: ProjectService
) -> Project | None:
    current = await project_service.get_current(db_user)
    if current is None:
        await message.answer(texts.NO_PROJECT, reply_markup=keyboards.role_kb())
        return None
    return current.project


async def text_question(
    message: Message,
    bot: Bot,
    db_user: User,
    project_service: ProjectService = Depends(get_obj_project_service),
    question_service: QuestionService = Depends(get_obj_question_service),
    notifier: Notifier = Depends(get_obj_notifier),
) -> None:
    if await views.refuse_password_as_question(message, message.text):
        return
    project = await _current_project(message, db_user, project_service)
    if project is None:
        return
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    asker = Asker(db_user.telegram_id, message.chat.id, message.message_id)
    try:
        outcome = await question_service.ask_text(
            project.id, message.text, asker, RateLimitScope.QUESTION
        )
    except DomainError as error:
        await message.answer(views.error_text(error))
        return
    except LLMUnavailableError:
        await message.answer(texts.LLM_UNAVAILABLE)
        return
    await deliver_outcome(message, project, outcome, question_service, notifier)


async def image_question(
    message: Message,
    bot: Bot,
    db_user: User,
    project_service: ProjectService = Depends(get_obj_project_service),
    question_service: QuestionService = Depends(get_obj_question_service),
    notifier: Notifier = Depends(get_obj_notifier),
) -> None:
    if await views.refuse_password_as_question(message, message.caption):
        return
    project = await _current_project(message, db_user, project_service)
    if project is None:
        return
    if message.photo:
        # Фото Telegram сжимает сам — оно заведомо меньше лимита на скачивание
        file_id = message.photo[-1].file_id
    else:
        file_id = message.document.file_id
        try:
            DocsService.check_file_size(message.document.file_size)
        except FileTooLarge as error:
            await message.answer(views.error_text(error))
            return

    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    try:
        image = (await bot.download(file_id)).read()
    except TelegramAPIError as error:
        logger.warning("Cannot download screenshot %s: %s", file_id, error)
        await message.answer(texts.DOWNLOAD_FAILED)
        return
    asker = Asker(db_user.telegram_id, message.chat.id, message.message_id)
    try:
        outcome = await question_service.ask_image(
            project.id, image, file_id, message.caption, asker
        )
    except DomainError as error:
        await message.answer(views.error_text(error))
        return
    except LLMUnavailableError:
        await message.answer(texts.LLM_UNAVAILABLE)
        return
    await deliver_outcome(message, project, outcome, question_service, notifier)


def create_router() -> Router:
    router = Router(name="questions")
    router.message.filter(F.chat.type == ChatType.PRIVATE, StateFilter(None))
    router.message.register(text_question, F.text, ~F.text.startswith("/"))
    router.message.register(image_question, F.photo)
    router.message.register(image_question, F.document.mime_type.startswith("image/"))
    return router
