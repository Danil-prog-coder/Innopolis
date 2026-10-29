from datetime import UTC, datetime, timedelta

from app.core.enums import DocsMode, QuestionStatus


async def test_active_invites_exclude_expired_and_revoked(invite_work, project, admin):
    now = datetime.now(UTC)
    await invite_work.create("ok", project.id, admin.id, now + timedelta(days=1))
    await invite_work.create("old", project.id, admin.id, now - timedelta(seconds=1))
    revoked = await invite_work.create("rev", project.id, admin.id, now + timedelta(days=1))
    assert await invite_work.revoke(revoked.id, project.id, now) is True
    active = await invite_work.get_active_for_project(project.id, now)
    assert [i.code for i in active] == ["ok"]


async def test_revoke_twice_and_foreign_project(invite_work, project, other_project, admin):
    now = datetime.now(UTC)
    invite = await invite_work.create("c", project.id, admin.id, now + timedelta(days=1))
    assert await invite_work.revoke(invite.id, other_project.id, now) is False
    assert await invite_work.revoke(invite.id, project.id, now) is True
    assert await invite_work.revoke(invite.id, project.id, now) is False


async def test_get_by_code(invite_work, project, admin):
    await invite_work.create("code-1", project.id, admin.id, datetime.now(UTC))
    assert (await invite_work.get_by_code("code-1")).project_id == project.id
    assert await invite_work.get_by_code("nope") is None


async def test_mark_answered_only_once(question_work, project, admin, second_admin):
    question = await question_work.create(project.id, 1, 1, 1, "why?", None)
    assert question.status == QuestionStatus.PENDING
    now = datetime.now(UTC)
    assert await question_work.mark_answered(question.id, admin.id, "because", now) is True
    assert await question_work.mark_answered(question.id, second_admin.id, "again", now) is False
    stored = await question_work.get_by_id(question.id)
    assert stored.answer == "because"
    assert stored.answered_by_id == admin.id
    assert stored.status == QuestionStatus.ANSWERED


async def test_doc_update_roundtrip(doc_update_work, project, admin):
    update = await doc_update_work.create(project.id, admin.id, DocsMode.REPLACE, "S")
    stored = await doc_update_work.get_by_id(update.id)
    assert stored.summary == "S" and stored.mode == DocsMode.REPLACE


async def test_group_bind_rebinds(group_chat_work, project, other_project):
    await group_chat_work.bind(-100, project.id, 1)
    await group_chat_work.bind(-100, other_project.id, 2)
    chat = await group_chat_work.get_by_chat_id(-100)
    assert chat.project_id == other_project.id
    assert chat.bound_by_telegram_id == 2
    assert await group_chat_work.get_by_chat_id(-200) is None
