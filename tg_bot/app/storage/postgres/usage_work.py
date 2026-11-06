"""Persistent attempt ledger for paid provider calls.

This is deliberately a direct insert, not a full INN-12 ledger/outbox. It stores
reported and unknown usage for every terminal attempt, including errors and
cancellations. Replaying the exact same payload is idempotent; conflicting data
for one ``attempt_id`` is an explicit error.
"""

from tortoise.exceptions import IntegrityError

from app.llm.gateway import AttemptUsage, CostStatus, UsageStatus
from app.storage.postgres.models import LlmAttempt


class UsageLedgerConflict(RuntimeError):
    """attempt_id was already recorded with a different project/request/usage."""


class PostgresUsageLedger:
    def __init__(self, client: object | None = None) -> None:
        # Kept for DI symmetry; Tortoise models use the default connection.
        self._client = client

    async def record(
        self,
        project_id: int,
        attempt: AttemptUsage,
        *,
        outcome: str,
        error_code: str | None = None,
    ) -> LlmAttempt:
        usage = attempt.usage
        defaults = {
            "project_id": project_id,
            "request_id": attempt.request_id,
            "outcome": outcome,
            "error_code": error_code,
            "usage_status": usage.status,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "reasoning_output_tokens": usage.reasoning_output_tokens,
            "cost": attempt.cost,
            "cost_status": attempt.cost_status,
            "currency": attempt.currency,
            "rate_version": attempt.rate_version,
            "priced_at": attempt.priced_at,
        }
        try:
            record, created = await LlmAttempt.get_or_create(
                attempt_id=attempt.attempt_id,
                defaults=defaults,
            )
        except IntegrityError:
            record = await LlmAttempt.get(attempt_id=attempt.attempt_id)
            created = False
        if not created:
            self._require_same_payload(record, defaults)
        return record

    @staticmethod
    def _require_same_payload(record: LlmAttempt, expected: dict[str, object]) -> None:
        actual = {
            "project_id": record.project_id,
            "request_id": record.request_id,
            "outcome": record.outcome,
            "error_code": record.error_code,
            "usage_status": UsageStatus(record.usage_status),
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cached_input_tokens": record.cached_input_tokens,
            "reasoning_output_tokens": record.reasoning_output_tokens,
            "cost": record.cost,
            "cost_status": (
                CostStatus(record.cost_status) if record.cost_status is not None else None
            ),
            "currency": record.currency,
            "rate_version": record.rate_version,
            "priced_at": record.priced_at,
        }
        if actual != expected:
            raise UsageLedgerConflict(
                f"attempt_id {record.attempt_id} already has a different payload"
            )

    async def get_by_attempt_id(self, attempt_id: str) -> LlmAttempt | None:
        return await LlmAttempt.get_or_none(attempt_id=attempt_id)

    async def count_for_request(self, request_id: str) -> int:
        return await LlmAttempt.filter(request_id=request_id).count()
