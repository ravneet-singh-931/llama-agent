# The findings documents — read this first

Every part of this project has its own document. They are not summaries: each
finding is **symptom → cause → fix → the test or number that proves it**. If a
claim in these files had no proof, it would not be here.

This page is the map. Start at the table, then open the one document that
interests you. You do not need to read them in order.

## The documents

| # | Document | What it covers | Findings |
|---|---|---|---:|
| 0 | [`AUDIT_OVERVIEW.md`](AUDIT_OVERVIEW.md) | **The whole audit across every session**, in order, with the verdict on each part | 13 parts |
| 1 | [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md) | The measuring stick: 60 cases, judges, grounding, regression detection | 19 |
| 2 | [`AGENT_FINDINGS.md`](AGENT_FINDINGS.md) | The five agents, the critic, the team orchestrator | 5 fixed, 4 open |
| 3 | [`OBSERVABILITY_FINDINGS.md`](OBSERVABILITY_FINDINGS.md) | Tracing, metrics, the log module, alert rules | 9 |
| 4 | [`RAG_FINDINGS.md`](RAG_FINDINGS.md) | Retrieval, reranking, caches, cross-user leaks | 6 |
| 5 | [`SECURITY_FINDINGS.md`](SECURITY_FINDINGS.md) | Auth, CORS, containment, PII, fact input limits, WebSocket tickets | 10 |
| 6 | [`DARK_MODULES.md`](DARK_MODULES.md) | 23 dead public names, verified by reference not line count | 23 + addenda |
| 7 | [`KUBERNETES_CHECKS.md`](KUBERNETES_CHECKS.md) | 9 live-cluster checks against all 31 manifests | 9 |
| 8 | [`CODE_QUALITY.md`](CODE_QUALITY.md) | Env vars, silent handlers, unused locals, the Streamlit deletion, the route split | 8 |
| 9 | [`E2E_JOURNEY.md`](E2E_JOURNEY.md) | The first end-to-end test, and the four wrong assumptions it caught | 4 |
| 10 | [`MIGRATIONS.md`](MIGRATIONS.md) | `create_all` cannot add a column; Alembic baseline; the logging bug | 3 |
| 11 | [`COVERAGE_API.md`](COVERAGE_API.md) | What is missing in `api.py`, measured; ownership, `/chat/stream`, agent/team bodies, uploads re-index, WebSocket, startup | 8 |
| 12 | [`CASE_STUDIES.md`](CASE_STUDIES.md) | The long-form write-ups of the bugs worth reading in full | 6 |
| 13 | [`CI_FINDINGS.md`](CI_FINDINGS.md) | The pipeline: what it claimed, what it did, and the three times it broke | 8 |
| 14 | [`MY_ERRORS.md`](MY_ERRORS.md) | **My own eleven mistakes**, kept deliberately — the audit applies to the auditor | 11 |
| — | [`ROADMAP.md`](ROADMAP.md) | What is done, what is next, what is deliberately not being done | — |
| — | [`SLO.md`](SLO.md) | Chat latency SLOs per route | — |

## Where to start, depending on why you are here

- **"What has actually been done?"** → [`AUDIT_OVERVIEW.md`](AUDIT_OVERVIEW.md).
  One page, every part, in the order it happened, with its status.
- **"Show me the worst bug."** → [`CASE_STUDIES.md`](CASE_STUDIES.md) #1
  (a test that could not fail while CORS was misconfigured) and #2 (a card
  number from an uploaded document reaching the browser).
- **"Is my data safe from other users?"** → [`COVERAGE_API.md`](COVERAGE_API.md)
  and [`SECURITY_FINDINGS.md`](SECURITY_FINDINGS.md).
- **"Can I trust the test numbers?"** → every document ends with sabotage
  results. If a specific test is named, it was broken on purpose and watched
  failing before the fix was accepted.

## The rule these documents follow

Three things are not allowed here, and each one was a real mistake that got
caught and corrected:

1. **A number that was not measured.** "Mostly error branches" was written for
   weeks; when it was finally counted it was 18%. That correction is in
   `COVERAGE_API.md`.
2. **A check that was never seen to fail.** `test_cors_headers_present` printed
   a checkmark and passed with CORS deleted. A green test is evidence of a test;
   only a failing sabotage is evidence of a check.
3. **A finding that was already documented.** Re-reporting a known issue as new
   work is how a backlog stops meaning anything.

## What is deliberately not covered

- **`k8s/`** — out of scope by decision. `KUBERNETES_CHECKS.md` records what was
  verified before that decision.
- **Deploy to staging/production** — the jobs are skipped on purpose while work
  goes elsewhere. They are not broken. See `CI_FINDINGS.md` #7.
- **Frontend tests** — currently **0 across 34 `.tsx` files**. Deferred, not
  forgotten. It is the single largest remaining gap.
