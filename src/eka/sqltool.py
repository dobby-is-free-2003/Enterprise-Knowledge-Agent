"""Read-only SQL over an allow-listed schema.

Defence in depth (none of it depends on the model behaving):
  1. database opened read-only (SQLite mode=ro)
  2. one statement only, must start with SELECT or WITH
  3. SQLite authorizer allows only SELECT, reads of tables the *user's groups* may see, and a short
     list of safe functions; everything else (PRAGMA, ATTACH, writes, other tables) is denied
  4. row limit enforced by wrapping the query; wall-clock timeout via progress handler
"""
from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import SQL_TABLE_ACCESS
from .models import UserCtx

ALLOWED_FUNCS = {
    "count", "sum", "avg", "min", "max", "round", "lower", "upper", "length", "coalesce", "ifnull",
    "date", "strftime", "substr", "abs", "nullif",
}


@dataclass
class SqlResult:
    ok: bool
    sql: str
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    truncated: bool = False
    error: str = ""


def tables_for(user: UserCtx) -> set[str]:
    groups = set(user.groups)
    return {t for t, allowed in SQL_TABLE_ACCESS.items() if groups & allowed}


class SqlTool:
    def __init__(self, db_path: Path, row_limit: int = 50, timeout_s: float = 2.0):
        self.db_path, self.row_limit, self.timeout_s = Path(db_path), row_limit, timeout_s

    def describe(self, user: UserCtx) -> str:
        """Schema text for the model: only tables this user may query."""
        allowed = tables_for(user)
        if not allowed or not self.db_path.exists():
            return ""
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            lines = []
            for t in sorted(allowed):
                cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
                lines.append(f"{t}(" + ", ".join(f"{c[1]} {c[2]}" for c in cols) + ")")
            return "\n".join(lines)
        finally:
            conn.close()

    def run(self, sql: str, user: UserCtx) -> SqlResult:
        sql = (sql or "").strip().rstrip(";").strip()
        allowed_tables = tables_for(user)
        if not allowed_tables:
            return SqlResult(False, sql, error="blocked: you do not have access to any database tables")
        if not re.match(r"(?is)^(select|with)\b", sql):
            return SqlResult(False, sql, error="blocked: only SELECT queries are allowed")
        if ";" in sql:
            return SqlResult(False, sql, error="blocked: only one statement is allowed")

        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        deadline = time.monotonic() + self.timeout_s

        def authorizer(action, arg1, arg2, dbname, source):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_OK if arg1 in allowed_tables else sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_OK if (arg2 or "").lower() in ALLOWED_FUNCS else sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_DENY

        conn.set_authorizer(authorizer)
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 5000)
        try:
            wrapped = f"SELECT * FROM ({sql}) LIMIT {self.row_limit + 1}"
            cur = conn.execute(wrapped)
            cols = [d[0] for d in cur.description or []]
            rows = [list(r) for r in cur.fetchall()]
            truncated = len(rows) > self.row_limit
            return SqlResult(True, sql, cols, rows[: self.row_limit], truncated)
        except sqlite3.Error as e:
            msg = str(e).lower()
            if "not authorized" in msg or "authoriz" in msg:
                return SqlResult(False, sql, error="blocked: query touches tables, columns or functions you may not use")
            if "interrupted" in msg:
                return SqlResult(False, sql, error="blocked: query took too long")
            return SqlResult(False, sql, error="query error: " + str(e)[:120])
        finally:
            conn.close()
