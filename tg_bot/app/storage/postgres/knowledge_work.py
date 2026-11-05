"""PostgreSQL authority for published knowledge generations.

Qdrant remains the candidate/index store and its manifest is verified separately.
This module owns the atomic project -> generation -> version pointer used by
retrieval. Old generation rows are retained; publication never deletes history.
"""

import re
from dataclasses import dataclass

from tortoise.transactions import in_transaction

from app.core.clock import utc_now
from app.core.enums import DocsMode
from app.llm.gateway import KnowledgeSnapshot
from app.llm.published import PublishedSnapshotUnavailable
from app.storage.postgres.models import DocUpdate, KnowledgeGeneration, Project

_MANIFEST_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class KnowledgePublishConflict(RuntimeError):
    """Another publisher won the project/version CAS or the generation exists."""


class KnowledgeProjectNotFound(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VerifiedGeneration:
    """Fields already checked against the candidate manifest by the caller."""

    project_id: int
    generation_id: str
    knowledge_version: int
    manifest_hash: str
    mode: DocsMode
    base_generation_id: str | None = None

    def __post_init__(self) -> None:
        if self.project_id < 1:
            raise ValueError("project_id must be positive")
        if not self.generation_id:
            raise ValueError("generation_id is required")
        if self.knowledge_version < 1:
            raise ValueError("knowledge_version must be positive")
        if not _MANIFEST_HASH_RE.fullmatch(self.manifest_hash):
            raise ValueError("manifest_hash must be a sha256 hex digest")
        object.__setattr__(self, "mode", DocsMode(self.mode))
        if self.mode is DocsMode.APPEND:
            if not self.base_generation_id:
                raise ValueError("APPEND requires base_generation_id")
            if self.base_generation_id == self.generation_id:
                raise ValueError("APPEND requires a new generation_id")
        elif self.base_generation_id is not None:
            raise ValueError("REPLACE must not have base_generation_id")


@dataclass(frozen=True, slots=True)
class KnowledgePublication:
    generation: KnowledgeGeneration
    doc_update: DocUpdate


class PostgresKnowledgeWork:
    def __init__(self, client: object | None = None) -> None:
        # Client kept for DI symmetry; Tortoise models use the default connection.
        self._client = client

    async def get(self, project_id: int) -> KnowledgeSnapshot | None:
        """Read the active pointer and its generation under the same lock.

        Publication also locks the project row, so a reader cannot observe the
        pointer from one generation and the row from another.
        """
        async with in_transaction() as connection:
            project = (
                await Project.select_for_update().using_db(connection).get_or_none(id=project_id)
            )
            if project is None or not project.active_generation_id:
                return None
            generation = (
                await KnowledgeGeneration.filter(
                    project_id=project_id,
                    generation_id=project.active_generation_id,
                    knowledge_version=project.docs_version,
                )
                .using_db(connection)
                .first()
            )
            if generation is None:
                raise PublishedSnapshotUnavailable(
                    "active generation pointer has no matching published row"
                )
            return KnowledgeSnapshot(
                project_id=project_id,
                generation_id=generation.generation_id,
                knowledge_version=generation.knowledge_version,
            )

    async def get_exact(self, project_id: int, snapshot: KnowledgeSnapshot) -> KnowledgeSnapshot:
        """Verify a pinned generation row, even if it is no longer active.

        Retention is the reason a request can safely finish on the snapshot it
        pinned before a concurrent publication.
        """
        if snapshot.project_id != project_id:
            raise PublishedSnapshotUnavailable("pinned snapshot project mismatch")
        generation = await KnowledgeGeneration.get_or_none(
            project_id=project_id,
            generation_id=snapshot.generation_id,
            knowledge_version=snapshot.knowledge_version,
        )
        if generation is None:
            raise PublishedSnapshotUnavailable(
                "pinned snapshot is not a retained published generation"
            )
        return KnowledgeSnapshot(
            project_id=project_id,
            generation_id=generation.generation_id,
            knowledge_version=generation.knowledge_version,
        )

    async def publish(
        self,
        candidate: VerifiedGeneration,
        *,
        expected_version: int,
        created_by_id: int,
        summary: str,
    ) -> KnowledgePublication:
        """Atomically publish one verified generation and its DocUpdate.

        The project row is locked, then the version CAS keeps concurrent
        publishers from both winning. APPEND additionally requires its base to
        be the current active generation inside the same lock. The old active row
        stays in history; only ``projects.active_generation_id`` moves.
        """
        if candidate.knowledge_version != expected_version + 1:
            raise KnowledgePublishConflict("candidate version is not expected_version + 1")
        async with in_transaction() as connection:
            project = (
                await Project.select_for_update()
                .using_db(connection)
                .get_or_none(id=candidate.project_id)
            )
            if project is None:
                raise KnowledgeProjectNotFound(f"project {candidate.project_id} not found")
            if project.docs_version != expected_version:
                raise KnowledgePublishConflict("project docs_version changed")

            if candidate.mode is DocsMode.APPEND:
                if project.active_generation_id != candidate.base_generation_id:
                    raise KnowledgePublishConflict("APPEND base is not the active generation")
                base = (
                    await KnowledgeGeneration.filter(
                        project_id=candidate.project_id,
                        generation_id=candidate.base_generation_id,
                        knowledge_version=expected_version,
                    )
                    .using_db(connection)
                    .first()
                )
                if base is None:
                    raise KnowledgePublishConflict("APPEND base generation row is missing")
            elif candidate.base_generation_id is not None:
                raise KnowledgePublishConflict("REPLACE must not have a base generation")

            existing = (
                await KnowledgeGeneration.filter(
                    project_id=candidate.project_id,
                    generation_id=candidate.generation_id,
                )
                .using_db(connection)
                .first()
            )
            if existing is not None:
                raise KnowledgePublishConflict("generation_id already published")

            generation = await KnowledgeGeneration.create(
                project_id=candidate.project_id,
                generation_id=candidate.generation_id,
                knowledge_version=candidate.knowledge_version,
                manifest_hash=candidate.manifest_hash,
                mode=candidate.mode,
                base_generation_id=candidate.base_generation_id,
                created_by_id=created_by_id,
                published_at=utc_now(),
                using_db=connection,
            )
            updated = (
                await Project.filter(id=candidate.project_id, docs_version=expected_version)
                .using_db(connection)
                .update(
                    docs_version=candidate.knowledge_version,
                    active_generation_id=candidate.generation_id,
                )
            )
            if updated != 1:
                raise KnowledgePublishConflict("project version CAS failed")
            doc_update = await DocUpdate.create(
                project_id=candidate.project_id,
                created_by_id=created_by_id,
                mode=candidate.mode,
                summary=summary,
                using_db=connection,
            )
            return KnowledgePublication(generation=generation, doc_update=doc_update)
