import asyncio

import pytest
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendMessage

from app.bot import texts
from app.bot.callbacks import (
    AdminStartAction,
    AdminStartCb,
    DocsCancelCb,
    DocsDoneCb,
    InviteTtlCb,
    MenuAction,
    MenuCb,
    PasswordRevealCb,
    RemoveMemberCb,
    RevokeInviteCb,
)
from app.core.enums import DocsMode, MemberRole
from app.storage.postgres.models import Invite, Membership, Project
from tests.bot.conftest import ADMIN_TG, FRESH_TG, OUTSIDER_TG, SECOND_ADMIN_TG, USER_TG


def _doc(file_id: str, name: str, size: int = 100) -> dict:
    return {
        "file_id": file_id,
        "file_unique_id": f"u-{file_id}",
        "file_name": name,
        "file_size": size,
    }


@pytest.fixture
async def created_project(feed, session, project_service, user_work):
    """Проект, созданный через бота пользователем FRESH_TG, и его пароль."""
    await feed.callback(AdminStartCb(action=AdminStartAction.CREATE).pack(), from_user=FRESH_TG)
    await feed.message("Дельта", from_user=FRESH_TG)
    creator = await user_work.get_by_telegram_id(FRESH_TG.id)
    project = await Project.get(name="Дельта")
    password = await project_service.reveal_password(creator, project.id)
    session.clear()
    return project, password


async def test_create_project_flow_password_only_in_alert(feed, session):
    await feed.callback(AdminStartCb(action=AdminStartAction.CREATE).pack(), from_user=FRESH_TG)
    assert session.last(SendMessage).text == texts.ASK_PROJECT_NAME
    await feed.message("Дельта", from_user=FRESH_TG)

    reveal = next(b for b in session.buttons() if b.callback_data.startswith("pwreveal"))
    await feed.callback(reveal.callback_data, from_user=FRESH_TG)
    alert = session.last(AnswerCallbackQuery)
    assert alert.show_alert is True
    assert alert.text.startswith((await Project.get(name="Дельта")).public_id + "-")
    assert all(alert.text not in (m.text or "") for m in session.all(SendMessage))
    assert texts.BTN_MEMBERS in [b.text for b in session.buttons()]  # создатель — админ


async def test_create_project_invalid_name_keeps_state(feed, session):
    await feed.callback(AdminStartCb(action=AdminStartAction.CREATE).pack(), from_user=FRESH_TG)
    await feed.message("x" * 65, from_user=FRESH_TG)
    assert session.last(SendMessage).text == texts.INVALID_PROJECT_NAME
    await feed.message("Ок", from_user=FRESH_TG)
    assert await Project.filter(name="Ок").exists()


async def test_password_reveal_by_other_user_rejected(feed, session, created_project):
    project, _ = created_project
    await feed.callback(PasswordRevealCb(project_id=project.id).pack(), from_user=OUTSIDER_TG)
    assert session.last(AnswerCallbackQuery).text == texts.NO_RIGHTS


async def test_password_reveal_expired(feed, session, created_project, redis_client):
    project, _ = created_project
    await redis_client.redis.flushall()
    await feed.callback(PasswordRevealCb(project_id=project.id).pack(), from_user=FRESH_TG)
    assert session.last(AnswerCallbackQuery).text == texts.PASSWORD_EXPIRED


async def test_join_as_admin_deletes_password_message(feed, session, created_project):
    project, password = created_project
    await feed.callback(AdminStartCb(action=AdminStartAction.JOIN).pack(), from_user=OUTSIDER_TG)
    message = await feed.message(password, from_user=OUTSIDER_TG)
    assert session.last(DeleteMessage).message_id == message.message_id
    membership = await Membership.get(project_id=project.id, user__telegram_id=OUTSIDER_TG.id)
    assert membership.role == MemberRole.ADMIN


async def test_join_as_admin_wrong_password_keeps_state(feed, session, created_project):
    project, password = created_project
    await feed.callback(AdminStartCb(action=AdminStartAction.JOIN).pack(), from_user=OUTSIDER_TG)
    await feed.message("deadbeef-wrong", from_user=OUTSIDER_TG)
    assert session.last(SendMessage).text == texts.INVALID_PASSWORD
    assert len(session.all(DeleteMessage)) == 1
    await feed.message(password, from_user=OUTSIDER_TG)
    assert await Membership.filter(project_id=project.id, user__telegram_id=OUTSIDER_TG.id).exists()


async def test_invite_flow(feed, session, project):
    await feed.callback(
        MenuCb(action=MenuAction.INVITE, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    assert session.last(SendMessage).text == texts.CHOOSE_INVITE_TTL
    await feed.callback(InviteTtlCb(project_id=project.id, days=7).pack(), from_user=ADMIN_TG)
    invite = await Invite.get(project_id=project.id)
    assert f"https://t.me/test_bot?start={invite.code}" in session.last(SendMessage).text

    await feed.callback(
        MenuCb(action=MenuAction.INVITES, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    assert invite.code in session.last(SendMessage).text
    await feed.callback(
        RevokeInviteCb(project_id=project.id, invite_id=invite.id).pack(), from_user=ADMIN_TG
    )
    assert session.last(AnswerCallbackQuery).text == texts.INVITE_REVOKED_OK
    await feed.callback(
        MenuCb(action=MenuAction.INVITES, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    assert session.last(SendMessage).text == texts.NO_ACTIVE_INVITES


async def test_forged_ttl_ignored(feed, project):
    await feed.callback(InviteTtlCb(project_id=project.id, days=3650).pack(), from_user=ADMIN_TG)
    assert not await Invite.filter(project_id=project.id).exists()


@pytest.mark.parametrize("action", list(MenuAction)[2:])  # все админские действия
async def test_user_cannot_use_admin_buttons(feed, session, project, action):
    await feed.callback(MenuCb(action=action, project_id=project.id).pack(), from_user=USER_TG)
    assert session.last(AnswerCallbackQuery).text == texts.NO_RIGHTS


async def test_members_list_and_remove(feed, session, project, user):
    await feed.callback(
        MenuCb(action=MenuAction.MEMBERS, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    assert "Участники проекта</b>: 3" in session.last(SendMessage).text
    await feed.callback(
        RemoveMemberCb(project_id=project.id, user_id=user.id).pack(), from_user=ADMIN_TG
    )
    assert "Удалить" in session.last(EditMessageText).text
    await feed.callback(
        RemoveMemberCb(project_id=project.id, user_id=user.id, confirm=True).pack(),
        from_user=ADMIN_TG,
    )
    assert not await Membership.filter(project_id=project.id, user_id=user.id).exists()
    notice = session.last(SendMessage)
    assert notice.chat_id == USER_TG.id and "Вас удалили" in notice.text


async def test_cannot_remove_creator_via_button(feed, session, project, admin):
    await feed.callback(
        RemoveMemberCb(project_id=project.id, user_id=admin.id, confirm=True).pack(),
        from_user=SECOND_ADMIN_TG,
    )
    assert session.last(AnswerCallbackQuery).text == texts.CANNOT_REMOVE_CREATOR


async def test_replace_docs_requires_password(feed, session, project):
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_REPLACE, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    assert session.last(SendMessage).text == texts.ASK_PASSWORD_FOR_REPLACE
    await feed.message("a1b2c3d4-wrong", from_user=ADMIN_TG)
    assert session.last(SendMessage).text == texts.INVALID_PASSWORD


async def test_replace_docs_full_flow(feed, session, created_project, fake_llm, user_work):
    project, password = created_project
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_REPLACE, project_id=project.id).pack(), from_user=FRESH_TG
    )
    pw_message = await feed.message(password, from_user=FRESH_TG)
    assert session.last(DeleteMessage).message_id == pw_message.message_id
    await feed.message(document=_doc("f1", "new.md"), from_user=FRESH_TG)
    await feed.callback(DocsDoneCb(project_id=project.id).pack(), from_user=FRESH_TG)
    assert fake_llm.uploads == [(project.id, DocsMode.REPLACE, ["new.md"])]


async def test_too_large_document_rejected(feed, session, project):
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_APPEND, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    await feed.message(document=_doc("big", "big.pdf", 20 * 1024 * 1024 + 1), from_user=ADMIN_TG)
    assert session.last(SendMessage).text == texts.FILE_TOO_LARGE


async def test_files_limit(feed, session, project, settings):
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_APPEND, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    for i in range(settings.docs_max_files):
        await feed.message(document=_doc(f"f{i}", f"{i}.md"), from_user=ADMIN_TG)
    await feed.message(document=_doc("extra", "extra.md"), from_user=ADMIN_TG)
    assert session.last(SendMessage).text == texts.too_many_files(settings.docs_max_files)


async def test_text_in_upload_state_gets_hint(feed, session, project):
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_APPEND, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    await feed.message("а где кнопка?", from_user=ADMIN_TG)
    assert session.last(SendMessage).text == texts.SEND_DOCUMENT_HINT


async def test_docs_done_notifies_members(
    feed, session, project, fake_llm, notifier, admin, second_admin, user
):
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_APPEND, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    await feed.message(document=_doc("f1", "a.md"), from_user=ADMIN_TG)
    await feed.message(document=_doc("f2", "b.md"), from_user=ADMIN_TG)
    await feed.callback(DocsDoneCb(project_id=project.id).pack(), from_user=ADMIN_TG)
    await notifier.wait_background()

    assert fake_llm.uploads == [(project.id, DocsMode.APPEND, ["a.md", "b.md"])]
    assert (await Project.get(id=project.id)).docs_version == 1
    notified = [m for m in session.all(SendMessage) if m.text == texts.docs_updated("Альфа")]
    assert {m.chat_id for m in notified} == {
        admin.telegram_id,
        second_admin.telegram_id,
        user.telegram_id,
    }
    assert notified[0].reply_markup.inline_keyboard[0][0].text == texts.BTN_CHANGES


async def test_docs_done_without_files(feed, session, project):
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_APPEND, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    await feed.callback(DocsDoneCb(project_id=project.id).pack(), from_user=ADMIN_TG)
    assert session.last(AnswerCallbackQuery).text == texts.NO_FILES


async def test_docs_done_llm_down_keeps_draft(feed, session, project, fake_llm):
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_APPEND, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    await feed.message(document=_doc("f1", "a.md"), from_user=ADMIN_TG)
    fake_llm.fail = True
    await feed.callback(DocsDoneCb(project_id=project.id).pack(), from_user=ADMIN_TG)
    assert session.last(SendMessage).text == texts.LLM_UNAVAILABLE
    fake_llm.fail = False
    await feed.callback(DocsDoneCb(project_id=project.id).pack(), from_user=ADMIN_TG)
    assert fake_llm.uploads == [(project.id, DocsMode.APPEND, ["a.md"])]


async def test_docs_cancel(feed, session, project, fake_llm):
    await feed.callback(
        MenuCb(action=MenuAction.DOCS_APPEND, project_id=project.id).pack(), from_user=ADMIN_TG
    )
    await feed.message(document=_doc("f1", "a.md"), from_user=ADMIN_TG)
    await feed.callback(DocsCancelCb(project_id=project.id).pack(), from_user=ADMIN_TG)
    assert session.last(SendMessage).text == texts.DOCS_CANCELLED
    await feed.callback(DocsDoneCb(project_id=project.id).pack(), from_user=ADMIN_TG)
    assert fake_llm.uploads == []


async def test_created_project_has_copy_button_with_password(
    feed, session, notifier, project_service, user_work
):
    await feed.callback(AdminStartCb(action=AdminStartAction.CREATE).pack(), from_user=FRESH_TG)
    await feed.message("Эпсилон", from_user=FRESH_TG)
    creator = await user_work.get_by_telegram_id(FRESH_TG.id)
    project = await Project.get(name="Эпсилон")
    password = await project_service.reveal_password(creator, project.id)

    copy = next(b for b in session.buttons() if b.copy_text is not None)
    assert copy.text == texts.BTN_COPY_PASSWORD
    assert copy.copy_text.text == password
    # Пароль не появляется текстом ни в одном сообщении
    assert all(password not in (m.text or "") for m in session.all(SendMessage))
    # Снятие кнопок запланировано; остановка бота его отменяет, а не ждёт 10 минут
    assert len(notifier._scheduled) == 1
    await asyncio.wait_for(notifier.shutdown(), timeout=1)
    assert not notifier._scheduled
