"""Real ingestion -> verified candidate -> PG publication.

APPEND is rejected: the bot does not retain the complete previous corpus, so a
true merge cannot be proven. Only REPLACE is safe until corpus storage exists.
"""

import asyncio
import logging
import uuid

from app.core.enums import DocsMode
from app.core.errors import ProjectNotFound
from app.llm.gateway import DocFile, KnowledgeSnapshot, LLMContractError, LLMUnavailableError
from app.rag.chunking import RagError
from app.rag.index import IndexManifest, RagIndex
from app.storage.postgres.knowledge_work import (
    KnowledgeProjectNotFound,
    KnowledgePublication,
    KnowledgePublishConflict,
    PostgresKnowledgeWork,
    VerifiedGeneration,
)
from app.storage.postgres.project_work import PostgresProjectWork

logger = logging.getLogger(__name__)


class RagIngestionService:
    def __init__(
        self,
        index: RagIndex,
        knowledge_work: PostgresKnowledgeWork,
        project_work: PostgresProjectWork,
    ) -> None:
        self._index = index
        self._knowledge_work = knowledge_work
        self._project_work = project_work

    async def ingest_and_publish(
        self,
        project_id: int,
        mode: DocsMode,
        files: list[DocFile],
        *,
        created_by_id: int,
    ) -> KnowledgePublication:
        if mode is DocsMode.APPEND:
            raise LLMUnavailableError(
                "APPEND requires the complete previous corpus; not supported until corpus storage"
            )
        project = await self._project_work.get_by_id(project_id)
        if project is None:
            raise ProjectNotFound
        expected_version = project.docs_version
        new_version = expected_version + 1
        generation_id = f"gen-{project_id}-{new_version}-{uuid.uuid4().hex[:12]}"

        try:
            manifest = await asyncio.to_thread(
                self._index.index_generation,
                files,
                project_id=project_id,
                generation_id=generation_id,
                knowledge_version=new_version,
                mode=mode,
            )
            snapshot = KnowledgeSnapshot(project_id, generation_id, new_version)
            stored = await asyncio.to_thread(self._index.verify_generation, snapshot)
        except RagError as error:
            raise LLMUnavailableError("document indexing failed") from error
        except Exception as error:
            logger.exception("RAG indexing failed for project %s", project_id)
            raise LLMUnavailableError("document indexing failed") from error

        self._verify_manifest(manifest, stored, snapshot)
        summary = (
            f"Опубликована версия {new_version}: "
            f"документов {len(manifest.documents)}, чанков {manifest.chunk_count}."
        )
        candidate = VerifiedGeneration(
            project_id=project_id,
            generation_id=generation_id,
            knowledge_version=new_version,
            manifest_hash=manifest.identity_hash,
            mode=mode,
            base_generation_id=manifest.base_generation_id,
        )
        try:
            return await self._knowledge_work.publish(
                candidate,
                expected_version=expected_version,
                created_by_id=created_by_id,
                summary=summary,
            )
        except KnowledgeProjectNotFound as error:
            raise ProjectNotFound from error
        except KnowledgePublishConflict as error:
            # The Qdrant candidate stays inert; the upload can be retried with a
            # new generation id without touching the previous active version.
            raise LLMUnavailableError("knowledge publication conflict; retry upload") from error

    @staticmethod
    def _verify_manifest(
        manifest: IndexManifest,
        stored: dict[str, object],
        snapshot: KnowledgeSnapshot,
    ) -> None:
        if (
            manifest.project_id != snapshot.project_id
            or manifest.generation_id != snapshot.generation_id
            or manifest.knowledge_version != snapshot.knowledge_version
        ):
            raise LLMContractError("candidate manifest scope mismatch")
        if stored.get("project_id") != snapshot.project_id:
            raise LLMContractError("stored manifest project mismatch")
        if stored.get("generation_id") != snapshot.generation_id:
            raise LLMContractError("stored manifest generation mismatch")
        if stored.get("knowledge_version") != snapshot.knowledge_version:
            raise LLMContractError("stored manifest version mismatch")
        if stored.get("manifest_hash") != manifest.identity_hash:
            raise LLMContractError("stored manifest hash mismatch")
        if stored.get("chunk_count") != manifest.chunk_count:
            raise LLMContractError("stored manifest chunk count mismatch")
        if stored.get("mode") != manifest.mode:
            raise LLMContractError("stored manifest mode mismatch")
        if stored.get("base_generation_id") != manifest.base_generation_id:
            raise LLMContractError("stored manifest base mismatch")
        if manifest.mode == DocsMode.REPLACE and manifest.base_generation_id is not None:
            raise LLMContractError("REPLACE manifest has a base generation")
        if manifest.mode == DocsMode.APPEND and not manifest.base_generation_id:
            raise LLMContractError("APPEND manifest has no base generation")
