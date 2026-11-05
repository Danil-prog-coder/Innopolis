"""Вопросы по документации в личке. Подключается последним: ловит любой текст без состояния."""

import logging
import uuid
from collections.abc import Awaitable

from aiogram import Bot, F, Router
from aiogram.enums import ChatAction, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    InputRichMessage,
    Message,
    MessageGenerationStopped,
    ReplyParameters,
)

from app.bot import keyboards, streaming, texts, views
from app.bot.notifier import Notifier
from app.bot.streaming import AskCall, GenerationRegistry, StreamTiming
from app.config import Settings
from app.core.depends import Depends
from app.core.enums import RateLimitScope
from app.core.errors import DomainError, FileTooLarge
from app.dependencies import (
    get_obj_active_draft_registry,
    get_obj_notifier,
    get_obj_project_service,
    get_obj_question_service,
    get_obj_settings,
)
from app.llm.gateway import AskEventType, AskResult, LLMUnavailableError
from app.llm.telegram_stream import TelegramStreamDelivery
from app.services.active_draft_registry import ActiveDraftRegistry, DraftAlreadyActive
from app.services.docs_service import DocsService
from app.services.project_service import ProjectService
from app.services.question_service import Asker, AskOutcome, AskStreamState, QuestionService
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
        # Источники форматирует сервер; ответ ИИ — Markdown для Rich Message.
        text = views.with_sources(outcome.answer, outcome.sources)
        rich = InputRichMessage(
            markdown=text,
            # Метаданные источников недоверенные: не даём автоссылкам/упоминаниям
            # исполняться из title/section_path.
            skip_entity_detection=bool(outcome.sources),
        )
        await views.send_rich(
            message.bot,
            message.chat.id,
            rich,
            fallback=text,
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


def _stream_rich(result: AskResult) -> InputRichMessage:
    text = views.with_sources(result.answer or "", result.sources)
    return InputRichMessage(markdown=text, skip_entity_detection=bool(result.sources))


def _stream_fallback(result: AskResult) -> str:
    return views.with_sources(result.answer or "", result.sources)


async def stop_command(
    message: Message,
    db_user: User,
    generation_registry: GenerationRegistry,
    draft_registry: ActiveDraftRegistry = Depends(get_obj_active_draft_registry),
) -> None:
    stopped_replay = generation_registry.stop_context(
        chat_id=message.chat.id,
        thread_id=message.message_thread_id,
        actor_user_id=db_user.telegram_id,
    )
    stopped_stream = draft_registry.stop_context(
        chat_id=message.chat.id,
        message_thread_id=message.message_thread_id,
        actor_user_id=db_user.telegram_id,
    )
    if stopped_replay or stopped_stream:
        await message.answer(texts.ANSWER_STOPPED)
    else:
        await message.answer(texts.NOTHING_TO_STOP)


async def _current_project(
    message: Message, db_user: User, project_service: ProjectService
) -> Project | None:
    current = await project_service.get_current(db_user)
    if current is None:
        await message.answer(texts.NO_PROJECT, reply_markup=keyboards.role_kb())
        return None
    return current.project


async def ask_private(
    message: Message,
    bot: Bot,
    ask: AskCall,
    settings: Settings,
    registry: GenerationRegistry,
) -> AskOutcome | None:
    """PR #2 replay path for stub/final-only gateways. ``None`` — stop pressed."""
    if not settings.answer_streaming_enabled:
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        return await ask()
    preview = streaming.PrivateDraftPreview(
        bot, message.chat.id, message.message_id, message.message_thread_id
    )
    return await streaming.stream_answer(
        preview,
        ask,
        StreamTiming.private(settings),
        registry,
        stop_key=(message.chat.id, message.message_id),
        stop_meta=(
            message.message_thread_id,
            message.from_user.id if message.from_user is not None else None,
        ),
    )


async def _ask_private_realtime(
    message: Message,
    bot: Bot,
    project: Project,
    asker: Asker,
    question_service: QuestionService,
    notifier: Notifier,
    draft_registry: ActiveDraftRegistry,
) -> None:
    """True event-stream path for real mode; final is sent exactly once."""
    request_id = uuid.uuid4().hex
    try:
        registration = draft_registry.register(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            actor_user_id=asker.telegram_id,
        )
    except DraftAlreadyActive:
        await message.answer(texts.ANSWER_IN_PROGRESS)
        return

    stop_event = registration.event
    state = AskStreamState()
    delivery = TelegramStreamDelivery(
        bot,
        message.chat.id,
        draft_id=registration.draft_id,
        message_thread_id=message.message_thread_id,
        reply_parameters=ReplyParameters(
            message_id=message.message_id, allow_sending_without_reply=True
        ),
    )
    try:
        delivered = await delivery.deliver(
            question_service.ask_text_stream(
                project.id,
                message.text,
                asker,
                RateLimitScope.QUESTION,
                request_id=request_id,
                state=state,
                stop_event=stop_event,
            ),
            request_id,
            render_final=_stream_rich,
            render_fallback=_stream_fallback,
            stop_event=stop_event,
        )
    finally:
        draft_registry.unregister(registration)

    terminal = delivered.terminal
    if delivered.aborted_by_stop or terminal is None:
        await message.answer(texts.ANSWER_STOPPED)
        return
    if terminal.type is AskEventType.CANCELLED:
        await message.answer(texts.ANSWER_STOPPED)
        return
    if terminal.type is AskEventType.ERROR:
        await message.answer(texts.LLM_UNAVAILABLE)
        return
    outcome = state.outcome
    if outcome is None:
        await message.answer(texts.LLM_UNAVAILABLE)
        return
    if outcome.answer is not None:
        # TelegramStreamDelivery already sent the final rich message.
        return
    await deliver_outcome(message, project, outcome, question_service, notifier)


async def text_question(
    message: Message,
    bot: Bot,
    db_user: User,
    generation_registry: GenerationRegistry,
    project_service: ProjectService = Depends(get_obj_project_service),
    question_service: QuestionService = Depends(get_obj_question_service),
    notifier: Notifier = Depends(get_obj_notifier),
    settings: Settings = Depends(get_obj_settings),
    draft_registry: ActiveDraftRegistry = Depends(get_obj_active_draft_registry),
) -> None:
    if await views.refuse_password_as_question(message, message.text):
        return
    project = await _current_project(message, db_user, project_service)
    if project is None:
        return
    asker = Asker(db_user.telegram_id, message.chat.id, message.message_id)

    def ask() -> Awaitable[AskOutcome]:
        return question_service.ask_text(project.id, message.text, asker, RateLimitScope.QUESTION)

    try:
        if settings.rag_mode == "real" and settings.answer_streaming_enabled:
            await _ask_private_realtime(
                message,
                bot,
                project,
                asker,
                question_service,
                notifier,
                draft_registry,
            )
            return
        outcome = await ask_private(message, bot, ask, settings, generation_registry)
    except DomainError as error:
        await message.answer(views.error_text(error))
        return
    except LLMUnavailableError:
        await message.answer(texts.LLM_UNAVAILABLE)
        return
    if outcome is None:
        await message.answer(texts.GENERATION_STOPPED)
        return
    await deliver_outcome(message, project, outcome, question_service, notifier)


async def image_question(
    message: Message,
    bot: Bot,
    db_user: User,
    generation_registry: GenerationRegistry,
    project_service: ProjectService = Depends(get_obj_project_service),
    question_service: QuestionService = Depends(get_obj_question_service),
    notifier: Notifier = Depends(get_obj_notifier),
    settings: Settings = Depends(get_obj_settings),
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

    def ask() -> Awaitable[AskOutcome]:
        return question_service.ask_image(project.id, image, file_id, message.caption, asker)

    try:
        if settings.rag_mode == "real":
            # Image RAG is not implemented in real mode; do not show a preview.
            outcome = await ask()
        else:
            outcome = await ask_private(message, bot, ask, settings, generation_registry)
    except DomainError as error:
        await message.answer(views.error_text(error))
        return
    except LLMUnavailableError:
        await message.answer(texts.LLM_UNAVAILABLE)
        return
    if outcome is None:
        await message.answer(texts.GENERATION_STOPPED)
        return
    await deliver_outcome(message, project, outcome, question_service, notifier)


async def generation_stopped(
    event: MessageGenerationStopped,
    generation_registry: GenerationRegistry,
    draft_registry: ActiveDraftRegistry = Depends(get_obj_active_draft_registry),
) -> None:
    """Stop both replay tasks (PR #2) and true-stream drafts (INN-8)."""
    stopped_task = generation_registry.stop(event.chat.id, event.draft_id)
    stopped_stream = draft_registry.stop(
        chat_id=event.chat.id,
        message_thread_id=event.message_thread_id,
        draft_id=event.draft_id,
        actor_user_id=event.chat.id,
    )
    if not (stopped_task or stopped_stream):
        logger.info(
            "Nothing to stop in chat %s, thread %s, draft %s",
            event.chat.id,
            event.message_thread_id,
            event.draft_id,
        )


def create_router() -> Router:
    router = Router(name="questions")
    router.stopped_message_generation.register(generation_stopped)
    router.message.filter(F.chat.type == ChatType.PRIVATE, StateFilter(None))
    router.message.register(stop_command, Command("stop"))
    router.message.register(text_question, F.text, ~F.text.startswith("/"))
    router.message.register(image_question, F.photo)
    router.message.register(image_question, F.document.mime_type.startswith("image/"))
    return router
