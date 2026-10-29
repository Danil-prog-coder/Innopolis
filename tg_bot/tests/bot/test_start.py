from aiogram.methods import SendMessage
from aiogram.types import ReplyKeyboardMarkup

from app import dependencies
from app.bot import texts
from app.bot.callbacks import RoleCb
from app.core.enums import MemberRole
from tests.bot.conftest import ADMIN_TG, FRESH_TG, OUTSIDER_TG


async def test_all_factories_resolve(resolver):
    names = [n for n in dir(dependencies) if n.startswith("get_obj_")]
    assert len(names) >= 27
    for name in names:
        if name == "get_obj_fsm_storage":
            continue  # требует реального RedisClient; собирается в __main__
        assert await resolver.resolve(getattr(dependencies, name)) is not None, name


async def test_start_shows_roles(feed, session):
    await feed.message("/start", from_user=FRESH_TG)
    sent = session.last(SendMessage)
    assert sent.text == texts.START
    labels = [b.text for b in session.buttons()]
    assert labels == [texts.BTN_ROLE_ADMIN, texts.BTN_ROLE_USER]


async def test_start_registers_user(feed, user_work):
    await feed.message("/start", from_user=FRESH_TG)
    assert (await user_work.get_by_telegram_id(FRESH_TG.id)).full_name == "Fresh"


async def test_start_with_valid_code_joins(feed, session, invite_service, project, admin):
    invite = await invite_service.create_invite(admin, project.id, 7)
    await feed.message(f"/start {invite.code}", from_user=OUTSIDER_TG)
    reply_markups = [m.reply_markup for m in session.all(SendMessage)]
    reply_kb = next(m for m in reply_markups if isinstance(m, ReplyKeyboardMarkup))
    assert reply_kb.keyboard[0][0].text == texts.project_button("Альфа", MemberRole.USER)
    assert texts.BTN_INSTRUCTIONS in [b.text for b in session.buttons()]
    assert texts.BTN_MEMBERS not in [b.text for b in session.buttons()]  # не админ


async def test_start_with_revoked_code_explains(feed, session, invite_service, project, admin):
    invite = await invite_service.create_invite(admin, project.id, 7)
    await invite_service.revoke(admin, project.id, invite.id)
    await feed.message(f"/start {invite.code}", from_user=OUTSIDER_TG)
    assert session.last(SendMessage).text == texts.INVITE_REVOKED


async def test_menu_for_admin_has_admin_buttons(feed, session, project_service, project, admin):
    await project_service.switch_project(admin, project.id)
    await feed.message("/menu", from_user=ADMIN_TG)
    labels = [b.text for b in session.buttons()]
    assert texts.BTN_DOCS_REPLACE in labels and texts.BTN_MEMBERS in labels


async def test_menu_without_project(feed, session):
    await feed.message("/menu", from_user=FRESH_TG)
    assert session.last(SendMessage).text == texts.NO_PROJECT


async def test_role_user_asks_for_code(feed, session):
    await feed.callback(RoleCb(role=MemberRole.USER).pack(), from_user=FRESH_TG)
    assert session.last(SendMessage).text == texts.ASK_INVITE_CODE


async def test_role_admin_shows_options(feed, session):
    await feed.callback(RoleCb(role=MemberRole.ADMIN).pack(), from_user=FRESH_TG)
    labels = [b.text for b in session.buttons()]
    assert labels == [texts.BTN_CREATE_PROJECT, texts.BTN_JOIN_AS_ADMIN]


async def test_cancel(feed, session):
    await feed.message("/cancel", from_user=FRESH_TG)
    assert session.last(SendMessage).text == texts.NOTHING_TO_CANCEL
    await feed.callback(RoleCb(role=MemberRole.USER).pack(), from_user=FRESH_TG)
    await feed.message("/cancel", from_user=FRESH_TG)
    assert session.last(SendMessage).text == texts.CANCELLED
