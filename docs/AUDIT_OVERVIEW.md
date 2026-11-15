# The audit, part by part

This is the one document to read if you want to know **what was done, in what
order, and whether it is finished**. Every part below is a real work session with
commits behind it. Nothing here is a plan; if it says fixed, there is a test that
fails when the fix is reverted.

Read top to bottom for the story, or jump to a part number.

| Part | Area | Verdict | Document |
|---:|---|---|---|
| 1 | Verification bugs (the tests that could not fail) | **Fixed** | [CI_FINDINGS](CI_FINDINGS.md) |
| 2 | Coverage holes | **In progress** | [COVERAGE_API](COVERAGE_API.md) |
| 3 | Over-engineering | **Fixed** | [CODE_QUALITY](CODE_QUALITY.md) |
| 4 | Wiring (env vars, secrets, unused symbols) | **Fixed** | [CODE_QUALITY](CODE_QUALITY.md) |
| 5 | Correctness (silent handlers, blocking writes) | **Fixed** | [CODE_QUALITY](CODE_QUALITY.md) |
| 6 | Dead code | **Fixed** | [DARK_MODULES](DARK_MODULES.md) |
| 7 | Per-folder verdict | **Done** | this page |
| 8 | Feature flags | **Fixed** | [CODE_QUALITY](CODE_QUALITY.md) |
| 9 | Route split (`api.py`) | **Deferred by decision** | [CODE_QUALITY](CODE_QUALITY.md) |
| 10 | Clean — verified, do not re-audit | **Done** | this page |
| 11 | My own errors | **Kept deliberately** | [CASE_STUDIES](CASE_STUDIES.md) |
| 12 | The pattern behind it all | **—** | [CASE_STUDIES](CASE_STUDIES.md) |
| 13 | What is next | **Open** | [ROADMAP](ROADMAP.md) |

---

## Part 1 — Verification bugs: tests that pass no matter what

**Why this came first.** A wrong test that fails is cheap. A wrong test that
*passes* is the expensive kind: it reports "checked" for something nobody
checked, and the README then cites it as proof.

**Found and fixed:**

- `test_cors_headers_present` built a list of middleware, **discarded it**,
  printed a checkmark, and passed. Deleting `CORSMiddleware` from `api.py`
  entirely left it green. Underneath that non-test, development ran
  `allow_origins=["*"]` with `allow_credentials=True`, which browsers turn into
  a reflected origin **plus** credentials — any site the developer visited could
  make authenticated cross-origin calls to their API.
- The CI `integration` job hand-listed five unpinned `pip install` lines while
  the `test` job used `requirements.txt`. It installed the newest `mcp<2`
  instead of the pinned `1.30.0`, plus a standalone `fastmcp` that nothing
  imports. The job could stay green while the image broke.

**Proof.** Sabotaging `add_middleware` now fails the CORS test with `assert []`.
`curl -H 'Origin: https://evil.example'`: header reflected → **absent**.

---

## Part 2 — Coverage holes

**The problem.** The repo reported a coverage number and never gated on it, so it
could fall and stay green while the build passed.

**Fixed so far.** `--cov-fail-under=74` in CI, with the floor set 4 points below
the measured value as a ratchet. `api.py` went
**46% → 51% → 55.5% → 58.7% → 61.9% → 64.4% → 67% → 70% → 71%**. The order matters more than the
number: conversations, `/chat/stream`, WebSocket/startup, then the agent and
`@team` streaming bodies and the uploads re-index.

**Still open.** `api.py` is 71%. The remaining gap is measured, not guessed —
see `COVERAGE_API.md`, including the correction that it is **not** "mostly error
branches" (only 18% is).

**The honest framing:** line coverage counts executed lines. It does not know
which lines decide who is allowed to see what. Ten tests over an ownership check
are worth more than a hundred over an `except` block.

---

## Part 3 — Over-engineering

**Found:** `modules/database_improvements.py` was **678 lines** of which
`MigrationManager`, `BackupManager`, `ConnectionPool`, `QueryOptimizer` and
`create_initial_migrations` had **zero** references in any non-test file. Three
had module-level singletons that called `os.makedirs()` in `__init__`, so merely
**importing** the module created `backups/` and `migrations/` in the working
directory — on every process start, in every container.

**Fixed:** 678 → 103 lines. Dead code that is never called is untidy; dead code
that runs at import executes whether or not anyone asked it to.

**Proof.** A test re-imports the module with a temporary cwd and requires the
directory to stay empty. File coverage 29% → 74%.

---

## Part 4 — Wiring

**Checked, and this is where "checked and clean" is worth recording** so nobody
audits it twice.

| Thing | Claim | Result |
|---|---|---|
| Env vars read by code | 111 read, 76 undocumented (68%) | **Fixed** — now 107 read, 2 not in `.env.example`, and both are false positives (`RERANKER_TEST_MODEL_DIR` is used only by a test; `"X"` is inside a docstring) |
| Docker secrets vs k8s Secrets | two mechanisms | Correct on both, documented |
| 29 flagged "tests importing missing symbols" | real breakage? | **0 real** |
| `evals/checks.py` 5 names flagged unused | dead? | All 5 wired |
| 24 Prometheus alert rules | real metrics? | All 24 real |
| `scripts/` | audited | One file, correctly wired |

**The lesson:** "flagged unused" is a hypothesis, not a finding. Every one of
these needed checking before being reported, and most were wrong.

---

## Part 5 — Correctness

**Found:** the audit recorded 32 `except Exception: pass` handlers. An AST scan
found **60** whose body was a single `pass` or `continue`, **41 with no
comment**. All 60 were deliberate fallbacks — but `except: pass` is textually
identical to a handler written to hide a bug, so nothing told a reader which one
they were looking at.

**Fixed:** `modules/silent.py`. `degrade(what, exc)` logs at DEBUG and **takes
the explanation as a required argument**, so it cannot be dropped by a formatter
or a lazy edit. 59 converted across 19 files, each message stating the
consequence rather than the exception ("redis rate-limit check failed
(per-process in-memory fallback)").

**Status now:** `7` bare `pass`/`continue` handlers remain repo-wide, and
**none are in application code** — 2 in `tests/`, 3 in `evals/`, 2 elsewhere.

---

## Part 6 — Dead code

**Found:** 23 of 61 public names unused across modules, 13 of them also
untested. **Deleted, not just counted:** `services.py` (84 lines, kept alive by
a test whose only assertion was `get_services() is get_services()`),
`database_improvements` dead classes, 3 dead functions, 7 unread feature flags,
and the entire Streamlit UI (~1,900 lines).

**The trap that was avoided:** low coverage is not the same as dead code.
`PhoenixTracer` is constructed nowhere — but the live tracing integration is
`api.py`'s lifespan under a different name. **Grep for the capability, not the
class name.**

---

## Part 7 — Per-folder verdict

| Folder | Verdict |
|---|---|
| `modules/` | Audited, heavily tested, dead code removed |
| `api.py` | Split partially (5 commits), remaining split deferred, 55.5% covered |
| `evals/` | 19 findings, all with proof |
| `k8s/` | Out of scope by decision; `KUBERNETES_CHECKS.md` records what was verified |
| `frontend/` | **0 tests across 34 `.tsx` files** — the largest open gap |
| `scripts/`, `config/` | Audited, correct |

---

## Part 8 — Feature flags

**Found:** `config/feature_flags.json` shipped **8 flags, 7 of which no code ever
read**. Setting any to `false` changed nothing. Three names also existed in a
*second* flag system (`modules/config.py::FeatureFlags` from `FEATURE_*` env
vars), so there were two knobs per concept and neither gated the behaviour.

**Fixed:** the 7 deleted; `enable_mcp_agents` kept because it is genuinely read
twice. A test now fails if a flag is ever added that no code asks about.

**Status:** `config/feature_flags.json` contains exactly one flag, and it has two
real call sites (`api.py:2690`, `api.py:2713`).

---

## Part 9 — The route split (deferred)

`api.py` was 2,700+ lines. Five commits extracted zero-dependency helpers,
the document cluster, request identity, and streaming helpers. **Then it was
stopped by decision** ("go for A for now").

This is not an oversight. Splitting further touches every route at once, and the
current tests cover routes end-to-end rather than by module, so a split is safe
but slow. It is recorded as deferred so it is not silently forgotten.

---

## Part 10 — Checked and clean (do not re-audit)

Recorded so the next session does not repeat the work:

- 29 "tests importing missing symbols" — **0 real**
- `evals/checks.py` 5 flagged names — all wired
- All 24 Prometheus alert rules reference metrics that exist
- `scripts/` and `config/` — correctly wired
- 2 feature flags → 1 real flag, both call sites real

---

## Part 11 — My own errors

Kept deliberately, because it is the most useful section in the audit. Full
write-ups in `CASE_STUDIES.md`. The short list:

1. Called the Docker job "decorative." It builds on every main push. **Verify with
   `gh run view --json jobs` before calling a job fake.**
2. Reported "mostly error branches." Measured: **18%**.
3. Wrote a `/facts` sabotage that patched the **first of five** identical lines —
   a different handler. The test passed an unchanged program.
4. Assumed a passing sabotage meant a weak test, when `delete_conversation`
   simply checks ownership **twice**.
5. Reported a finding that was **already documented** — not a finding.

---

## Part 12 — The pattern behind all of it

Nearly every real bug in this audit had the same shape: **a control that was
present but not connected.**

- A test present, asserting nothing.
- A validation function present, called from nowhere.
- A feature flag present, read by nothing.
- A CI job present, installing a different dependency set.
- A `create_all` call present, unable to add a column.
- A reranker present, falling back to keyword overlap silently.

Nothing was missing. Everything was *there*, and unplugged. That is why the audit
method had to be sabotage rather than inspection: reading the code confirms the
part exists. Only breaking it shows whether it is wired.

---

## Part 13 — What is next

See [`ROADMAP.md`](ROADMAP.md). Current order:

1. `/chat/stream` — 133 uncovered lines, the path every user touches
2. WebSocket ticket lifecycle — a re-usable ticket would be an auth hole
3. `lifespan` failure paths — startup with the database down
4. **Frontend tests — 0 across 34 files.** Deferred by decision, largest gap.

## The numbers, as of this document

| | |
|---|---|
| Tests | **1070 passed, 6 skipped** |
| `api.py` coverage | **64.4%** (was 46% when this audit started) |
| Repo coverage (CI scope `--cov=.`) | **85%**, gate at 74 |
| Test files | 51 |
| Frontend tests | **0 across 34 `.tsx` files** |
| CI | 7 jobs, all green; 3 deploy jobs skipped on purpose |
