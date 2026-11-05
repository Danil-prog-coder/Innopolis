"""Постепенный показ ответа: черновики в личке, заглушка в группе, кнопка «Стоп»."""

import asyncio

import pytest
from aiogram.methods import (
    DeleteMessage,
    EditMessageText,
    SendChatAction,
    SendMessage,
    SendMessageDraft,
    SendRichMessage,
    SendRichMessageDraft,
)
from aiogram.types import Chat, MessageGenerationStopped, Update

from app.bot import texts
from app.bot.streaming import (
    GenerationRegistry,
    StreamTiming,
    balance_markdown,
    plain_preview,
    replay_frames,
    stream_answer,
    truncate,
)
from app.llm.gateway import AskResult
from app.services.question_service import AskOutcome
from tests.bot.conftest import OUTSIDER_TG, USER_TG

ANSWER = "## Как сохранить\n\nНажмите **Сохранить** в правом верхнем углу экрана настроек"
GROUP = -100500


@pytest.fixture
async def user_in_project(project_service, project, user):
    await project_service.switch_project(user, project.id)
    return user


@pytest.fixture
async def bound_group(feed, project_password):
    await feed.message(f"/join@test_bot {project_password}", chat_type="supergroup", chat_id=GROUP)


def _ask_in_group(feed, text="что такое X?"):
    return feed.message(
        f"/ask@test_bot {text}", chat_type="supergroup", chat_id=GROUP, from_user=OUTSIDER_TG
    )


def _stopped(chat_id: int, draft_id: int, update_id: int = 9000) -> Update:
    return Update(
        update_id=update_id,
        stopped_message_generation=MessageGenerationStopped(
            chat=Chat(id=chat_id, type="private"), draft_id=draft_id
        ),
    )


# --- Разметка недописанного ответа ---


def test_replay_frames_grow_by_whole_words():
    text = "один два три четыре пять шесть семь восемь"
    frames = replay_frames(text, 4)
    assert len(frames) == 3
    assert text not in frames  # полный текст покажет финальное сообщение
    assert all(text.startswith(f) for f in frames)
    assert [len(f) for f in frames] == sorted({len(f) for f in frames})
    words = set(text.split())
    assert all(set(f.split()) <= words for f in frames)  # слова не рвутся пополам


@pytest.mark.parametrize("count", [0, 1])
def test_replay_frames_without_animation(count):
    assert replay_frames("текст", count) == []


def test_replay_frames_of_one_long_word_cuts_hard():
    frames = replay_frames("a" * 10, 2)
    assert frames == ["a" * 5]


def test_replay_frames_empty_text():
    assert replay_frames("", 5) == []


def test_balance_closes_open_code_block():
    assert balance_markdown("Пример:\n```python\nprint(1)") == "Пример:\n```python\nprint(1)\n```"


def test_balance_drops_unpaired_inline_markers():
    assert balance_markdown("Нажмите **Сохра") == "Нажмите Сохра"
    assert balance_markdown("Команда `git sta") == "Команда git sta"
    assert balance_markdown("~~старое") == "старое"


def test_balance_keeps_complete_markdown():
    text = "**жирный** и `код`\n```\nблок\n```"
    assert balance_markdown(text) == text


def test_plain_preview_strips_markup():
    markdown = (
        "## Заголовок\n\n"
        "- пункт **важный**\n"
        "* второй с `кодом`\n\n"
        "> цитата\n\n"
        "| Поле | Тип |\n|---|:---:|\n| id | int |\n\n"
        "[ссылка](https://example.com)\n"
        "```python\nx = 1\n```\n"
        "Обрыв **жирн"
    )
    assert plain_preview(markdown) == (
        "Заголовок\n\n"
        "• пункт важный\n"
        "• второй с кодом\n\n"
        "цитата\n\n"
        "Поле │ Тип\n"
        "id │ int\n\n"
        "ссылка\n"
        "x = 1\n"
        "Обрыв жирн"
    )


def test_plain_preview_keeps_snake_case():
    # Одиночные подчёркивания — часть имён, а не разметка
    assert plain_preview("поле user_name_id в `config_file`") == "поле user_name_id в config_file"


def test_truncate():
    assert truncate("коротко", 10) == "коротко"
    cut = truncate("а" * 20, 10)
    assert len(cut) == 10 and cut.endswith("…")


# --- Реестр генераций ---


async def test_registry_stop():
    registry = GenerationRegistry()
    task = asyncio.ensure_future(asyncio.sleep(10))
    registry.add(1, 2, task)
    assert registry.stop(1, 3) is False
    assert registry.stop(1, 2) is True
    with pytest.raises(asyncio.CancelledError):
        await task
    assert registry.stop(1, 2) is False  # уже остановлена


async def test_registry_discard_keeps_newer_task():
    registry = GenerationRegistry()
    old, new = asyncio.ensure_future(asyncio.sleep(0)), asyncio.ensure_future(asyncio.sleep(0))
    registry.add(1, 2, old)
    registry.add(1, 2, new)
    registry.discard(1, 2, old)
    assert len(registry) == 1
    registry.discard(1, 2, new)
    assert len(registry) == 0
    await asyncio.gather(old, new)


# --- stream_answer без Telegram ---


class FakePreview:
    def __init__(self, on_show=None) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._on_show = on_show

    async def start(self):
        self.calls.append(("start", None))

    async def keepalive(self):
        self.calls.append(("keepalive", None))

    async def show(self, markdown):
        self.calls.append(("show", markdown))
        if self._on_show:
            self._on_show()

    async def finish(self):
        self.calls.append(("finish", None))


TIMING = StreamTiming(frame_interval=0.001, replay_seconds=0.003, keepalive_seconds=10)


async def test_stream_answer_shows_frames_then_finishes():
    preview = FakePreview()

    async def ask():
        return AskOutcome(answer="раз два три четыре")

    outcome = await stream_answer(preview, ask, TIMING)
    assert outcome.answer == "раз два три четыре"
    names = [name for name, _ in preview.calls]
    assert names[0] == "start" and names[-1] == "finish"
    shown = [text for name, text in preview.calls if name == "show"]
    assert shown == ["раз", "раз два три"]


async def test_stream_answer_not_found_shows_nothing():
    preview = FakePreview()

    async def ask():
        return AskOutcome(question=None)

    await stream_answer(preview, ask, TIMING)
    assert [name for name, _ in preview.calls] == ["start", "finish"]


async def test_stream_answer_error_propagates_after_cleanup():
    preview = FakePreview()

    async def ask():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await stream_answer(preview, ask, TIMING)
    assert preview.calls[-1] == ("finish", None)


async def test_stream_answer_keeps_preview_alive_while_waiting():
    preview = FakePreview()

    async def ask():
        await asyncio.sleep(0.05)
        return AskOutcome(question=None)

    timing = StreamTiming(frame_interval=0.001, replay_seconds=0, keepalive_seconds=0.01)
    await stream_answer(preview, ask, timing)
    assert sum(name == "keepalive" for name, _ in preview.calls) >= 2


async def test_stop_during_replay_returns_none():
    registry = GenerationRegistry()
    preview = FakePreview(on_show=lambda: registry.stop(7, 8))

    async def ask():
        return AskOutcome(answer="раз два три четыре пять")

    outcome = await stream_answer(preview, ask, TIMING, registry, stop_key=(7, 8))
    assert outcome is None
    assert sum(name == "show" for name, _ in preview.calls) == 1
    assert preview.calls[-1] == ("finish", None)
    assert len(registry) == 0


# --- Личка ---


async def test_private_answer_is_streamed_as_drafts(feed, session, fake_llm, user_in_project):
    fake_llm.answer = AskResult(found=True, answer=ANSWER)
    message = await feed.message("как сохранить?", from_user=USER_TG)

    thinking = session.all(SendMessageDraft)[0]
    assert thinking.text == "" and thinking.can_stop is True
    assert thinking.draft_id == message.message_id and thinking.parse_mode is None

    drafts = [d.rich_message.markdown for d in session.all(SendRichMessageDraft)]
    assert len(drafts) == 2  # replay 0.03 с / кадр 0.01 с → 3 шага, полный текст — финалом
    assert {d.draft_id for d in session.all(SendRichMessageDraft)} == {message.message_id}
    assert len(drafts[0]) < len(drafts[1]) < len(ANSWER)
    assert all(d.count("**") % 2 == 0 for d in drafts)  # оборванная разметка не протекает

    final = session.last(SendRichMessage)
    assert final.rich_message.markdown == ANSWER
    last_draft = session.all(SendRichMessageDraft)[-1]
    assert session.requests.index(final) > session.requests.index(last_draft)
    assert session.all(SendChatAction) == []


async def test_streaming_disabled_answers_as_before(
    feed, session, fake_llm, settings, user_in_project
):
    settings.answer_streaming_enabled = False
    fake_llm.answer = AskResult(found=True, answer=ANSWER)
    await feed.message("как сохранить?", from_user=USER_TG)
    assert session.all(SendMessageDraft) == [] and session.all(SendRichMessageDraft) == []
    assert session.last(SendChatAction).action == "typing"
    assert session.last(SendRichMessage).rich_message.markdown == ANSWER


async def test_rejected_rich_draft_falls_back_to_plain_draft(
    feed, session, fake_llm, user_in_project
):
    session.failing_methods.add("SendRichMessageDraft")
    fake_llm.answer = AskResult(found=True, answer=ANSWER)
    await feed.message("как сохранить?", from_user=USER_TG)
    assert len(session.all(SendRichMessageDraft)) == 1  # после отказа rich больше не пробуем
    plain = [d.text for d in session.all(SendMessageDraft) if d.text]
    assert len(plain) == 2
    full = "Как сохранить\n\nНажмите Сохранить в правом верхнем углу экрана настроек"
    assert all(full.startswith(text) and "**" not in text and "#" not in text for text in plain)
    assert session.last(SendRichMessage).rich_message.markdown == ANSWER


async def test_drafts_unavailable_still_answers(feed, session, fake_llm, user_in_project):
    session.failing_methods.update({"SendMessageDraft", "SendRichMessageDraft"})
    fake_llm.answer = AskResult(found=True, answer=ANSWER)
    await feed.message("как сохранить?", from_user=USER_TG)
    assert len(session.all(SendMessageDraft)) == 1  # после отказа черновики не шлём
    assert session.all(SendRichMessageDraft) == []
    assert session.last(SendRichMessage).rich_message.markdown == ANSWER


async def test_private_not_found_has_no_answer_drafts(feed, session, user_in_project):
    await feed.message("вопрос без ответа", from_user=USER_TG)
    assert session.all(SendRichMessageDraft) == []
    to_user = [m for m in session.all(SendMessage) if m.chat_id == USER_TG.id]
    assert to_user[-1].text == texts.NOT_FOUND_FORWARDED


async def test_private_llm_down(feed, session, fake_llm, user_in_project):
    fake_llm.fail = True
    await feed.message("как сохранить?", from_user=USER_TG)
    assert session.last(SendMessage).text == texts.LLM_UNAVAILABLE


async def test_screenshot_answer_is_streamed(feed, session, fake_llm, user_in_project):
    fake_llm.answer = AskResult(found=True, answer=ANSWER)
    await feed.message(photo=True, from_user=USER_TG)
    assert session.all(SendRichMessageDraft)
    assert session.last(SendRichMessage).rich_message.markdown == ANSWER


async def test_stop_button_cancels_generation(feed, session, fake_llm, user_in_project, dp, bot):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow_ask(project_id, question):
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return AskResult(found=True, answer=ANSWER)

    fake_llm.ask = slow_ask
    asking = asyncio.create_task(feed.message("как сохранить?", from_user=USER_TG))
    await asyncio.wait_for(started.wait(), 1)

    draft = session.last(SendMessageDraft)
    await dp.feed_update(bot, _stopped(USER_TG.id, draft.draft_id))
    await asyncio.wait_for(asking, 1)

    assert cancelled.is_set()  # запрос к ИИ брошен, а не дожидается ответа
    assert session.last(SendMessage).text == texts.GENERATION_STOPPED
    assert session.all(SendRichMessage) == []
    assert len(dp["generation_registry"]) == 0


async def test_stop_for_other_chat_is_ignored(feed, session, fake_llm, user_in_project, dp, bot):
    started, release = asyncio.Event(), asyncio.Event()

    async def slow_ask(project_id, question):
        started.set()
        await release.wait()
        return AskResult(found=True, answer=ANSWER)

    fake_llm.ask = slow_ask
    asking = asyncio.create_task(feed.message("как сохранить?", from_user=USER_TG))
    await asyncio.wait_for(started.wait(), 1)
    draft = session.last(SendMessageDraft)

    # Чужой чат с тем же draft_id не может остановить генерацию
    await dp.feed_update(bot, _stopped(USER_TG.id + 1, draft.draft_id))
    release.set()
    await asyncio.wait_for(asking, 1)
    assert session.last(SendRichMessage).rich_message.markdown == ANSWER


async def test_stop_after_answer_is_noop(feed, session, fake_llm, user_in_project, dp, bot):
    fake_llm.answer = AskResult(found=True, answer=ANSWER)
    message = await feed.message("как сохранить?", from_user=USER_TG)
    session.clear()
    await dp.feed_update(bot, _stopped(USER_TG.id, message.message_id))
    assert session.requests == []


# --- Группа ---


async def test_group_answer_is_streamed_by_editing_placeholder(
    feed, session, fake_llm, bound_group
):
    fake_llm.answer = AskResult(found=True, answer=ANSWER)
    session.clear()
    question = await _ask_in_group(feed)

    placeholder = session.all(SendMessage)[0]
    assert placeholder.chat_id == GROUP and placeholder.text == texts.ANSWER_PLACEHOLDER
    assert placeholder.reply_parameters.message_id == question.message_id

    edits = session.all(EditMessageText)
    assert edits and all(e.parse_mode is None and "**" not in e.text for e in edits)
    assert session.all(SendMessageDraft) == []  # в группах черновиков нет

    deleted = session.last(DeleteMessage)
    final = session.last(SendRichMessage)
    assert final.chat_id == GROUP and final.rich_message.markdown == ANSWER
    assert session.requests.index(deleted) < session.requests.index(final)


async def test_group_not_found_removes_placeholder(feed, session, bound_group):
    session.clear()
    await _ask_in_group(feed, "где логи?")
    assert session.all(EditMessageText) == []
    assert len(session.all(DeleteMessage)) == 1
    to_group = [m.text for m in session.all(SendMessage) if m.chat_id == GROUP]
    assert to_group == [texts.ANSWER_PLACEHOLDER, texts.NOT_FOUND_FORWARDED]


async def test_group_edit_failure_still_answers(feed, session, fake_llm, bound_group):
    session.failing_methods.add("EditMessageText")
    fake_llm.answer = AskResult(found=True, answer=ANSWER)
    await _ask_in_group(feed)
    assert session.last(SendRichMessage).rich_message.markdown == ANSWER


async def test_group_streaming_disabled(feed, session, fake_llm, settings, bound_group):
    settings.answer_streaming_enabled = False
    fake_llm.answer = AskResult(found=True, answer=ANSWER)
    session.clear()
    await _ask_in_group(feed)
    assert session.all(SendMessage) == [] and session.all(DeleteMessage) == []
    assert session.last(SendRichMessage).rich_message.markdown == ANSWER


def test_stream_timing_frames():
    assert StreamTiming(0.3, 1.5, 20).frame_count == 5
    assert StreamTiming(1.0, 0, 20).frame_count == 0
