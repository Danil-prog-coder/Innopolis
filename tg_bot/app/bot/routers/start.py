from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards, texts, views
from app.bot.callbacks import RoleCb
from app.bot.states import JoinAsUser
from app.core.depends import Depends
from app.core.enums import MemberRole
from app.core.errors import InviteInvalid
from app.dependencies import get_obj_invite_service, get_obj_project_service
from app.services.invite_service import InviteService
from app.services.project_service import ProjectService
from app.storage.postgres.models import User


async def start_with_code(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    bot: Bot,
    db_user: User,
    invite_service: InviteService = Depends(get_obj_invite_service),
) -> None:
    await state.clear()
    try:
        current = await invite_service.redeem(db_user, command.args or "")
    except InviteInvalid as error:
        await message.answer(views.error_text(error), reply_markup=keyboards.role_kb())
        return
    await views.send_project_home(bot, message.chat.id, current)


async def start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(texts.START, reply_markup=keyboards.role_kb())


async def menu(
    message: Message,
    state: FSMContext,
    bot: Bot,
    db_user: User,
    project_service: ProjectService = Depends(get_obj_project_service),
) -> None:
    await state.clear()
    current = await project_service.get_current(db_user)
    if current is None:
        await message.answer(texts.NO_PROJECT, reply_markup=keyboards.role_kb())
        return
    await views.send_project_home(bot, message.chat.id, current)


async def cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer(texts.NOTHING_TO_CANCEL)
        return
    await state.clear()
    await message.answer(texts.CANCELLED)


async def choose_role(callback: CallbackQuery, callback_data: RoleCb, state: FSMContext) -> None:
    await state.clear()
    if callback_data.role == MemberRole.ADMIN:
        await callback.message.answer(texts.ADMIN_START, reply_markup=keyboards.admin_start_kb())
    else:
        await state.set_state(JoinAsUser.code)
        await callback.message.answer(texts.ASK_INVITE_CODE)
    await callback.answer()


def create_router() -> Router:
    router = Router(name="start")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(start_with_code, CommandStart(deep_link=True))
    router.message.register(start, CommandStart())
    router.message.register(menu, Command("menu"))
    router.message.register(cancel, Command("cancel"))
    router.callback_query.register(choose_role, RoleCb.filter())
    return router
