# Decisions

1. **Offline mode as a first-class citizen.** Deterministic stand-ins let the whole system, tests and evaluation run with no key or network, and let CI fail on any permission leak. Cost: offline answer quality is not representative of Gemini.
2. **Filter at the store and re-check at the source.** The index can be stale; Drive is the source of truth. A second check closes the revocation gap at the price of one extra lookup per hit.
3. **Relevance gate before generation; refuse over guess.** A wrong confident answer is worse than "not found". Threshold 0.45 chosen on dev; 0.40 produced a wrong answer on a dev case.
4. **Build the response from tool state, not model text (ADK).** The model cannot fabricate SQL results, approvals or permissions.
5. **Approval gate for every side effect.** Drafts only; the user approves; policy is re-checked then.
6. **Plain-code controls around the model.** Permission, SQL, redaction and approval logic is ordinary tested code, so it holds regardless of the model.
7. **Pipeline runtime default, ADK optional.** The pipeline is simpler to evaluate and debug; ADK demonstrates the multi-agent form of the same contract.
8. **Qdrant and in-memory stores.** Vertex Vector Search is paid; Qdrant has a free tier and runs locally.
9. **No suffix stemmer beyond a few rules.** Left as a documented limit rather than tuned on the test split.
