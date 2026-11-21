"""KnowledgeAgent: the deterministic orchestration used by the API and the evaluation suite.

The control flow (permissions, relevance gate, sanitising, grounding check, SQL validation, approvals,
audit) is plain Python. The language model is only asked for the four judgement calls in `Reasoner`.
That keeps every safety property testable without a model in the loop.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace

from .actions import ApprovalStore
from .audit import AuditLog
from .guardrails import (
    check_grounding, cited_labels, filter_output, redact_pii, sanitize_passage, strip_labels,
)
from .models import Passage, UserCtx
from .reasoner import Reasoner, action_topic, summarize_rows
from .retrieval import Retriever
from .sqltool import SqlTool, tables_for

NOT_FOUND = "I could not find this in the documents you have access to."


@dataclass
class AgentResponse:
    request_id: str
    status: str  # answered | refused | blocked | pending_approval | needs_info
    intent: str
    answer: str
    citations: list[dict] = field(default_factory=list)
    retrieved_files: list[str] = field(default_factory=list)
    sql: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    approval: dict | None = None
    flags: list[str] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _citation(p: Passage) -> dict:
    # snippets are shown to the user verbatim, so they get the same sensitive-identifier redaction as answers
    snippet, _ = redact_pii(p.chunk.text.strip().replace("\n", " "))
    return {
        "label": p.label, "file_id": p.chunk.file_id, "title": p.chunk.title, "url": p.chunk.url,
        "section": p.chunk.heading_path, "snippet": snippet[:280] + ("..." if len(snippet) > 280 else ""),
    }


class KnowledgeAgent:
    def __init__(self, retriever: Retriever, reasoner: Reasoner, sql_tool: SqlTool, approvals: ApprovalStore, audit: AuditLog, settings):
        self.retriever, self.reasoner, self.sql, self.approvals, self.audit, self.cfg = retriever, reasoner, sql_tool, approvals, audit, settings

    # ------------------------------------------------------------------ public
    def ask(self, user: UserCtx, question: str, request_id: str | None = None) -> AgentResponse:
        t0 = time.perf_counter()
        rid = request_id or uuid.uuid4().hex[:12]
        tin0, tout0 = self.reasoner.usage()
        flags: list[str] = []
        safe_q, kinds = redact_pii(question)
        if kinds:
            flags.append("pii_in_query:" + ",".join(kinds))
        question = question.strip()[:2000]

        intent = self.reasoner.classify(question)
        tools = ["classify"]
        if intent == "data":
            resp = self._data(user, question, rid, tools, flags)
        elif intent == "action":
            resp = self._action(user, question, rid, tools, flags)
        else:
            resp = self._docs(user, question, rid, tools, flags)

        resp.intent = intent
        resp.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        tin1, tout1 = self.reasoner.usage()
        self.audit.log(
            user=user.email, query=safe_q[:500], intent=intent,
            chunk_ids=[c["label"] + ":" + c["file_id"] for c in resp.citations] or resp.retrieved_files,
            sql_executed=resp.sql, tools_called=tools,
            approval_status=(resp.approval or {}).get("status", ""), answer_status=resp.status,
            flags=resp.flags, latency_ms=resp.latency_ms, tokens_in=tin1 - tin0, tokens_out=tout1 - tout0, request_id=rid,
        )
        return resp

    # ------------------------------------------------------------------ docs
    def _retrieve(self, user, question, tools, flags):
        res = self.retriever.search(user, question, self.cfg.top_k, self.cfg.candidates)
        tools.append("search_docs")
        if res.blocked_by_recheck:
            flags.append(f"recheck_blocked:{res.blocked_by_recheck}")
        # retrieved text is data: strip instruction-like sentences before the model sees it
        for p in res.passages:
            clean, hit = sanitize_passage(p.chunk.text)
            if hit:
                flags.append(f"injection_filtered:{p.chunk.file_id}:{'+'.join(hit)}")
                p.chunk = replace(p.chunk, text=clean)  # copy: never mutate the stored chunk
        return res

    def _docs(self, user, question, rid, tools, flags) -> AgentResponse:
        res = self._retrieve(user, question, tools, flags)
        files = list(dict.fromkeys(p.chunk.file_id for p in res.passages))
        if not self.retriever.is_relevant(res):
            return AgentResponse(rid, "refused", "docs", NOT_FOUND, retrieved_files=files, flags=flags)
        draft = self.reasoner.answer(question, res.passages)
        tools.append("answer")
        if draft.refused:
            return AgentResponse(rid, "refused", "docs", NOT_FOUND, retrieved_files=files, flags=flags)
        labels = cited_labels(draft.answer)
        by_label = {p.label: p for p in res.passages}
        cited = [by_label[l] for l in labels if l in by_label]
        ok, support = check_grounding(draft.answer, [p.chunk.text for p in cited], self.cfg.min_support)
        if not cited or not ok:
            flags.append(f"grounding_failed:{support}")
            return AgentResponse(rid, "refused", "docs", NOT_FOUND, retrieved_files=files, flags=flags)
        text, blocked = filter_output(strip_labels(draft.answer))
        if blocked:
            flags.append("output_redacted:" + ",".join(blocked))
        return AgentResponse(rid, "answered", "docs", text, [_citation(p) for p in cited], files, flags=flags)

    # ------------------------------------------------------------------ data
    def _data(self, user, question, rid, tools, flags) -> AgentResponse:
        if not tables_for(user):
            return AgentResponse(rid, "blocked", "data", "You do not have access to the company database.", flags=flags)
        sql = self.reasoner.write_sql(question, self.sql.describe(user))
        tools.append("write_sql")
        if not sql:
            return AgentResponse(rid, "refused", "data", "I could not turn that into a database query.", flags=flags)
        result = self.sql.run(sql, user)
        tools.append("run_sql")
        if not result.ok:
            flags.append("sql_blocked" if result.error.startswith("blocked") else "sql_error")
            return AgentResponse(rid, "blocked", "data", result.error, sql=sql, flags=flags)
        return AgentResponse(
            rid, "answered", "data", summarize_rows(result.columns, result.rows, result.truncated),
            sql=result.sql, columns=result.columns, rows=result.rows, flags=flags,
        )

    # ------------------------------------------------------------------ actions
    def _action(self, user, question, rid, tools, flags) -> AgentResponse:
        topic = action_topic(question)
        res = self._retrieve(user, topic, tools, flags)
        passages = res.passages if self.retriever.is_relevant(res) else []
        plan = self.reasoner.plan_action(question, passages)
        tools.append("plan_action")
        if plan is None:
            return AgentResponse(
                rid, "needs_info", "action",
                "I need a recipient email address (and, for meetings, a date and time such as 2026-11-20 at 15:00).", flags=flags,
            )
        proposal = self.approvals.propose(user, plan)
        tools.append("propose_action")
        if not proposal["ok"]:
            flags.append("action_rejected")
            return AgentResponse(rid, "blocked", "action", "I cannot prepare that action: " + "; ".join(proposal["errors"]), flags=flags)
        files = list(dict.fromkeys(p.chunk.file_id for p in passages))
        return AgentResponse(
            rid, "pending_approval", "action",
            "I prepared a draft. Nothing has been created yet. Review it and approve to continue.",
            citations=[_citation(p) for p in passages[:3]], retrieved_files=files, approval=proposal, flags=flags,
        )
