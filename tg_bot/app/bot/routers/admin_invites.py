from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from app.bot import keyboards, texts, views
from app.bot.callbacks import InviteTtlCb, MenuAction, MenuCb, RevokeInviteCb
from app.core.depends import Depends
from app.core.errors import DomainError
from app.dependencies import get_obj_access_guard, get_obj_invite_service
from app.services.access_service import AccessGuard
from app.services.invite_service import InviteService
from app.storage.postgres.models import User

EXPIRES_FORMAT = "%d.%m.%Y %H:%M UTC"
MAX_SHOWN_INVITES = 20


async def choose_ttl(
    callback: CallbackQuery,
    callback_data: MenuCb,
    db_user: User,
    access_guard: AccessGuard = Depends(get_obj_access_guard),
) -> None:
    try:
        await access_guard.require_admin(db_user.id, callback_data.project_id)
    except DomainError as error:
        await views.alert(callback, error)
        return
    await callback.message.answer(
        texts.CHOOSE_INVITE_TTL, reply_markup=keyboards.invite_ttl_kb(callback_data.project_id)
    )
    await callback.answer()


async def create_invite(
    callback: CallbackQuery,
    callback_data: InviteTtlCb,
    bot: Bot,
    db_user: User,
    invite_service: InviteService = Depends(get_obj_invite_service),
) -> None:
    try:
        invite = await invite_service.create_invite(
            db_user, callback_data.project_id, callback_data.days
        )
    except DomainError as error:
        await views.alert(callback, error)
        return
    except ValueError:
        await callback.answer()
        return
    link = texts.invite_link(await views.bot_username(bot), invite.code)
    await callback.message.answer(texts.invite_created(link, callback_data.days))
    await callback.answer()


async def list_invites(
    callback: CallbackQuery,
    callback_data: MenuCb,
    db_user: User,
    invite_service: InviteService = Depends(get_obj_invite_service),
) -> None:
    try:
        invites = await invite_service.list_active(db_user, callback_data.project_id)
    except DomainError as error:
        await views.alert(callback, error)
        return
    if not invites:
        await callback.message.answer(texts.NO_ACTIVE_INVITES)
    else:
        # Свежие сверху; больше MAX_SHOWN_INVITES не влезет в лимиты сообщения и клавиатуры
        shown = invites[::-1][:MAX_SHOWN_INVITES]
        lines = [texts.invite_line(i.code, i.expires_at.strftime(EXPIRES_FORMAT)) for i in shown]
        if len(invites) > len(shown):
            lines.append(texts.more_invites(len(invites) - len(shown)))
        await callback.message.answer(
            texts.active_invites(lines),
            reply_markup=keyboards.active_invites_kb(callback_data.project_id, shown),
        )
    await callback.answer()


async def revoke_invite(
    callback: CallbackQuery,
    callback_data: RevokeInviteCb,
    db_user: User,
    invite_service: InviteService = Depends(get_obj_invite_service),
) -> None:
    try:
        revoked = await invite_service.revoke(
            db_user, callback_data.project_id, callback_data.invite_id
        )
    except DomainError as error:
        await views.alert(callback, error)
        return
    await callback.answer(
        texts.INVITE_REVOKED_OK if revoked else texts.INVITE_ALREADY_INACTIVE, show_alert=True
    )


def create_router() -> Router:
    router = Router(name="admin_invites")
    router.callback_query.register(choose_ttl, MenuCb.filter(F.action == MenuAction.INVITE))
    router.callback_query.register(list_invites, MenuCb.filter(F.action == MenuAction.INVITES))
    router.callback_query.register(create_invite, InviteTtlCb.filter())
    router.callback_query.register(revoke_invite, RevokeInviteCb.filter())
    return router
