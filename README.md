# Enterprise Knowledge Agent

A permission-aware assistant for company knowledge, built on **Google ADK** and **Gemini**, designed to run on free tiers. Employees ask questions in plain English. The agent answers from documents with citations, answers counting questions from a database, and drafts emails or meetings that a human must approve. The core rule: **an answer may only contain what the asking user is allowed to read.**

> **Status:** works end to end offline on a synthetic company ("Northwind") with 94 passing tests and a 72-case evaluation. The Gemini, Google Drive, Gmail/Calendar and Cloud Run paths are written but **not tested against live services**. See [docs/STATUS.md](docs/STATUS.md) and [eval/NOTES.md](eval/NOTES.md).

## Run it in 2 minutes (no API key, no network)
```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
eka generate-data && eka ingest
eka serve --port 8080          # open http://localhost:8080, pick a demo user
pytest -q && eka eval          # tests, then the evaluation report
```
Try the same question as two users: *"What is the salary band for a Staff Engineer?"* as Priya (engineer) and as Rahul (HR). Then *"How many customers are in the north region?"* as Priya (no database access) and as Anita (sales).

## What it does
| Capability | How it is made safe |
| --- | --- |
| Document Q&A with citations | ACL filter in the index query, then a second live check against the source before any text is used; relevance gate; grounding check that refuses unsupported answers |
| Questions over a SQL database | Read-only connection, table allow-list per group, function allow-list, row limit, timeout; the answer is built from the executed rows |
| Email drafts and calendar events | Draft only. Recipient-domain allow-list, re-validated when the user approves; only the owner can approve; everything audited |
| Prompt injection in documents | Hostile sentences stripped before the model sees them; passages are wrapped as untrusted data; instructions in documents cannot trigger actions (tested) |
| Sensitive data | PII redaction (cards with Luhn, Aadhaar, PAN, phones, API keys) in answers, citations, drafts and audit logs |

## Architecture in one picture
```
Browser UI --> FastAPI (signed cookie, CSRF header, rate limit, CSP)
                 |
                 +--> pipeline runtime (default)  or  ADK runtime (AGENT_RUNTIME=adk)
                        classify -> retrieve (ACL + recheck + gate) -> answer -> verify -> audit
                        sql tool (guarded)    actions (approval queue)
                 +--> Vector store (in-memory | Qdrant)   Source (local folder | Drive)
```
Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Why each choice: [docs/DECISIONS.md](docs/DECISIONS.md).

## Using Gemini (free tier)
Put `GEMINI_API_KEY` in `.env` (see `.env.example`), set `EKA_MODE=auto`, then `eka ingest` and `eka serve`. Set `AGENT_RUNTIME=adk` for the ADK multi-agent runtime. **Free-tier Gemini API content may be used to improve Google products; do not send real confidential data.** Steps for Drive and Gmail/Calendar: [docs/SETUP_GOOGLE.md](docs/SETUP_GOOGLE.md).

## Layout
`src/eka/` code, `eval/` cases and reports, `tests/` pytest suite, `deploy/` Cloud Run script, `docs/` documentation.

## Honest limits
Everything measured here comes from a corpus and cases written by the author. The offline mode proves the controls (permissions, SQL, approvals, redaction), not answer quality. Use `eval/cases.yaml` format to evaluate your own documents.
