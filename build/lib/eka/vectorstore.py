"""Vector stores behind one interface. Access control is enforced inside `search`."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Iterable, Protocol

import numpy as np

from .models import Chunk


class VectorStore(Protocol):
    meta: dict

    def upsert(self, chunks: list[Chunk], vectors: np.ndarray) -> None: ...

    def search(self, vector: np.ndarray, limit: int, principals: set[str]) -> list[tuple[Chunk, float]]: ...

    def delete_file(self, file_id: str) -> None: ...

    def iter_chunks(self) -> Iterable[Chunk]: ...

    def count(self) -> int: ...


class InMemoryStore:
    """numpy cosine search with a principal filter. Persists to a directory."""

    def __init__(self) -> None:
        self.chunks: list[Chunk] = []
        self.vectors: np.ndarray | None = None
        self.meta: dict = {}

    def upsert(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        if not chunks:
            return
        ids = {c.chunk_id for c in chunks}
        self._drop(lambda c: c.chunk_id in ids)
        self.chunks.extend(chunks)
        self.vectors = vectors if self.vectors is None or len(self.vectors) == 0 else np.vstack([self.vectors, vectors])

    def _drop(self, pred) -> None:
        if not self.chunks:
            return
        keep = [i for i, c in enumerate(self.chunks) if not pred(c)]
        self.chunks = [self.chunks[i] for i in keep]
        self.vectors = self.vectors[keep] if self.vectors is not None and keep else None

    def delete_file(self, file_id: str) -> None:
        self._drop(lambda c: c.file_id == file_id)

    def search(self, vector: np.ndarray, limit: int, principals: set[str]) -> list[tuple[Chunk, float]]:
        if self.vectors is None or not self.chunks:
            return []
        allowed = [i for i, c in enumerate(self.chunks) if principals.intersection(c.allowed_principals)]
        if not allowed:
            return []
        scores = self.vectors[allowed] @ vector
        order = np.argsort(-scores)[:limit]
        return [(self.chunks[allowed[i]], float(scores[i])) for i in order]

    def iter_chunks(self) -> Iterable[Chunk]:
        return iter(self.chunks)

    def count(self) -> int:
        return len(self.chunks)

    # persistence -----------------------------------------------------------------------------
    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self.vectors if self.vectors is not None else np.zeros((0, 1), np.float32))
        (directory / "chunks.json").write_text(json.dumps([c.to_payload() for c in self.chunks]))
        (directory / "meta.json").write_text(json.dumps(self.meta))

    @classmethod
    def load(cls, directory: Path) -> "InMemoryStore":
        s = cls()
        if not (directory / "chunks.json").exists():
            return s
        s.chunks = [Chunk.from_payload(p) for p in json.loads((directory / "chunks.json").read_text())]
        vec = np.load(directory / "vectors.npy")
        s.vectors = vec if len(s.chunks) else None
        s.meta = json.loads((directory / "meta.json").read_text())
        return s


class QdrantStore:
    """Qdrant backend. `location=":memory:"` or a path uses embedded local mode; a URL uses a server."""

    COLLECTION = "eka_chunks"

    def __init__(self, dim: int, location: str | None = ":memory:", api_key: str | None = None):
        from qdrant_client import QdrantClient, models

        self._m = models
        if location and location.startswith("http"):
            self.client = QdrantClient(url=location, api_key=api_key)
        elif location in (None, ":memory:"):
            self.client = QdrantClient(":memory:")
        else:
            self.client = QdrantClient(path=location)
        self.dim = dim
        self.meta: dict = {}
        if not self.client.collection_exists(self.COLLECTION):
            self.client.create_collection(
                self.COLLECTION,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )
            for field in ("allowed_principals", "file_id"):
                self.client.create_payload_index(self.COLLECTION, field, models.PayloadSchemaType.KEYWORD)

    @staticmethod
    def _pid(chunk_id: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))

    def upsert(self, chunks: list[Chunk], vectors: np.ndarray) -> None:
        if not chunks:
            return
        pts = [
            self._m.PointStruct(id=self._pid(c.chunk_id), vector=v.tolist(), payload=c.to_payload())
            for c, v in zip(chunks, vectors)
        ]
        self.client.upsert(self.COLLECTION, points=pts)

    def search(self, vector: np.ndarray, limit: int, principals: set[str]) -> list[tuple[Chunk, float]]:
        flt = self._m.Filter(
            must=[self._m.FieldCondition(key="allowed_principals", match=self._m.MatchAny(any=sorted(principals)))]
        )
        res = self.client.query_points(
            self.COLLECTION, query=vector.tolist(), query_filter=flt, limit=limit, with_payload=True
        ).points
        return [(Chunk.from_payload(p.payload), float(p.score)) for p in res]

    def delete_file(self, file_id: str) -> None:
        self.client.delete(
            self.COLLECTION,
            points_selector=self._m.FilterSelector(
                filter=self._m.Filter(must=[self._m.FieldCondition(key="file_id", match=self._m.MatchValue(value=file_id))])
            ),
        )

    def iter_chunks(self) -> Iterable[Chunk]:
        offset = None
        while True:
            pts, offset = self.client.scroll(self.COLLECTION, limit=256, offset=offset, with_payload=True)
            for p in pts:
                yield Chunk.from_payload(p.payload)
            if offset is None:
                break

    def count(self) -> int:
        return self.client.count(self.COLLECTION, exact=True).count
