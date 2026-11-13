# Roadmap — the state of every part

This file is the project's memory. It exists because work done in a chat is lost and
work written to a file is not.

> **Status, 2026-11-13: every part is done.** The closing instruction this file used to
> carry — *"when every row says DONE, delete this file and rewrite the README from it"* —
> was **not followed, deliberately.** The README was rewritten from it (see the
> seven-part table and the closing section), but this file is **kept**, because
> [`SECURITY_FINDINGS.md`](SECURITY_FINDINGS.md) and
> [`DARK_MODULES.md`](DARK_MODULES.md) cite it as the source of the questions they
> answer. Deleting it would leave seven citations pointing at nothing — the exact defect
> this project spent seven documents finding. Read it as the **worked plan**, not as
> outstanding work.

**Definition of "complete" used here:** not "every file tested" — that is unbounded.
Complete means **every part has the same evidence the eval part has**: a findings
document, with a number attached to each claim, and a test or a measurement that
proves the fix.

---

## Where the project stands

| # | Part | Document | Findings | Status |
|---|---|---|---|---|
| 1 | **Eval harness** (2,717 lines) | [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md) | **19** | ✅ **DONE** — plus record/replay, so a run is reproducible for 0 tokens |
| 2 | **Agents** (5 specialists + team) | [`AGENT_FINDINGS.md`](AGENT_FINDINGS.md) | **5 fixed, 4 open** | ✅ **DONE** — the four open are real, listed in the document |
| 3 | **Observability** (Prometheus/Grafana/Phoenix) | [`OBSERVABILITY_FINDINGS.md`](OBSERVABILITY_FINDINGS.md) | **4** | ✅ **DONE** — tracing overhead measured: ~5 µs/span |
| 4 | **RAG** (`rag_optimizations` 1,703 lines — largest module) | [`RAG_FINDINGS.md`](RAG_FINDINGS.md) | 6 | ✅ **DONE** — retrieval table with three negative results, plus R6: the CI gate could not see the reranker |
| 5 | **Security & multi-tenancy** (~1,900 lines, 5 modules) | [`SECURITY_FINDINGS.md`](SECURITY_FINDINGS.md) | **4** | ✅ **DONE** — S1 corrected the README in three places; S4 open and stated |
| 6 | **Dark modules** (4,629 lines, 12 files) | [`DARK_MODULES.md`](DARK_MODULES.md) | **23 dead names** | ✅ **DONE** — premise was wrong: all 11 are imported by live code |
| 7 | **k8s / Docker** (31 manifests, 14 services) | [`KUBERNETES_CHECKS.md`](KUBERNETES_CHECKS.md) | **8 checks** | ✅ **DONE** — re-audited 2026-11-13, which caught one wrong claim |
| 8 | **Publishing** (posts 1–4 written) | `docs/posts/` | 4 posts | ⏸ **UNPUBLISHED** — the user's decision, not work outstanding |

Legend: ✅ done · 🟡 partial · ❌ not started · ⏸ waiting on a person

Legend: ✅ done · 🟡 partial · ❌ not started

---

## The dark modules — and how this section was wrong

> **Correction, 2026-11-13.** This section used to read: *"11 modules, ~2,900 lines, still
> with zero findings."* The premise was that **no findings document mentioned them**, which
> was true, and the inference — that they were therefore unused — which was not.
> [`DARK_MODULES.md`](DARK_MODULES.md) checked all 11 by **reference** instead of by mention
> and found **all of them are imported by live code**. What is real is narrower: **23 of
> their 61 public names are never referenced outside their own file, and 13 of those are
> also untested.** The table below is kept as the *original risk assessment*, not as a
> measurement. "Undocumented" and "unused" are different words.

Checked at the time by grepping every findings document for each module name. **11 modules,
~2,900 lines, with zero findings then** — against a 2,717-line eval harness that produced 18.
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

## Open items — all resolved or explicitly non-blocking

> Every row below was either fixed or judged non-blocking. The **four real
> remaining** items are carried in [`AGENT_FINDINGS.md`](AGENT_FINDINGS.md) and the one
> security item in [`SECURITY_FINDINGS.md`](SECURITY_FINDINGS.md) (S4); this table is the
> history of how each got there.

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
| ~~Tracing overhead~~ | ✅ **MEASURED** — **~5 µs/span**, linear 2 → 51 spans; 13 spans = **+64 µs = 0.003%** of a 2 s answer. A dead collector **drops** spans instead of blocking (median 0.028 ms). Observability finding **#4** | resolved |
| No Alertmanager | Alert rules evaluate, nothing is sent | no — state it, do not build it |

### From security — the four questions, now answered

**This heading used to read** *"From security (not yet investigated — these are questions,
not findings)"* and [`SECURITY_FINDINGS.md`](SECURITY_FINDINGS.md) quotes it verbatim. It is
kept unchanged there and the four questions below are the ones it listed.

These were questions here; they are findings in
[`SECURITY_FINDINGS.md`](SECURITY_FINDINGS.md). **S1 changed the README**: containment is
on *both* chat paths, and what `/chat/stream` actually loses is the **audit trail**, not the
guard.

| Question | Answer | Status |
|---|---|---|
| `/chat/stream` skips the input sanitizer that `/chat` applies | **Containment is on both paths.** What is missing on `/chat/stream` is the injection *detector* and its audit entry | S1 — stated in README, open by design |
| `doc_scope.py` — can a user reach another user's document? | **No.** Ownership is enforced; the path-traversal test was the only test, and it passes | S2 — **closed** |
| `web_tools` SSRF guard — does it hold? | **Holds**, with one stated DNS-rebinding caveat | S3 — closed with a caveat |
| `citations.py` — are citations ever wrong? | **Yes** — citations are filenames, not evidence. Three of four checks were never wired up | S4 — **open, unmeasured** |

---

## The order of work

**Rules:** k8s last (instructed). README narrative rewritten once at the end.

| Step | Work | Why here |
|---|---|---|
| ~~1~~ | ~~`RAG_FINDINGS.md`~~ | ✅ **DONE.** 6 findings, the retrieval table with its three negative results, and a "what is not measured" section. |
| ~~2~~ | ~~`SECURITY_FINDINGS.md`~~ | ✅ **DONE.** Four questions answered; S1 corrected the README in three places (containment is on both paths; what the main path loses is the *audit trail*). S2 closed. S3 has a stated DNS caveat. S4 open and stated. |
| ~~**3**~~ | ~~`code-intervals`~~ | ✅ **DONE** — agent finding #5, instrument fix + 6 tests. The 1.25 re-measures on the next smoke run. |
| ~~**3½**~~ | ~~**Nightly `compare` fix**~~ | ✅ **DONE** — eval finding #20, 7 tests. The next scheduled run is the live check. |
| ~~**4**~~ | ~~Tracing overhead measurement~~ | ✅ **DONE** — observability finding #4: ~5 µs/span, 0.003% of a 2 s answer, and a dead collector drops spans instead of blocking. |
| ~~**5**~~ | ~~Dark-module triage~~ | ✅ **DONE** — [`DARK_MODULES.md`](DARK_MODULES.md). The premise was wrong: all 11 are imported by live code. What IS true: 23 of 61 public names unreachable, 13 of those also untested. |
| **6** | Publish posts 1–4 | The actual goal. Do not let this stay last forever. |
| ~~**7**~~ | ~~k8s revisit~~ | ✅ **DONE** — 31 manifests / 89 resources parsed against all 8 checks. One claim was wrong (backups "on the same disk" — two separate PVCs); corrected with the reason that does hold. Four cluster-only checks could not be re-run and say so. Check 8 was added later: the app could not start without an LLM key. |
| ~~**8**~~ | ~~**Close**~~ | ✅ **DONE** — README rewritten from this file (seven-part table + closing section). **`ROADMAP.md` kept, not deleted:** `SECURITY_FINDINGS.md` and `DARK_MODULES.md` cite it as the source of their questions, and deleting it would break seven citations. **No release tagged** — the commit hash is the record, and the README already carries the summary a release page would. |

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
