from __future__ import annotations

import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from .chunking import chunk_document
from .db import connect
from .llm import Embedder
from .sources import Source
from .vectorstore import VectorStore


@dataclass
class IngestStats:
    seen: int = 0
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    chunks_written: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def _doc_hash(doc) -> str:
    h = hashlib.sha256()
    h.update(doc.title.encode())
    h.update(doc.text.encode())
    h.update(doc.acl_signature().encode())  # an ACL change must trigger re-indexing
    return h.hexdigest()[:16]


def ingest(
    source: Source,
    store: VectorStore,
    embedder: Embedder,
    state_path: Path,
    max_words: int = 450,
    overlap_words: int = 60,
    full: bool = False,
) -> IngestStats:
    """Incremental sync: skip unchanged files, re-index changed ones, remove deleted ones."""
    stats = IngestStats()
    conn = connect(state_path)
    known = {r["file_id"]: r["content_hash"] for r in conn.execute("SELECT * FROM ingest_state")}
    seen_ids: set[str] = set()
    now = datetime.now(timezone.utc).isoformat()

    for doc in source.iter_documents():
        stats.seen += 1
        seen_ids.add(doc.file_id)
        h = _doc_hash(doc)
        if not full and known.get(doc.file_id) == h:
            stats.unchanged += 1
            continue
        if doc.file_id in known:
            store.delete_file(doc.file_id)
            stats.updated += 1
        else:
            stats.new += 1
        chunks = chunk_document(doc, max_words, overlap_words)
        if chunks:
            vectors = embedder.embed_documents([c.embed_text() for c in chunks])
            store.upsert(chunks, vectors)
            stats.chunks_written += len(chunks)
        conn.execute(
            "INSERT OR REPLACE INTO ingest_state(file_id, content_hash, last_synced) VALUES (?,?,?)",
            (doc.file_id, h, now),
        )

    for gone in set(known) - seen_ids:
        store.delete_file(gone)
        conn.execute("DELETE FROM ingest_state WHERE file_id=?", (gone,))
        stats.deleted += 1

    conn.commit()
    conn.close()
    store.meta.update({"embedder": embedder.name, "dim": embedder.dim, "synced_at": now})
    return stats
