from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .db import connect


class AuditLog:
    """Append-only record of every request: who asked, what was used, what ran, what was approved."""

    def __init__(self, state_path: Path):
        self.path = Path(state_path)
        connect(self.path).close()

    def log(
        self,
        *,
        user: str,
        query: str,
        intent: str = "",
        chunk_ids: list[str] | None = None,
        sql_executed: str = "",
        tools_called: list[str] | None = None,
        approval_status: str = "",
        answer_status: str = "",
        flags: list[str] | None = None,
        latency_ms: float = 0.0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        request_id: str | None = None,
    ) -> str:
        rid = request_id or uuid.uuid4().hex[:12]
        conn = connect(self.path)
        conn.execute(
            "INSERT OR REPLACE INTO audit_log VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid, datetime.now(timezone.utc).isoformat(), user, query, intent,
                json.dumps(chunk_ids or []), sql_executed, json.dumps(tools_called or []),
                approval_status, answer_status, json.dumps(flags or []), round(latency_ms, 1),
                tokens_in, tokens_out,
            ),
        )
        conn.commit()
        conn.close()
        return rid

    def recent(self, limit: int = 50, user: str | None = None) -> list[dict]:
        conn = connect(self.path)
        if user:
            rows = conn.execute("SELECT * FROM audit_log WHERE user=? ORDER BY ts DESC LIMIT ?", (user, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        out = []
        for r in rows:
            d = dict(r)
            for k in ("chunk_ids", "tools_called", "flags"):
                d[k] = json.loads(d[k] or "[]")
            out.append(d)
        return out
