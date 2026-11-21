"""Approval-gated actions.

The agent can only *propose* an action. Nothing is written to Gmail or Calendar until the same user
clicks approve. Gmail support is limited to creating DRAFTS (never sending). Recipients must be in an
allow-listed domain, which blocks the classic "email the data to an outside address" exfiltration path.
"""
from __future__ import annotations

import base64
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from .db import connect
from .guardrails import redact_pii
from .models import UserCtx

_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


@dataclass
class ActionPlan:
    kind: str  # email_draft | calendar_event
    to: list[str] = field(default_factory=list)  # recipients or attendees
    subject: str = ""  # subject or event title
    body: str = ""
    start: str = ""  # ISO 8601, events only
    end: str = ""

    def to_payload(self) -> dict:
        return dict(self.__dict__)


def validate_plan(plan: ActionPlan, allowed_domains: list[str]) -> list[str]:
    errs: list[str] = []
    if plan.kind not in {"email_draft", "calendar_event"}:
        errs.append("unknown action type")
    if not plan.to:
        errs.append("at least one recipient or attendee is required")
    for addr in plan.to:
        if not _EMAIL.match(addr):
            errs.append(f"invalid address: {addr}")
        elif addr.split("@")[1].lower() not in {d.lower() for d in allowed_domains}:
            errs.append(f"recipient outside allowed domains: {addr}")
    if not plan.subject or len(plan.subject) > 200:
        errs.append("subject/title must be 1 to 200 characters")
    if plan.kind == "email_draft" and not (1 <= len(plan.body) <= 5000):
        errs.append("email body must be 1 to 5000 characters")
    if plan.kind == "calendar_event":
        try:
            s, e = datetime.fromisoformat(plan.start), datetime.fromisoformat(plan.end)
            if e <= s:
                errs.append("event must end after it starts")
            elif (e - s).total_seconds() > 8 * 3600:
                errs.append("event longer than 8 hours")
        except ValueError:
            errs.append("start and end must be ISO 8601 datetimes")
    return errs


# ----------------------------------------------------------------------------- backends
class OutboxBackend:
    """Writes approved actions to JSON files. Safe default for demos and tests."""

    def __init__(self, outbox_dir: Path):
        self.dir = Path(outbox_dir)

    def execute(self, kind: str, payload: dict, user: UserCtx) -> dict:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}-{kind}.json"
        path.write_text(json.dumps({"kind": kind, "created_by": user.email, "payload": payload}, indent=2))
        return {"outbox_file": path.name}


class GoogleBackend:
    """Gmail drafts and Calendar events using the approving user's OAuth credentials.

    Untested against live Google APIs in this repo's offline run. Scopes needed:
    gmail.compose and calendar.events. Drafts only; there is no send call anywhere in this class.
    """

    def __init__(self, credentials):
        from googleapiclient.discovery import build

        self.gmail = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        self.cal = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def execute(self, kind: str, payload: dict, user: UserCtx) -> dict:
        if kind == "email_draft":
            msg = EmailMessage()
            msg["To"], msg["Subject"] = ", ".join(payload["to"]), payload["subject"]
            msg.set_content(payload["body"])
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            d = self.gmail.users().drafts().create(userId="me", body={"message": {"raw": raw}}).execute()
            return {"gmail_draft_id": d.get("id")}
        if kind == "calendar_event":
            ev = {
                "summary": payload["subject"],
                "description": payload.get("body", ""),
                "start": {"dateTime": payload["start"], "timeZone": "Asia/Kolkata"},
                "end": {"dateTime": payload["end"], "timeZone": "Asia/Kolkata"},
                "attendees": [{"email": a} for a in payload["to"]],
            }
            e = self.cal.events().insert(calendarId="primary", body=ev, sendUpdates="none").execute()
            return {"calendar_event_id": e.get("id"), "link": e.get("htmlLink")}
        raise ValueError(kind)


# ----------------------------------------------------------------------------- approvals
class ApprovalStore:
    def __init__(self, state_path: Path, backend, allowed_domains: list[str]):
        self.path, self.backend, self.allowed_domains = Path(state_path), backend, allowed_domains
        connect(self.path).close()

    def propose(self, user: UserCtx, plan: ActionPlan) -> dict:
        # drafts are built from retrieved text: never let card numbers, Aadhaar, PAN or keys into a draft
        plan.subject, _ = redact_pii(plan.subject)
        plan.body, _ = redact_pii(plan.body)
        errs = validate_plan(plan, self.allowed_domains)
        if errs:
            return {"ok": False, "errors": errs}
        aid = uuid.uuid4().hex[:10]
        conn = connect(self.path)
        conn.execute(
            "INSERT INTO approvals(approval_id,user,kind,payload,status,created_at) VALUES (?,?,?,?,?,?)",
            (aid, user.email, plan.kind, json.dumps(plan.to_payload()), "pending", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
        return {"ok": True, "approval_id": aid, "status": "pending", "kind": plan.kind, "payload": plan.to_payload()}

    def get(self, approval_id: str) -> dict | None:
        conn = connect(self.path)
        r = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        conn.close()
        if not r:
            return None
        d = dict(r)
        d["payload"] = json.loads(d["payload"])
        d["result"] = json.loads(d["result"]) if d["result"] else None
        return d

    def pending_for(self, user: UserCtx) -> list[dict]:
        conn = connect(self.path)
        rows = conn.execute("SELECT approval_id FROM approvals WHERE user=? AND status='pending' ORDER BY created_at", (user.email,)).fetchall()
        conn.close()
        return [self.get(r["approval_id"]) for r in rows]

    def decide(self, user: UserCtx, approval_id: str, approve: bool) -> dict:
        rec = self.get(approval_id)
        if rec is None or rec["user"] != user.email:  # same response for "missing" and "not yours"
            return {"ok": False, "error": "approval not found"}
        if rec["status"] != "pending":
            return {"ok": False, "error": f"already {rec['status']}"}
        status, result = "rejected", None
        if approve:
            # re-validate at execution time: policy may have changed since the proposal
            plan = ActionPlan(kind=rec["kind"], **{k: v for k, v in rec["payload"].items() if k != "kind"})
            errs = validate_plan(plan, self.allowed_domains)
            if errs:
                status, result = "failed", {"errors": errs}
            else:
                try:
                    result = self.backend.execute(rec["kind"], rec["payload"], user)
                    status = "executed"
                except Exception as e:  # noqa: BLE001
                    status, result = "failed", {"error": str(e)[:200]}
        conn = connect(self.path)
        conn.execute(
            "UPDATE approvals SET status=?, decided_at=?, result=? WHERE approval_id=?",
            (status, datetime.now(timezone.utc).isoformat(), json.dumps(result) if result else None, approval_id),
        )
        conn.commit()
        conn.close()
        return {"ok": status in {"executed", "rejected"}, "status": status, "result": result}
