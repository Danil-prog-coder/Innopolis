import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot import keyboards, texts, views
from app.bot.callbacks import MembersPageCb, MenuAction, MenuCb, RemoveMemberCb
from app.core.depends import Depends
from app.core.errors import DomainError, NotProjectMember
from app.dependencies import get_obj_membership_service, get_obj_project_work
from app.services.membership_service import MembershipService
from app.storage.postgres.models import User
from app.storage.postgres.project_work import PostgresProjectWork

logger = logging.getLogger(__name__)


async def _show_page(
    callback: CallbackQuery,
    membership_service: MembershipService,
    db_user: User,
    project_id: int,
    page: int,
    edit: bool,
) -> None:
    try:
        members = await membership_service.list_members(db_user, project_id, page)
    except DomainError as error:
        await views.alert(callback, error)
        return
    text = texts.members_header(members.total, members.page, members.pages)
    markup = keyboards.members_kb(project_id, members)
    if edit:
        try:
            await callback.message.edit_text(text, reply_markup=markup)
        except TelegramBadRequest as error:
            # «message is not modified» при повторном нажатии — не ошибка для пользователя
            logger.debug("Members page not edited: %s", error.message)
    else:
        await callback.message.answer(text, reply_markup=markup)
    await callback.answer()


async def show_members(
    callback: CallbackQuery,
    callback_data: MenuCb,
    db_user: User,
    membership_service: MembershipService = Depends(get_obj_membership_service),
) -> None:
    await _show_page(callback, membership_service, db_user, callback_data.project_id, 0, edit=False)


async def members_page(
    callback: CallbackQuery,
    callback_data: MembersPageCb,
    db_user: User,
    membership_service: MembershipService = Depends(get_obj_membership_service),
) -> None:
    await _show_page(
        callback,
        membership_service,
        db_user,
        callback_data.project_id,
        callback_data.page,
        edit=True,
    )


async def confirm_remove(
    callback: CallbackQuery,
    callback_data: RemoveMemberCb,
    db_user: User,
    membership_service: MembershipService = Depends(get_obj_membership_service),
) -> None:
    try:
        # Только участник этого проекта: иначе по подделанной кнопке можно было бы
        # узнать имя любого пользователя бота
        target = await membership_service.get_member(
            db_user, callback_data.project_id, callback_data.user_id
        )
    except DomainError as error:
        await views.alert(callback, error)
        return
    await callback.message.edit_text(
        texts.confirm_remove(target.full_name),
        reply_markup=keyboards.confirm_remove_kb(callback_data.project_id, target.id),
    )
    await callback.answer()


async def remove_member(
    callback: CallbackQuery,
    callback_data: RemoveMemberCb,
    bot: Bot,
    db_user: User,
    membership_service: MembershipService = Depends(get_obj_membership_service),
    project_work: PostgresProjectWork = Depends(get_obj_project_work),
) -> None:
    try:
        removed = await membership_service.remove_member(
            db_user, callback_data.project_id, callback_data.user_id
        )
    except NotProjectMember:
        await callback.answer(texts.MEMBER_ALREADY_REMOVED, show_alert=True)
        return
    except DomainError as error:
        await views.alert(callback, error)
        return
    await callback.message.edit_text(texts.member_removed(removed.full_name))
    await callback.answer()

    project = await project_work.get_by_id(callback_data.project_id)
    try:
        await bot.send_message(removed.telegram_id, texts.removed_from_project(project.name))
    except TelegramAPIError as error:
        logger.info("Cannot notify removed member %s: %s", removed.telegram_id, error)


def create_router() -> Router:
    router = Router(name="admin_members")
    router.callback_query.register(show_members, MenuCb.filter(F.action == MenuAction.MEMBERS))
    router.callback_query.register(members_page, MembersPageCb.filter())
    router.callback_query.register(remove_member, RemoveMemberCb.filter(F.confirm))
    router.callback_query.register(confirm_remove, RemoveMemberCb.filter(~F.confirm))
    return router
