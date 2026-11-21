"""Generate the synthetic company: documents + ACL manifest, users, and a SQLite database."""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .corpus_data import ACLS, CUSTOMER_NAMES, DOCS, INDUSTRIES, REGIONS, TIERS, USERS


def generate_corpus(corpus_dir: Path) -> int:
    corpus_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for slug, title, owner, acl_key, body in DOCS:
        (corpus_dir / f"{slug}.md").write_text(body.strip() + "\n")
        files[slug] = {
            "path": f"{slug}.md",
            "title": title,
            "owner": f"{owner}@northwind.example",
            "allowed_principals": ACLS[acl_key],
            "acl_key": acl_key,
            "modified_at": "2026-10-30T09:00:00+05:30",
        }
    (corpus_dir / "manifest.json").write_text(json.dumps({"files": files}, indent=2))
    return len(files)


def generate_users(users_path: Path) -> int:
    users_path.parent.mkdir(parents=True, exist_ok=True)
    users_path.write_text(json.dumps(USERS, indent=2))
    return len(USERS)


def generate_sql(db_path: Path, seed: int = 7) -> dict:
    rng = random.Random(seed)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, industry TEXT, region TEXT, tier TEXT, signup_date TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, amount_inr REAL, status TEXT, order_date TEXT);
        CREATE TABLE tickets (id INTEGER PRIMARY KEY, customer_id INTEGER, priority TEXT, status TEXT, subject TEXT, created_at TEXT);
        """
    )
    start = date(2025, 1, 1)
    for i, name in enumerate(CUSTOMER_NAMES, start=1):
        conn.execute(
            "INSERT INTO customers VALUES (?,?,?,?,?,?)",
            (i, name, rng.choice(INDUSTRIES), rng.choice(REGIONS), rng.choices(TIERS, [3, 4, 5])[0],
             (start + timedelta(days=rng.randint(0, 600))).isoformat()),
        )
    n_orders = 0
    for cid in range(1, len(CUSTOMER_NAMES) + 1):
        for _ in range(rng.randint(2, 9)):
            n_orders += 1
            conn.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?)",
                (n_orders, cid, round(rng.uniform(15000, 650000), 2), rng.choices(["paid", "pending", "refunded"], [8, 2, 1])[0],
                 (date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))).isoformat()),
            )
    subjects = ["Login failure", "Slow queries", "Billing question", "API 429 errors", "Backup restore", "Region request", "Quota increase"]
    n_tickets = 0
    for _ in range(90):
        n_tickets += 1
        conn.execute(
            "INSERT INTO tickets VALUES (?,?,?,?,?,?)",
            (n_tickets, rng.randint(1, len(CUSTOMER_NAMES)), rng.choice(["critical", "high", "medium", "low"]),
             rng.choices(["open", "closed", "pending"], [4, 5, 1])[0], rng.choice(subjects),
             (date(2026, 8, 1) + timedelta(days=rng.randint(0, 100))).isoformat()),
        )
    conn.commit()
    conn.close()
    return {"customers": len(CUSTOMER_NAMES), "orders": n_orders, "tickets": n_tickets}


def generate_all(data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    docs = generate_corpus(data_dir / "corpus")
    users = generate_users(data_dir / "users.json")
    sql = generate_sql(data_dir / "northwind.db")
    return {"documents": docs, "users": users, **sql}
