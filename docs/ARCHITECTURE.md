# Architecture

## Request path (pipeline runtime)
1. **Auth.** HMAC-signed cookie (HttpOnly, SameSite=Lax, Secure when not in demo mode). Every state-changing request needs `X-Requested-With: eka`. The server resolves the cookie to a `UserCtx` (email, groups) from the user directory. The model never supplies identity.
2. **Classify** the request: docs, data (SQL), or action. Offline: rules. Gemini: a JSON classification prompt.
3. **Retrieve (docs).** Query the index with the user's principals (`user:<email>`, `group:<name>`, `group:all-staff`) as a filter. Then re-check each hit against the source (`check_access`) to catch permissions revoked since the last sync. Then apply the relevance gate (IDF-weighted term coverage >= 0.45). Compound questions are split into at most 3 sub-queries and every part must be relevant.
4. **Sanitize.** Strip sentences that look like instructions to the assistant; wrap passages as untrusted data; redact PII.
5. **Answer.** Offline: extractive sentences above a 0.34 floor. Gemini: prompt with labelled passages `[P1]..`.
6. **Verify.** Grounding check (answer terms must be supported by the cited passages) and output filter. Failing answers become a refusal, not a guess.
7. **Audit.** One row per request: user, redacted query, intent, chunk ids, SQL executed, tools, approval status, flags, latency, request id.

## SQL path
Question -> single SELECT (rule-based offline, Gemini online) -> `SqlTool`: connection opened `mode=ro`; SQLite authorizer allows only SELECT/READ on tables permitted for the user's groups and a function allow-list; blocks PRAGMA, ATTACH, sqlite_master, extensions, multiple statements; 50-row cap via wrapper; progress-handler timeout. The reply is generated from the returned rows by code, so the model cannot invent numbers.

## Action path
`plan_action` produces an `ActionPlan` -> `ApprovalStore.propose` validates (recipient domain allow-list, address syntax, size limits, event ordering and 8-hour cap), redacts PII, stores as pending. Only the proposing user can `decide`; policy is **re-validated at approval time**; approved items are written to an outbox (or Gmail/Calendar when configured). Other users get the same "not found" as for a missing id.

## ADK runtime
`build_agents` creates a root `knowledge_agent` routing to `docs_agent`, `data_agent`, `actions_agent`. Tools read the identity from session state `eka_user_email`. Tool results (passages, SQL result, approval) are stored in session state and the final `AgentResponse` is built **from state**, not from model prose, then passes the same grounding and output checks. Tested with a scripted model through the real ADK runner.

## Ingestion
Source (local folder or Drive) -> chunk by headings with overlap -> embed -> upsert. Incremental: a content hash that includes the ACL, so permission-only changes re-index. Deleted files are removed.

## Threat model summary
| Threat | Control | Test |
| --- | --- | --- |
| Reading restricted docs | index filter + live recheck | test_retrieval, eval permission_* |
| Model asked to impersonate | identity from session | test_adk |
| Injection in documents | sentence stripping, untrusted wrapping, actions need approval | test_guardrails, eval injection |
| Hostile SQL | ro connection, authorizer, allow-lists | test_sqltool, eval sql_abuse |
| Data exfil via email | domain allow-list, approval, redaction | test_actions |
| PII in outputs and logs | redaction everywhere | test_guardrails, test_agent |
| Forged/expired session | HMAC + expiry, startup guard on default secret | test_api |
Out of scope: DLP beyond regex PII, multi-tenant isolation, per-row SQL security, encryption at rest, SSO group sync.
