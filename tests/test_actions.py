from eka.actions import ActionPlan, validate_plan

DOMAINS = ["northwind.example"]


def _email(**kw):
    base = dict(kind="email_draft", to=["a@northwind.example"], subject="Hi", body="Body")
    base.update(kw)
    return ActionPlan(**base)


def test_validation_rules():
    assert validate_plan(_email(), DOMAINS) == []
    assert validate_plan(_email(to=["x@evil.example"]), DOMAINS)            # outside domain
    assert validate_plan(_email(to=[]), DOMAINS)                           # no recipient
    assert validate_plan(_email(to=["not-an-email"]), DOMAINS)
    assert validate_plan(_email(subject=""), DOMAINS)
    assert validate_plan(_email(body="x" * 6000), DOMAINS)
    ev = ActionPlan(kind="calendar_event", to=["a@northwind.example"], subject="Sync",
                    start="2026-11-20T15:00:00+05:30", end="2026-11-20T15:30:00+05:30")
    assert validate_plan(ev, DOMAINS) == []
    ev.end = "2026-11-20T14:00:00+05:30"
    assert validate_plan(ev, DOMAINS)
    ev.end = "2026-11-21T15:00:00+05:30"
    assert validate_plan(ev, DOMAINS)                                      # longer than 8 hours
    ev.start = "tomorrow"
    assert validate_plan(ev, DOMAINS)


def test_nothing_executes_until_approved(deps, U):
    me = U("priya.sharma")
    p = deps.approvals.propose(me, _email())
    assert p["ok"] and p["status"] == "pending"
    assert list(deps.settings.outbox_dir.glob("*.json")) == []
    out = deps.approvals.decide(me, p["approval_id"], True)
    assert out["status"] == "executed" and len(list(deps.settings.outbox_dir.glob("*.json"))) == 1


def test_other_users_cannot_decide(deps, U):
    p = deps.approvals.propose(U("priya.sharma"), _email())
    out = deps.approvals.decide(U("anita.desai"), p["approval_id"], True)
    assert not out["ok"] and out["error"] == "approval not found"           # same answer as a missing id
    assert list(deps.settings.outbox_dir.glob("*.json")) == []


def test_cannot_decide_twice_and_reject_works(deps, U):
    me = U("priya.sharma")
    a = deps.approvals.propose(me, _email())
    assert deps.approvals.decide(me, a["approval_id"], False)["status"] == "rejected"
    assert not deps.approvals.decide(me, a["approval_id"], True)["ok"]
    assert list(deps.settings.outbox_dir.glob("*.json")) == []


def test_policy_is_rechecked_at_approval_time(deps, U):
    me = U("priya.sharma")
    p = deps.approvals.propose(me, _email())
    deps.approvals.allowed_domains = ["other.example"]   # policy tightened after the proposal
    out = deps.approvals.decide(me, p["approval_id"], True)
    assert out["status"] == "failed" and list(deps.settings.outbox_dir.glob("*.json")) == []


def test_sensitive_identifiers_never_enter_a_draft(deps, U):
    me = U("priya.sharma")
    p = deps.approvals.propose(me, _email(body="Card 4111 1111 1111 1111 for bookings."))
    assert "4111" not in p["payload"]["body"] and "[REDACTED:card]" in p["payload"]["body"]
