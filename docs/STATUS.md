# Status

## Verified (run in this repo)
- 94 pytest tests pass: chunking, guardrails (incl. regression tests), retrieval and ACL recheck, incremental ingest, SQL controls, approvals, agent, ADK runtime with a scripted model, HTTP API.
- Evaluation: 69/72 (dev 35/36, test 34/36, first-run test 32/36), 0 permission leaks. See `eval/reports/latest.md` and `eval/NOTES.md`.
- UI loaded and driven in headless Chromium: login, chat, refusal, draft, approve.
- Non-editable `pip install .` includes the web assets and prompts and runs.

## Written but NOT tested against live services
- Gemini generation and embeddings (`GeminiLLM`, `GeminiReasoner`, `GeminiEmbedder`)
- Google Drive source, Google sign-in, Gmail/Calendar execution
- Docker image build and Cloud Run deployment (no Docker daemon or GCP access when written)
- Qdrant store against a real server

## Known weaknesses
See eval/NOTES.md (s07, n06, m04, padding in offline answers).

## Not built
SSO group sync, per-row security, encryption at rest, multi-tenant isolation, long-term memory, evaluation of real Gemini answer quality.
