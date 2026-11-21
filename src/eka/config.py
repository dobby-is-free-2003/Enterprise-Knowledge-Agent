from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Which groups may query which SQL tables. Governance lives here, not in prompts.
SQL_TABLE_ACCESS: dict[str, set[str]] = {
    "customers": {"sales", "support", "finance", "exec"},
    "orders": {"sales", "finance", "exec"},
    "tickets": {"support", "sales", "exec"},
}


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    mode: str = "offline"  # offline | gemini
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    embed_model: str = "gemini-embedding-001"
    embed_dim: int = 768
    agent_runtime: str = "pipeline"  # pipeline | adk
    data_dir: Path = Path("./data")
    vector_backend: str = "memory"  # memory | qdrant
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    source: str = "local"  # local | drive
    drive_folder_id: str | None = None
    company_domain: str = "northwind.example"
    allowed_recipient_domains: list[str] = field(default_factory=lambda: ["northwind.example"])
    action_backend: str = "outbox"  # outbox | google
    demo_auth: bool = True
    session_secret: str = "dev-secret-change-me"
    google_oauth_client_id: str | None = None
    max_rpm: int = 30
    # retrieval and grounding knobs
    top_k: int = 5
    candidates: int = 20
    min_coverage: float = 0.45
    min_embed_score: float = 1.1  # >1 disables the embedding-score gate (offline hashing vectors)
    min_support: float = 0.5
    chunk_max_words: int = 450
    chunk_overlap_words: int = 60
    sql_row_limit: int = 50
    sql_timeout_s: float = 2.0

    @property
    def corpus_dir(self) -> Path:
        return self.data_dir / "corpus"

    @property
    def users_path(self) -> Path:
        return self.data_dir / "users.json"

    @property
    def sql_db_path(self) -> Path:
        return self.data_dir / "northwind.db"

    @property
    def state_db_path(self) -> Path:
        return self.data_dir / "eka_state.db"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def outbox_dir(self) -> Path:
        return self.data_dir / "outbox"

    @property
    def token_dir(self) -> Path:
        return self.data_dir / "tokens"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.corpus_dir, self.index_dir, self.outbox_dir):
            p.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, **overrides) -> "Settings":
        key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        requested = os.getenv("EKA_MODE", "auto").lower()
        mode = ("gemini" if key else "offline") if requested == "auto" else requested
        domains = [
            d.strip()
            for d in os.getenv("ALLOWED_RECIPIENT_DOMAINS", "northwind.example").split(",")
            if d.strip()
        ]
        s = cls(
            mode=mode,
            gemini_api_key=key,
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
            embed_model=os.getenv("EMBED_MODEL", "gemini-embedding-001"),
            embed_dim=int(os.getenv("EMBED_DIM", "768")),
            agent_runtime=os.getenv("AGENT_RUNTIME", "pipeline"),
            data_dir=Path(os.getenv("EKA_DATA_DIR", "./data")),
            vector_backend=os.getenv("VECTOR_BACKEND", "memory"),
            qdrant_url=os.getenv("QDRANT_URL") or None,
            qdrant_api_key=os.getenv("QDRANT_API_KEY") or None,
            source=os.getenv("SOURCE", "local"),
            drive_folder_id=os.getenv("DRIVE_FOLDER_ID") or None,
            company_domain=os.getenv("COMPANY_DOMAIN", "northwind.example"),
            allowed_recipient_domains=domains,
            action_backend=os.getenv("ACTION_BACKEND", "outbox"),
            demo_auth=_env_bool("DEMO_AUTH", True),
            session_secret=os.getenv("SESSION_SECRET", "dev-secret-change-me"),
            google_oauth_client_id=os.getenv("GOOGLE_OAUTH_CLIENT_ID") or None,
            max_rpm=int(os.getenv("MAX_REQUESTS_PER_MINUTE", "30")),
            min_coverage=float(os.getenv("EKA_MIN_COVERAGE", "0.45")),
        )
        if s.mode == "gemini":
            # embedding scores from a real model are on a different scale than hashed vectors
            s.min_embed_score = 0.62
            s.min_support = 0.45
        for k, v in overrides.items():
            setattr(s, k, v)
        return s
