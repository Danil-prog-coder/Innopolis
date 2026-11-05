"""PERS-43 RAG ingestion/retrieval (candidate-only, no publication)."""

from app.rag.chunking import (
    Chunk,
    DocumentAlreadyExistsError,
    EmptyCorpusError,
    GenerationAlreadyExistsError,
    RagError,
    load_document,
)
from app.rag.embeddings import E5Embedder
from app.rag.index import IndexManifest, RagConfig, RagIndex
from app.rag.store import QdrantChunkStore

__all__ = [
    "Chunk",
    "DocumentAlreadyExistsError",
    "E5Embedder",
    "EmptyCorpusError",
    "GenerationAlreadyExistsError",
    "IndexManifest",
    "QdrantChunkStore",
    "RagConfig",
    "RagError",
    "RagIndex",
    "load_document",
]
