"""Real E5 + Qdrant integration tests; run with RAG_INTEGRATION=1."""

import os

import pytest
from qdrant_client import models

from app.core.enums import DocsMode
from app.llm.gateway import DocFile, KnowledgeSnapshot
from app.rag.chunking import (
    DocumentAlreadyExistsError,
    GenerationAlreadyExistsError,
    RagError,
    UnsupportedDocumentError,
    chunk_document,
    count_input_tokens,
    load_document,
)
from app.rag.embeddings import E5Embedder
from app.rag.index import (
    AppendBaseRequiredError,
    AppendDocumentChangedError,
    AppendMissingDocumentError,
    IncompatibleIndexError,
    ManifestNotFoundError,
    PartialGenerationError,
    RagConfig,
    RagIndex,
    SourceScopeError,
)
from app.rag.store import COLLECTION_NAME, MANIFEST_COLLECTION, QdrantChunkStore

pytestmark = pytest.mark.skipif(
    os.getenv("RAG_INTEGRATION") != "1",
    reason="real E5/Qdrant integration: set RAG_INTEGRATION=1",
)


@pytest.fixture(scope="session")
def embedder() -> E5Embedder:
    return E5Embedder()


@pytest.fixture
def index(tmp_path, embedder) -> RagIndex:
    store = QdrantChunkStore(tmp_path / "qdrant")
    rag = RagIndex(store, embedder)
    try:
        yield rag
    finally:
        rag.close()


def doc(filename: str, body: str) -> DocFile:
    return DocFile(filename, body.encode())


def alpha_docs(code: str = "ALPHA-42") -> list[DocFile]:
    return [
        doc(
            "alpha.md",
            "# Альфа\n\n## Доступ\n\n"
            f"Код доступа Альфа: {code}. Используйте его в личном кабинете.\n\n"
            "## Поддержка\n\nОбращайтесь в поддержку Альфы по рабочим дням.",
        )
    ]


def gamma_doc() -> DocFile:
    return doc(
        "gamma.md",
        "# Гамма\n\n## Доступ\n\nКод доступа Гамма: GAMMA-5.",
    )


def beta_docs() -> list[DocFile]:
    return [
        doc(
            "beta.md",
            "# Бета\n\n## Доступ\n\nКод доступа Бета: BETA-77. Он не подходит для Альфы.",
        )
    ]


def snapshot(project_id: int, generation: str, version: int) -> KnowledgeSnapshot:
    return KnowledgeSnapshot(project_id, generation, version)


def test_real_query_and_project_generation_isolation(index: RagIndex):
    alpha = index.index_generation(
        alpha_docs(), project_id=1, generation_id="alpha-gen-1", knowledge_version=1
    )
    beta = index.index_generation(
        beta_docs(), project_id=2, generation_id="beta-gen-1", knowledge_version=1
    )
    alpha_v2 = index.index_generation(
        alpha_docs("ALPHA-99"), project_id=1, generation_id="alpha-gen-2", knowledge_version=2
    )

    assert alpha.chunk_count > 0 and beta.chunk_count > 0 and alpha_v2.chunk_count > 0
    assert alpha.to_dict()["status"] == "candidate"

    question = "Какой код доступа у Альфы?"
    alpha_sources = index.search(snapshot(1, "alpha-gen-1", 1), question)
    beta_sources = index.search(snapshot(2, "beta-gen-1", 1), question)
    alpha_v2_sources = index.search(snapshot(1, "alpha-gen-2", 2), question)

    assert any("ALPHA-42" in source.excerpt for source in alpha_sources)
    assert all("ALPHA-42" not in source.excerpt for source in beta_sources)
    assert any("ALPHA-99" in source.excerpt for source in alpha_v2_sources)
    assert all("ALPHA-42" not in source.excerpt for source in alpha_v2_sources)
    assert all(source.project_id == 1 for source in alpha_sources)
    assert all(source.generation_id == "alpha-gen-1" for source in alpha_sources)
    assert all(source.knowledge_version == 1 for source in alpha_sources)

    # Source lookup also filters project + generation.
    chunk_id = alpha_sources[0].chunk_id
    assert index.get_sources(snapshot(2, "beta-gen-1", 1), [chunk_id]) == []
    assert index.get_sources(snapshot(1, "alpha-gen-2", 2), [chunk_id]) == []
    assert index.get_sources(snapshot(1, "alpha-gen-1", 1), [chunk_id])[0].chunk_id == chunk_id


def test_manifest_hashes_counts_and_candidate_immutability(index: RagIndex):
    first = index.index_generation(
        alpha_docs(), project_id=1, generation_id="gen-1", knowledge_version=1
    )
    payload = first.to_dict()

    assert payload["manifest_version"] == 1
    assert payload["status"] == "candidate"
    assert payload["model_revision"] == "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    assert payload["dimension"] == 384
    assert payload["metric"] == "cosine"
    assert payload["normalization"] == "l2"
    assert payload["query_prefix"] == "query: "
    assert payload["passage_prefix"] == "passage: "
    assert payload["parser_version"] == "markdown-sections-v1"
    assert payload["chunker_version"] == "token-structural-v1"
    assert payload["chunk_count"] == first.chunk_count
    assert payload["corpus_hash"]
    assert payload["config_hash"]
    assert payload["manifest_hash"] == first.identity_hash

    same = index.index_generation(
        alpha_docs(), project_id=1, generation_id="gen-1", knowledge_version=1
    )
    assert same.identity_hash == first.identity_hash
    assert index.count(snapshot(1, "gen-1", 1)) == first.chunk_count

    old_count = index.count(snapshot(1, "gen-1", 1))
    with pytest.raises(GenerationAlreadyExistsError):
        index.index_generation(
            alpha_docs("ALPHA-00"),
            project_id=1,
            generation_id="gen-1",
            knowledge_version=1,
        )
    assert index.count(snapshot(1, "gen-1", 1)) == old_count

    # APPEND is a new candidate generation; base remains untouched.
    base = snapshot(1, "gen-1", 1)
    appended = index.index_generation(
        [*alpha_docs(), gamma_doc()],
        project_id=1,
        generation_id="gen-append",
        knowledge_version=2,
        mode=DocsMode.APPEND,
        base_snapshot=base,
    )
    assert appended.base_generation_id == "gen-1"
    assert appended.base_manifest_hash == first.identity_hash
    assert index.count(base) == old_count
    assert index.count(snapshot(1, "gen-append", 2)) == appended.chunk_count
    assert any(
        "GAMMA-5" in source.excerpt
        for source in index.search(snapshot(1, "gen-append", 2), "код доступа Гамма")
    )
    assert all(
        "GAMMA-5" not in source.excerpt for source in index.search(base, "код доступа Гамма")
    )

    with pytest.raises(AppendMissingDocumentError):
        index.index_generation(
            [gamma_doc()],
            project_id=1,
            generation_id="append-missing",
            knowledge_version=2,
            mode=DocsMode.APPEND,
            base_snapshot=base,
        )
    with pytest.raises(AppendDocumentChangedError):
        index.index_generation(
            [alpha_docs("ALPHA-00")[0], gamma_doc()],
            project_id=1,
            generation_id="append-changed",
            knowledge_version=2,
            mode=DocsMode.APPEND,
            base_snapshot=base,
        )
    with pytest.raises(AppendBaseRequiredError):
        index.index_generation(
            [*alpha_docs(), gamma_doc()],
            project_id=1,
            generation_id="append-no-base",
            knowledge_version=2,
            mode=DocsMode.APPEND,
        )
    assert index.count(snapshot(1, "append-missing", 2)) == 0
    assert index.count(snapshot(1, "append-changed", 2)) == 0


def test_config_change_changes_hash_and_repeat_is_refused(tmp_path, embedder):
    store = QdrantChunkStore(tmp_path / "qdrant")
    try:
        default = RagConfig()
        changed = RagConfig(target_tokens=300)
        assert default.config_hash != changed.config_hash

        index = RagIndex(store, embedder, default)
        index.index_generation(
            alpha_docs(), project_id=1, generation_id="gen-config", knowledge_version=1
        )
        with pytest.raises(GenerationAlreadyExistsError):
            RagIndex(store, embedder, changed).index_generation(
                alpha_docs(),
                project_id=1,
                generation_id="gen-config",
                knowledge_version=1,
            )
    finally:
        store.close()


def test_batch_error_and_duplicate_documents_write_nothing(index: RagIndex):
    with pytest.raises(UnsupportedDocumentError):
        index.index_generation(
            [
                doc("one.md", "# One\n\nтекст"),
                DocFile("bad.pdf", b"%PDF"),
            ],
            project_id=1,
            generation_id="bad-gen",
            knowledge_version=1,
        )
    assert index.count(snapshot(1, "bad-gen", 1)) == 0

    with pytest.raises(DocumentAlreadyExistsError):
        index.index_generation(
            [doc("same.md", "# One\n\nтекст"), doc("same.md", "# One\n\nтекст")],
            project_id=1,
            generation_id="dup-gen",
            knowledge_version=1,
        )
    assert index.count(snapshot(1, "dup-gen", 1)) == 0

    with pytest.raises(DocumentAlreadyExistsError):
        index.index_generation(
            [doc("same.md", "# One\n\nтекст"), doc("same.md", "# Two\n\nдругой")],
            project_id=1,
            generation_id="dup-gen-2",
            knowledge_version=1,
        )
    assert index.count(snapshot(1, "dup-gen-2", 1)) == 0


def test_candidate_and_context_top_k_are_separate(index: RagIndex):
    chunks = [
        doc(
            "long.md",
            "# Длинный\n\n"
            + "\n\n".join(
                f"## Раздел {i}\n\n" + " ".join([f"факт-{i}-{j}" for j in range(40)])
                for i in range(8)
            ),
        )
    ]
    manifest = index.index_generation(
        chunks, project_id=1, generation_id="topk", knowledge_version=1
    )
    assert manifest.chunk_count > 2

    with pytest.raises(RagError):
        index.search(
            snapshot(1, "topk", 1),
            "факт",
            candidate_k=1,
            context_top_k=2,
        )
    sources = index.search(snapshot(1, "topk", 1), "факт", candidate_k=3, context_top_k=1)
    assert len(sources) == 1


def test_chunking_keeps_exact_excerpts_offsets_and_token_budget(embedder):
    document = load_document(
        doc(
            "structured.md",
            "# Альфа\n\n## Доступ\n\n"
            + " ".join(f"правило-{i}" for i in range(250))
            + "\n\n## Поддержка\n\nКороткий раздел.",
        )
    )
    chunks = chunk_document(document, embedder.tokenizer)

    assert len(chunks) > 1
    assert {chunk.section_path for chunk in chunks} == {"Альфа → Доступ", "Альфа → Поддержка"}
    for chunk in chunks:
        assert document.text[chunk.start_char : chunk.end_char] == chunk.text
        assert count_input_tokens(embedder.tokenizer, chunk.text) <= 512


def _delete_manifest(rag: RagIndex, snapshot: KnowledgeSnapshot) -> None:
    rag.store.client.delete(
        collection_name=MANIFEST_COLLECTION,
        points_selector=models.PointIdsList(
            points=[rag.store._manifest_id(snapshot.project_id, snapshot.generation_id)]
        ),
    )


def test_reading_without_complete_manifest_fails_closed(index: RagIndex):
    index.index_generation(
        alpha_docs(), project_id=1, generation_id="manifest-missing", knowledge_version=1
    )
    snap = snapshot(1, "manifest-missing", 1)
    source = index.search(snap, "код доступа Альфа")[0]
    _delete_manifest(index, snap)

    with pytest.raises(ManifestNotFoundError):
        index.search(snap, "код доступа Альфа")
    with pytest.raises(ManifestNotFoundError):
        index.get_sources(snap, [source.chunk_id])


def test_wrong_version_and_partial_count_fail_closed(index: RagIndex):
    index.index_generation(
        alpha_docs(), project_id=1, generation_id="versioned", knowledge_version=1
    )
    snap = snapshot(1, "versioned", 1)
    source = index.search(snap, "код доступа Альфа")[0]

    with pytest.raises(IncompatibleIndexError, match="knowledge_version"):
        index.search(snapshot(1, "versioned", 2), "код доступа Альфа")

    point_id = index.store.point_id(1, "versioned", source.chunk_id)
    index.store.client.delete(
        collection_name=COLLECTION_NAME,
        points_selector=models.PointIdsList(points=[point_id]),
    )
    with pytest.raises(PartialGenerationError):
        index.search(snap, "код доступа Альфа")
    with pytest.raises(PartialGenerationError):
        index.get_sources(snap, [source.chunk_id])


def test_config_and_encoder_mismatch_fail_closed(index: RagIndex):
    with pytest.raises(IncompatibleIndexError):
        RagIndex(index.store, index.embedder, RagConfig(model_name="other/model"))
    with pytest.raises(IncompatibleIndexError):
        RagIndex(index.store, index.embedder, RagConfig(metric="dot"))
    with pytest.raises(IncompatibleIndexError):
        RagIndex(index.store, index.embedder, RagConfig(query_prefix="x: "))

    index.index_generation(
        alpha_docs(), project_id=1, generation_id="config-guard", knowledge_version=1
    )
    changed = RagIndex(index.store, index.embedder, RagConfig(target_tokens=300))
    with pytest.raises(IncompatibleIndexError, match="config_hash"):
        changed.search(snapshot(1, "config-guard", 1), "код доступа Альфа")


def test_source_payload_version_mismatch_fails_closed(index: RagIndex):
    index.index_generation(
        alpha_docs(), project_id=1, generation_id="payload-scope", knowledge_version=1
    )
    snap = snapshot(1, "payload-scope", 1)
    source = index.search(snap, "код доступа Альфа")[0]
    point_id = index.store.point_id(1, "payload-scope", source.chunk_id)
    index.store.client.set_payload(
        collection_name=COLLECTION_NAME,
        payload={"knowledge_version": 99},
        points=[point_id],
    )

    with pytest.raises(SourceScopeError, match="snapshot"):
        index.search(snap, "код доступа Альфа")
    with pytest.raises(SourceScopeError, match="snapshot"):
        index.get_sources(snap, [source.chunk_id])


def test_fenced_code_headings_do_not_break_sections(embedder):
    code = "\n".join(f"# comment {i}\nvalue_{i} = {i}" for i in range(100))
    document = load_document(
        doc(
            "fenced.md",
            "# Альфа\n\n## Доступ\n\n```python\n"
            + code
            + "\n```\n\n## После блока\n\nТекст после кода.\n",
        )
    )

    chunks = chunk_document(document, embedder.tokenizer)
    paths = {chunk.section_path for chunk in chunks}

    assert paths == {"Альфа → Доступ", "Альфа → После блока"}
    assert all("comment" not in (path or "") for path in paths)
    assert sum(1 for chunk in chunks if chunk.section_path == "Альфа → Доступ") > 1
    assert any("# comment 0" in chunk.text for chunk in chunks)
    for chunk in chunks:
        assert document.text[chunk.start_char : chunk.end_char] == chunk.text
        assert count_input_tokens(embedder.tokenizer, chunk.text) <= 512
