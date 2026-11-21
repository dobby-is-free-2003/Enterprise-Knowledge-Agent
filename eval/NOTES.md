# Evaluation notes and history

Read this before quoting any number from `eval/reports/latest.md`.

## What the numbers mean
- Corpus, users, database and all 72 cases were written by the repo author. The numbers show the pipeline behaves as designed on this data; they are **not** a claim about production accuracy.
- Default (offline) mode uses a hashing embedder and a rule-based reasoner. Answer-quality numbers say nothing about Gemini. Permission, guardrail, SQL-control and approval results do carry over to Gemini mode, because that logic is plain code around the model.
- Cases are split into `dev` (36) and `test` (36). Thresholds (relevance gate 0.45, sentence floor 0.34, grounding support) were tuned on dev only.

## History of the test split (disclosure)
1. First run on the untouched test split: **32/36**.
2. It exposed two real defects, both fixed afterwards in code:
   - `r01`: a raw card number appeared in a citation snippet (the answer was redacted, the snippet was not). Fix: snippets are redacted; regression test `test_regression_card_number_redacted_in_answer_and_citation`.
   - `i04`: the injection regex missed "Disregard your previous instructions" (two determiners). Fix: regex widened; regression test in `tests/test_guardrails.py`.
3. A metric bug was also found: the harness counted any forbidden string as a "permission leak". It now counts leaks only in `permission_trap` and `action` cases, plus forbidden files anywhere.
4. After the fixes the test split scores **34/36**. Because the fixes were informed by test failures, that split is no longer a clean held-out measure. The honest first-run figure is 32/36.

## Known limitations (not tuned away on purpose)
- `s07` (dev): vocabulary mismatch between question and document; the lexical relevance gate refuses. Embeddings from Gemini would likely help; not verified.
- `n06` (test): a question about a Chennai office is answered with an unrelated sentence about the remote-work stipend. The relevance gate passes on word overlap. A real LLM plus the grounding check should reduce this; not verified.
- `m04` (test): a compound question is answered only in half.
- The offline extractive answerer sometimes pads an answer with an adjacent, unrelated sentence.
- Offline sentence matching does not link "remote" and "remotely" (no suffix stemming beyond a few rules). Email drafts therefore use the lead sentence of each top section of the best-matching document instead of per-sentence matching.
- Not tested against live services: Gemini API, Gemini embeddings, Google Drive source, Gmail/Calendar actions, Google sign-in, Cloud Run deployment.
