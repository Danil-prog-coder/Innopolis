import os

import pytest

from app.core.enums import DocsMode
from app.llm.gateway import KnowledgeSnapshot
from app.llm.published import PublishedSnapshotUnavailable
from app.storage.postgres.knowledge_work import (
    KnowledgePublishConflict,
    VerifiedGeneration,
)
from app.storage.postgres.models import DocUpdate, KnowledgeGeneration, Project

HASH_1 = "a" * 64
HASH_2 = "b" * 64


def candidate(
    project_id: int,
    generation_id: str,
    version: int,
    *,
    mode: DocsMode = DocsMode.REPLACE,
    base: str | None = None,
    manifest_hash: str = HASH_1,
) -> VerifiedGeneration:
    return VerifiedGeneration(
        project_id=project_id,
        generation_id=generation_id,
        knowledge_version=version,
        manifest_hash=manifest_hash,
        mode=mode,
        base_generation_id=base,
    )


async def test_publish_is_atomic_and_get_reads_pg_pointer(knowledge_work, project, admin):
    publication = await knowledge_work.publish(
        candidate(project.id, "gen-1", 1),
        expected_version=0,
        created_by_id=admin.id,
        summary="v1",
    )

    stored_project = await Project.get(id=project.id)
    assert stored_project.docs_version == 1
    assert stored_project.active_generation_id == "gen-1"
    assert publication.generation.project_id == project.id
    assert publication.generation.knowledge_version == 1
    assert publication.doc_update.project_id == project.id
    assert await DocUpdate.filter(project_id=project.id).count() == 1
    assert await knowledge_work.get(project.id) == KnowledgeSnapshot(project.id, "gen-1", 1)


async def test_replace_keeps_old_generation_and_moves_active_pointer(
    knowledge_work, project, admin
):
    await knowledge_work.publish(
        candidate(project.id, "gen-1", 1),
        expected_version=0,
        created_by_id=admin.id,
        summary="v1",
    )
    await knowledge_work.publish(
        candidate(project.id, "gen-2", 2, manifest_hash=HASH_2),
        expected_version=1,
        created_by_id=admin.id,
        summary="v2",
    )

    stored_project = await Project.get(id=project.id)
    assert stored_project.docs_version == 2
    assert stored_project.active_generation_id == "gen-2"
    generations = await KnowledgeGeneration.filter(project_id=project.id).order_by("id")
    assert [generation.generation_id for generation in generations] == ["gen-1", "gen-2"]
    assert await knowledge_work.get(project.id) == KnowledgeSnapshot(project.id, "gen-2", 2)
    assert await knowledge_work.get_exact(
        project.id, KnowledgeSnapshot(project.id, "gen-1", 1)
    ) == KnowledgeSnapshot(project.id, "gen-1", 1)
    assert await DocUpdate.filter(project_id=project.id).count() == 2

    with pytest.raises(PublishedSnapshotUnavailable):
        await knowledge_work.get_exact(project.id, KnowledgeSnapshot(project.id, "gen-removed", 1))


async def test_stale_version_conflict_does_not_bump_or_create_update(
    knowledge_work, project, admin
):
    await knowledge_work.publish(
        candidate(project.id, "gen-1", 1),
        expected_version=0,
        created_by_id=admin.id,
        summary="v1",
    )

    with pytest.raises(KnowledgePublishConflict):
        await knowledge_work.publish(
            candidate(project.id, "gen-stale", 1, manifest_hash=HASH_2),
            expected_version=0,
            created_by_id=admin.id,
            summary="stale",
        )

    stored_project = await Project.get(id=project.id)
    assert stored_project.docs_version == 1
    assert stored_project.active_generation_id == "gen-1"
    assert await DocUpdate.filter(project_id=project.id).count() == 1
    assert await KnowledgeGeneration.filter(project_id=project.id).count() == 1


def test_replace_rejects_base_and_append_requires_new_base(project):
    with pytest.raises(ValueError, match="REPLACE"):
        candidate(project.id, "gen-1", 1, base="gen-old")
    with pytest.raises(ValueError, match="APPEND"):
        candidate(project.id, "gen-1", 1, mode=DocsMode.APPEND)
    with pytest.raises(ValueError, match="new generation"):
        candidate(project.id, "gen-1", 1, mode=DocsMode.APPEND, base="gen-1")


async def test_append_requires_base_to_be_current_active(knowledge_work, project, admin):
    await knowledge_work.publish(
        candidate(project.id, "gen-1", 1),
        expected_version=0,
        created_by_id=admin.id,
        summary="v1",
    )

    with pytest.raises(KnowledgePublishConflict, match="base"):
        await knowledge_work.publish(
            candidate(project.id, "gen-2", 2, mode=DocsMode.APPEND, base="other"),
            expected_version=1,
            created_by_id=admin.id,
            summary="bad append",
        )

    publication = await knowledge_work.publish(
        candidate(project.id, "gen-2", 2, mode=DocsMode.APPEND, base="gen-1"),
        expected_version=1,
        created_by_id=admin.id,
        summary="append",
    )
    assert publication.generation.base_generation_id == "gen-1"
    assert (await Project.get(id=project.id)).active_generation_id == "gen-2"


async def test_get_fails_closed_when_pointer_has_no_generation(knowledge_work, project):
    await Project.filter(id=project.id).update(active_generation_id="missing", docs_version=1)

    with pytest.raises(PublishedSnapshotUnavailable):
        await knowledge_work.get(project.id)


async def test_get_without_active_pointer_is_none(knowledge_work, project):
    assert await knowledge_work.get(project.id) is None


@pytest.mark.skipif(
    not os.getenv("TEST_POSTGRES_URL"),
    reason="real PostgreSQL integration needs a disposable TEST_POSTGRES_URL",
)
async def test_concurrent_publish_has_one_winner(knowledge_work, project, admin):
    import asyncio

    results = await asyncio.gather(
        knowledge_work.publish(
            candidate(project.id, "gen-a", 1),
            expected_version=0,
            created_by_id=admin.id,
            summary="a",
        ),
        knowledge_work.publish(
            candidate(project.id, "gen-b", 1, manifest_hash=HASH_2),
            expected_version=0,
            created_by_id=admin.id,
            summary="b",
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, KnowledgePublishConflict) for result in results) == 1
    assert sum(not isinstance(result, BaseException) for result in results) == 1
    stored_project = await Project.get(id=project.id)
    assert stored_project.docs_version == 1
    assert stored_project.active_generation_id in {"gen-a", "gen-b"}
    assert await KnowledgeGeneration.filter(project_id=project.id).count() == 1
    assert await DocUpdate.filter(project_id=project.id).count() == 1
