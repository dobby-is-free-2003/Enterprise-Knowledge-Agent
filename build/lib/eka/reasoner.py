"""The four judgement calls the agent needs from a language model, behind one interface.

  classify      -> docs | data | action
  answer        -> grounded answer citing passage labels, or a refusal
  write_sql     -> one SELECT (or None)
  plan_action   -> an ActionPlan (or None)

OfflineReasoner is a deterministic, rule-based and extractive stand-in. It lets the full pipeline, test
suite and evaluation run with no network. Evaluation numbers produced with it measure the *pipeline*
(retrieval, permissions, guardrails, approvals), not Gemini's answer quality.

GeminiReasoner asks Gemini for the same four decisions as JSON. Its prompts live in src/eka/prompts/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from .actions import ActionPlan
from .guardrails import wrap_untrusted
from .models import Passage
from .text_utils import Lexicon, content_terms, split_sentences

PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text()


@dataclass
class Draft:
    answer: str
    refused: bool = False


class Reasoner(Protocol):
    def classify(self, question: str) -> str: ...

    def answer(self, question: str, passages: list[Passage]) -> Draft: ...

    def write_sql(self, question: str, schema_text: str) -> str | None: ...

    def plan_action(self, question: str, passages: list[Passage]) -> ActionPlan | None: ...

    def usage(self) -> tuple[int, int]: ...


def summarize_rows(columns: list[str], rows: list[list], truncated: bool = False) -> str:
    """Deterministic summary of SQL results (no model involved, so numbers cannot be misreported)."""
    if not rows:
        return "The query returned no rows."
    if len(rows) == 1 and len(columns) == 1:
        return f"{columns[0].replace('_', ' ')}: {rows[0][0]}"
    lines = [", ".join(f"{c}={v}" for c, v in zip(columns, r)) for r in rows[:10]]
    more = f" (showing 10 of {len(rows)})" if len(rows) > 10 else ""
    return f"{len(rows)} row(s){more}" + ("; results truncated by the row limit" if truncated else "") + ":\n" + "\n".join(lines)


# ============================================================================ offline
_ACTION_EMAIL = re.compile(r"\b(draft|write|compose|prepare)\b.{0,40}\be-?mail\b", re.I)
_ACTION_EVENT = re.compile(r"\b(schedule|book|set up|create|add)\b.{0,40}\b(meeting|event|call|calendar)\b", re.I)
_DATA_NOUNS = re.compile(r"\b(customers?|orders?|tickets?|revenue)\b", re.I)
_DATA_VERBS = re.compile(r"\b(how many|total|count|average|avg|sum|top \d+|highest|lowest)\b|\blist (all |the )?(customers|orders|tickets)\b", re.I)
_EMAIL_ADDR = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _q(s: str) -> str:
    return s.replace("'", "''")


class OfflineReasoner:
    def __init__(self, lexicon: Lexicon):
        self.lex = lexicon

    def usage(self) -> tuple[int, int]:
        return 0, 0

    def classify(self, question: str) -> str:
        if _ACTION_EMAIL.search(question) or _ACTION_EVENT.search(question):
            return "action"
        if _DATA_NOUNS.search(question) and _DATA_VERBS.search(question):
            return "data"
        return "docs"

    # -- grounded answer: pick the best sentence per passage, keep the strongest few
    def _best_sentences(self, question: str, passages: list[Passage], limit: int = 3, floor: float = 0.34):
        qterms = content_terms(question)
        picks = []
        for p in passages:
            best = None
            for s in split_sentences(p.chunk.text):
                if "[removed" in s:
                    continue
                ctx = f"{p.chunk.title} {p.chunk.heading_path} {s}"
                cov = self.lex.coverage(qterms, ctx)
                if best is None or cov > best[0]:
                    best = (cov, s, p)
            if best and best[0] >= floor:
                picks.append(best)
        picks.sort(key=lambda x: -x[0])
        seen_files, out = set(), []
        for cov, s, p in picks:
            if p.chunk.file_id in seen_files:
                continue
            seen_files.add(p.chunk.file_id)
            out.append((cov, s, p))
            if len(out) >= limit:
                break
        return out

    def answer(self, question: str, passages: list[Passage]) -> Draft:
        picks = self._best_sentences(question, passages)
        if not picks:
            return Draft("", refused=True)
        # keep sentences close to the best one so unrelated sources are not mixed in
        top = picks[0][0]
        picks = [x for x in picks if x[0] >= max(0.34, top - 0.25)]
        return Draft(" ".join(f"{s.rstrip('.')}. [{p.label}]" for _, s, p in picks))

    def write_sql(self, question: str, schema_text: str) -> str | None:
        q = question.strip().rstrip("?.! ")
        ql = q.lower()
        m = re.search(r"how many customers .*in the (\w+) region", ql)
        if m:
            return f"SELECT COUNT(*) AS customer_count FROM customers WHERE region = '{_q(m.group(1))}' COLLATE NOCASE"
        m = re.search(r"how many (enterprise|team|startup)[- ](?:tier )?customers", ql)
        if m:
            return f"SELECT COUNT(*) AS customer_count FROM customers WHERE tier = '{_q(m.group(1))}' COLLATE NOCASE"
        m = re.search(r"how many (critical|high|medium|low)[- ]priority (?:open )?tickets", ql)
        if m:
            return f"SELECT COUNT(*) AS ticket_count FROM tickets WHERE status = 'open' AND priority = '{_q(m.group(1))}'"
        m = re.search(r"how many (open|closed|pending) tickets", ql)
        if m:
            return f"SELECT COUNT(*) AS ticket_count FROM tickets WHERE status = '{m.group(1)}'"
        m = re.search(r"total order (?:amount|value) for (.+)$", q, re.I)
        if m:
            return (
                "SELECT ROUND(SUM(o.amount_inr), 2) AS total_amount_inr FROM orders o "
                f"JOIN customers c ON c.id = o.customer_id WHERE c.name = '{_q(m.group(1).strip())}' COLLATE NOCASE"
            )
        m = re.search(r"top (\d+) customers by (?:total )?(?:order )?(?:value|amount|revenue)", ql)
        if m:
            n = min(int(m.group(1)), 20)
            return (
                "SELECT c.name, ROUND(SUM(o.amount_inr), 2) AS total_amount_inr FROM customers c "
                f"JOIN orders o ON o.customer_id = c.id GROUP BY c.id ORDER BY total_amount_inr DESC, c.name LIMIT {n}"
            )
        if re.search(r"average order (amount|value)", ql):
            return "SELECT ROUND(AVG(amount_inr), 2) AS avg_order_inr FROM orders"
        if re.search(r"total revenue", ql):
            return "SELECT ROUND(SUM(amount_inr), 2) AS total_revenue_inr FROM orders WHERE status = 'paid'"
        m = re.search(r"list (?:all |the )?customers in the (\w+) industry", ql)
        if m:
            return f"SELECT name FROM customers WHERE industry = '{_q(m.group(1))}' COLLATE NOCASE ORDER BY name"
        return None

    def plan_action(self, question: str, passages: list[Passage]) -> ActionPlan | None:
        emails = _EMAIL_ADDR.findall(question)
        if not emails:
            return None
        if _ACTION_EMAIL.search(question):
            m = re.search(r"(?:about|regarding|summari[sz]ing|explaining|covering)\s+(.+?)(?:\s+(?:to|for)\s+\S+@|\.|$)", question, re.I)
            topic = (m.group(1).strip() if m else "your question").rstrip(".")
            # A draft summarises the best-matching document: its top sections, one lead sentence each.
            top_file = passages[0].chunk.file_id if passages else None
            lines = []
            for p in passages:
                if p.chunk.file_id != top_file or len(lines) >= 4:
                    continue
                sents = split_sentences(p.chunk.text)
                if sents:
                    lines.append(f"- {sents[0].rstrip('.')}.")
            lines = lines or ["- (no supporting documents were found for this topic)"]
            body = "Hi,\n\nHere is a short summary of " + topic + ":\n\n" + "\n".join(lines) + "\n\nRegards"
            return ActionPlan(kind="email_draft", to=emails[:1], subject=f"Summary: {topic}"[:200], body=body)
        m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(?:at\s+)?(\d{1,2}:\d{2})", question)
        if not m:
            return None
        dur = re.search(r"for (\d+)\s*(minutes?|mins?|hours?|hrs?)", question, re.I)
        minutes = 30
        if dur:
            minutes = int(dur.group(1)) * (60 if dur.group(2).lower().startswith("h") else 1)
        hh, mm = m.group(2).split(":")
        start = datetime.fromisoformat(f"{m.group(1)}T{int(hh):02d}:{mm}:00")
        end = start + timedelta(minutes=minutes)
        t = re.search(r"(?:called|titled|named)\s+['\"]?(.+?)['\"]?(?:\s+(?:on|with|at)\b|$)", question, re.I) or re.search(
            r"\babout\s+(.+?)(?:\s+(?:on|with|at)\b|$)", question, re.I
        )
        title = (t.group(1).strip() if t else "Meeting")[:200]
        return ActionPlan(
            kind="calendar_event", to=emails, subject=title,
            start=start.isoformat() + "+05:30", end=end.isoformat() + "+05:30",
        )


# ============================================================================ gemini
class GeminiReasoner:
    def __init__(self, llm):
        self.llm = llm

    def usage(self) -> tuple[int, int]:
        return self.llm.tokens_in, self.llm.tokens_out

    def classify(self, question: str) -> str:
        out = self.llm.generate_json(f"Question: {question}", system=load_prompt("classify"))
        return out.get("intent") if out.get("intent") in {"docs", "data", "action"} else "docs"

    def answer(self, question: str, passages: list[Passage]) -> Draft:
        prompt = f"{wrap_untrusted(passages)}\n\nQuestion: {question}"
        out = self.llm.generate_json(prompt, system=load_prompt("answer"))
        if not out or out.get("refused") or not out.get("answer"):
            return Draft("", refused=True)
        return Draft(str(out["answer"]))

    def write_sql(self, question: str, schema_text: str) -> str | None:
        out = self.llm.generate_json(f"Schema:\n{schema_text}\n\nQuestion: {question}", system=load_prompt("sql"))
        sql = out.get("sql") if out else None
        return sql if isinstance(sql, str) and sql.strip() else None

    def plan_action(self, question: str, passages: list[Passage]) -> ActionPlan | None:
        ctx = wrap_untrusted(passages) if passages else ""
        out = self.llm.generate_json(f"{ctx}\n\nRequest: {question}", system=load_prompt("action"))
        if not out or out.get("kind") not in {"email_draft", "calendar_event"}:
            return None
        return ActionPlan(
            kind=out["kind"], to=list(out.get("to") or []), subject=str(out.get("subject") or ""),
            body=str(out.get("body") or ""), start=str(out.get("start") or ""), end=str(out.get("end") or ""),
        )


def action_topic(question: str) -> str:
    """The subject of a draft request, used as the retrieval query instead of the raw command text."""
    m = re.search(r"(?:about|regarding|summari[sz]ing|explaining|covering)\s+(.+?)(?:\s+(?:to|for)\s+\S+@|\.|$)", question, re.I)
    return m.group(1).strip().rstrip(".") if m else question
