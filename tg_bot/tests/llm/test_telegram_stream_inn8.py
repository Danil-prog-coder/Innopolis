"""Telegram draft/final delivery tests against a fake Bot API."""

import asyncio

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendRichMessage, SendRichMessageDraft
from aiogram.types import Chat, InputRichMessage, Message

from app.llm.gateway import (
    AskError,
    AskErrorCode,
    AskEvent,
    AskEventType,
    AskResult,
    KnowledgeSnapshot,
    Source,
)
from app.llm.telegram_stream import (
    DRAFT_PREFIX,
    DraftPolicy,
    TelegramStreamDelivery,
    draft_id_for,
)

SNAPSHOT = KnowledgeSnapshot(1, "published-gen", 3)
SOURCE = Source(
    project_id=1,
    generation_id="published-gen",
    knowledge_version=3,
    document_id="doc-1",
    document_version="v1",
    chunk_id="chunk-1",
    title="Настройка доступа",
    excerpt="Код доступа: ALPHA-42",
    citation_label="[1]",
    section_path="Профиль → Доступ",
)
RESULT = AskResult(
    found=True,
    answer="Ответ [1]",
    sources=(SOURCE,),
    snapshot=SNAPSHOT,
    request_id="req-1",
)
CANCELLED = AskEvent(
    type=AskEventType.CANCELLED,
    request_id="req-1",
    seq=4,
    error=AskError(code=AskErrorCode.CANCELLED, retryable=False),
)
FINAL = AskEvent(
    type=AskEventType.FINAL,
    request_id="req-1",
    seq=4,
    result=RESULT,
)
NO_ANSWER_RESULT = AskResult(
    found=False,
    answer=None,
    snapshot=SNAPSHOT,
    request_id="req-1",
)
NO_ANSWER_FINAL = AskEvent(
    type=AskEventType.FINAL,
    request_id="req-1",
    seq=4,
    result=NO_ANSWER_RESULT,
)


def delta(text: str, seq: int) -> AskEvent:
    return AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=seq, text=text)


def status(seq: int = 1) -> AskEvent:
    return AskEvent(type=AskEventType.STATUS, request_id="req-1", seq=seq)


class FakeBot:
    def __init__(self) -> None:
        self.drafts: list[tuple[int, int, str]] = []
        self.draft_skip_detection: list[bool | None] = []
        self.finals: list[str] = []
        self.messages: list[str] = []
        self.final_errors: list[Exception] = []
        self.draft_errors: list[Exception] = []
        self.bad_rich_message: str | None = None
        self.block_send = False
        self.send_started = asyncio.Event()
        self.first_final_attempt = asyncio.Event()
        self.final_calls = 0

    async def send_rich_message_draft(
        self, chat_id, draft_id, rich_message, *, message_thread_id=None, **kwargs
    ):
        if self.draft_errors:
            raise self.draft_errors.pop(0)
        self.drafts.append((chat_id, draft_id, rich_message.markdown or ""))
        self.draft_skip_detection.append(rich_message.skip_entity_detection)
        return True

    async def send_rich_message(
        self, chat_id, rich_message, *, message_thread_id=None, reply_parameters=None
    ):
        self.final_calls += 1
        self.first_final_attempt.set()
        if self.bad_rich_message is not None:
            raise TelegramBadRequest(
                method=SendRichMessage(chat_id=chat_id, rich_message=rich_message),
                message=self.bad_rich_message,
            )
        if self.block_send:
            self.send_started.set()
            await asyncio.Event().wait()
        if self.final_errors:
            raise self.final_errors.pop(0)
        text = rich_message.markdown or ""
        self.finals.append(text)
        return Message(
            message_id=1,
            date=0,
            chat=Chat(id=chat_id, type="private"),
            text=text,
        )

    async def send_message(
        self, chat_id, text, *, parse_mode=None, message_thread_id=None, reply_parameters=None
    ):
        self.messages.append(text)
        return Message(
            message_id=2,
            date=0,
            chat=Chat(id=chat_id, type="private"),
            text=text,
        )


async def stream(*events: AskEvent):
    for event in events:
        yield event


def render(result: AskResult) -> InputRichMessage:
    return InputRichMessage(markdown=result.answer or "", skip_entity_detection=True)


def retry_after(seconds: int = 0, method_name: str = "draft") -> TelegramRetryAfter:
    method = (
        SendRichMessageDraft(chat_id=1, draft_id=1, rich_message=InputRichMessage(markdown="x"))
        if method_name == "draft"
        else SendRichMessage(chat_id=1, rich_message=InputRichMessage(markdown="x"))
    )
    return TelegramRetryAfter(method=method, message="slow down", retry_after=seconds)


async def test_private_stream_throttles_and_coalesces_drafts_then_sends_final():
    bot = FakeBot()
    delivery = TelegramStreamDelivery(bot, 100, policy=DraftPolicy(min_interval_seconds=10))
    events = stream(status(), delta("A", 2), delta("B [1]", 3), FINAL)

    outcome = await delivery.deliver(events, "req-1", render_final=render)

    assert outcome.final_sent is True
    assert outcome.terminal is not None and outcome.terminal.type is AskEventType.FINAL
    assert outcome.drafts_sent == 1
    assert len(bot.drafts) == 1
    assert bot.drafts[0][0] == 100
    assert bot.drafts[0][1] == draft_id_for(100, "req-1")
    assert bot.drafts[0][2].startswith(DRAFT_PREFIX)
    assert bot.drafts[0][2].endswith("A")
    assert bot.finals == ["Ответ [1]"]


async def test_zero_interval_sends_each_delta_as_same_draft():
    bot = FakeBot()
    delivery = TelegramStreamDelivery(bot, 100, policy=DraftPolicy(min_interval_seconds=0))
    events = stream(status(), delta("A", 2), delta("B [1]", 3), FINAL)

    outcome = await delivery.deliver(events, "req-1", render_final=render)

    assert outcome.drafts_sent == 2
    assert [draft[1] for draft in bot.drafts] == [draft_id_for(100, "req-1")] * 2
    assert [draft[2][len(DRAFT_PREFIX) :] for draft in bot.drafts] == ["A", "AB [1]"]


async def test_explicit_registry_draft_id_is_used():
    bot = FakeBot()
    delivery = TelegramStreamDelivery(
        bot,
        100,
        draft_id=777,
        policy=DraftPolicy(min_interval_seconds=0),
    )

    await delivery.deliver(stream(status(), delta("A [1]", 2), FINAL), "req-1", render_final=render)

    assert bot.drafts[0][1] == 777


async def test_group_receives_only_final():
    bot = FakeBot()
    delivery = TelegramStreamDelivery(bot, -100, policy=DraftPolicy(min_interval_seconds=0))
    events = stream(status(), delta("A", 2), delta("B [1]", 3), FINAL)

    outcome = await delivery.deliver(events, "req-1", render_final=render, group=True)

    assert outcome.final_sent is True
    assert outcome.drafts_sent == 0
    assert bot.drafts == []
    assert bot.finals == ["Ответ [1]"]


async def test_no_answer_final_is_not_sent_as_empty_message():
    bot = FakeBot()
    delivery = TelegramStreamDelivery(bot, 100)

    outcome = await delivery.deliver(stream(NO_ANSWER_FINAL), "req-1", render_final=render)

    assert outcome.final_sent is False
    assert outcome.terminal is NO_ANSWER_FINAL
    assert bot.final_calls == 0
    assert bot.finals == []


async def test_cancelled_never_sends_partial_as_final():
    bot = FakeBot()
    delivery = TelegramStreamDelivery(bot, 100)
    events = stream(status(), delta("частичный", 2), CANCELLED)

    outcome = await delivery.deliver(events, "req-1", render_final=render)

    assert outcome.final_sent is False
    assert outcome.terminal is not None and outcome.terminal.type is AskEventType.CANCELLED
    assert bot.finals == []


async def test_final_send_retry_does_not_reconsume_model_stream():
    bot = FakeBot()
    bot.final_errors = [retry_after(0, "final")]
    delivery = TelegramStreamDelivery(bot, 100, policy=DraftPolicy(max_send_attempts=2))
    consumed = 0

    async def counting_stream():
        nonlocal consumed
        for event in (status(), delta("A", 2), delta(" [1]", 3), FINAL):
            consumed += 1
            yield event

    outcome = await delivery.deliver(counting_stream(), "req-1", render_final=render)

    assert consumed == 4
    assert outcome.final_sent is True
    assert bot.finals == ["Ответ [1]"]


async def test_final_send_gives_up_after_budget():
    bot = FakeBot()
    bot.final_errors = [retry_after(0, "final"), retry_after(0, "final")]
    delivery = TelegramStreamDelivery(bot, 100, policy=DraftPolicy(max_send_attempts=1))

    with pytest.raises(TelegramRetryAfter):
        await delivery.deliver(stream(FINAL), "req-1", render_final=render)

    assert bot.finals == []


async def test_network_error_is_not_retried():
    bot = FakeBot()
    bot.final_errors = [
        TelegramNetworkError(
            method=SendRichMessage(chat_id=100, rich_message=InputRichMessage(markdown="x")),
            message="request timeout",
        )
    ]
    delivery = TelegramStreamDelivery(bot, 100, policy=DraftPolicy(max_send_attempts=3))

    with pytest.raises(TelegramNetworkError):
        await delivery.deliver(stream(FINAL), "req-1", render_final=render)

    assert bot.final_calls == 1
    assert bot.finals == []


async def test_draft_failure_does_not_block_final():
    bot = FakeBot()
    bot.draft_errors = [
        TelegramBadRequest(
            method=SendRichMessageDraft(
                chat_id=100,
                draft_id=1,
                rich_message=InputRichMessage(markdown="x"),
            ),
            message="draft not supported",
        )
    ]
    delivery = TelegramStreamDelivery(bot, 100, policy=DraftPolicy(min_interval_seconds=0))

    outcome = await delivery.deliver(
        stream(status(), delta("A [1]", 2), FINAL),
        "req-1",
        render_final=render,
    )

    assert outcome.final_sent is True
    assert outcome.drafts_sent == 0
    assert bot.finals == ["Ответ [1]"]


async def test_broken_rich_markdown_falls_back_to_plain_text():
    bot = FakeBot()
    bot.bad_rich_message = "can't parse entities: unexpected end of tag"
    delivery = TelegramStreamDelivery(bot, 100)

    outcome = await delivery.deliver(
        stream(FINAL),
        "req-1",
        render_final=render,
        render_fallback=lambda result: result.answer or "",
    )

    assert outcome.final_sent is True
    assert bot.finals == []
    assert bot.messages == ["Ответ [1]"]


async def test_permission_bad_request_does_not_use_fallback():
    bot = FakeBot()
    bot.bad_rich_message = "not enough rights to send messages"
    delivery = TelegramStreamDelivery(bot, 100)

    with pytest.raises(TelegramBadRequest):
        await delivery.deliver(
            stream(FINAL),
            "req-1",
            render_final=render,
            render_fallback=lambda result: result.answer or "",
        )

    assert bot.messages == []


async def test_broken_rich_markdown_without_fallback_is_typed_failure():
    bot = FakeBot()
    bot.bad_rich_message = "can't parse entities: unexpected end of tag"
    delivery = TelegramStreamDelivery(bot, 100)

    with pytest.raises(TelegramBadRequest):
        await delivery.deliver(stream(FINAL), "req-1", render_final=render)


async def test_draft_is_marked_preliminary_and_urls_are_defanged():
    bot = FakeBot()
    delivery = TelegramStreamDelivery(bot, 100, policy=DraftPolicy(min_interval_seconds=0))

    await delivery.deliver(
        stream(
            status(),
            delta(
                "Смотри https://evil.example/x и www.evil.example [клик](https://evil.example)", 2
            ),
            FINAL,
        ),
        "req-1",
        render_final=render,
    )

    draft = bot.drafts[0][2]
    assert draft.startswith(DRAFT_PREFIX)
    assert "https://" not in draft
    assert "www." not in draft
    assert "evil.example" not in draft
    assert bot.draft_skip_detection == [True]


async def test_stop_after_final_event_before_send_aborts():
    bot = FakeBot()
    delivery = TelegramStreamDelivery(bot, 100)
    stop = asyncio.Event()

    async def events():
        try:
            yield FINAL
        finally:
            stop.set()

    outcome = await delivery.deliver(events(), "req-1", render_final=render, stop_event=stop)

    assert outcome.terminal is None
    assert outcome.final_sent is False
    assert outcome.aborted_by_stop is True
    assert bot.final_calls == 0
    assert bot.finals == []


async def test_stop_during_retry_delay_aborts_without_final():
    bot = FakeBot()
    bot.final_errors = [retry_after(10, "final")]
    delivery = TelegramStreamDelivery(bot, 100, policy=DraftPolicy(max_send_attempts=2))
    stop = asyncio.Event()

    task = asyncio.create_task(
        delivery.deliver(stream(FINAL), "req-1", render_final=render, stop_event=stop)
    )
    await asyncio.wait_for(bot.first_final_attempt.wait(), timeout=1)
    stop.set()
    outcome = await asyncio.wait_for(task, timeout=1)

    assert outcome.terminal is None
    assert outcome.final_sent is False
    assert outcome.aborted_by_stop is True
    assert bot.final_calls == 1
    assert bot.finals == []


async def test_stop_during_inflight_final_cancels_send():
    bot = FakeBot()
    bot.block_send = True
    delivery = TelegramStreamDelivery(bot, 100)
    stop = asyncio.Event()

    task = asyncio.create_task(
        delivery.deliver(stream(FINAL), "req-1", render_final=render, stop_event=stop)
    )
    await asyncio.wait_for(bot.send_started.wait(), timeout=1)
    stop.set()
    outcome = await asyncio.wait_for(task, timeout=1)

    assert outcome.terminal is None
    assert outcome.final_sent is False
    assert outcome.aborted_by_stop is True
    assert bot.finals == []


async def test_stop_event_stops_delivery_before_final():
    bot = FakeBot()
    delivery = TelegramStreamDelivery(bot, 100)
    stop = asyncio.Event()
    stop.set()

    outcome = await delivery.deliver(
        stream(status(), delta("A", 2), FINAL),
        "req-1",
        render_final=render,
        stop_event=stop,
    )

    assert outcome.terminal is None
    assert outcome.final_sent is False
    assert outcome.aborted_by_stop is True
    assert bot.finals == []


def test_draft_id_is_stable_positive_and_request_local():
    first = draft_id_for(100, "req-1")
    assert first == draft_id_for(100, "req-1")
    assert 1 <= first <= 0x7FFFFFFF
    assert first != draft_id_for(100, "req-2")
    assert first != draft_id_for(101, "req-1")
