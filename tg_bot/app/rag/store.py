"""Qdrant local/server adapter with mandatory project + generation filters."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from app.llm.gateway import KnowledgeSnapshot

COLLECTION_NAME = "kb_chunks"
MANIFEST_COLLECTION = "kb_generations"
# Collection point ID must include all three: payload filter can't protect an
# overwrite, and candidate generation must never replace active generation.
_POINT_NAMESPACE = NAMESPACE_URL


@dataclass(frozen=True, slots=True)
class ChunkRecord:
    project_id: int
    generation_id: str
    knowledge_version: int
    document_id: str
    document_version: str
    chunk_id: str
    text: str
    title: str
    section_path: str | None
    start_char: int
    end_char: int
    ordinal: int
    source_name: str

    def payload(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "generation_id": self.generation_id,
            "knowledge_version": self.knowledge_version,
            "document_id": self.document_id,
            "document_version": self.document_version,
            "chunk_id": self.chunk_id,
            "text": self.text,
            "title": self.title,
            "section_path": self.section_path,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "ordinal": self.ordinal,
            "source_name": self.source_name,
        }


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    point_id: str
    score: float
    payload: dict[str, Any]


class QdrantChunkStore:
    def __init__(
        self,
        path: str | Path,
        *,
        collection_name: str = COLLECTION_NAME,
        url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.collection_name = collection_name
        if url:
            self.client = QdrantClient(url=url, api_key=api_key)
        else:
            self.client = QdrantClient(path=str(Path(path)))
        self.ensure_collection()

    def ensure_collection(self, *, dimension: int = 384) -> None:
        if not self.client.collection_exists(self.collection_name):
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
            )
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="project_id",
                field_schema=models.PayloadSchemaType.INTEGER,
            )
            self.client.create_payload_index(
                collection_name=self.collection_name,
                field_name="generation_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        if not self.client.collection_exists(MANIFEST_COLLECTION):
            self.client.create_collection(
                collection_name=MANIFEST_COLLECTION,
                vectors_config=models.VectorParams(size=1, distance=models.Distance.DOT),
            )
            self.client.create_payload_index(
                collection_name=MANIFEST_COLLECTION,
                field_name="project_id",
                field_schema=models.PayloadSchemaType.INTEGER,
            )
            self.client.create_payload_index(
                collection_name=MANIFEST_COLLECTION,
                field_name="generation_id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    @staticmethod
    def point_id(project_id: int, generation_id: str, chunk_id: str) -> str:
        return str(uuid5(_POINT_NAMESPACE, f"innopolis:{project_id}:{generation_id}:{chunk_id}"))

    @staticmethod
    def _manifest_id(project_id: int, generation_id: str) -> str:
        return str(uuid5(_POINT_NAMESPACE, f"innopolis:manifest:{project_id}:{generation_id}"))

    @staticmethod
    def filter_for(snapshot: KnowledgeSnapshot) -> models.Filter:
        return models.Filter(
            must=[
                models.FieldCondition(
                    key="project_id",
                    match=models.MatchValue(value=snapshot.project_id),
                ),
                models.FieldCondition(
                    key="generation_id",
                    match=models.MatchValue(value=snapshot.generation_id),
                ),
            ]
        )

    def upsert_vectors(
        self,
        records: list[ChunkRecord],
        vectors: list[list[float]] | Any,
    ) -> None:
        if len(records) != len(vectors):
            raise ValueError("records/vectors length mismatch")
        points = [
            models.PointStruct(
                id=self.point_id(record.project_id, record.generation_id, record.chunk_id),
                vector=[float(value) for value in vector],
                payload=record.payload(),
            )
            for record, vector in zip(records, vectors, strict=True)
        ]
        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
            wait=True,
        )

    def count(self, snapshot: KnowledgeSnapshot) -> int:
        result = self.client.count(
            collection_name=self.collection_name,
            count_filter=self.filter_for(snapshot),
            exact=True,
        )
        return int(result.count)

    def search(self, snapshot: KnowledgeSnapshot, vector: Any, *, limit: int) -> list[ScoredChunk]:
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=[float(value) for value in vector],
            query_filter=self.filter_for(snapshot),
            limit=limit,
            with_payload=True,
        )
        result: list[ScoredChunk] = []
        for point in response.points:
            payload = point.payload or {}
            if (
                payload.get("project_id") != snapshot.project_id
                or payload.get("generation_id") != snapshot.generation_id
            ):
                continue
            result.append(
                ScoredChunk(point_id=str(point.id), score=float(point.score), payload=dict(payload))
            )
        return result

    def get_by_chunk_ids(
        self, snapshot: KnowledgeSnapshot, chunk_ids: list[str]
    ) -> list[ScoredChunk]:
        if not chunk_ids:
            return []
        ids = [self.point_id(snapshot.project_id, snapshot.generation_id, cid) for cid in chunk_ids]
        scroll_filter = self.filter_for(snapshot)
        scroll_filter.must = [
            *(scroll_filter.must or []),
            models.HasIdCondition(has_id=ids),
        ]
        points, _ = self.client.scroll(
            collection_name=self.collection_name,
            scroll_filter=scroll_filter,
            limit=len(ids),
            with_payload=True,
        )
        by_id = {str(point.id): point for point in points}
        result: list[ScoredChunk] = []
        for chunk_id, point_id in zip(chunk_ids, ids, strict=True):
            point = by_id.get(point_id)
            if point is None:
                continue
            payload = point.payload or {}
            if (
                payload.get("project_id") != snapshot.project_id
                or payload.get("generation_id") != snapshot.generation_id
                or payload.get("chunk_id") != chunk_id
            ):
                continue
            result.append(ScoredChunk(point_id=point_id, score=0.0, payload=dict(payload)))
        return result

    def put_manifest(
        self,
        project_id: int,
        generation_id: str,
        manifest_hash: str,
        manifest: dict[str, object],
    ) -> None:
        point_id = self._manifest_id(project_id, generation_id)
        self.client.upsert(
            collection_name=MANIFEST_COLLECTION,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector=[0.0],
                    payload={
                        "project_id": project_id,
                        "generation_id": generation_id,
                        "manifest_hash": manifest_hash,
                        "manifest": manifest,
                    },
                )
            ],
            wait=True,
        )

    def get_manifest(self, snapshot: KnowledgeSnapshot) -> tuple[str, dict[str, object]] | None:
        point_id = self._manifest_id(snapshot.project_id, snapshot.generation_id)
        points = self.client.retrieve(
            collection_name=MANIFEST_COLLECTION,
            ids=[point_id],
            with_payload=True,
        )
        if not points:
            return None
        payload = points[0].payload or {}
        if (
            payload.get("project_id") != snapshot.project_id
            or payload.get("generation_id") != snapshot.generation_id
        ):
            return None
        manifest = payload.get("manifest")
        manifest_hash = payload.get("manifest_hash")
        if not isinstance(manifest, dict) or not isinstance(manifest_hash, str):
            return None
        return manifest_hash, manifest

    def close(self) -> None:
        self.client.close()
