"""Published snapshot boundary.

PG is the single publication authority. The Qdrant manifest remains an
independent index-integrity check, not a second publish switch. Real mode uses
the PG-backed provider (``PostgresKnowledgeWork``); the unavailable provider
stays available for explicit fail-closed contexts and tests.
"""

from typing import Protocol

from app.llm.gateway import KnowledgeSnapshot, LLMUnavailableError


class PublishedSnapshotUnavailable(LLMUnavailableError):
    retryable = False


class PublishedSnapshotProvider(Protocol):
    async def get(self, project_id: int) -> KnowledgeSnapshot | None:
        """Return the atomically published snapshot or None."""
        ...


class ExactPublishedSnapshotProvider(Protocol):
    async def get_exact(self, project_id: int, snapshot: KnowledgeSnapshot) -> KnowledgeSnapshot:
        """Verify a pinned published snapshot still exists for retrieval."""
        ...


class UnavailablePublishedSnapshotProvider:
    """Real-mode placeholder until INN-12 PG publisher is implemented."""

    async def get(self, project_id: int) -> KnowledgeSnapshot | None:
        raise PublishedSnapshotUnavailable(
            "published snapshot provider is not implemented until INN-12"
        )
