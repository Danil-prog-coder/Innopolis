"""Регрессионные тесты на замечания независимого ревью."""

import asyncio

import pytest
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendMessage

from app.bot import texts
from app.bot.callbacks import (
    AdminStartAction,
    AdminStartCb,
    DocsDoneCb,
    MenuAction,
    MenuCb,
    RemoveMemberCb,
)
from app.bot.dispatcher import build_dispatcher
from app.core.enums import MemberRole
from app.core.errors import GroupRebindForbidden, RemovedFromProject
from app.core.security import ProjectPasswordManager
from app.storage.postgres.models import DocUpdate, GroupChat, Membership, Project, Question
from tests.bot.conftest import ADMIN_TG, FRESH_TG, OUTSIDER_TG, USER_TG, Feeder

GROUP = -100777


def _doc(file_id: str, name: str) -> dict:
    return {
        "file_id": file_id,
        "file_unique_id": f"u-{file_id}",
        "file_name": name,
        "file_size": 10,
    }


@pytest.fixture
async def real_password(project_service, outsider):
    project = await project_service.create_project(outsider, "Гамма")
    return project, await project_service.reveal_password(outsider, project.id)


# 1. Пароль после исчерпания попыток не должен уйти в вопросы и к админам


async def test_password_after_too_many_attempts_is_not_forwarded(
    feed, session, project_service, project, user, real_password
):
    await project_service.switch_project(user, project.id)
    _, password = real_password
    await feed.callback(AdminStartCb(action=AdminStartAction.JOIN).pack(), from_user=USER_TG)
    for _ in range(6):
        await feed.message("deadbeef-wrong", from_user=USER_TG)
    assert session.last(SendMessage).text == texts.TOO_MANY_ATTEMPTS
    await feed.message(password, from_user=USER_TG)
    assert session.last(SendMessage).text == texts.TOO_MANY_ATTEMPTS
    assert not await Question.exists()
    assert all(password not in (m.text or "") for m in session.all(SendMessage))


async def test_password_typed_as_question_is_deleted_and_not_forwarded(
    feed, session, project_service, project, user, real_password
):
    await project_service.switch_project(user, project.id)
    _, password = real_password
    message = await feed.message(f"мой пароль {password}", from_user=USER_TG)
    assert session.last(DeleteMessage).message_id == message.message_id
    assert session.last(SendMessage).text == texts.PASSWORD_NOT_A_QUESTION
    assert not await Question.exists()


def test_looks_like_password_has_no_false_positive_on_normal_text():
    assert not ProjectPasswordManager.looks_like_password("deadbeef-123 что это за ошибка?")
    assert not ProjectPasswordManager.looks_like_password("a1b2c3d4-short")
    password = ProjectPasswordManager.generate("a1b2c3d4")
    assert ProjectPasswordManager.looks_like_password(f"вот: {password}")


# 2. Посторонний не может перепривязать чат команды к своему проекту


async def test_outsider_cannot_rebind_group(
    feed, session, project, project_password, other_project_password
):
    await feed.message(f"/join@test_bot {project_password}", chat_type="supergroup", chat_id=GROUP)
    await feed.message(
        f"/join@test_bot {other_project_password}",
        chat_type="supergroup",
        chat_id=GROUP,
        from_user=OUTSIDER_TG,
    )
    assert session.last(SendMessage).text == texts.GROUP_REBIND_FORBIDDEN
    assert (await GroupChat.get(chat_id=GROUP)).project_id == project.id


async def test_admin_of_bound_project_can_rebind(
    group_chat_service,
    membership_work,
    admin,
    other_project,
    project_password,
    other_project_password,
):
    await group_chat_service.bind(GROUP, project_password, admin)
    await membership_work.add_or_upgrade(admin.id, other_project.id, MemberRole.ADMIN)
    rebound = await group_chat_service.bind(GROUP, other_project_password, admin)
    assert rebound.id == other_project.id


async def test_rebind_service_rejects_non_admin(
    group_chat_service, admin, outsider, project_password, other_project_password
):
    await group_chat_service.bind(GROUP, project_password, admin)
    with pytest.raises(GroupRebindForbidden):
        await group_chat_service.bind(GROUP, other_project_password, outsider)


# 3. Удалённый участник теряет доступ: старая ссылка и пароль не работают, новая ссылка — да


async def test_removed_user_cannot_rejoin_with_old_invite(
    invite_service, membership_service, project, admin, user
):
    old_invite = await invite_service.create_invite(admin, project.id, 30)
    await membership_service.remove_member(admin, project.id, user.id)
    with pytest.raises(RemovedFromProject):
        await invite_service.redeem(user, old_invite.code)
    new_invite = await invite_service.create_invite(admin, project.id, 7)
    assert (await invite_service.redeem(user, new_invite.code)).project.id == project.id


async def test_removed_admin_cannot_rejoin_with_password(
    project_service, invite_service, membership_service, real_password, outsider, user
):
    project, password = real_password
    await project_service.join_as_admin(user, password)
    await membership_service.remove_member(outsider, project.id, user.id)
    with pytest.raises(RemovedFromProject):
        await project_service.join_as_admin(user, password)
    reinvite = await invite_service.create_invite(outsider, project.id, 7)
    await invite_service.redeem(user, reinvite.code)
    assert (await project_service.join_as_admin(user, password)).role == MemberRole.ADMIN


async def test_removed_user_old_link_via_bot(
    feed, session, invite_service, membership_service, project, admin, user
):
    old_invite = await invite_service.create_invite(admin, project.id, 30)
    await membership_service.remove_member(admin, project.id, user.id)
    await feed.message(f"/start {old_invite.code}", from_user=USER_TG)
    assert session.last(SendMessage).text == texts.REMOVED_FROM_PROJECT_BEFORE
    assert not await Membership.filter(project_id=project.id, user_id=user.id).exists()


# 4. Двойное нажатие «Готово» загружает документацию один раз


async def test_double_done_uploads_once(feed, session, fake_llm, notifier, project):
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_APPEND, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    await feed.message(document=_doc("f1", "a.md"), from_user=ADMIN_TG)
    done = DocsDoneCb(project_id=project.id).pack()
    await asyncio.gather(
        feed.callback(done, from_user=ADMIN_TG), feed.callback(done, from_user=ADMIN_TG)
    )
    await notifier.wait_background()
    assert len(fake_llm.uploads) == 1
    assert await DocUpdate.filter(project_id=project.id).count() == 1
    assert (await Project.get(id=project.id)).docs_version == 1


# 5. Кнопка проекта посреди другого шага открывает список проектов, а не становится вводом


async def test_project_button_during_create_project(feed, session, project_service, project, admin):
    await project_service.switch_project(admin, project.id)
    await feed.callback(AdminStartCb(action=AdminStartAction.CREATE).pack(), from_user=ADMIN_TG)
    await feed.message(texts.project_button("Альфа", MemberRole.ADMIN), from_user=ADMIN_TG)
    assert session.last(SendMessage).text == texts.PROJECTS_LIST
    assert not await Project.filter(name__startswith="📁").exists()


async def test_project_button_during_password_does_not_spend_attempt(
    feed, session, project_service, project, admin
):
    await project_service.switch_project(admin, project.id)
    await feed.callback(AdminStartCb(action=AdminStartAction.JOIN).pack(), from_user=ADMIN_TG)
    await feed.message(texts.project_button("Альфа", MemberRole.ADMIN), from_user=ADMIN_TG)
    assert session.all(DeleteMessage) == []
    assert session.last(SendMessage).text == texts.PROJECTS_LIST


# 6. Экран удаления не раскрывает имена пользователей вне проекта


async def test_confirm_remove_rejects_non_member(feed, session, other_project, user):
    await feed.callback(
        RemoveMemberCb(project_id=other_project.id, user_id=user.id).pack(), from_user=OUTSIDER_TG
    )
    assert session.last(AnswerCallbackQuery).text == texts.NOT_MEMBER
    assert session.all(EditMessageText) == []


# 7. Подтверждение паролем для замены документации устаревает


async def test_replace_confirmation_expires(
    feed, session, fake_llm, real_password, redis_client, monkeypatch
):
    project, password = real_password
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_REPLACE, project_id=project.id).pack(), from_user=OUTSIDER_TG
    )
    await feed.message(password, from_user=OUTSIDER_TG)
    await feed.message(document=_doc("f1", "new.md"), from_user=OUTSIDER_TG)

    from app.bot.routers import admin_docs

    monkeypatch.setattr(admin_docs, "REPLACE_CONFIRM_SECONDS", -1)
    await feed.callback(DocsDoneCb(project_id=project.id).pack(), from_user=OUTSIDER_TG)
    assert session.last(SendMessage).text == texts.REPLACE_CONFIRMATION_EXPIRED
    assert fake_llm.uploads == []


# 8. Список приглашений укладывается в лимиты Telegram


async def test_many_invites_list_is_capped(feed, session, invite_service, project, admin):
    for _ in range(30):
        await invite_service.create_invite(admin, project.id, 7)
    await feed.callback(
        MenuCb(action=MenuAction.INVITES, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    sent = session.last(SendMessage)
    assert len(sent.text) < 4096
    assert len(sent.reply_markup.inline_keyboard) == 20
    assert "ещё 10" in sent.text


# 9. Никакой тишины: непонятный ввод получает ответ, ошибка хендлера — сообщение


async def test_sticker_in_state_gets_hint(feed, session):
    await feed.callback(AdminStartCb(action=AdminStartAction.CREATE).pack(), from_user=FRESH_TG)
    await feed.message(photo=True, from_user=FRESH_TG)
    assert session.last(SendMessage).text == texts.WAITING_OTHER_INPUT


async def test_unknown_command_answered(feed, session):
    await feed.message("/foo", from_user=FRESH_TG)
    assert session.last(SendMessage).text == texts.UNKNOWN_COMMAND


async def test_group_plain_messages_stay_silent(feed, session):
    await feed.message("болтаем", chat_type="supergroup", chat_id=GROUP)
    assert session.all(SendMessage) == []


async def test_error_handler_reports_to_user(bot, session, redis_client, resolver, monkeypatch):
    from aiogram.fsm.storage.redis import RedisStorage

    from app.bot.routers import start

    async def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(start, "start", boom)
    dp = build_dispatcher(RedisStorage(redis=redis_client.redis), resolver, catch_errors=True)
    await Feeder(bot, dp).message("/start", from_user=FRESH_TG)
    assert session.last(SendMessage).text == texts.INTERNAL_ERROR
