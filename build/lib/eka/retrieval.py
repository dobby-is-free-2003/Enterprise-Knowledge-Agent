"""Permission-aware retrieval: ACL filter in the store, rerank, then a live second permission check.

Compound questions ("X and Y") dilute single-chunk coverage, so when the whole question fails the
relevance gate it is split into sub-queries. The question is only treated as answerable if EVERY
sub-query clears the gate on its own, so an uncovered half still produces a refusal, not a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm import Embedder
from .models import Passage, UserCtx
from .sources import Source
from .text_utils import Lexicon, content_terms
from .vectorstore import VectorStore

_SPLIT = re.compile(r"\s+(?:and|as well as|plus)\s+|[;?]\s*(?=\S)", re.I)
MAX_PARTS = 3
PER_PART = 3


def subqueries(query: str) -> list[str]:
    parts = [p.strip(" ?.,") for p in _SPLIT.split(query) if p and p.strip(" ?.,")]
    parts = [p for p in parts if len(content_terms(p)) >= 2]
    return parts[:MAX_PARTS] if len(parts) >= 2 else []


@dataclass
class RetrievalResult:
    passages: list[Passage] = field(default_factory=list)
    blocked_by_recheck: int = 0
    best_coverage: float = 0.0
    best_score: float = 0.0
    relevant_override: bool | None = None  # set when sub-queries were each checked separately
    decomposed: bool = False


class Retriever:
    def __init__(self, store: VectorStore, embedder: Embedder, source: Source, min_coverage: float = 0.45, min_embed_score: float = 1.1):
        self.store, self.embedder, self.source = store, embedder, source
        self.min_coverage, self.min_embed_score = min_coverage, min_embed_score
        self.lexicon = Lexicon()
        self.refresh()

    def refresh(self) -> None:
        """Recompute corpus term statistics after ingestion."""
        self.lexicon.fit(c.embed_text() for c in self.store.iter_chunks())

    # ------------------------------------------------------------------ single query
    def _search_one(self, user: UserCtx, query: str, k: int, candidates: int) -> RetrievalResult:
        qvec = self.embedder.embed_query(query)
        # 1) first line of defence: the store only returns chunks whose ACL matches the user's principals
        hits = self.store.search(qvec, candidates, user.principals)
        qterms = content_terms(query)
        scored = []
        for chunk, score in hits:
            cov = self.lexicon.coverage(qterms, chunk.embed_text())
            scored.append((score + 0.5 * cov, score, cov, chunk))
        scored.sort(key=lambda x: -x[0])

        result = RetrievalResult()
        recheck_cache: dict[str, bool] = {}
        for _, score, cov, chunk in scored:
            # 2) second line: re-read the file's *current* permissions (catches a revoked share between syncs)
            if chunk.file_id not in recheck_cache:
                recheck_cache[chunk.file_id] = self.source.check_access(chunk.file_id, user)
            if not recheck_cache[chunk.file_id]:
                result.blocked_by_recheck += 1
                continue
            result.passages.append(Passage(label=f"P{len(result.passages) + 1}", chunk=chunk, score=score, coverage=cov))
            if len(result.passages) >= k:
                break
        if result.passages:
            result.best_coverage = max(p.coverage for p in result.passages)
            result.best_score = max(p.score for p in result.passages)
        return result

    # ------------------------------------------------------------------ public
    def search(self, user: UserCtx, query: str, k: int = 5, candidates: int = 20) -> RetrievalResult:
        whole = self._search_one(user, query, k, candidates)
        if self.is_relevant(whole):
            return whole
        parts = subqueries(query)
        if not parts:
            return whole
        results = [self._search_one(user, p, PER_PART, candidates) for p in parts]
        if not all(self.is_relevant(r) for r in results):
            return whole  # at least one half is not covered: keep the refusal
        merged, seen = RetrievalResult(decomposed=True, relevant_override=True), set()
        for rank in range(PER_PART):
            for r in results:
                if rank < len(r.passages) and r.passages[rank].chunk.chunk_id not in seen:
                    p = r.passages[rank]
                    seen.add(p.chunk.chunk_id)
                    merged.passages.append(Passage(label=f"P{len(merged.passages) + 1}", chunk=p.chunk, score=p.score, coverage=p.coverage))
        merged.blocked_by_recheck = sum(r.blocked_by_recheck for r in results)
        merged.best_coverage = min(r.best_coverage for r in results)
        merged.best_score = min(r.best_score for r in results)
        return merged

    def is_relevant(self, result: RetrievalResult) -> bool:
        """Relevance gate: lets the agent answer "not found" instead of guessing."""
        if result.relevant_override is not None:
            return result.relevant_override and bool(result.passages)
        return bool(result.passages) and (
            result.best_coverage >= self.min_coverage or result.best_score >= self.min_embed_score
        )
