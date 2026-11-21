import dataclasses
import json
import time

import pytest
from fastapi.testclient import TestClient

from eka.api import create_app, sign_token, verify_token

H = {"X-Requested-With": "eka"}
D = "@northwind.example"


@pytest.fixture()
def client(deps):
    return TestClient(create_app(deps))


def login(client, name):
    r = client.post("/api/login", json={"email": name + D}, headers=H)
    assert r.status_code == 200, r.text
    return r


def test_health_and_index_and_security_headers(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.get("/")
    assert r.status_code == 200 and "Content-Security-Policy" in r.headers
    assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/../config.py").status_code in (400, 404)


def test_requires_login_and_csrf_header(client):
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/chat", json={"question": "hi"}, headers=H).status_code == 401
    assert client.post("/api/login", json={"email": "priya.sharma" + D}).status_code == 403   # no CSRF header
    login(client, "priya.sharma")
    assert client.post("/api/chat", json={"question": "How many days of annual leave?"}).status_code == 403


def test_unknown_user_and_forged_or_expired_cookie(client, deps):
    assert client.post("/api/login", json={"email": "nobody" + D}, headers=H).status_code == 404
    secret = deps.settings.session_secret
    forged = sign_token({"email": "vikram.rao" + D, "exp": time.time() + 999}, "wrong-secret")
    client.cookies.set("eka_session", forged)
    assert client.get("/api/me").status_code == 401
    expired = sign_token({"email": "vikram.rao" + D, "exp": time.time() - 5}, secret)
    client.cookies.set("eka_session", expired)
    assert client.get("/api/me").status_code == 401
    assert verify_token("garbage", secret) is None and verify_token("a.b", secret) is None


def test_chat_returns_citations_and_respects_identity(client):
    login(client, "priya.sharma")
    r = client.post("/api/chat", json={"question": "What is the salary band for a Staff Engineer?"}, headers=H).json()
    assert "35 to 55" not in json.dumps(r)
    client.post("/api/logout", headers=H)
    login(client, "rahul.verma")
    r = client.post("/api/chat", json={"question": "What is the salary band for a Staff Engineer?"}, headers=H).json()
    assert r["status"] == "answered" and "35 to 55" in r["answer"] and r["citations"]


def test_input_validation(client):
    login(client, "priya.sharma")
    assert client.post("/api/chat", json={"question": ""}, headers=H).status_code == 422
    assert client.post("/api/chat", json={"question": "x" * 5000}, headers=H).status_code == 422
    assert client.post("/api/chat", json={}, headers=H).status_code == 422


def test_stream_verifies_then_streams(client):
    login(client, "priya.sharma")
    r = client.post("/api/chat/stream", json={"question": "How many days of annual leave do employees get?"}, headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [e.split("\n")[0] for e in r.text.strip().split("\n\n")]
    assert events[0] == "event: status" and events[-1] == "event: final" and "event: token" in events
    final = json.loads(r.text.strip().split("\n\n")[-1].split("data: ", 1)[1])
    assert final["status"] == "answered" and "24 days" in final["answer"]


def test_approval_flow_over_http_and_ownership(client, deps):
    login(client, "priya.sharma")
    r = client.post("/api/chat", json={"question": "Draft an email to rahul.verma@northwind.example about the remote work policy"}, headers=H).json()
    aid = r["approval"]["approval_id"]
    assert [a["approval_id"] for a in client.get("/api/approvals").json()["approvals"]] == [aid]
    client.post("/api/logout", headers=H)
    login(client, "anita.desai")
    assert client.get("/api/approvals").json()["approvals"] == []
    assert client.post(f"/api/approvals/{aid}", json={"approve": True}, headers=H).status_code == 404
    assert list(deps.settings.outbox_dir.glob("*.json")) == []
    client.post("/api/logout", headers=H)
    login(client, "priya.sharma")
    out = client.post(f"/api/approvals/{aid}", json={"approve": True}, headers=H).json()
    assert out["status"] == "executed" and len(list(deps.settings.outbox_dir.glob("*.json"))) == 1


def test_audit_scope_and_admin_endpoints(client):
    login(client, "priya.sharma")
    client.post("/api/chat", json={"question": "How many days of annual leave do employees get?"}, headers=H)
    mine = client.get("/api/audit").json()
    assert mine["scope"] == "self" and all(e["user"] == "priya.sharma" + D for e in mine["entries"])
    assert client.post("/api/admin/ingest", headers=H).status_code == 403
    client.post("/api/logout", headers=H)
    login(client, "vikram.rao")
    assert client.get("/api/audit").json()["scope"] == "all"
    assert client.post("/api/admin/ingest", headers=H).json()["unchanged"] > 0


def test_rate_limit(deps):
    deps.settings = dataclasses.replace(deps.settings, max_rpm=3)
    c = TestClient(create_app(deps))
    login(c, "priya.sharma")
    codes = [c.post("/api/chat", json={"question": "How many days of annual leave?"}, headers=H).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_refuses_to_start_in_production_mode_with_default_secret(deps):
    deps.settings = dataclasses.replace(deps.settings, demo_auth=False)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_app(deps)
    deps.settings = dataclasses.replace(deps.settings, demo_auth=False, session_secret="x" * 40)
    create_app(deps)


def test_demo_login_can_be_disabled(deps):
    deps.settings = dataclasses.replace(deps.settings, demo_auth=False, session_secret="x" * 40)
    c = TestClient(create_app(deps))
    assert c.post("/api/login", json={"email": "priya.sharma" + D}, headers=H).status_code == 403


def test_unhandled_errors_do_not_leak_details(deps):
    app = create_app(deps)
    deps.agent.ask = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret path /etc/passwd"))
    c = TestClient(app, raise_server_exceptions=False)
    login(c, "priya.sharma")
    r = c.post("/api/chat", json={"question": "hello there"}, headers=H)
    assert r.status_code == 500 and "passwd" not in r.text
