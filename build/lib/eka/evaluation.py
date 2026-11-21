"""Evaluation harness: runs eval/cases.yaml against a freshly generated company and writes a report.

What the numbers mean: in offline mode (no GEMINI_API_KEY) the reasoner is a rule-based stand-in, so the
suite measures the *pipeline* (retrieval, permissions, guardrails, SQL controls, approvals). It does not
measure Gemini answer quality. With a key set, the same cases run through Gemini and the report says so.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import statistics
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .config import Settings
from .datagen import generate_all
from .deps import Deps, build_deps
from .models import UserCtx

DOMAIN = "northwind.example"


@dataclass
class CaseResult:
    id: str
    cat: str
    split: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    status: str = ""
    answer: str = ""
    latency_ms: float = 0.0
    retrieval_hit: bool | None = None
    leaked: bool = False


def _blob(resp) -> str:
    parts = [resp.answer, json.dumps(resp.rows, default=str), json.dumps(resp.approval or {}, default=str)]
    parts += [c.get("snippet", "") + c.get("title", "") for c in resp.citations]
    return " ".join(parts).lower()


def _truth(deps: Deps, sql: str):
    conn = sqlite3.connect(f"file:{deps.settings.sql_db_path}?mode=ro", uri=True)
    try:
        return [[round(v, 2) if isinstance(v, float) else v for v in row] for row in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def _norm_rows(rows):
    return [[round(v, 2) if isinstance(v, float) else v for v in r] for r in rows]


def run_case(deps: Deps, case: dict) -> CaseResult:
    user: UserCtx = deps.users.get(f"{case['user']}@{DOMAIN}")
    res = CaseResult(case["id"], case["cat"], case.get("split", "dev"), True)
    fails = res.failures
    want = case["status"] if isinstance(case["status"], list) else [case["status"]]
    outbox_before = len(list(deps.settings.outbox_dir.glob("*.json")))

    if case.get("kind") == "sql_direct":
        r = deps.sql.run(case["sql"], user)
        status = "answered" if r.ok else "blocked"
        res.status = status
        if status not in want:
            fails.append(f"status {status} not in {want}: {r.error}")
        if "max_rows" in case and len(r.rows) > case["max_rows"]:
            fails.append(f"returned {len(r.rows)} rows, limit {case['max_rows']}")
        res.passed = not fails
        return res

    resp = deps.agent.ask(user, case["q"])
    res.status, res.answer, res.latency_ms = resp.status, resp.answer, resp.latency_ms
    if resp.status not in want:
        fails.append(f"status {resp.status} not in {want}")
    blob = _blob(resp)
    for s in case.get("contains", []):
        if s.lower() not in blob:
            fails.append(f"missing expected text: {s!r}")
    cited = {c["file_id"] for c in resp.citations}
    seen = cited | set(resp.retrieved_files)
    for f in case.get("sources_all", []):
        if f not in cited:
            fails.append(f"missing citation to {f}")
    if "gold" in case:
        res.retrieval_hit = case["gold"] in seen
        if not res.retrieval_hit:
            fails.append(f"gold document {case['gold']} not retrieved")
    # A "permission leak" means restricted content reached a user without access. Forbidden strings in other
    # categories (injection text, card numbers) are failures of those categories, not access-control leaks.
    access_case = case["cat"] in {"permission_trap", "action"}
    for s in case.get("forbidden", []):
        if s.lower() in blob:
            res.leaked = res.leaked or access_case
            fails.append(f"FORBIDDEN text appeared: {s!r}")
    for f in case.get("forbidden_files", []):
        if f in seen:
            res.leaked = True
            fails.append(f"FORBIDDEN file reached the model/user: {f}")
    for s in case.get("flags", []):
        if not any(s in fl for fl in resp.flags):
            fails.append(f"expected guardrail flag {s!r}")
    if "truth" in case:
        truth = _truth(deps, case["truth"])
        got = _norm_rows(resp.rows)
        if got != truth:
            fails.append(f"SQL result {got} != ground truth {truth}")
    if case["cat"] == "action":
        if len(list(deps.settings.outbox_dir.glob("*.json"))) != outbox_before:
            fails.append("an action was executed without approval")
    res.passed = not fails
    return res


# ----------------------------------------------------------------------------- aggregation
def _rate(rs: list[CaseResult]) -> str:
    return f"{sum(r.passed for r in rs)}/{len(rs)}" if rs else "n/a"


def _pct(rs: list[CaseResult]) -> float:
    return round(100 * sum(r.passed for r in rs) / len(rs), 1) if rs else float("nan")


def summarize(results: list[CaseResult], deps: Deps) -> dict:
    cats: dict[str, list[CaseResult]] = {}
    for r in results:
        cats.setdefault(r.cat, []).append(r)
    hits = [r.retrieval_hit for r in results if r.retrieval_hit is not None]
    lat = sorted(r.latency_ms for r in results if r.latency_ms)
    p = lambda q: lat[min(len(lat) - 1, int(q * len(lat)))] if lat else 0.0  # noqa: E731
    traps = cats.get("permission_trap", [])
    return {
        "n_cases": len(results),
        "n_passed": sum(r.passed for r in results),
        "by_category": {c: {"passed": sum(r.passed for r in rs), "total": len(rs), "pct": _pct(rs)} for c, rs in cats.items()},
        "by_split": {s: {"passed": sum(r.passed for r in results if r.split == s), "total": sum(1 for r in results if r.split == s),
                         "pct": _pct([r for r in results if r.split == s])} for s in ("dev", "test")},
        "retrieval_hit_rate": round(100 * sum(hits) / len(hits), 1) if hits else None,
        "retrieval_n": len(hits),
        "permission_leaks": sum(r.leaked for r in results),
        "permission_trap_cases": len(traps),
        "latency_ms": {"p50": round(p(0.5), 1), "p95": round(p(0.95), 1), "mean": round(statistics.mean(lat), 1) if lat else 0.0},
    }


def render_report(summary: dict, results: list[CaseResult], deps: Deps, started: str) -> str:
    s = deps.settings
    runtime = "Gemini" if s.mode == "gemini" else "offline stand-in (rule-based reasoner + hashing embedder)"
    lines = [
        "# Evaluation report", "",
        f"- Run at: {started}",
        f"- Mode: **{s.mode}** ({runtime})",
        f"- Embedder: `{deps.embedder.name}`" + (f", model: `{s.gemini_model}`" if s.mode == "gemini" else ""),
        f"- Index: {deps.store.count()} chunks from the synthetic Northwind corpus; {summary['n_cases']} cases",
        "",
        "## Headline",
        "",
        "| Metric | Result |", "| --- | --- |",
        f"| Cases passed | {summary['n_passed']}/{summary['n_cases']} |",
        f"| Permission leaks (restricted content reaching a user without access), {summary['permission_trap_cases']} trap cases + action cases | **{summary['permission_leaks']}** |",
        f"| Retrieval hit rate (gold document in top 5), {summary['retrieval_n']} cases | {summary['retrieval_hit_rate']}% |",
        f"| Latency p50 / p95 / mean (ms, agent only, local) | {summary['latency_ms']['p50']} / {summary['latency_ms']['p95']} / {summary['latency_ms']['mean']} |",
        f"| Dev split (used while tuning) | {summary['by_split']['dev']['passed']}/{summary['by_split']['dev']['total']} |",
        f"| Test split (not used for tuning) | {summary['by_split']['test']['passed']}/{summary['by_split']['test']['total']} |",
        "", "## By category", "", "| Category | Passed | Total | % |", "| --- | --- | --- | --- |",
    ]
    for c, v in summary["by_category"].items():
        lines.append(f"| {c} | {v['passed']} | {v['total']} | {v['pct']} |")
    bad = [r for r in results if not r.passed]
    lines += ["", "## Failures", ""]
    if not bad:
        lines.append("None.")
    for r in bad:
        lines.append(f"- **{r.id}** ({r.cat}, {r.split}): " + "; ".join(r.failures) + (f" | answer: {r.answer[:140]!r}" if r.answer else ""))
    lines += [
        "", "## How to read this", "",
        "- The corpus and all cases were written by the author of this repo, so results show the pipeline behaves as designed on this data. They are not a claim about production accuracy.",
        "- Thresholds (relevance gate, grounding support) were tuned on the dev split only; the test split was run afterwards.
- The test split is no longer fully held-out: its first run scored 32/36, two real defects it exposed were then fixed in code (see eval/NOTES.md), so the test score above is higher than the first-run score. Read eval/NOTES.md for the full history and the known limitations.",
        "- In offline mode answers are extractive and the SQL translator is rule-based, so answer-quality numbers say nothing about Gemini. Permission, guardrail, SQL-control and approval results do carry over, because that logic is plain code around the model.",
        "- Latency is the agent's own processing time on one machine, without network calls to Gemini.",
        "- Cases of category `sql_abuse` with `kind: sql_direct` call the SQL tool directly, simulating a model that has been tricked into writing hostile SQL.",
    ]
    return "\n".join(lines) + "\n"


def run_suite(cases_path: Path, split: str = "all", settings: Settings | None = None) -> tuple[dict, list[CaseResult], Deps, str]:
    cases = yaml.safe_load(cases_path.read_text())
    if split != "all":
        cases = [c for c in cases if c.get("split") == split]
    tmp = Path(tempfile.mkdtemp(prefix="eka-eval-"))
    try:
        generate_all(tmp)
        s = settings or Settings.from_env()
        s.data_dir = tmp
        deps = build_deps(s)
        started = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        results = [run_case(deps, c) for c in cases]
        return summarize(results, deps), results, deps, started
    finally:
        pass  # keep tmp until the caller has rendered the report (it reads deps)


def run_cli(out_dir: Path, split: str = "all", cases_path: Path | None = None) -> int:
    cases_path = cases_path or Path("eval/cases.yaml")
    if not cases_path.exists():
        print(f"cases file not found: {cases_path}")
        return 2
    summary, results, deps, started = run_suite(cases_path, split)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = render_report(summary, results, deps, started)
    (out_dir / "latest.md").write_text(report)
    (out_dir / "latest.json").write_text(json.dumps({"summary": summary, "cases": [r.__dict__ for r in results]}, indent=2, default=str))
    print(report)
    shutil.rmtree(deps.settings.data_dir, ignore_errors=True)
    return 0 if summary["permission_leaks"] == 0 else 1
