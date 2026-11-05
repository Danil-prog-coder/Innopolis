"""Candidate-only ingestion and filtered retrieval.

PERS-43 deliberately does not publish generations: no PostgreSQL CAS, no
docs_version bump, no DocUpdate/outbox. Candidate points are inert until
PERS-48 publishes their generation_id. Candidate identity is additionally
guarded by a manifest point; sequential repeat with changed corpus/config is
rejected instead of mutating the previous candidate.
"""

import hashlib
import json
from dataclasses import dataclass

from app.core.enums import DocsMode
from app.llm.gateway import DocFile, KnowledgeSnapshot, Source
from app.rag.chunking import (
    CHUNKER_VERSION,
    MAX_INPUT_TOKENS,
    OVERLAP_TOKENS,
    PARSER_VERSION,
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    TARGET_TOKENS,
    Chunk,
    DocumentAlreadyExistsError,
    EmptyCorpusError,
    GenerationAlreadyExistsError,
    LoadedDocument,
    RagError,
    chunk_document,
    load_document,
)
from app.rag.embeddings import EMBEDDING_DIMENSION, MODEL_NAME, MODEL_REVISION, E5Embedder
from app.rag.store import COLLECTION_NAME, ChunkRecord, QdrantChunkStore, ScoredChunk


class IndexingError(RagError):
    pass


class ManifestNotFoundError(RagError):
    """Чтение без готового candidate manifest запрещено."""


class IncompatibleIndexError(RagError):
    """Manifest/encoder/config не совпадают с текущим retrieval."""


class PartialGenerationError(RagError):
    """Manifest есть, но точек меньше/больше записанного counts."""


class SourceScopeError(RagError):
    """Payload источника не совпадает со snapshot запроса."""


class AppendBaseRequiredError(RagError):
    """APPEND требует явно переданный завершённый base snapshot."""


class AppendMissingDocumentError(RagError):
    """В полном corpus APPEND отсутствует документ из base."""


class AppendDocumentChangedError(RagError):
    """Документ из base изменён; неоднозначное обновление отклоняется."""


@dataclass(frozen=True, slots=True)
class RagConfig:
    target_tokens: int = TARGET_TOKENS
    overlap_tokens: int = OVERLAP_TOKENS
    max_input_tokens: int = MAX_INPUT_TOKENS
    candidate_k: int = 20
    context_top_k: int = 5
    collection_name: str = COLLECTION_NAME
    model_name: str = MODEL_NAME
    model_revision: str = MODEL_REVISION
    metric: str = "cosine"
    normalization: str = "l2"
    query_prefix: str = QUERY_PREFIX
    passage_prefix: str = PASSAGE_PREFIX
    parser_version: str = PARSER_VERSION
    chunker_version: str = CHUNKER_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "target_tokens": self.target_tokens,
            "overlap_tokens": self.overlap_tokens,
            "max_input_tokens": self.max_input_tokens,
            "candidate_k": self.candidate_k,
            "context_top_k": self.context_top_k,
            "collection_name": self.collection_name,
            "model_name": self.model_name,
            "model_revision": self.model_revision,
            "metric": self.metric,
            "normalization": self.normalization,
            "query_prefix": self.query_prefix,
            "passage_prefix": self.passage_prefix,
            "parser_version": self.parser_version,
            "chunker_version": self.chunker_version,
        }

    def identity_dict(self) -> dict[str, object]:
        """Поля, меняющие индекс; retrieval top-k сюда не входят."""
        return {
            "target_tokens": self.target_tokens,
            "overlap_tokens": self.overlap_tokens,
            "max_input_tokens": self.max_input_tokens,
            "collection_name": self.collection_name,
            "model_name": self.model_name,
            "model_revision": self.model_revision,
            "metric": self.metric,
            "normalization": self.normalization,
            "query_prefix": self.query_prefix,
            "passage_prefix": self.passage_prefix,
            "parser_version": self.parser_version,
            "chunker_version": self.chunker_version,
        }

    @property
    def config_hash(self) -> str:
        raw = json.dumps(self.identity_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class DocumentManifest:
    filename: str
    document_id: str
    document_version: str
    bytes: int
    chars: int
    chunks: int

    def to_dict(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "bytes": self.bytes,
            "chars": self.chars,
            "chunks": self.chunks,
        }


@dataclass(frozen=True, slots=True)
class IndexManifest:
    project_id: int
    generation_id: str
    knowledge_version: int
    mode: str
    config: RagConfig
    documents: tuple[DocumentManifest, ...]
    chunk_count: int
    corpus_hash: str
    config_hash: str
    base_generation_id: str | None = None
    base_manifest_hash: str | None = None

    def _payload(self) -> dict[str, object]:
        return {
            "manifest_version": 1,
            "status": "candidate",
            "mode": self.mode,
            "base_generation_id": self.base_generation_id,
            "base_manifest_hash": self.base_manifest_hash,
            "project_id": self.project_id,
            "generation_id": self.generation_id,
            "knowledge_version": self.knowledge_version,
            "collection": self.config.collection_name,
            "model": self.config.model_name,
            "model_revision": self.config.model_revision,
            "dimension": EMBEDDING_DIMENSION,
            "metric": self.config.metric,
            "normalization": self.config.normalization,
            "query_prefix": self.config.query_prefix,
            "passage_prefix": self.config.passage_prefix,
            "parser_version": self.config.parser_version,
            "chunker_version": self.config.chunker_version,
            "target_tokens": self.config.target_tokens,
            "overlap_tokens": self.config.overlap_tokens,
            "max_input_tokens": self.config.max_input_tokens,
            "candidate_k": self.config.candidate_k,
            "context_top_k": self.config.context_top_k,
            "documents": [doc.to_dict() for doc in self.documents],
            "chunk_count": self.chunk_count,
            "corpus_hash": self.corpus_hash,
            "config_hash": self.config_hash,
        }

    def _identity_payload(self) -> dict[str, object]:
        payload = self._payload()
        # Retrieval defaults and mode не меняют уже построенный candidate.
        for key in ("candidate_k", "context_top_k", "mode"):
            payload.pop(key, None)
        return payload

    @property
    def identity_hash(self) -> str:
        raw = json.dumps(self._identity_payload(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict[str, object]:
        payload = self._payload()
        payload["manifest_hash"] = self.identity_hash
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RagIndex:
    def __init__(
        self,
        store: QdrantChunkStore,
        embedder: E5Embedder,
        config: RagConfig | None = None,
    ) -> None:
        config = config or RagConfig()
        if embedder.dimension != EMBEDDING_DIMENSION:
            raise RagError(f"expected {EMBEDDING_DIMENSION}-dim embeddings")
        if config.candidate_k < config.context_top_k:
            raise RagError("candidate_k must be >= context_top_k")
        if not 0 < config.target_tokens < config.max_input_tokens:
            raise RagError("target_tokens must be in (0, max_input_tokens)")
        if not 0 <= config.overlap_tokens < config.target_tokens:
            raise RagError("overlap_tokens must be < target_tokens")
        if config.model_name != embedder.model_name:
            raise IncompatibleIndexError("config model_name does not match encoder")
        if config.model_revision != embedder.revision:
            raise IncompatibleIndexError("config model_revision does not match encoder")
        if config.query_prefix != embedder.query_prefix:
            raise IncompatibleIndexError("config query_prefix does not match encoder")
        if config.passage_prefix != embedder.passage_prefix:
            raise IncompatibleIndexError("config passage_prefix does not match encoder")
        if config.metric != embedder.metric:
            raise IncompatibleIndexError("config metric does not match encoder")
        if config.normalization != embedder.normalization:
            raise IncompatibleIndexError("config normalization does not match encoder")
        if config.max_input_tokens != embedder.max_input_tokens:
            raise IncompatibleIndexError("config max_input_tokens does not match encoder")
        if config.collection_name != store.collection_name:
            raise IncompatibleIndexError("config collection does not match store")
        self.store = store
        self.embedder = embedder
        self.config = config
        self.store.ensure_collection(dimension=EMBEDDING_DIMENSION)

    def index_generation(
        self,
        files: list[DocFile],
        *,
        project_id: int,
        generation_id: str,
        knowledge_version: int,
        mode: DocsMode = DocsMode.REPLACE,
        base_snapshot: KnowledgeSnapshot | None = None,
    ) -> IndexManifest:
        """Проиндексировать полный candidate corpus; публикации здесь нет."""
        snapshot = KnowledgeSnapshot(project_id, generation_id, knowledge_version)
        if mode is not DocsMode.APPEND and base_snapshot is not None:
            raise RagError("base_snapshot допустим только для APPEND")
        base_manifest: dict[str, object] | None = None
        if mode is DocsMode.APPEND:
            if base_snapshot is None:
                raise AppendBaseRequiredError("APPEND requires base_snapshot")
            if base_snapshot.project_id != project_id:
                raise IncompatibleIndexError("base snapshot project mismatch")
            if base_snapshot.generation_id == generation_id:
                raise RagError("APPEND requires a new generation_id")
            base_manifest = self._require_manifest(base_snapshot)
        if not files:
            raise EmptyCorpusError("нет документов")

        documents: list[LoadedDocument] = []
        seen: set[str] = set()
        seen_names: set[str] = set()
        for file in files:
            document = load_document(file)
            if document.filename in seen_names:
                raise DocumentAlreadyExistsError(f"duplicate filename {document.filename}")
            if document.document_id in seen:
                raise DocumentAlreadyExistsError(f"duplicate document {document.filename}")
            seen.add(document.document_id)
            seen_names.add(document.filename)
            documents.append(document)

        if base_manifest is not None:
            self._verify_append_base(base_manifest, documents)

        chunks: list[Chunk] = []
        chunks_by_document = {document.document_id: 0 for document in documents}
        for document in documents:
            document_chunks = chunk_document(
                document,
                self.embedder.tokenizer,
                target_tokens=self.config.target_tokens,
                overlap_tokens=self.config.overlap_tokens,
                max_input_tokens=self.config.max_input_tokens,
            )
            chunks_by_document[document.document_id] += len(document_chunks)
            chunks.extend(document_chunks)
        if not chunks:
            raise EmptyCorpusError("документы не дали ни одного чанка")
        if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
            raise IndexingError("duplicate deterministic chunk_id")

        manifest_docs = tuple(
            DocumentManifest(
                filename=document.filename,
                document_id=document.document_id,
                document_version=document.document_version,
                bytes=len(file.content),
                chars=len(document.text),
                chunks=chunks_by_document[document.document_id],
            )
            for document, file in zip(documents, files, strict=True)
        )
        corpus_hash = hashlib.sha256(
            "\n".join(
                f"{doc.document_id}:{doc.document_version}"
                for doc in sorted(manifest_docs, key=lambda item: item.document_id)
            ).encode()
        ).hexdigest()
        manifest = IndexManifest(
            project_id=project_id,
            generation_id=generation_id,
            knowledge_version=knowledge_version,
            mode=mode.value,
            config=self.config,
            documents=manifest_docs,
            chunk_count=len(chunks),
            corpus_hash=corpus_hash,
            config_hash=self.config.config_hash,
            base_generation_id=(base_snapshot.generation_id if base_snapshot is not None else None),
            base_manifest_hash=(
                str(base_manifest.get("manifest_hash")) if base_manifest is not None else None
            ),
        )

        existing = self.store.get_manifest(snapshot)
        if existing is not None:
            existing_hash, _ = existing
            if existing_hash != manifest.identity_hash:
                raise GenerationAlreadyExistsError(
                    "candidate already exists with a different manifest; "
                    "repeat with changed corpus/config refused"
                )
            if self.store.count(snapshot) != len(chunks):
                raise IndexingError("candidate manifest exists but point count differs")
            return manifest
        if self.store.count(snapshot) > 0:
            raise GenerationAlreadyExistsError(
                "partial candidate points exist without manifest; refusing overwrite"
            )

        # Embeddings считаются до upsert: ошибка модели не оставляет candidate.
        vectors = self.embedder.embed_passages([chunk.text for chunk in chunks])
        document_by_id = {document.document_id: document for document in documents}
        records: list[ChunkRecord] = []
        for chunk in chunks:
            document = document_by_id[chunk.document_id]
            records.append(
                ChunkRecord(
                    project_id=project_id,
                    generation_id=generation_id,
                    knowledge_version=knowledge_version,
                    document_id=chunk.document_id,
                    document_version=document.document_version,
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    title=chunk.title,
                    section_path=chunk.section_path,
                    start_char=chunk.start_char,
                    end_char=chunk.end_char,
                    ordinal=chunk.ordinal,
                    source_name=document.filename,
                )
            )
        self.store.upsert_vectors(records, vectors)
        if self.store.count(snapshot) != len(records):
            raise IndexingError("candidate count mismatch after upsert; not published")
        self.store.put_manifest(
            project_id, generation_id, manifest.identity_hash, manifest.to_dict()
        )
        return manifest

    @staticmethod
    def _verify_append_base(
        base_manifest: dict[str, object], documents: list[LoadedDocument]
    ) -> None:
        base_documents = base_manifest.get("documents")
        if not isinstance(base_documents, list):
            raise IncompatibleIndexError("base manifest documents missing")
        by_id = {document.document_id: document for document in documents}
        by_name = {document.filename: document for document in documents}
        for raw in base_documents:
            if not isinstance(raw, dict):
                raise IncompatibleIndexError("base document entry invalid")
            document_id = raw.get("document_id")
            document_version = raw.get("document_version")
            filename = raw.get("filename")
            current = (
                by_name.get(str(filename)) if filename is not None else by_id.get(str(document_id))
            )
            if current is None:
                raise AppendMissingDocumentError(
                    f"base document missing: {filename or document_id}"
                )
            if current.document_id != document_id or current.document_version != document_version:
                raise AppendDocumentChangedError(
                    f"base document changed: {filename or document_id}"
                )

    def _require_manifest(self, snapshot: KnowledgeSnapshot) -> dict[str, object]:
        existing = self.store.get_manifest(snapshot)
        if existing is None:
            raise ManifestNotFoundError(
                "generation has no completed manifest; partial candidate is unreadable"
            )
        stored_hash, manifest = existing
        expected = {
            "project_id": snapshot.project_id,
            "generation_id": snapshot.generation_id,
            "knowledge_version": snapshot.knowledge_version,
            "status": "candidate",
            "config_hash": self.config.config_hash,
            "model": self.embedder.model_name,
            "model_revision": self.embedder.revision,
            "dimension": self.embedder.dimension,
            "metric": self.embedder.metric,
            "normalization": self.embedder.normalization,
            "query_prefix": self.embedder.query_prefix,
            "passage_prefix": self.embedder.passage_prefix,
        }
        for key, value in expected.items():
            if manifest.get(key) != value:
                raise IncompatibleIndexError(f"manifest {key} mismatch")
        if manifest.get("manifest_hash") != stored_hash:
            raise IncompatibleIndexError("manifest hash mismatch")
        if manifest.get("chunk_count") != self.store.count(snapshot):
            raise PartialGenerationError("manifest chunk_count != stored points")
        documents = manifest.get("documents")
        if not isinstance(documents, list) or sum(
            int(doc.get("chunks", 0)) for doc in documents if isinstance(doc, dict)
        ) != manifest.get("chunk_count"):
            raise IncompatibleIndexError("manifest document counts mismatch")
        return manifest

    def search_chunks(
        self,
        snapshot: KnowledgeSnapshot,
        question: str,
        *,
        candidate_k: int | None = None,
        context_top_k: int | None = None,
    ) -> list[ScoredChunk]:
        candidate = self.config.candidate_k if candidate_k is None else candidate_k
        context = self.config.context_top_k if context_top_k is None else context_top_k
        if candidate < context or context <= 0:
            raise RagError("candidate_k must be >= context_top_k > 0")
        self._require_manifest(snapshot)
        vector = self.embedder.embed_query(question)
        return self.store.search(snapshot, vector, limit=candidate)[:context]

    def search(
        self,
        snapshot: KnowledgeSnapshot,
        question: str,
        *,
        candidate_k: int | None = None,
        context_top_k: int | None = None,
    ) -> list[Source]:
        chunks = self.search_chunks(
            snapshot,
            question,
            candidate_k=candidate_k,
            context_top_k=context_top_k,
        )
        return self._sources(snapshot, chunks)

    def get_sources(self, snapshot: KnowledgeSnapshot, chunk_ids: list[str]) -> list[Source]:
        self._require_manifest(snapshot)
        chunks = self.store.get_by_chunk_ids(snapshot, chunk_ids)
        return self._sources(snapshot, chunks)

    def count(self, snapshot: KnowledgeSnapshot) -> int:
        return self.store.count(snapshot)

    def _sources(self, snapshot: KnowledgeSnapshot, chunks: list[ScoredChunk]) -> list[Source]:
        sources: list[Source] = []
        for index, chunk in enumerate(chunks, start=1):
            payload = chunk.payload
            if (
                payload.get("project_id") != snapshot.project_id
                or payload.get("generation_id") != snapshot.generation_id
                or payload.get("knowledge_version") != snapshot.knowledge_version
            ):
                raise SourceScopeError("source payload does not match snapshot")
            required = ("document_id", "document_version", "chunk_id", "title", "text")
            if any(not payload.get(key) for key in required):
                raise SourceScopeError("source payload is incomplete")
            sources.append(
                Source(
                    project_id=int(payload["project_id"]),
                    generation_id=str(payload["generation_id"]),
                    knowledge_version=int(payload["knowledge_version"]),
                    document_id=str(payload["document_id"]),
                    document_version=str(payload["document_version"]),
                    chunk_id=str(payload["chunk_id"]),
                    title=str(payload["title"]),
                    excerpt=str(payload["text"]),
                    citation_label=f"[{index}]",
                    section_path=payload.get("section_path"),
                    page=None,
                )
            )
        return sources

    def close(self) -> None:
        self.store.close()
