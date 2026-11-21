"""Structure-aware chunking: split on headings, pack paragraphs, overlap only when splitting."""
from __future__ import annotations

import hashlib
import re

from .models import Chunk, Document

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def _sections(text: str) -> list[tuple[str, list[str]]]:
    """Return [(heading_path, [paragraph, ...])] in document order."""
    stack: list[tuple[int, str]] = []
    sections: list[tuple[str, list[str]]] = []
    para: list[str] = []
    paras: list[str] = []
    current_path = ""

    def flush_para():
        nonlocal para
        if para:
            paras.append("\n".join(para).strip())
            para = []

    def flush_section():
        nonlocal paras
        flush_para()
        if paras:
            sections.append((current_path, paras))
        paras = []

    for line in text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush_section()
            level, title = len(m.group(1)), m.group(2).strip()
            stack = [(lv, t) for lv, t in stack if lv < level] + [(level, title)]
            current_path = " > ".join(t for _, t in stack)
        elif not line.strip():
            flush_para()
        else:
            para.append(line)
    flush_section()
    return sections


def _split_long(words: list[str], max_words: int, overlap: int) -> list[str]:
    out, i = [], 0
    step = max(1, max_words - overlap)
    while i < len(words):
        out.append(" ".join(words[i : i + max_words]))
        if i + max_words >= len(words):
            break
        i += step
    return out


def chunk_document(doc: Document, max_words: int = 450, overlap_words: int = 60) -> list[Chunk]:
    pieces: list[tuple[str, str]] = []  # (heading_path, text)
    for path, paras in _sections(doc.text):
        buf: list[str] = []
        count = 0
        for p in paras:
            w = p.split()
            if len(w) > max_words:
                if buf:
                    pieces.append((path, "\n\n".join(buf)))
                    buf, count = [], 0
                for part in _split_long(w, max_words, overlap_words):
                    pieces.append((path, part))
                continue
            if count + len(w) > max_words and buf:
                pieces.append((path, "\n\n".join(buf)))
                tail = " ".join(" ".join(buf).split()[-overlap_words:]) if overlap_words else ""
                buf, count = ([tail] if tail else []), len(tail.split())
            buf.append(p)
            count += len(w)
        if buf:
            pieces.append((path, "\n\n".join(buf)))

    chunks = []
    for i, (path, text) in enumerate(pieces):
        chunks.append(
            Chunk(
                chunk_id=f"{doc.file_id}#{i}",
                file_id=doc.file_id,
                title=doc.title,
                url=doc.url,
                text=text,
                chunk_index=i,
                heading_path=path,
                owner=doc.owner,
                allowed_principals=list(doc.allowed_principals),
                mime_type=doc.mime_type,
                modified_at=doc.modified_at,
                content_hash=hashlib.sha256(text.encode()).hexdigest()[:16],
            )
        )
    return chunks
