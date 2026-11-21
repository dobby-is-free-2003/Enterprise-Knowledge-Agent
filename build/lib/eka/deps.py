"""Dependency wiring: builds every component from Settings."""
from __future__ import annotations

from dataclasses import dataclass

from .actions import ApprovalStore, GoogleBackend, OutboxBackend
from .agent import KnowledgeAgent
from .audit import AuditLog
from .config import Settings
from .ingest import IngestStats, ingest
from .llm import Embedder, GeminiEmbedder, GeminiLLM, HashingEmbedder
from .reasoner import GeminiReasoner, OfflineReasoner
from .retrieval import Retriever
from .sources import DriveSource, LocalFolderSource, Source
from .sqltool import SqlTool
from .users import UserDirectory
from .vectorstore import InMemoryStore, QdrantStore, VectorStore


@dataclass
class Deps:
    settings: Settings
    embedder: Embedder
    store: VectorStore
    source: Source
    retriever: Retriever
    reasoner: object
    sql: SqlTool
    approvals: ApprovalStore
    audit: AuditLog
    users: UserDirectory
    agent: KnowledgeAgent
    llm: GeminiLLM | None = None

    def reindex(self, full: bool = False) -> IngestStats:
        stats = ingest(
            self.source, self.store, self.embedder, self.settings.state_db_path,
            self.settings.chunk_max_words, self.settings.chunk_overlap_words, full=full,
        )
        if isinstance(self.store, InMemoryStore):
            self.store.save(self.settings.index_dir)
        self.retriever.refresh()
        return stats


def build_embedder(s: Settings) -> Embedder:
    if s.mode == "gemini":
        return GeminiEmbedder(s.gemini_api_key, s.embed_model, s.embed_dim)
    return HashingEmbedder()


def build_store(s: Settings, embedder: Embedder) -> VectorStore:
    if s.vector_backend == "qdrant":
        loc = s.qdrant_url or str(s.index_dir / "qdrant")
        store = QdrantStore(embedder.dim, loc, s.qdrant_api_key)
        store.meta = {"embedder": embedder.name, "dim": embedder.dim}
        return store
    store = InMemoryStore.load(s.index_dir)
    if store.meta.get("embedder") != embedder.name:  # index built with a different embedder: start clean
        store = InMemoryStore()
        store.meta = {"embedder": embedder.name, "dim": embedder.dim}
    return store


def build_source(s: Settings) -> Source:
    if s.source == "drive":
        from .google_auth import load_credentials

        creds = load_credentials(s.token_dir / "service.json")
        return DriveSource(creds, s.drive_folder_id or "", s.company_domain)
    return LocalFolderSource(s.corpus_dir)


def build_backend(s: Settings):
    if s.action_backend == "google":
        from .google_auth import load_credentials

        return GoogleBackend(load_credentials(s.token_dir / "service.json"))
    return OutboxBackend(s.outbox_dir)


def build_deps(s: Settings | None = None, auto_ingest: bool = True) -> Deps:
    s = s or Settings.from_env()
    s.ensure_dirs()
    embedder = build_embedder(s)
    store = build_store(s, embedder)
    source = build_source(s)
    retriever = Retriever(store, embedder, source, s.min_coverage, s.min_embed_score)
    llm = None
    if s.mode == "gemini":
        llm = GeminiLLM(s.gemini_api_key, s.gemini_model)
        reasoner = GeminiReasoner(llm)
    else:
        reasoner = OfflineReasoner(retriever.lexicon)
    sql = SqlTool(s.sql_db_path, s.sql_row_limit, s.sql_timeout_s)
    approvals = ApprovalStore(s.state_db_path, build_backend(s), s.allowed_recipient_domains)
    audit = AuditLog(s.state_db_path)
    agent = KnowledgeAgent(retriever, reasoner, sql, approvals, audit, s)
    deps = Deps(s, embedder, store, source, retriever, reasoner, sql, approvals, audit, UserDirectory(s.users_path), agent, llm)
    if auto_ingest and store.count() == 0:
        deps.reindex(full=True)
    return deps
