import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.enums import QuestionStatus, RateLimitScope
from app.llm.gateway import (
    AskResult,
    AttemptUsage,
    CostStatus,
    KnowledgeSnapshot,
    LLMContractError,
    LLMUnavailableError,
    NoAnswerReason,
    Source,
    TokenUsage,
    UsageStatus,
)
from app.services.question_service import Asker
from app.storage.postgres.models import Question

ASKER = Asker(telegram_id=1002, chat_id=1002, message_id=10)


def snapshot_for(project) -> KnowledgeSnapshot:
    return KnowledgeSnapshot.compat(project.id, project.docs_version)


def source_for(snapshot: KnowledgeSnapshot, label: str = "[1]") -> Source:
    return Source(
        project_id=snapshot.project_id,
        generation_id=snapshot.generation_id,
        knowledge_version=snapshot.knowledge_version,
        document_id="doc-1",
        document_version="v1",
        chunk_id=f"chunk-{label}",
        title="Раздел",
        excerpt="Фрагмент документации",
        citation_label=label,
    )


async def test_ask_preserves_sources_and_cache_roundtrip(
    question_service, fake_llm, answer_cache_work, project
):
    snapshot = snapshot_for(project)
    source = source_for(snapshot)
    fake_llm.answer = AskResult(
        found=True,
        answer="Ответ",
        sources=(source,),
        snapshot=snapshot,
        request_id="req-1",
    )

    first = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert first.answer == "Ответ"
    assert first.sources == (source,)
    assert first.snapshot == snapshot
    assert first.no_answer_reason is None

    raw = await answer_cache_work.get(project.id, snapshot.knowledge_version, "Q")
    assert raw is not None
    cached = AskResult.from_json(raw)
    assert cached.sources == (source,)
    assert cached.snapshot == snapshot

    second = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)
    assert second.answer == "Ответ"
    assert second.sources == (source,)
    assert fake_llm.ask_calls == 1


async def test_found_without_sources_is_contract_error_not_no_answer(
    question_service, fake_llm, answer_cache_work, project
):
    async def broken_ask(project_id, question, *, snapshot=None):
        return AskResult(
            found=True,
            answer="A",
            snapshot=snapshot,
            legacy_no_sources=True,
        )

    fake_llm.ask = broken_ask  # type: ignore[method-assign]

    with pytest.raises(LLMContractError, match="sources"):
        await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert await Question.filter(text="Q").count() == 0
    assert (
        await answer_cache_work.get(project.id, snapshot_for(project).knowledge_version, "Q")
        is None
    )


async def test_missing_snapshot_is_contract_error(
    question_service, fake_llm, answer_cache_work, project
):
    async def broken_ask(project_id, question, *, snapshot=None):
        return AskResult(found=False, answer=None)

    fake_llm.ask = broken_ask  # type: ignore[method-assign]

    with pytest.raises(LLMContractError, match="snapshot"):
        await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert await Question.filter(text="Q").count() == 0
    assert (
        await answer_cache_work.get(project.id, snapshot_for(project).knowledge_version, "Q")
        is None
    )


async def test_old_snapshot_answer_is_rejected(question_service, fake_llm, project):
    stale = KnowledgeSnapshot(project.id, "old-generation", project.docs_version)

    async def stale_ask(project_id, question, *, snapshot=None):
        return AskResult(
            found=True,
            answer="A",
            sources=(source_for(stale),),
            snapshot=stale,
        )

    fake_llm.ask = stale_ask  # type: ignore[method-assign]

    with pytest.raises(LLMContractError, match="snapshot"):
        await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)


async def test_no_answer_is_not_cached_and_keeps_reason(
    question_service, fake_llm, answer_cache_work, project
):
    snapshot = snapshot_for(project)
    fake_llm.answer = AskResult(
        found=False,
        answer=None,
        snapshot=snapshot,
        no_answer_reason=NoAnswerReason.NOT_ANSWERABLE,
    )

    outcome = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert outcome.answer is None
    assert outcome.question is not None
    assert outcome.question.status == QuestionStatus.PENDING
    assert outcome.no_answer_reason is NoAnswerReason.NOT_ANSWERABLE
    assert await answer_cache_work.get(project.id, snapshot.knowledge_version, "Q") is None


async def test_legacy_plain_string_cache_is_miss(
    question_service, fake_llm, answer_cache_work, project
):
    snapshot = snapshot_for(project)
    await answer_cache_work.set(project.id, snapshot.knowledge_version, "Q", "legacy")
    fake_llm.answer = AskResult(
        found=True,
        answer="A",
        sources=(source_for(snapshot),),
        snapshot=snapshot,
    )

    outcome = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert outcome.answer == "A"
    assert fake_llm.ask_calls == 1
    parsed = AskResult.from_json(
        await answer_cache_work.get(project.id, snapshot.knowledge_version, "Q")
    )
    assert parsed.sources


async def test_cached_found_without_sources_is_miss(
    question_service, fake_llm, answer_cache_work, project
):
    snapshot = snapshot_for(project)
    legacy = AskResult(
        found=True,
        answer="legacy",
        snapshot=snapshot,
        legacy_no_sources=True,
    )
    await answer_cache_work.set(project.id, snapshot.knowledge_version, "Q", legacy.to_json())
    fake_llm.answer = AskResult(
        found=True,
        answer="fresh",
        sources=(source_for(snapshot),),
        snapshot=snapshot,
    )

    outcome = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert outcome.answer == "fresh"
    assert fake_llm.ask_calls == 1


async def test_cached_old_snapshot_is_miss(question_service, fake_llm, answer_cache_work, project):
    current = snapshot_for(project)
    stale = KnowledgeSnapshot(project.id, "old-generation", current.knowledge_version)
    old_result = AskResult(
        found=True,
        answer="old",
        sources=(source_for(stale),),
        snapshot=stale,
    )
    await answer_cache_work.set(project.id, current.knowledge_version, "Q", old_result.to_json())
    fake_llm.answer = AskResult(
        found=True,
        answer="fresh",
        sources=(source_for(current),),
        snapshot=current,
    )

    outcome = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert outcome.answer == "fresh"
    assert fake_llm.ask_calls == 1


async def test_malformed_cache_is_miss(question_service, fake_llm, answer_cache_work, project):
    snapshot = snapshot_for(project)
    await answer_cache_work.set(
        project.id, snapshot.knowledge_version, "Q", json.dumps({"broken": True})
    )
    fake_llm.answer = AskResult(
        found=True,
        answer="A",
        sources=(source_for(snapshot),),
        snapshot=snapshot,
    )

    outcome = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert outcome.answer == "A"
    assert fake_llm.ask_calls == 1


async def test_cache_distinguishes_code_indentation(question_service, fake_llm, project):
    snapshot = snapshot_for(project)
    fake_llm.answer = AskResult(
        found=True,
        answer="A",
        sources=(source_for(snapshot),),
        snapshot=snapshot,
    )
    four_spaces = "```python\nif x:\n    print('a')\n```"
    two_spaces = "```python\nif x:\n  print('a')\n```"

    await question_service.ask_text(project.id, four_spaces, ASKER, RateLimitScope.QUESTION)
    await question_service.ask_text(project.id, two_spaces, ASKER, RateLimitScope.QUESTION)

    assert fake_llm.ask_calls == 2


async def test_cache_distinguishes_case_and_numbers(question_service, fake_llm, project):
    snapshot = snapshot_for(project)
    fake_llm.answer = AskResult(
        found=True,
        answer="A",
        sources=(source_for(snapshot),),
        snapshot=snapshot,
    )

    for question in ("User", "user", "не 5", "не 6"):
        await question_service.ask_text(project.id, question, ASKER, RateLimitScope.QUESTION)

    assert fake_llm.ask_calls == 4


async def test_ask_image_preserves_sources(question_service, fake_llm, project):
    snapshot = snapshot_for(project)
    source = source_for(snapshot)
    fake_llm.answer = AskResult(
        found=True,
        answer="A",
        sources=(source,),
        snapshot=snapshot,
    )

    outcome = await question_service.ask_image(project.id, b"img", "f", None, ASKER)

    assert outcome.answer == "A"
    assert outcome.sources == (source,)
    assert outcome.snapshot == snapshot


async def test_llm_error_is_not_no_answer_and_not_cached(
    question_service, fake_llm, answer_cache_work, project
):
    snapshot = snapshot_for(project)
    fake_llm.fail = True

    with pytest.raises(LLMUnavailableError):
        await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert await Question.filter(text="Q").count() == 0
    assert await answer_cache_work.get(project.id, snapshot.knowledge_version, "Q") is None


async def test_cache_hit_does_not_repeat_attempt_usage(
    question_service, fake_llm, answer_cache_work, project
):
    snapshot = snapshot_for(project)
    source = source_for(snapshot)
    first_usage = AttemptUsage(
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
    fake_llm.answer = AskResult(
        found=True,
        answer="A",
        sources=(source,),
        snapshot=snapshot,
        request_id="req-1",
        usage=first_usage,
    )

    first = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)
    assert first.answer == "A"

    raw = await answer_cache_work.get(project.id, snapshot.knowledge_version, "Q")
    assert raw is not None
    cached = AskResult.from_json(raw)
    assert cached.usage is None
    assert cached.request_id is None
    assert cached.sources == (source,)

    second_usage = AttemptUsage(
        request_id="req-2",
        attempt_id="attempt-2",
        usage=TokenUsage(
            input_tokens=999,
            output_tokens=999,
            status=UsageStatus.REPORTED,
        ),
        cost=Decimal("99.99"),
        cost_status=CostStatus.REPORTED,
        currency="USD",
        rate_version="tariff-2026-11",
        priced_at=datetime(2026, 11, 5, tzinfo=UTC),
    )
    fake_llm.answer = AskResult(
        found=True,
        answer="B",
        sources=(source,),
        snapshot=snapshot,
        request_id="req-2",
        usage=second_usage,
    )

    second = await question_service.ask_text(project.id, "Q", ASKER, RateLimitScope.QUESTION)

    assert second.answer == "A"
    assert second.sources == (source,)
    assert fake_llm.ask_calls == 1

    cached_again = AskResult.from_json(
        await answer_cache_work.get(project.id, snapshot.knowledge_version, "Q")
    )
    assert cached_again.usage is None
    assert cached_again.request_id is None
