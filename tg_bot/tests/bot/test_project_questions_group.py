import pytest
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteMessage,
    SendMessage,
    SendPhoto,
    SendRichMessage,
)
from aiogram.types import ReplyKeyboardMarkup

from app.bot import texts
from app.bot.callbacks import (
    AnswerQuestionCb,
    ChangesCb,
    DocsDoneCb,
    MenuAction,
    MenuCb,
    RoleCb,
    SaveKnowledgeCb,
    SwitchProjectCb,
)
from app.core.enums import DocsMode, MemberRole, QuestionStatus
from app.llm.gateway import AskResult, DocFile
from app.storage.postgres.models import GroupChat, Question
from tests.bot.conftest import ADMIN_TG, FRESH_TG, OUTSIDER_TG, SECOND_ADMIN_TG, USER_TG

FOUND = AskResult(found=True, answer="ответ")


@pytest.fixture
async def user_in_project(project_service, project, user):
    await project_service.switch_project(user, project.id)
    return user


@pytest.fixture
async def invite(invite_service, project, admin):
    return await invite_service.create_invite(admin, project.id, 7)


def _reply_keyboards(session) -> list[str]:
    return [
        m.reply_markup.keyboard[0][0].text
        for m in session.all(SendMessage)
        if isinstance(m.reply_markup, ReplyKeyboardMarkup)
    ]


# --- Подключение и переключение ---


async def test_paste_link_joins_project(feed, session, invite):
    await feed.callback(RoleCb(role=MemberRole.USER).pack(), from_user=OUTSIDER_TG)
    await feed.message(f"Держи: https://t.me/test_bot?start={invite.code}", from_user=OUTSIDER_TG)
    assert _reply_keyboards(session) == [texts.project_button("Альфа", MemberRole.USER)]


async def test_paste_garbage_keeps_waiting(feed, session, invite):
    await feed.callback(RoleCb(role=MemberRole.USER).pack(), from_user=OUTSIDER_TG)
    await feed.message("привет", from_user=OUTSIDER_TG)
    assert session.last(SendMessage).text == texts.BAD_INVITE_FORMAT
    await feed.message(invite.code, from_user=OUTSIDER_TG)
    assert _reply_keyboards(session)


async def test_project_button_lists_and_switches(
    feed, session, project_service, project_work, membership_work, user_in_project, user
):
    other = await project_work.create("Бета", "0000beef", "h", user.id)
    await membership_work.add_or_upgrade(user.id, other.id, MemberRole.ADMIN)
    await feed.message(texts.project_button("Альфа", MemberRole.USER), from_user=USER_TG)
    labels = [b.text for b in session.buttons()]
    assert "✅ Альфа · пользователь" in labels and "Бета · админ" in labels

    await feed.callback(SwitchProjectCb(project_id=other.id).pack(), from_user=USER_TG)
    assert _reply_keyboards(session)[-1] == texts.project_button("Бета", MemberRole.ADMIN)


async def test_switch_to_foreign_project_via_forged_button(
    feed, session, user_in_project, other_project
):
    await feed.callback(SwitchProjectCb(project_id=other_project.id).pack(), from_user=USER_TG)
    assert session.last(AnswerCallbackQuery).text == texts.NOT_MEMBER


async def test_instructions_and_join_other(feed, session, project):
    await feed.callback(
        MenuCb(action=MenuAction.INSTRUCTIONS, project_id=project.id).pack(), from_user=USER_TG
    )
    assert "/join@test_bot" in session.last(SendMessage).text
    assert session.buttons()[-1].url == "https://t.me/test_bot?startgroup=true"
    await feed.callback(
        MenuCb(action=MenuAction.JOIN_OTHER, project_id=project.id).pack(), from_user=USER_TG
    )
    assert session.last(SendMessage).text == texts.START


# --- Вопросы в личке ---


async def test_text_without_project_prompts_join(feed, session):
    await feed.message("как настроить?", from_user=FRESH_TG)
    assert session.last(SendMessage).text == texts.NO_PROJECT


async def test_answer_found_is_rich_markdown(feed, session, fake_llm, user_in_project, project):
    fake_llm.answer = AskResult(found=True, answer="## Как сохранить\n\n- Нажмите **Сохранить**")
    message = await feed.message("как сохранить?", from_user=USER_TG)
    sent = session.last(SendRichMessage)
    assert sent.rich_message.markdown == "## Как сохранить\n\n- Нажмите **Сохранить**"
    assert sent.reply_parameters.message_id == message.message_id
    assert fake_llm.last_project_id == project.id


async def test_rejected_rich_answer_falls_back_to_plain_text(
    feed, session, fake_llm, user_in_project
):
    session.failing_methods.add("SendRichMessage")
    fake_llm.answer = AskResult(found=True, answer="Нажмите <Сохранить> & готово")
    message = await feed.message("как сохранить?", from_user=USER_TG)
    sent = session.last(SendMessage)
    assert sent.text == "Нажмите <Сохранить> & готово" and sent.parse_mode is None
    assert sent.reply_parameters.message_id == message.message_id


async def test_not_found_goes_to_admins(feed, session, user_in_project, admin, second_admin):
    await feed.message("вопрос <X>", from_user=USER_TG)
    to_user = [m for m in session.all(SendMessage) if m.chat_id == USER_TG.id]
    assert to_user[-1].text == texts.NOT_FOUND_FORWARDED
    to_admins = [
        m for m in session.all(SendMessage) if m.chat_id in (ADMIN_TG.id, SECOND_ADMIN_TG.id)
    ]
    assert len(to_admins) == 2
    assert "вопрос &lt;X&gt;" in to_admins[0].text
    assert [b.text for b in to_admins[0].reply_markup.inline_keyboard[0]] == [texts.BTN_ANSWER]


async def test_very_long_question_fits_admin_message(feed, session, user_in_project):
    await feed.message("я" * 4000, from_user=USER_TG)
    to_admin = [m for m in session.all(SendMessage) if m.chat_id == ADMIN_TG.id][-1]
    assert len(to_admin.text) <= 4096


async def test_long_answer_is_one_rich_message(feed, session, fake_llm, user_in_project):
    fake_llm.answer = AskResult(found=True, answer="a" * 9000)
    await feed.message("q", from_user=USER_TG)
    assert len(session.last(SendRichMessage).rich_message.markdown) == 9000


async def test_too_long_answer_is_split_as_plain_text(feed, session, fake_llm, user_in_project):
    fake_llm.answer = AskResult(found=True, answer="a" * (texts.TELEGRAM_RICH_MESSAGE_LIMIT + 1))
    await feed.message("q", from_user=USER_TG)
    assert session.all(SendRichMessage) == []
    parts = [m for m in session.all(SendMessage) if m.chat_id == USER_TG.id]
    assert [len(m.text) for m in parts][-9:] == [4096] * 8 + [1]
    assert parts[-9].reply_parameters is not None and parts[-1].reply_parameters is None


async def test_llm_down_message(feed, session, fake_llm, user_in_project):
    fake_llm.fail = True
    await feed.message("q", from_user=USER_TG)
    assert session.last(SendMessage).text == texts.LLM_UNAVAILABLE


async def test_rate_limited_message(feed, session, settings, user_in_project):
    for _ in range(settings.question_rate_limit + 1):
        await feed.message("q", from_user=USER_TG)
    assert session.last(SendMessage).text == texts.RATE_LIMITED


async def test_removed_user_question_not_answered(
    feed, session, membership_work, user_in_project, project, user
):
    await membership_work.remove(user.id, project.id)
    await feed.message("q", from_user=USER_TG)
    assert session.last(SendMessage).text == texts.NO_PROJECT


async def test_screenshot_question_forwards_photo_to_admins(
    feed, session, user_in_project, fake_llm
):
    await feed.message(photo=True, caption="ошибка тут", from_user=USER_TG)
    assert fake_llm.last_question == "ошибка тут"
    photos = session.all(SendPhoto)
    assert {p.chat_id for p in photos} == {ADMIN_TG.id, SECOND_ADMIN_TG.id}
    assert photos[0].photo == "big" and len(photos[0].caption) <= 1024


async def test_screenshot_as_image_document(feed, session, user_in_project, fake_llm):
    fake_llm.answer = FOUND
    doc = {
        "file_id": "img",
        "file_unique_id": "u",
        "file_name": "s.png",
        "mime_type": "image/png",
        "file_size": 1000,
    }
    await feed.message(document=doc, from_user=USER_TG)
    assert session.last(SendRichMessage).rich_message.markdown == "ответ"


async def test_unknown_command_is_not_a_question(feed, session, fake_llm, user_in_project):
    await feed.message("/foo", from_user=USER_TG)
    assert fake_llm.ask_calls == 0


# --- Ответ админа ---


async def _ask_unanswered(feed) -> Question:
    await feed.message("вопрос X", from_user=USER_TG)
    return await Question.get(text="вопрос X")


async def test_admin_answer_reaches_asker_and_saves_knowledge(
    feed, session, fake_llm, user_in_project, project
):
    question = await _ask_unanswered(feed)
    await feed.callback(AnswerQuestionCb(question_id=question.id).pack(), from_user=ADMIN_TG)
    assert session.last(SendMessage).text == texts.ASK_ANSWER
    await feed.message("вот <ответ>\nвторая строка", from_user=ADMIN_TG)

    delivered = session.last(SendRichMessage)
    assert delivered.chat_id == USER_TG.id
    assert delivered.rich_message.html == (
        "<p><b>Ответ админа на ваш вопрос</b></p><blockquote>вопрос X</blockquote>"
        "<p>вот &lt;ответ&gt;</p><p>вторая строка</p>"
    )
    assert delivered.reply_parameters.message_id == question.message_id
    assert session.last(SendMessage).text == texts.ANSWER_SENT

    await feed.callback(SaveKnowledgeCb(question_id=question.id).pack(), from_user=ADMIN_TG)
    assert session.last(AnswerCallbackQuery).text == texts.KNOWLEDGE_SAVED
    assert fake_llm.knowledge == [(project.id, "вопрос X", "вот <ответ>\nвторая строка")]


async def test_admin_answer_falls_back_to_plain_text(feed, session, user_in_project):
    question = await _ask_unanswered(feed)
    session.failing_methods.add("SendRichMessage")
    await feed.callback(AnswerQuestionCb(question_id=question.id).pack(), from_user=ADMIN_TG)
    await feed.message("вот <ответ>", from_user=ADMIN_TG)
    delivered = [m for m in session.all(SendMessage) if m.chat_id == USER_TG.id][-1]
    assert delivered.text == texts.answer_to_asker("вопрос X", "вот <ответ>")
    assert delivered.parse_mode is None
    assert delivered.reply_parameters.message_id == question.message_id
    assert session.last(SendMessage).text == texts.ANSWER_SENT


async def test_second_admin_sees_already_answered(feed, session, user_in_project):
    question = await _ask_unanswered(feed)
    await feed.callback(AnswerQuestionCb(question_id=question.id).pack(), from_user=ADMIN_TG)
    await feed.callback(AnswerQuestionCb(question_id=question.id).pack(), from_user=SECOND_ADMIN_TG)
    await feed.message("первый", from_user=ADMIN_TG)
    await feed.message("второй", from_user=SECOND_ADMIN_TG)
    assert session.last(SendMessage).text == texts.ALREADY_ANSWERED
    assert (await Question.get(id=question.id)).answer == "первый"
    assert (await Question.get(id=question.id)).status == QuestionStatus.ANSWERED


async def test_user_cannot_press_answer(feed, session, user_in_project):
    question = await _ask_unanswered(feed)
    await feed.callback(AnswerQuestionCb(question_id=question.id).pack(), from_user=USER_TG)
    assert session.last(AnswerCallbackQuery).text == texts.NO_RIGHTS


# --- «Что изменилось» ---


async def test_changes_button(feed, session, docs_service, project, admin, fake_llm):
    fake_llm.summary = "- Добавлен раздел **API**"
    update = await docs_service.apply_update(
        admin, project.id, DocsMode.APPEND, [DocFile("a", b"")]
    )
    await feed.callback(ChangesCb(update_id=update.id).pack(), from_user=USER_TG)
    sent = session.last(SendRichMessage)
    assert sent.rich_message.markdown == "- Добавлен раздел **API**"
    await feed.callback(ChangesCb(update_id=update.id).pack(), from_user=OUTSIDER_TG)
    assert session.last(AnswerCallbackQuery).text == texts.NOT_MEMBER


async def test_stale_button_answers(feed, session, project):
    await feed.callback(DocsDoneCb(project_id=project.id).pack(), from_user=ADMIN_TG)
    assert session.last(AnswerCallbackQuery).text == texts.BUTTON_EXPIRED


# --- Групповой чат ---

GROUP = -100500


async def test_join_binds_group(feed, session, project_password, project):
    await feed.message(
        f"/join@test_bot {project_password}",
        chat_type="supergroup",
        chat_id=GROUP,
        from_user=OUTSIDER_TG,
    )
    assert (await GroupChat.get(chat_id=GROUP)).project_id == project.id
    assert "Альфа" in session.last(SendMessage).text


async def test_join_without_code(feed, session):
    await feed.message("/join@test_bot", chat_type="group", chat_id=GROUP)
    assert session.last(SendMessage).text == texts.JOIN_USAGE


async def test_command_for_other_bot_ignored(feed, session, project_password):
    await feed.message(f"/join@other_bot {project_password}", chat_type="group", chat_id=GROUP)
    assert not await GroupChat.filter(chat_id=GROUP).exists()


async def test_ask_in_unbound_group(feed, session):
    await feed.message("/ask@test_bot что такое X?", chat_type="group", chat_id=GROUP)
    assert session.last(SendMessage).text == texts.group_not_bound("test_bot")


async def test_mention_question_answered_in_group(
    feed, session, fake_llm, project_password, project
):
    await feed.message(f"/join@test_bot {project_password}", chat_type="supergroup", chat_id=GROUP)
    fake_llm.answer = FOUND
    await feed.message(
        "@test_bot что такое X?",
        entities=[{"type": "mention", "offset": 0, "length": 9}],
        chat_type="supergroup",
        chat_id=GROUP,
        from_user=OUTSIDER_TG,
    )
    assert fake_llm.last_question == "что такое X?"
    assert fake_llm.last_project_id == project.id
    assert session.last(SendMessage).chat_id == GROUP


async def test_group_ignores_plain_messages(feed, fake_llm, project_password):
    await feed.message(f"/join@test_bot {project_password}", chat_type="supergroup", chat_id=GROUP)
    await feed.message("просто болтаем", chat_type="supergroup", chat_id=GROUP)
    assert fake_llm.ask_calls == 0


async def test_group_not_found_and_admin_answer_goes_to_group(feed, session, project_password):
    await feed.message(f"/join@test_bot {project_password}", chat_type="supergroup", chat_id=GROUP)
    await feed.message(
        "/ask@test_bot где логи?", chat_type="supergroup", chat_id=GROUP, from_user=OUTSIDER_TG
    )
    assert session.last(SendMessage).chat_id in (ADMIN_TG.id, SECOND_ADMIN_TG.id)
    question = await Question.get(text="где логи?")
    assert question.chat_id == GROUP
    await feed.callback(AnswerQuestionCb(question_id=question.id).pack(), from_user=ADMIN_TG)
    await feed.message("в /var/log", from_user=ADMIN_TG)
    delivered = session.last(SendRichMessage)
    assert delivered.chat_id == GROUP and "<p>в /var/log</p>" in delivered.rich_message.html


async def test_empty_ask(feed, session, project_password):
    await feed.message(f"/join@test_bot {project_password}", chat_type="supergroup", chat_id=GROUP)
    await feed.message("/ask@test_bot", chat_type="supergroup", chat_id=GROUP)
    assert session.last(SendMessage).text == texts.ASK_USAGE


async def test_join_deletes_password_and_never_echoes_it(feed, session, project_password):
    message = await feed.message(
        f"/join@test_bot {project_password}", chat_type="supergroup", chat_id=GROUP
    )
    assert session.last(DeleteMessage).message_id == message.message_id
    assert all(project_password not in (m.text or "") for m in session.all(SendMessage))
    assert texts.DELETE_PASSWORD_MANUALLY not in session.last(SendMessage).text


async def test_join_without_delete_rights_asks_to_delete(feed, session, project, project_password):
    session.failing_methods.add("DeleteMessage")
    await feed.message(f"/join@test_bot {project_password}", chat_type="supergroup", chat_id=GROUP)
    reply = session.last(SendMessage).text
    assert "Альфа" in reply and texts.DELETE_PASSWORD_MANUALLY in reply
    assert (await GroupChat.get(chat_id=GROUP)).project_id == project.id


async def test_join_with_wrong_password(feed, session, project_password):
    await feed.message(
        "/join@test_bot a1b2c3d4-wrongwrongwrongwrongwrong", chat_type="group", chat_id=GROUP
    )
    assert session.last(SendMessage).text.startswith(texts.INVALID_PASSWORD)
    assert not await GroupChat.filter(chat_id=GROUP).exists()


async def test_join_with_invite_code_no_longer_works(feed, session, invite):
    await feed.message(f"/join@test_bot {invite.code}", chat_type="group", chat_id=GROUP)
    assert not await GroupChat.filter(chat_id=GROUP).exists()
