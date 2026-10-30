from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputRichMessage, Message

from app.bot import keyboards, texts, views
from app.bot.callbacks import ChangesCb, MenuAction, MenuCb, SwitchProjectCb
from app.bot.states import JoinAsUser
from app.core.depends import Depends
from app.core.errors import DomainError, InviteInvalid
from app.dependencies import get_obj_docs_service, get_obj_invite_service, get_obj_project_service
from app.services.docs_service import DocsService
from app.services.invite_service import InviteService
from app.services.project_service import ProjectService
from app.storage.postgres.models import User


async def join_by_code(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db_user: User,
    invite_service: InviteService = Depends(get_obj_invite_service),
) -> None:
    code = InviteService.extract_code(message.text)
    if code is None:
        await message.answer(texts.BAD_INVITE_FORMAT)
        return
    try:
        current = await invite_service.redeem(db_user, code)
    except InviteInvalid as error:
        await message.answer(views.error_text(error))
        return
    await state.clear()
    await views.send_project_home(bot, message.chat.id, current)


async def show_projects(
    message: Message,
    db_user: User,
    project_service: ProjectService = Depends(get_obj_project_service),
) -> None:
    memberships = await project_service.list_user_projects(db_user)
    if not memberships:
        await message.answer(texts.NO_PROJECT, reply_markup=keyboards.role_kb())
        return
    await message.answer(
        texts.PROJECTS_LIST,
        reply_markup=keyboards.projects_kb(memberships, db_user.current_project_id),
    )


async def switch_project(
    callback: CallbackQuery,
    callback_data: SwitchProjectCb,
    state: FSMContext,
    bot: Bot,
    db_user: User,
    project_service: ProjectService = Depends(get_obj_project_service),
) -> None:
    try:
        current = await project_service.switch_project(db_user, callback_data.project_id)
    except DomainError as error:
        await views.alert(callback, error)
        return
    await state.clear()
    await callback.answer()
    await views.send_project_home(bot, callback.from_user.id, current)


async def join_other(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer(texts.START, reply_markup=keyboards.role_kb())
    await callback.answer()


async def instructions(callback: CallbackQuery, bot: Bot) -> None:
    username = await views.bot_username(bot)
    await callback.message.answer(
        texts.group_instructions(username), reply_markup=keyboards.add_to_group_kb(username)
    )
    await callback.answer()


async def show_changes(
    callback: CallbackQuery,
    callback_data: ChangesCb,
    db_user: User,
    docs_service: DocsService = Depends(get_obj_docs_service),
) -> None:
    try:
        summary = await docs_service.get_summary(db_user, callback_data.update_id)
    except DomainError as error:
        await views.alert(callback, error)
        return
    await callback.answer()
    # Сводку пишет ИИ-часть в Markdown
    await views.send_rich(
        callback.bot, callback.message.chat.id, InputRichMessage(markdown=summary), fallback=summary
    )


def create_router() -> Router:
    router = Router(name="projects")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(join_by_code, JoinAsUser.code, F.text)
    router.message.register(show_projects, F.text.startswith(texts.PROJECT_BUTTON_PREFIX))
    router.callback_query.register(switch_project, SwitchProjectCb.filter())
    router.callback_query.register(join_other, MenuCb.filter(F.action == MenuAction.JOIN_OTHER))
    router.callback_query.register(instructions, MenuCb.filter(F.action == MenuAction.INSTRUCTIONS))
    router.callback_query.register(show_changes, ChangesCb.filter())
    return router
