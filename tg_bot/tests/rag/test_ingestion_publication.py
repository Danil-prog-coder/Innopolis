from dataclasses import dataclass

import pytest

from app.core.enums import DocsMode
from app.llm.gateway import DocFile, LLMContractError, LLMUnavailableError
from app.rag.ingestion import RagIngestionService
from app.storage.postgres.models import DocUpdate, Project

HASH = "a" * 64


@dataclass
class FakeManifest:
    project_id: int
    generation_id: str
    knowledge_version: int
    mode: str
    base_generation_id: str | None
    documents: tuple[object, ...]
    chunk_count: int
    manifest_hash: str = HASH

    @property
    def identity_hash(self) -> str:
        return self.manifest_hash


class FakeIndex:
    def __init__(self, *, stored_hash: str = HASH, project_offset: int = 0) -> None:
        self.stored_hash = stored_hash
        self.project_offset = project_offset
        self.calls: list[dict[str, object]] = []
        self.last: FakeManifest | None = None

    def index_generation(
        self,
        files,
        *,
        project_id,
        generation_id,
        knowledge_version,
        mode=DocsMode.REPLACE,
        base_snapshot=None,
    ):
        self.calls.append(
            {
                "files": [file.filename for file in files],
                "project_id": project_id,
                "generation_id": generation_id,
                "knowledge_version": knowledge_version,
                "mode": mode,
                "base_snapshot": base_snapshot,
            }
        )
        self.last = FakeManifest(
            project_id=project_id + self.project_offset,
            generation_id=generation_id,
            knowledge_version=knowledge_version,
            mode=mode.value,
            base_generation_id=base_snapshot.generation_id if base_snapshot else None,
            documents=(object(),),
            chunk_count=3,
        )
        return self.last

    def verify_generation(self, snapshot):
        assert self.last is not None
        return {
            "project_id": self.last.project_id,
            "generation_id": self.last.generation_id,
            "knowledge_version": self.last.knowledge_version,
            "manifest_hash": self.stored_hash,
            "chunk_count": self.last.chunk_count,
            "mode": self.last.mode,
            "base_generation_id": self.last.base_generation_id,
        }


async def test_replace_publishes_verified_candidate_and_doc_update(
    project_work, knowledge_work, project, admin
):
    index = FakeIndex()
    service = RagIngestionService(index, knowledge_work, project_work)

    publication = await service.ingest_and_publish(
        project.id,
        DocsMode.REPLACE,
        [DocFile("a.md", b"# A\n\nALPHA-42")],
        created_by_id=admin.id,
    )

    stored = await Project.get(id=project.id)
    assert stored.docs_version == 1
    assert stored.active_generation_id == publication.generation.generation_id
    assert publication.generation.manifest_hash == HASH
    assert publication.doc_update.summary.startswith("Опубликована версия 1")
    assert await DocUpdate.filter(project_id=project.id).count() == 1
    assert index.calls[0]["knowledge_version"] == 1
    assert index.calls[0]["mode"] is DocsMode.REPLACE


async def test_append_fails_closed_before_index_or_publish(
    project_work, knowledge_work, project, admin
):
    index = FakeIndex()
    service = RagIngestionService(index, knowledge_work, project_work)

    with pytest.raises(LLMUnavailableError, match="APPEND"):
        await service.ingest_and_publish(
            project.id,
            DocsMode.APPEND,
            [DocFile("a.md", b"# A\n\nALPHA-42")],
            created_by_id=admin.id,
        )

    assert index.calls == []
    assert (await Project.get(id=project.id)).docs_version == 0
    assert await DocUpdate.filter(project_id=project.id).count() == 0


async def test_manifest_scope_mismatch_blocks_pg_publication(
    project_work, knowledge_work, project, admin
):
    index = FakeIndex(project_offset=1)
    service = RagIngestionService(index, knowledge_work, project_work)

    with pytest.raises(LLMContractError, match="scope"):
        await service.ingest_and_publish(
            project.id,
            DocsMode.REPLACE,
            [DocFile("a.md", b"# A\n\nALPHA-42")],
            created_by_id=admin.id,
        )

    assert (await Project.get(id=project.id)).docs_version == 0
    assert await DocUpdate.filter(project_id=project.id).count() == 0


async def test_manifest_hash_mismatch_blocks_pg_publication(
    project_work, knowledge_work, project, admin
):
    index = FakeIndex(stored_hash="b" * 64)
    service = RagIngestionService(index, knowledge_work, project_work)

    with pytest.raises(LLMContractError, match="hash"):
        await service.ingest_and_publish(
            project.id,
            DocsMode.REPLACE,
            [DocFile("a.md", b"# A\n\nALPHA-42")],
            created_by_id=admin.id,
        )

    assert (await Project.get(id=project.id)).docs_version == 0
    assert await DocUpdate.filter(project_id=project.id).count() == 0
