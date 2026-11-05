import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.llm.gateway import (
    AskError,
    AskErrorCode,
    AskEvent,
    AskEventType,
    AskResult,
    AskStreamContractError,
    AttemptUsage,
    CostStatus,
    KnowledgeSnapshot,
    LLMCancelledError,
    LLMUnavailableError,
    NoAnswerReason,
    Source,
    StubLLMGateway,
    TokenUsage,
    UsageStatus,
    consume_stream,
    iter_validated_events,
    validate_event_sequence,
)

SNAPSHOT = KnowledgeSnapshot(project_id=7, generation_id="gen-1", knowledge_version=3)


def make_source(**overrides):
    values = {
        "project_id": 7,
        "generation_id": "gen-1",
        "knowledge_version": 3,
        "document_id": "doc-1",
        "document_version": "v1",
        "chunk_id": "chunk-1",
        "title": "Заголовок",
        "excerpt": "Фрагмент",
        "citation_label": "[1]",
    }
    values.update(overrides)
    return Source(**values)


# --- Snapshot ---


def test_snapshot_validation_and_compat_marker():
    snapshot = KnowledgeSnapshot.compat(project_id=5, docs_version=2)

    assert snapshot.project_id == 5
    assert snapshot.knowledge_version == 2
    assert snapshot.generation_id == "compat-docs-2"

    for kwargs in (
        {"project_id": 0, "generation_id": "g", "knowledge_version": 0},
        {"project_id": 1, "generation_id": "", "knowledge_version": 0},
        {"project_id": 1, "generation_id": "g", "knowledge_version": -1},
    ):
        with pytest.raises(ValueError):
            KnowledgeSnapshot(**kwargs)


def test_snapshot_json_roundtrip():
    assert KnowledgeSnapshot.from_dict(SNAPSHOT.to_dict()) == SNAPSHOT


# --- Source / AskResult ---


def test_source_validation():
    assert make_source().citation_label == "[1]"

    for field, value in (
        ("document_id", ""),
        ("generation_id", ""),
        ("citation_label", " "),
        ("citation_label", "[evil2]"),
        ("citation_label", "1"),
        ("page", 0),
    ):
        with pytest.raises(ValueError):
            make_source(**{field: value})


def test_found_requires_sources_or_explicit_legacy_flag():
    with pytest.raises(ValueError, match="sources"):
        AskResult(found=True, answer="A")

    legacy = AskResult(found=True, answer="A", legacy_no_sources=True)
    assert legacy.answer == "A"
    assert legacy.sources == ()

    with pytest.raises(ValueError, match="legacy"):
        AskResult(found=True, answer="A", sources=(make_source(),), legacy_no_sources=True)


def test_found_with_sources_requires_matching_snapshot_scope():
    result = AskResult(
        found=True,
        answer="A",
        sources=(make_source(),),
        snapshot=SNAPSHOT,
        request_id="req-1",
    )

    assert result.sources[0].citation_label == "[1]"

    for overrides in (
        {"project_id": 8},
        {"generation_id": "other"},
        {"knowledge_version": 4},
    ):
        with pytest.raises(ValueError, match="snapshot"):
            AskResult(
                found=True,
                answer="A",
                sources=(make_source(**overrides),),
                snapshot=SNAPSHOT,
            )

    with pytest.raises(ValueError, match="snapshot"):
        AskResult(found=True, answer="A", sources=(make_source(),))


def test_sources_require_unique_citations():
    with pytest.raises(ValueError, match="citation_label"):
        AskResult(
            found=True,
            answer="A",
            sources=(make_source(), make_source(chunk_id="chunk-2")),
            snapshot=SNAPSHOT,
        )


def test_no_answer_is_typed_and_normalizes_answer():
    result = AskResult(found=False, answer="  ")

    assert result.answer is None
    assert result.sources == ()
    assert result.no_answer_reason is NoAnswerReason.NO_EVIDENCE

    typed = AskResult(
        found=False,
        answer=None,
        snapshot=SNAPSHOT,
        no_answer_reason=NoAnswerReason.NOT_ANSWERABLE,
    )
    assert typed.no_answer_reason is NoAnswerReason.NOT_ANSWERABLE

    with pytest.raises(ValueError):
        AskResult(found=False, answer="есть ответ")
    with pytest.raises(ValueError):
        AskResult(found=False, answer=None, sources=(make_source(),), snapshot=SNAPSHOT)


def test_found_cannot_carry_no_answer_reason():
    with pytest.raises(ValueError):
        AskResult(
            found=True,
            answer="A",
            legacy_no_sources=True,
            no_answer_reason=NoAnswerReason.NO_EVIDENCE,
        )


def test_result_json_roundtrip_with_sources_and_no_answer():
    found = AskResult(
        found=True,
        answer="Ответ",
        sources=(make_source(),),
        snapshot=SNAPSHOT,
        request_id="req-1",
    )
    assert AskResult.from_json(found.to_json()) == found

    no_answer = AskResult(
        found=False,
        answer=None,
        snapshot=SNAPSHOT,
        no_answer_reason=NoAnswerReason.NOT_ANSWERABLE,
    )
    assert AskResult.from_json(no_answer.to_json()) == no_answer


@pytest.mark.parametrize(
    "raw",
    [
        "not-json",
        '{"v":2,"found":false}',
        '{"v":1,"found":true,"answer":"A"}',
        '{"v":1,"found":false,"no_answer_reason":"unknown"}',
    ],
)
def test_invalid_cached_json_is_rejected(raw):
    with pytest.raises(ValueError):
        AskResult.from_json(raw)


# --- AskEvent ---


def test_delta_accepts_whitespace_and_formatting_chunks():
    chunks = [" ", "\n", "  \n", "**", "```", "\u200b"]

    for seq, chunk in enumerate(chunks, start=1):
        event = AskEvent(
            type=AskEventType.DELTA,
            request_id="req-1",
            seq=seq,
            text=chunk,
        )
        assert event.text == chunk

    with pytest.raises(ValueError):
        AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=1, text="")
    with pytest.raises(ValueError):
        AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=1, text=None)


def test_final_event_requires_result_and_matching_request():
    result = AskResult(
        found=True,
        answer="A",
        sources=(make_source(),),
        snapshot=SNAPSHOT,
        request_id="req-1",
    )
    event = AskEvent(
        type=AskEventType.FINAL,
        request_id="req-1",
        seq=1,
        result=result,
    )
    assert event.result == result

    with pytest.raises(ValueError, match="result"):
        AskEvent(type=AskEventType.FINAL, request_id="req-1", seq=1)
    with pytest.raises(ValueError, match="request_id"):
        AskEvent(
            type=AskEventType.FINAL,
            request_id="other",
            seq=1,
            result=result,
        )


def test_error_and_cancelled_are_typed_and_terminal():
    error = AskError(code=AskErrorCode.UNAVAILABLE, retryable=True)
    event = AskEvent(type=AskEventType.ERROR, request_id="req-1", seq=2, error=error)
    assert event.error is error

    cancelled = AskError(code=AskErrorCode.CANCELLED, retryable=False)
    event = AskEvent(type=AskEventType.CANCELLED, request_id="req-1", seq=2, error=cancelled)
    assert event.error is cancelled

    with pytest.raises(ValueError):
        AskError(code=AskErrorCode.CANCELLED, retryable=True)
    with pytest.raises(ValueError):
        AskEvent(
            type=AskEventType.ERROR,
            request_id="req-1",
            seq=1,
            error=cancelled,
        )
    with pytest.raises(ValueError):
        AskEvent(
            type=AskEventType.CANCELLED,
            request_id="req-1",
            seq=1,
            error=error,
        )


def test_status_and_delta_reject_payload_mix():
    with pytest.raises(ValueError):
        AskEvent(
            type=AskEventType.STATUS,
            request_id="req-1",
            seq=1,
            result=AskResult(found=False, answer=None),
        )
    with pytest.raises(ValueError):
        AskEvent(type=AskEventType.STATUS, request_id="req-1", seq=0)


async def test_stub_stream_is_final_only_and_typed():
    events = [
        event async for event in StubLLMGateway().ask_stream(1, "q", "req-1", snapshot=SNAPSHOT)
    ]

    assert len(events) == 1
    assert events[0].type is AskEventType.FINAL
    assert events[0].result is not None
    assert events[0].result.found is False
    assert events[0].result.no_answer_reason is NoAnswerReason.NO_EVIDENCE
    assert events[0].result.snapshot == SNAPSHOT


# --- Stream sequence ---


def _final_event(seq: int = 4) -> AskEvent:
    result = AskResult(
        found=True,
        answer="A",
        sources=(make_source(),),
        snapshot=SNAPSHOT,
        request_id="req-1",
    )
    return AskEvent(type=AskEventType.FINAL, request_id="req-1", seq=seq, result=result)


def test_valid_stream_sequence_returns_final():
    events = [
        AskEvent(type=AskEventType.STATUS, request_id="req-1", seq=1),
        AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=2, text=" "),
        AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=3, text="\n"),
        _final_event(4),
    ]

    result = validate_event_sequence(events, request_id="req-1")

    assert result.found is True


def test_stream_sequence_rejects_bad_seq_or_request():
    with pytest.raises(AskStreamContractError, match="seq"):
        validate_event_sequence(
            [AskEvent(type=AskEventType.STATUS, request_id="req-1", seq=2)], "req-1"
        )

    with pytest.raises(AskStreamContractError, match="request_id"):
        validate_event_sequence(
            [AskEvent(type=AskEventType.STATUS, request_id="other", seq=1)], "req-1"
        )


def test_stream_without_terminal_is_rejected():
    with pytest.raises(AskStreamContractError, match="terminal"):
        validate_event_sequence(
            [AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=1, text="A")],
            "req-1",
        )


def test_stream_rejects_repeated_terminal():
    with pytest.raises(AskStreamContractError, match="terminal"):
        validate_event_sequence(
            [
                _final_event(1),
                AskEvent(type=AskEventType.STATUS, request_id="req-1", seq=2),
            ],
            "req-1",
        )


def test_stream_rejects_no_answer_after_delta():
    no_answer = AskResult(
        found=False,
        answer=None,
        snapshot=SNAPSHOT,
        no_answer_reason=NoAnswerReason.NOT_ANSWERABLE,
    )
    with pytest.raises(AskStreamContractError, match="found=False"):
        validate_event_sequence(
            [
                AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=1, text="A"),
                AskEvent(
                    type=AskEventType.FINAL,
                    request_id="req-1",
                    seq=2,
                    result=no_answer,
                ),
            ],
            "req-1",
        )


def test_stream_delta_then_error_is_service_error_not_no_answer():
    error = AskError(code=AskErrorCode.UNAVAILABLE, retryable=True)
    events = [
        AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=1, text="A"),
        AskEvent(type=AskEventType.ERROR, request_id="req-1", seq=2, error=error),
    ]

    with pytest.raises(LLMUnavailableError) as caught:
        validate_event_sequence(events, "req-1")

    assert not isinstance(caught.value, LLMCancelledError)


def test_stream_cancelled_is_typed_cancel():
    cancelled = AskError(code=AskErrorCode.CANCELLED, retryable=False)
    events = [
        AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=1, text="A"),
        AskEvent(type=AskEventType.CANCELLED, request_id="req-1", seq=2, error=cancelled),
    ]

    with pytest.raises(LLMCancelledError):
        validate_event_sequence(events, "req-1")


class ClosingStream:
    def __init__(self, events):
        self._events = iter(events)
        self.closed = False
        self.close_calls = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self):
        self.close_calls += 1
        if self.close_calls > 1:
            raise RuntimeError("upstream closed twice")
        self.closed = True


async def test_consume_stream_closes_upstream_on_cancel():
    cancelled = AskError(code=AskErrorCode.CANCELLED, retryable=False)
    stream = ClosingStream(
        [
            AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=1, text="A"),
            AskEvent(type=AskEventType.CANCELLED, request_id="req-1", seq=2, error=cancelled),
        ]
    )

    with pytest.raises(LLMCancelledError):
        await consume_stream(stream, "req-1")

    assert stream.closed is True
    assert stream.close_calls == 1


async def test_consume_stream_closes_upstream_on_error_and_final():
    error = AskError(code=AskErrorCode.UNAVAILABLE, retryable=True)
    failing = ClosingStream(
        [AskEvent(type=AskEventType.ERROR, request_id="req-1", seq=1, error=error)]
    )
    with pytest.raises(LLMUnavailableError):
        await consume_stream(failing, "req-1")
    assert failing.closed is True
    assert failing.close_calls == 1

    finishing = ClosingStream([_final_event(1)])
    result = await consume_stream(finishing, "req-1")
    assert result.found is True
    assert finishing.closed is True
    assert finishing.close_calls == 1


class GatedStream(ClosingStream):
    """Отдаёт delta, ждёт внешний gate, затем final — проверка реального порядка."""

    def __init__(self, gate: asyncio.Event, final_event: AskEvent):
        super().__init__([])
        self._gate = gate
        self._final = final_event
        self._step = 0

    async def __anext__(self):
        if self._step == 0:
            self._step = 1
            return AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=1, text="first")
        if self._step == 1:
            await self._gate.wait()
            self._step = 2
            return self._final
        raise StopAsyncIteration


async def test_iter_validated_events_yields_delta_before_final():
    gate = asyncio.Event()
    stream = GatedStream(gate, _final_event(2))
    iterator = iter_validated_events(stream, "req-1")

    first = await anext(iterator)
    assert first.type is AskEventType.DELTA
    assert first.text == "first"
    assert stream.closed is False

    gate.set()
    second = await anext(iterator)
    assert second.type is AskEventType.FINAL
    assert stream.closed is True
    assert stream.close_calls == 1

    with pytest.raises(StopAsyncIteration):
        await anext(iterator)


async def test_iter_validated_events_closes_upstream_on_aclose():
    stream = GatedStream(asyncio.Event(), _final_event(2))
    iterator = iter_validated_events(stream, "req-1")

    await anext(iterator)
    await iterator.aclose()

    assert stream.closed is True
    assert stream.close_calls == 1


async def test_iter_validated_events_surfaces_cancelled_and_closes():
    cancelled = AskError(code=AskErrorCode.CANCELLED, retryable=False)
    stream = ClosingStream(
        [
            AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=1, text="A"),
            AskEvent(type=AskEventType.CANCELLED, request_id="req-1", seq=2, error=cancelled),
        ]
    )

    events = [event async for event in iter_validated_events(stream, "req-1")]

    assert [event.type for event in events] == [
        AskEventType.DELTA,
        AskEventType.CANCELLED,
    ]
    assert stream.closed is True
    assert stream.close_calls == 1


async def test_iter_validated_events_without_terminal_is_contract_error():
    stream = ClosingStream([AskEvent(type=AskEventType.DELTA, request_id="req-1", seq=1, text="A")])

    with pytest.raises(AskStreamContractError, match="terminal"):
        async for _ in iter_validated_events(stream, "req-1"):
            pass

    assert stream.closed is True
    assert stream.close_calls == 1


# --- Usage / attempt ---


def test_token_usage_reported_subsets_and_no_double_counting():
    usage = TokenUsage(
        input_tokens=1000,
        output_tokens=500,
        cached_input_tokens=400,
        reasoning_output_tokens=100,
        status=UsageStatus.REPORTED,
    )

    assert usage.total_tokens == 1500
    assert usage.uncached_input_tokens == 600
    assert usage.non_reasoning_output_tokens == 400
    assert usage.input_tokens + usage.output_tokens == usage.total_tokens


def test_token_usage_unknown_is_not_zero():
    usage = TokenUsage(status=UsageStatus.UNKNOWN)

    assert usage.input_tokens is None
    assert usage.output_tokens is None
    assert usage.total_tokens is None
    assert usage.uncached_input_tokens is None

    for kwargs in (
        {"input_tokens": -1, "status": UsageStatus.REPORTED},
        {"input_tokens": 10, "cached_input_tokens": 11, "status": UsageStatus.REPORTED},
        {"output_tokens": 10, "reasoning_output_tokens": 11, "status": UsageStatus.REPORTED},
        {"input_tokens": 1, "status": UsageStatus.UNKNOWN},
        {"status": UsageStatus.REPORTED},
        {"status": UsageStatus.ESTIMATED},
    ):
        with pytest.raises(ValueError):
            TokenUsage(**kwargs)


def test_attempt_usage_has_separate_ids_and_nullable_cost():
    usage = TokenUsage(
        input_tokens=10,
        output_tokens=5,
        cached_input_tokens=2,
        reasoning_output_tokens=1,
        status=UsageStatus.ESTIMATED,
    )
    attempt = AttemptUsage(
        request_id="req-1",
        attempt_id="attempt-1",
        usage=usage,
        cost=Decimal("0.0042"),
        cost_status=CostStatus.REPORTED,
        currency="USD",
        rate_version="tariff-2026-11",
        priced_at=datetime(2026, 11, 5, tzinfo=UTC),
    )

    assert attempt.request_id != attempt.attempt_id
    assert attempt.cost == Decimal("0.0042")
    assert attempt.cost_status is CostStatus.REPORTED

    with pytest.raises(ValueError):
        AttemptUsage(
            request_id="req-1",
            attempt_id="attempt-1",
            usage=usage,
            cost=0.1,  # float запрещён
        )
    with pytest.raises(ValueError, match="cost_status"):
        AttemptUsage(
            request_id="req-1",
            attempt_id="attempt-1",
            usage=usage,
            cost=Decimal("0.1"),
            currency="USD",
            rate_version="v1",
            priced_at=datetime(2026, 11, 5, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="currency"):
        AttemptUsage(
            request_id="req-1",
            attempt_id="attempt-1",
            usage=usage,
            cost=Decimal("0.1"),
            cost_status=CostStatus.ESTIMATED,
        )

    # Подтверждённая сумма может быть известна при unknown tokens
    # (например, оборванный поток и счёт провайдера).
    reconciled = AttemptUsage(
        request_id="req-1",
        attempt_id="attempt-2",
        usage=TokenUsage(status=UsageStatus.UNKNOWN),
        cost=Decimal("0.1"),
        cost_status=CostStatus.RECONCILED,
        currency="USD",
        rate_version="v1",
        priced_at=datetime(2026, 11, 5, tzinfo=UTC),
    )
    assert reconciled.usage.total_tokens is None
    assert reconciled.cost == Decimal("0.1")


def test_result_usage_roundtrip_and_request_id_link():
    usage = AttemptUsage(
        request_id="req-1",
        attempt_id="attempt-1",
        usage=TokenUsage(
            input_tokens=10,
            output_tokens=5,
            status=UsageStatus.REPORTED,
        ),
        cost=Decimal("0.01"),
        cost_status=CostStatus.REPORTED,
        currency="USD",
        rate_version="tariff-2026-11",
        priced_at=datetime(2026, 11, 5, tzinfo=UTC),
    )
    result = AskResult(
        found=True,
        answer="A",
        sources=(make_source(),),
        snapshot=SNAPSHOT,
        request_id="req-1",
        usage=usage,
    )

    parsed = AskResult.from_json(result.to_json())
    assert parsed.usage == usage
    assert parsed.usage is not None
    assert parsed.usage.request_id == parsed.request_id
    assert parsed.usage.attempt_id == "attempt-1"

    with pytest.raises(ValueError, match="request_id"):
        AskResult(
            found=True,
            answer="A",
            sources=(make_source(),),
            snapshot=SNAPSHOT,
            request_id="other",
            usage=usage,
        )
