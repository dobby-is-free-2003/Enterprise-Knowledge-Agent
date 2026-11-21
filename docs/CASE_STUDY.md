# Case study: a knowledge agent that cannot leak

**Problem.** A company wants employees to ask questions of HR, security, sales and finance documents and data. The risk is not wrong answers alone; it is a correct answer given to the wrong person (salary bands, one-on-one notes), or an assistant that obeys instructions hidden in a document.

(Demo data: seven users across engineering, HR, sales, finance, support, exec and an intern.)

**Approach.** Treat the model as untrusted. Enforce permissions in the index query and again at the source, build SQL results and approvals from code, refuse when evidence is weak, and gate every side effect behind human approval.

**Evidence.** 94 automated tests and a 72-case evaluation with trap cases (same question, different users), injection documents, hostile SQL and exfiltration attempts: 0 permission leaks. The held-out split first scored 32/36 and exposed two real defects (card number in a citation snippet; an injection phrasing the filter missed). Both were fixed with regression tests and disclosed.

**Limits.** Synthetic data and author-written cases; offline stand-ins for the model; live Google integrations untested.

**What I would do next.** Run the same eval with Gemini and embeddings, compare answer quality, add a second reviewer-written case set, and pilot on a real Drive folder with consenting users.
