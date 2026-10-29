from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards, texts, views
from app.bot.callbacks import AdminStartAction, AdminStartCb, PasswordRevealCb
from app.bot.notifier import Notifier
from app.bot.states import CreateProject, JoinAsAdmin
from app.config import Settings
from app.core.depends import Depends
from app.core.errors import (
    DomainError,
    InvalidPassword,
    InvalidProjectName,
    NotProjectAdmin,
    TooManyAttempts,
)
from app.dependencies import get_obj_notifier, get_obj_project_service, get_obj_settings
from app.services.access_service import CurrentProject
from app.services.project_service import ProjectService
from app.storage.postgres.models import User


async def ask_project_name(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CreateProject.name)
    await callback.message.answer(texts.ASK_PROJECT_NAME)
    await callback.answer()


async def create_project(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db_user: User,
    project_service: ProjectService = Depends(get_obj_project_service),
    notifier: Notifier = Depends(get_obj_notifier),
    settings: Settings = Depends(get_obj_settings),
) -> None:
    try:
        project = await project_service.create_project(db_user, message.text)
    except InvalidProjectName as error:
        await message.answer(views.error_text(error))
        return
    await state.clear()
    password = await project_service.reveal_password(db_user, project.id)
    sent = await message.answer(
        texts.project_created(project.name, settings.password_reveal_ttl_seconds),
        reply_markup=keyboards.password_reveal_kb(project.id, password),
    )
    # Пароль лежит в разметке кнопки «Скопировать» — снимаем её вместе с окном показа
    notifier.remove_markup_later(
        sent.chat.id, sent.message_id, settings.password_reveal_ttl_seconds
    )
    current = await project_service.get_current(db_user)
    await views.send_project_home(bot, message.chat.id, current)


async def reveal_password(
    callback: CallbackQuery,
    callback_data: PasswordRevealCb,
    db_user: User,
    project_service: ProjectService = Depends(get_obj_project_service),
) -> None:
    try:
        password = await project_service.reveal_password(db_user, callback_data.project_id)
    except NotProjectAdmin as error:
        await views.alert(callback, error)
        return
    # Всплывающее окно не остаётся в истории переписки, в отличие от сообщения
    await callback.answer(password or texts.PASSWORD_EXPIRED, show_alert=True)


async def ask_admin_password(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(JoinAsAdmin.password)
    await callback.message.answer(texts.ASK_ADMIN_PASSWORD)
    await callback.answer()


async def join_as_admin(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db_user: User,
    project_service: ProjectService = Depends(get_obj_project_service),
) -> None:
    await views.delete_secret_message(message)
    try:
        current: CurrentProject = await project_service.join_as_admin(db_user, message.text)
    except (InvalidPassword, TooManyAttempts) as error:
        # Состояние не сбрасываем: иначе следующая попытка ввести пароль ушла бы
        # обработчику вопросов и переслалась бы админам. Выход — /cancel.
        await message.answer(views.error_text(error))
        return
    except DomainError as error:  # например, RemovedFromProject
        await state.clear()
        await message.answer(views.error_text(error))
        return
    await state.clear()
    await views.send_project_home(bot, message.chat.id, current)


def create_router() -> Router:
    router = Router(name="admin_project")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.register(
        ask_project_name, AdminStartCb.filter(F.action == AdminStartAction.CREATE)
    )
    router.callback_query.register(
        ask_admin_password, AdminStartCb.filter(F.action == AdminStartAction.JOIN)
    )
    router.callback_query.register(reveal_password, PasswordRevealCb.filter())
    router.message.register(create_project, CreateProject.name, F.text)
    router.message.register(join_as_admin, JoinAsAdmin.password, F.text)
    return router
