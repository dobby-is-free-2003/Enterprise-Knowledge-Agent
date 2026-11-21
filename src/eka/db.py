from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_state (
    file_id TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    last_synced TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    request_id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    user TEXT NOT NULL,
    query TEXT NOT NULL,
    intent TEXT,
    chunk_ids TEXT,
    sql_executed TEXT,
    tools_called TEXT,
    approval_status TEXT,
    answer_status TEXT,
    flags TEXT,
    latency_ms REAL,
    tokens_in INTEGER,
    tokens_out INTEGER
);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    result TEXT
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
