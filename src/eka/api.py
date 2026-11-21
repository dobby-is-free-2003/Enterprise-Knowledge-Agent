"""FastAPI service: auth, chat (plain and streaming), approvals, audit, admin ingest, static UI."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .deps import Deps, build_deps
from .models import UserCtx

WEB_DIR = Path(__file__).parent / "web"
COOKIE = "eka_session"
SESSION_TTL_S = 8 * 3600


# ----------------------------------------------------------------------------- signed session token
def sign_token(payload: dict, secret: str) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return f"{raw}.{sig}"


def verify_token(token: str, secret: str) -> dict | None:
    try:
        raw, sig = token.rsplit(".", 1)
        good = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good):
            return None
        payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return payload if payload.get("exp", 0) > time.time() else None
    except Exception:  # noqa: BLE001 - any malformed token is simply invalid
        return None


class RateLimiter:
    def __init__(self, per_minute: int):
        self.limit, self.hits = per_minute, defaultdict(deque)

    def allow(self, key: str) -> bool:
        now, q = time.monotonic(), self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


# ----------------------------------------------------------------------------- request models
class LoginBody(BaseModel):
    email: str = Field(max_length=200)


class GoogleLoginBody(BaseModel):
    id_token: str = Field(max_length=4096)


class ChatBody(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class DecisionBody(BaseModel):
    approve: bool


# ----------------------------------------------------------------------------- app factory
def create_app(deps: Deps | None = None, adk_model=None) -> FastAPI:
    deps = deps or build_deps(Settings.from_env())
    cfg = deps.settings
    if not cfg.demo_auth and (cfg.session_secret == "dev-secret-change-me" or len(cfg.session_secret) < 32):
        raise RuntimeError("Set SESSION_SECRET to a random value of at least 32 characters when DEMO_AUTH=false")
    app = FastAPI(title="Enterprise Knowledge Agent", version="0.1.0", docs_url="/api/docs", redoc_url=None)
    limiter = RateLimiter(cfg.max_rpm)
    app.state.deps = deps
    runtime = {"adk": None}

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/api/docs") and not request.url.path.startswith("/openapi"):
            resp.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
        return resp

    def csrf(request: Request) -> None:
        if request.headers.get("x-requested-with") != "eka":
            raise HTTPException(403, "missing X-Requested-With header")

    def current_user(request: Request) -> UserCtx:
        tok = request.cookies.get(COOKIE)
        payload = verify_token(tok, cfg.session_secret) if tok else None
        user = deps.users.get(payload["email"]) if payload else None
        if user is None:  # also covers users removed from the directory after login
            raise HTTPException(401, "not signed in")
        return user

    def set_session(resp: Response, email: str) -> None:
        token = sign_token({"email": email, "exp": time.time() + SESSION_TTL_S}, cfg.session_secret)
        resp.set_cookie(COOKIE, token, max_age=SESSION_TTL_S, httponly=True, samesite="lax", secure=not cfg.demo_auth)

    async def run_agent(user: UserCtx, question: str):
        if not limiter.allow(user.email):
            raise HTTPException(429, "too many requests, slow down")
        if cfg.agent_runtime == "adk":
            if runtime["adk"] is None:
                from .adk_app import AdkRuntime

                runtime["adk"] = AdkRuntime(deps, model=adk_model)
            return await runtime["adk"].ask(user, question)
        return await run_in_threadpool(deps.agent.ask, user, question)

    # ------------------------------------------------------------------ meta
    @app.get("/health")
    def health():
        return {"status": "ok", "mode": cfg.mode, "runtime": cfg.agent_runtime, "chunks": deps.store.count(), "embedder": deps.embedder.name}

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/static/{name}")
    def static_file(name: str):
        if name not in {"app.js", "style.css"}:
            raise HTTPException(404)
        return FileResponse(WEB_DIR / name)

    # ------------------------------------------------------------------ auth
    @app.get("/api/config")
    def public_config():
        return {
            "demo_auth": cfg.demo_auth,
            "google_client_id": cfg.google_oauth_client_id,
            "demo_users": [{"email": u.email, "name": u.display_name} for u in deps.users.all()] if cfg.demo_auth else [],
        }

    @app.post("/api/login", dependencies=[Depends(csrf)])
    def login(body: LoginBody, response: Response):
        if not cfg.demo_auth:
            raise HTTPException(403, "demo login is disabled")
        user = deps.users.get(body.email)
        if user is None:
            raise HTTPException(404, "unknown user")
        set_session(response, user.email)
        return {"email": user.email, "name": user.display_name, "groups": user.groups}

    @app.post("/api/login/google", dependencies=[Depends(csrf)])
    def login_google(body: GoogleLoginBody, response: Response):
        if not cfg.google_oauth_client_id:
            raise HTTPException(403, "Google sign-in is not configured")
        from .google_auth import verify_google_id_token

        email = verify_google_id_token(body.id_token, cfg.google_oauth_client_id)
        user = deps.users.get(email) if email else None
        if user is None:
            raise HTTPException(403, "this account is not allowed")
        set_session(response, user.email)
        return {"email": user.email, "name": user.display_name, "groups": user.groups}

    @app.post("/api/logout", dependencies=[Depends(csrf)])
    def logout(response: Response):
        response.delete_cookie(COOKIE)
        return {"ok": True}

    @app.get("/api/me")
    def me(user: UserCtx = Depends(current_user)):
        return {"email": user.email, "name": user.display_name, "groups": user.groups}

    # ------------------------------------------------------------------ chat
    @app.post("/api/chat", dependencies=[Depends(csrf)])
    async def chat(body: ChatBody, user: UserCtx = Depends(current_user)):
        return (await run_agent(user, body.question)).to_dict()

    @app.post("/api/chat/stream", dependencies=[Depends(csrf)])
    async def chat_stream(body: ChatBody, user: UserCtx = Depends(current_user)):
        """Server-sent events. The answer is verified first (permissions, grounding), then streamed."""

        def sse(event: str, data) -> str:
            return f"event: {event}\ndata: {json.dumps(data)}\n\n"

        async def gen():
            yield sse("status", {"message": "Searching your documents..."})
            try:
                resp = await run_agent(user, body.question)
            except HTTPException as e:
                yield sse("error", {"status": e.status_code, "message": e.detail})
                return
            words = resp.answer.split(" ")
            for i in range(0, len(words), 3):
                yield sse("token", {"text": " ".join(words[i : i + 3]) + " "})
                await asyncio.sleep(0.02)
            yield sse("final", resp.to_dict())

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ approvals
    @app.get("/api/approvals")
    def pending(user: UserCtx = Depends(current_user)):
        return {"approvals": deps.approvals.pending_for(user)}

    @app.post("/api/approvals/{approval_id}", dependencies=[Depends(csrf)])
    def decide(approval_id: str, body: DecisionBody, user: UserCtx = Depends(current_user)):
        out = deps.approvals.decide(user, approval_id, body.approve)
        deps.audit.log(
            user=user.email, query=f"approval:{approval_id}", intent="approval", tools_called=["decide_approval"],
            approval_status=out.get("status", "error"), answer_status="answered" if out.get("ok") else "blocked",
        )
        if not out.get("ok") and out.get("error") == "approval not found":
            raise HTTPException(404, "approval not found")
        return out

    # ------------------------------------------------------------------ audit and admin
    @app.get("/api/audit")
    def audit(limit: int = 50, user: UserCtx = Depends(current_user)):
        limit = max(1, min(limit, 200))
        is_admin = "exec" in user.groups
        return {"scope": "all" if is_admin else "self", "entries": deps.audit.recent(limit, None if is_admin else user.email)}

    @app.post("/api/admin/ingest", dependencies=[Depends(csrf)])
    async def admin_ingest(user: UserCtx = Depends(current_user)):
        if "exec" not in user.groups:
            raise HTTPException(403, "admin only")
        stats = await run_in_threadpool(deps.reindex)
        return stats.to_dict()

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        return JSONResponse({"detail": "internal error"}, status_code=500)  # never leak stack traces

    return app
