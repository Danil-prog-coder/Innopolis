import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards, texts, views
from app.bot.callbacks import DocsCancelCb, DocsDoneCb, MenuAction, MenuCb
from app.bot.notifier import Notifier
from app.bot.states import DocsUpload
from app.config import Settings
from app.core.clock import utc_now
from app.core.depends import Depends
from app.core.enums import DocsMode
from app.core.errors import (
    DomainError,
    FileTooLarge,
    InvalidPassword,
    NotProjectAdmin,
    TooManyAttempts,
)
from app.dependencies import (
    get_obj_docs_draft_work,
    get_obj_docs_service,
    get_obj_notifier,
    get_obj_project_service,
    get_obj_project_work,
    get_obj_settings,
)
from app.llm.gateway import DocFile, LLMUnavailableError
from app.services.docs_service import DocsService
from app.services.project_service import ProjectService
from app.storage.postgres.models import User
from app.storage.postgres.project_work import PostgresProjectWork
from app.storage.redis.docs_draft_work import DraftFile, RedisDocsDraftWork

logger = logging.getLogger(__name__)

# Подтверждение паролем для «Заменить документацию» действует ограниченное время
REPLACE_CONFIRM_SECONDS = 15 * 60
# С запасом на скачивание файлов и индексацию; снимается сразу по завершении
UPLOAD_LOCK_SECONDS = 15 * 60


async def start_upload(
    callback: CallbackQuery,
    callback_data: MenuCb,
    state: FSMContext,
    db_user: User,
    docs_service: DocsService = Depends(get_obj_docs_service),
    docs_draft_work: RedisDocsDraftWork = Depends(get_obj_docs_draft_work),
    settings: Settings = Depends(get_obj_settings),
) -> None:
    project_id = callback_data.project_id
    try:
        await docs_service.require_admin(db_user, project_id)
    except DomainError as error:
        await views.alert(callback, error)
        return
    await docs_draft_work.clear(db_user.telegram_id, project_id)
    if callback_data.action == MenuAction.DOCS_REPLACE:
        await state.set_state(DocsUpload.password)
        await state.set_data({"project_id": project_id, "mode": DocsMode.REPLACE})
        await callback.message.answer(texts.ASK_PASSWORD_FOR_REPLACE)
    else:
        await state.set_state(DocsUpload.files)
        await state.set_data({"project_id": project_id, "mode": DocsMode.APPEND})
        await callback.message.answer(
            texts.docs_send_files(settings.docs_max_files),
            reply_markup=keyboards.docs_upload_kb(project_id),
        )
    await callback.answer()


async def confirm_replace_password(
    message: Message,
    state: FSMContext,
    db_user: User,
    project_service: ProjectService = Depends(get_obj_project_service),
    settings: Settings = Depends(get_obj_settings),
) -> None:
    await views.delete_secret_message(message)
    project_id = (await state.get_data())["project_id"]
    try:
        await project_service.check_password(db_user, project_id, message.text)
    except (InvalidPassword, TooManyAttempts) as error:
        # Состояние не сбрасываем, чтобы следующий ввод пароля не ушёл в вопросы
        await message.answer(views.error_text(error))
        return
    except DomainError as error:
        await state.clear()
        await message.answer(views.error_text(error))
        return
    await state.set_state(DocsUpload.files)
    await state.update_data(replace_confirmed_at=utc_now().timestamp())
    await message.answer(
        texts.docs_send_files(settings.docs_max_files),
        reply_markup=keyboards.docs_upload_kb(project_id),
    )


async def receive_document(
    message: Message,
    state: FSMContext,
    db_user: User,
    docs_draft_work: RedisDocsDraftWork = Depends(get_obj_docs_draft_work),
    settings: Settings = Depends(get_obj_settings),
) -> None:
    document = message.document
    project_id = (await state.get_data())["project_id"]
    try:
        DocsService.check_file_size(document.file_size)
    except FileTooLarge as error:
        await message.answer(views.error_text(error))
        return
    filename = document.file_name or f"file-{document.file_unique_id}"
    draft = DraftFile(file_id=document.file_id, filename=filename, size=document.file_size)
    count = await docs_draft_work.add(
        db_user.telegram_id, project_id, draft, settings.docs_max_files
    )
    if count < 0:
        await message.answer(texts.too_many_files(settings.docs_max_files))
        return
    await message.answer(
        texts.file_accepted(filename, count, settings.docs_max_files),
        reply_markup=keyboards.docs_upload_kb(project_id),
    )


async def not_a_document(message: Message) -> None:
    await message.answer(texts.SEND_DOCUMENT_HINT)


async def finish_upload(
    callback: CallbackQuery,
    callback_data: DocsDoneCb,
    state: FSMContext,
    bot: Bot,
    db_user: User,
    docs_service: DocsService = Depends(get_obj_docs_service),
    docs_draft_work: RedisDocsDraftWork = Depends(get_obj_docs_draft_work),
    project_work: PostgresProjectWork = Depends(get_obj_project_work),
    notifier: Notifier = Depends(get_obj_notifier),
) -> None:
    data = await state.get_data()
    project_id = callback_data.project_id
    if data.get("project_id") != project_id:
        await callback.answer(texts.BUTTON_EXPIRED, show_alert=True)
        return
    mode = DocsMode(data["mode"])
    if mode == DocsMode.REPLACE and _replace_confirmation_expired(data):
        await docs_draft_work.clear(db_user.telegram_id, project_id)
        await state.clear()
        await callback.message.answer(texts.REPLACE_CONFIRMATION_EXPIRED)
        await callback.answer()
        return
    # Двойное нажатие «Готово» приходит параллельными апдейтами — загружаем один раз
    if not await docs_draft_work.try_lock(db_user.telegram_id, project_id, UPLOAD_LOCK_SECONDS):
        await callback.answer(texts.DOCS_ALREADY_PROCESSING, show_alert=True)
        return
    try:
        await _upload(
            callback,
            state,
            bot,
            db_user,
            project_id,
            mode,
            docs_service,
            docs_draft_work,
            project_work,
            notifier,
        )
    finally:
        await docs_draft_work.unlock(db_user.telegram_id, project_id)


def _replace_confirmation_expired(data: dict) -> bool:
    confirmed_at = data.get("replace_confirmed_at")
    return confirmed_at is None or utc_now().timestamp() - confirmed_at > REPLACE_CONFIRM_SECONDS


async def _upload(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    db_user: User,
    project_id: int,
    mode: DocsMode,
    docs_service: DocsService,
    docs_draft_work: RedisDocsDraftWork,
    project_work: PostgresProjectWork,
    notifier: Notifier,
) -> None:
    drafts = await docs_draft_work.list(db_user.telegram_id, project_id)
    if not drafts:
        await callback.answer(texts.NO_FILES, show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(texts.DOCS_PROCESSING)

    files: list[DocFile] = []
    for draft in drafts:
        try:
            content = await bot.download(draft.file_id)
        except TelegramAPIError as error:
            logger.warning("Cannot download %s: %s", draft.file_id, error)
            await callback.message.answer(texts.DOWNLOAD_FAILED)
            return
        files.append(DocFile(filename=draft.filename, content=content.read()))

    try:
        update = await docs_service.apply_update(db_user, project_id, mode, files)
    except LLMUnavailableError:
        # Черновик и состояние сохраняются — можно нажать «Готово» ещё раз
        await callback.message.answer(texts.LLM_UNAVAILABLE)
        return
    except NotProjectAdmin as error:
        await state.clear()
        await callback.message.answer(views.error_text(error))
        return

    await docs_draft_work.clear(db_user.telegram_id, project_id)
    await state.clear()
    await callback.message.answer(texts.DOCS_UPDATED_ADMIN)

    project = await project_work.get_by_id(project_id)
    member_ids = await docs_service.get_member_chat_ids(project_id)
    notifier.send_many_in_background(
        member_ids, texts.docs_updated(project.name), reply_markup=keyboards.changes_kb(update.id)
    )


async def cancel_upload(
    callback: CallbackQuery,
    callback_data: DocsCancelCb,
    state: FSMContext,
    db_user: User,
    docs_draft_work: RedisDocsDraftWork = Depends(get_obj_docs_draft_work),
) -> None:
    await docs_draft_work.clear(db_user.telegram_id, callback_data.project_id)
    await state.clear()
    await callback.message.answer(texts.DOCS_CANCELLED)
    await callback.answer()


def create_router() -> Router:
    router = Router(name="admin_docs")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.register(
        start_upload,
        MenuCb.filter(F.action.in_({MenuAction.DOCS_APPEND, MenuAction.DOCS_REPLACE})),
    )
    router.message.register(confirm_replace_password, DocsUpload.password, F.text)
    router.message.register(receive_document, DocsUpload.files, F.document)
    router.message.register(not_a_document, DocsUpload.files)
    router.callback_query.register(finish_upload, DocsDoneCb.filter(), DocsUpload.files)
    router.callback_query.register(cancel_upload, DocsCancelCb.filter())
    return router
