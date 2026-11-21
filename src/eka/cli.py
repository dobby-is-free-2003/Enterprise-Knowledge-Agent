"""Command line: eka generate-data | ingest | ask | serve | eval | google-auth"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings


def _settings() -> Settings:
    return Settings.from_env()


def cmd_generate(args) -> int:
    from .datagen import generate_all

    s = _settings()
    info = generate_all(s.data_dir)
    print(json.dumps(info, indent=2))
    print(f"wrote synthetic company data to {s.data_dir}")
    return 0


def cmd_ingest(args) -> int:
    from .deps import build_deps

    s = _settings()
    if not s.corpus_dir.exists() and s.source == "local":
        print("no corpus found; run `eka generate-data` first", file=sys.stderr)
        return 2
    deps = build_deps(s, auto_ingest=False)
    stats = deps.reindex(full=args.full)
    print(json.dumps({**stats.to_dict(), "mode": s.mode, "embedder": deps.embedder.name, "chunks_in_index": deps.store.count()}, indent=2))
    return 0


def cmd_ask(args) -> int:
    from .deps import build_deps

    deps = build_deps(_settings())
    user = deps.users.get(args.user)
    if user is None:
        print(f"unknown user {args.user}; known: {[u.email for u in deps.users.all()]}", file=sys.stderr)
        return 2
    r = deps.agent.ask(user, args.question)
    print(json.dumps(r.to_dict(), indent=2, default=str))
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    from .api import create_app

    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_eval(args) -> int:
    from .evaluation import run_cli

    return run_cli(Path(args.out), split=args.split)


def cmd_google_auth(args) -> int:
    from .google_auth import run_local_flow

    s = _settings()
    run_local_flow(Path(args.client_secret), s.token_dir / "service.json")
    print(f"token saved to {s.token_dir / 'service.json'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eka", description="Enterprise Knowledge Agent")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate-data", help="create synthetic documents, users and SQL data").set_defaults(fn=cmd_generate)
    s = sub.add_parser("ingest", help="chunk, embed and index the documents")
    s.add_argument("--full", action="store_true", help="re-index everything")
    s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("ask", help="ask one question as a user")
    s.add_argument("--user", required=True)
    s.add_argument("question")
    s.set_defaults(fn=cmd_ask)
    s = sub.add_parser("serve", help="run the API and web UI")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8080)
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("eval", help="run the evaluation suite")
    s.add_argument("--out", default="eval/reports")
    s.add_argument("--split", choices=["all", "dev", "test"], default="all")
    s.set_defaults(fn=cmd_eval)
    s = sub.add_parser("google-auth", help="one-time OAuth login for Drive, Gmail drafts and Calendar")
    s.add_argument("--client-secret", required=True)
    s.set_defaults(fn=cmd_google_auth)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
