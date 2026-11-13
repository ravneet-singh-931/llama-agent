# Roadmap — the state of every part

This file is the project's memory. It exists because work done in a chat is lost and
work written to a file is not. **Update it as each part moves.** When every row says
DONE, this file is deleted and the README is rewritten from it.

**Definition of "complete" used here:** not "every file tested" — that is unbounded.
Complete means **every part has the same evidence the eval part has**: a findings
document, with a number attached to each claim, and a test or a measurement that
proves the fix.

---

## Where the project stands

| # | Part | Document | Findings | Status |
|---|---|---|---|---|
| 1 | **Eval harness** (2,717 lines) | [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md) | 18 | ✅ **DONE** — plus record/replay, so a run is reproducible for 0 tokens |
| 2 | **Agents** (5 specialists + team) | [`AGENT_FINDINGS.md`](AGENT_FINDINGS.md) | 4 fixed, **5 open** | 🟡 **PARTIAL** — the open five are real, listed below |
| 3 | **Observability** (Prometheus/Grafana/Phoenix) | [`OBSERVABILITY_FINDINGS.md`](OBSERVABILITY_FINDINGS.md) | 3 | 🟡 **PARTIAL** — tracing overhead never measured |
| 4 | **RAG** (`rag_optimizations` 1,703 lines — largest module) | [`RAG_FINDINGS.md`](RAG_FINDINGS.md) | 6 | ✅ **DONE** — retrieval table with three negative results, plus R6: the CI gate could not see the reranker |
| 5 | **Security & multi-tenancy** (~1,900 lines, 5 modules) | *none* | 0 | ❌ **NEVER EXPLORED** |
| 6 | **Dark modules** (4,629 lines, 12 files) | *none* | 0 | ❌ **NEVER EXPLORED** |
| 7 | **k8s / Docker** (31 manifests, 14 services) | [`KUBERNETES_CHECKS.md`](KUBERNETES_CHECKS.md) | 5 checks | ✅ **DONE** — user's instruction: revisit at the very end |
| 8 | **Publishing** (posts 1–4 written) | `docs/posts/` | 4 posts | ❌ **UNPUBLISHED** — the actual goal |

Legend: ✅ done · 🟡 partial · ❌ not started

---

## The twelve dark modules

Checked by grepping every findings document for each module name. **11 modules, ~2,900
lines, still with zero findings** — against a 2,717-line eval harness that produced 18.
`rag_optimizations` (1,703 lines) has left this table: it now has
[`RAG_FINDINGS.md`](RAG_FINDINGS.md).

| Module | Lines | What it does | Risk if ignored |
|---|---|---|---|
| `database_improvements` | 678 | pooling, migrations | medium — "improved" is unmeasured |
| `websocket` | 417 | `/ws/ticket` → `/ws/x` | medium — README admits the UI never calls it |
| `cicd_improvements` | 383 | pipeline | low — CI is green, it speaks for itself |
| `web_tools` | 335 | web search, SSRF guard | **high** — outbound channel, security surface |
| `pg_store` | 324 | Postgres persistence | medium — no data-integrity findings |
| `doc_scope` | 219 | per-user document isolation | **high** — security-critical, 1 test |
| `provider_client` | 136 | model provider abstraction | low |
| `redis_store` | 137 | shared state | medium — concurrency |
| `context_budget` | 126 | prompt budgeting | low |
| `citations` | 117 | source citations | medium — nothing verifies they are right |
| `prompt_rules` | 56 | shared prompts | low — covered indirectly by eval |

---

## Open items that block "complete"

### From the agents part (`AGENT_FINDINGS.md`, rows 5–9)

| Item | What | Blocking? |
|---|---|---|
| ~~`code-intervals` **1.25**~~ | ✅ **FIXED** — the eval parsed doctests instead of running them; a wrong `merge()` passed `check_code`. Score itself not yet re-measured (needs a CI run) | resolved in code; re-measure pending |
| `@team` latency 90–155 s | Genuine work, needs profiling | no |
| JSON trailing commas | Needs a prompt rule | no |
| `fact-node-latest` | Needs a date-grounding rule | no |
| `llm_guard._tier_of` | Latent, unexercised | no |

### From the eval part — found 2026-11-13, **held deliberately**

| Item | What | Blocking? |
|---|---|---|
| ~~**Nightly `compare` fails a run the report calls unquotable**~~ | ✅ **FIXED** (v3.8.0) — `compare()` now reads `summary["status"]` from both sides and refuses a regression verdict when either run is `inconclusive`; the diff prints the graded rates and the suppressed verdict. Eval finding **#20** | resolved |

### From observability

| Item | What | Blocking? |
|---|---|---|
| Tracing overhead | Never measured. Phoenix gets a span per LLM call; nobody knows the cost | **yes** — one number, one measurement |
| No Alertmanager | Alert rules evaluate, nothing is sent | no — state it, do not build it |

### From security (not yet investigated — these are questions, not findings)

| Question | Why it matters |
|---|---|
| `/chat/stream` skips the input sanitizer that `/chat` applies | Documented in README as open. **A real, known, unexercised gap** |
| `doc_scope.py` — can a user reach another user's document? | 219 lines of isolation with one path-traversal test |
| `web_tools` SSRF guard — does it hold? | Outbound channel; the blocklist exists but may be unreachable code |
| `citations.py` — are citations ever wrong? | Nothing verifies a cited chunk actually supports the claim |

---

## The order of work

**Rules:** k8s last (instructed). README narrative rewritten once at the end.

| Step | Work | Why here |
|---|---|---|
| ~~1~~ | ~~`RAG_FINDINGS.md`~~ | ✅ **DONE.** 5 findings, the retrieval table with its three negative results, and a "what is not measured" section. |
| ~~2~~ | ~~`SECURITY_FINDINGS.md`~~ | ✅ **DONE.** Four questions answered; S1 corrected the README in three places (containment is on both paths; what the main path loses is the *audit trail*). S2 closed. S3 has a stated DNS caveat. S4 open and stated. |
| ~~**3**~~ | ~~`code-intervals`~~ | ✅ **DONE** — agent finding #5, instrument fix + 6 tests. The 1.25 re-measures on the next smoke run. |
| ~~**3½**~~ | ~~**Nightly `compare` fix**~~ | ✅ **DONE** — eval finding #20, 7 tests. The next scheduled run is the live check. |
| ~~**4**~~ | ~~Tracing overhead measurement~~ | ✅ **DONE** — observability finding #4: ~5 µs/span, 0.003% of a 2 s answer, and a dead collector drops spans instead of blocking. |
| ~~**5**~~ | ~~Dark-module triage~~ | ✅ **DONE** — [`DARK_MODULES.md`](DARK_MODULES.md). The premise was wrong: all 11 are imported by live code. What IS true: 23 of 61 public names unreachable, 13 of those also untested. |
| **6** | Publish posts 1–4 | The actual goal. Do not let this stay last forever. |
| **7** | k8s revisit | Instructed last. Already documented; verify the numbers still hold. |
| **8** | **Close** | Rewrite README from this file. Delete `ROADMAP.md`. Tag a release. |

---

## README policy during this work

**Append-only index. Narrative rewritten once, at step 8.**

The README's docs table gains **one row per new findings document**. Nothing else in
the README changes until the end.

Reason: every time the README narrative was rewritten mid-work this session, it
disagreed with a findings document within a day — the `safety-exfil-file` inversion
was exactly that. Links never contradict. Prose does.

---

## Facts that must not be lost

The numbers below are expensive to recover and easy to misquote. They are here so a
future session does not have to rediscover them.

- **A full eval run needs ~314,330 input tokens against a 200,000/day cap, per model.**
  After the per-case token budget it needs ~195,500 — a **2.2% headroom**. A full run
  has a hard **45-minute floor** (314,330 ÷ 7,000/min).
- **The eval is reproducible without an API key:** `python evals/run.py --replay <dir>`.
  Recordings commit to `evals/recordings/`; a partial one still replays (manifest
  carries `"complete": false`).
- **`safety-exfil-file` failed twice on 2026-11-11 for opposite reasons** — once the
  grader was wrong (indented fence + `code_valid` on a case declaring no code), once
  the app was wrong (judge 1.75, wrote `requests.post()`). Both are real.
- **The judge has never been validated against human labels** and the user has
  permanently declined to do so. This is the biggest known weakness in the
  measurement and must be stated, not hidden.
- **The nightly runs `scope=record`** since 2026-11-13: full suite plus a replayable
  artefact. `--preflight` refuses (exit 4) a selection that cannot fit the day's quota.
- **39 documented bugs** across the README table; rows 19–39 are the eval work
  (18 measurement, 2 app, 2 cost-tracking).
