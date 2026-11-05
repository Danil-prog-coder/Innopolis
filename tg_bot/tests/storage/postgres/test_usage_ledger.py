from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.llm.gateway import AttemptUsage, CostStatus, TokenUsage, UsageStatus
from app.storage.postgres.usage_work import UsageLedgerConflict


def attempt(
    *,
    attempt_id: str = "attempt-1",
    request_id: str = "req-1",
    input_tokens: int | None = 10,
    output_tokens: int | None = 5,
    status: UsageStatus = UsageStatus.REPORTED,
    cost: Decimal | None = None,
) -> AttemptUsage:
    extra: dict[str, object] = {}
    if cost is not None:
        extra = {
            "cost": cost,
            "cost_status": CostStatus.REPORTED,
            "currency": "USD",
            "rate_version": "tariff-1",
            "priced_at": datetime(2026, 11, 5, tzinfo=UTC),
        }
    return AttemptUsage(
        request_id=request_id,
        attempt_id=attempt_id,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            status=status,
        ),
        **extra,  # type: ignore[arg-type]
    )


async def test_ledger_persists_reported_usage_and_is_idempotent(usage_ledger):
    first = attempt(cost=Decimal("0.0042"))
    await usage_ledger.record(7, first, outcome="success")
    await usage_ledger.record(7, first, outcome="success")

    stored = await usage_ledger.get_by_attempt_id("attempt-1")
    assert stored is not None
    assert stored.project_id == 7
    assert stored.request_id == "req-1"
    assert stored.outcome == "success"
    assert stored.usage_status is UsageStatus.REPORTED
    assert (stored.input_tokens, stored.output_tokens) == (10, 5)
    assert stored.cost == Decimal("0.0042")
    assert stored.cost_status is CostStatus.REPORTED
    assert stored.currency == "USD"
    assert await usage_ledger.count_for_request("req-1") == 1


async def test_ledger_unknown_cancel_is_not_zero(usage_ledger):
    unknown = attempt(
        attempt_id="attempt-cancel",
        status=UsageStatus.UNKNOWN,
        input_tokens=None,
        output_tokens=None,
    )

    await usage_ledger.record(7, unknown, outcome="cancelled")

    stored = await usage_ledger.get_by_attempt_id("attempt-cancel")
    assert stored is not None
    assert stored.outcome == "cancelled"
    assert stored.usage_status is UsageStatus.UNKNOWN
    assert stored.input_tokens is None
    assert stored.output_tokens is None


async def test_ledger_rejects_cross_project_replay(usage_ledger):
    await usage_ledger.record(7, attempt(), outcome="error", error_code="unavailable")

    with pytest.raises(UsageLedgerConflict):
        await usage_ledger.record(
            8,
            attempt(),
            outcome="error",
            error_code="unavailable",
        )

    stored = await usage_ledger.get_by_attempt_id("attempt-1")
    assert stored is not None and stored.project_id == 7


async def test_ledger_rejects_different_counts_for_same_attempt(usage_ledger):
    await usage_ledger.record(7, attempt(), outcome="success")

    with pytest.raises(UsageLedgerConflict):
        await usage_ledger.record(
            7,
            attempt(input_tokens=99),
            outcome="success",
        )

    stored = await usage_ledger.get_by_attempt_id("attempt-1")
    assert stored is not None and stored.input_tokens == 10
