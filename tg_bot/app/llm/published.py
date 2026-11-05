"""Published snapshot boundary.

PG is the single publication authority (INN-12). The Qdrant manifest remains an
independent index-integrity check, not a second publish switch. Until INN-12
exists, production uses the unavailable provider and fails closed; tests may use
a fake publisher returning an explicit snapshot.
"""

from typing import Protocol

from app.llm.gateway import KnowledgeSnapshot, LLMUnavailableError


class PublishedSnapshotUnavailable(LLMUnavailableError):
    retryable = False


class PublishedSnapshotProvider(Protocol):
    async def get(self, project_id: int) -> KnowledgeSnapshot | None:
        """Return the atomically published snapshot or None."""
        ...


class UnavailablePublishedSnapshotProvider:
    """Real-mode placeholder until INN-12 PG publisher is implemented."""

    async def get(self, project_id: int) -> KnowledgeSnapshot | None:
        raise PublishedSnapshotUnavailable(
            "published snapshot provider is not implemented until INN-12"
        )
