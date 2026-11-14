# Code quality findings

Eight findings about the codebase itself: over-engineering, wiring that was never
connected, silent error handling, and dead files. Same shape as the other
documents — symptom, cause, fix, proof — with one addition: **what the check
would have missed**, because four of these eight looked correct on inspection.

The through-line: **every one of these was a control that existed and was not
connected.** Nothing was missing from the repository.

---

## 1. `database_improvements.py`: 678 lines, five classes, no callers — and it ran on import

**Symptom.** A 678-line module named as though it were core infrastructure.
Importing it created `backups/` and `migrations/` directories in whatever
directory the process happened to start in.

**Cause.** `MigrationManager`, `BackupManager`, `ConnectionPool`, `QueryOptimizer`
and `create_initial_migrations` had **zero** references in any non-test file. Three
of them instantiated module-level singletons whose `__init__` called
`os.makedirs(...)`. So the module **did work on import** — in every container, on
every process start — for code nobody called. Only `db_health_check` was wired
(`api.py:476`, imported inside the `/health` handler).

**Fix.** 678 → **104** lines. The three singletons went with the dead classes.

**Proof.** `tests/test_enterprise.py::test_dead_database_managers_stay_deleted`
asserts the five dead names stay deleted, then **re-imports the module in a
temporary cwd and requires that directory to still be empty** — so the import
side-effect cannot come back. File coverage 29% → 74%.

**The lesson.** Dead code that is never called is untidy. Dead code that **runs at
import** executes whether or not anyone asked it to, and it executes as root, in
production, at startup.

---

## 2. Two feature-flag systems, and 7 of the 8 shipped flags were never read

**Symptom.** `config/feature_flags.json` shipped eight flags. Setting seven of them
to `false` changed nothing.

**Cause.** Zero `is_enabled(...)` call sites for `enable_advanced_rag`,
`enable_phoenix_tracing`, `enable_caching`, `enable_rate_limiting`,
`enable_websockets`, `enable_file_upload`, `enable_beta_features`. Meanwhile
`modules/config.py::FeatureFlags` read a *different* set from `FEATURE_*` env
vars. Two knobs per concept, and **neither one gated the behaviour**: `api.py`
calls `rate_limiter.is_rate_limited()` unconditionally.

**Fix.** The seven deleted. `enable_mcp_agents` kept — it is genuinely read twice
(`api.py:2690`, `api.py:2713`).

**Proof.** `test_every_shipped_flag_is_actually_read` fails with
`flags configured but never read by any code: ['enable_beta_features']` when one
is added back.

**The lesson.** A configuration file is not a feature. A flag that no code asks
about is documentation of an intention, and it reads like a working switch.

---

## 3. `validate_fact_key` / `validate_fact_value`: limits declared, enforced nowhere

**Symptom.** `modules/security.py` declares `MAX_FACT_KEY_LENGTH = 100` and
`MAX_FACT_VALUE_LENGTH = 1000`, and defines validators directly beneath them.
Neither was called from anywhere.

**Why it survived an audit.** `POST /remember` *was* capped — by its Pydantic
model. An audit that stopped at the endpoint would have concluded the limits were
live. But **six other call sites** build a fact from free text
(`key, value = content.split("=", 1)`), where `content` is a chat message capped
at **10,000** characters. A 10,000-character chat message became a
10,000-character fact, stored forever, and injected into every later prompt.

**Fix.** Enforced in `FactMemory.remember()`, the single function every caller
goes through — so the chat path is covered as well as the endpoint.

**Proof.** `tests/test_security.py::TestFactInputLimits` — 5 tests: oversized key,
oversized value via the **chat** path, empty value, the unaffected happy path, and
the endpoint's own 422.

**The lesson.** Enforce a limit at the one function every caller passes through,
not at the endpoint where it was first noticed. The endpoint was the *least*
exposed of the seven paths.

---

## 4. `modules/services.py`: 84 lines kept alive by a test that existed to import it

**Symptom.** A service locator (`Services`, `get_services`, `reset_services`)
exposing lazy singletons for database, RAG, memory, facts and websockets.

**Cause.** Zero importers and zero references anywhere outside a test named
`test_services_singleton` (deleted with the module), whose only assertion was
`get_services() is get_services()`. Its only other inbound reference in the whole
repository was a row in `docs/DARK_MODULES.md`'s own table.

**Fix.** Module deleted. `broadcast_message`, `get_online_count` and
`reload_config` deleted with it, and `test_enterprise.py` now asserts these names
stay deleted.

**The lesson.** A test that exists only to import a module is not coverage; it is
a reason the module cannot be deleted. Deleting the test alone would have removed
the evidence that it was unused.

**Note.** `.gitignore` still contained `migrations/` because *this* module used to
create that directory at import. That rule was removed when Alembic took the name
— see `MIGRATIONS.md`.

---

## 5. 60 silent exception handlers, 41 with no comment

**Symptom.** The audit recorded 32 `except Exception: pass` handlers. An AST scan
found **60** whose body is a single `pass` or `continue`, **41 with no comment**.

**Why this matters even though none were bugs.** All 60 turned out to be
deliberate fallbacks. But `except Exception: pass` is **textually identical** to a
handler written to hide a bug. Nothing in the code told a reader which one they
were looking at, so every future reader had to re-do the analysis.

**Fix.** `modules/silent.py`. `degrade(what, exc)` logs at DEBUG and takes the
explanation as a **required argument**, so it cannot be dropped by a formatter or
a careless edit. `STRICT` mode raises `DegradedError` so tests can assert that a
fallback actually fired. No control-flow change.

59 converted across 19 files, each message stating the **consequence** rather than
the exception:

```python
degrade("redis rate-limit check failed (per-process in-memory fallback)", _e)
degrade("tool call not written to the audit log", _e)
```

**Status now.** 7 bare handlers remain repo-wide and **none are in application
code**: 2 in `tests/`, 3 in `evals/`, 2 elsewhere — all deliberate.

**The lesson.** The comment is the artefact. An `except: pass` with a reason is
engineering; without one it is indistinguishable from concealment, and the next
reader has no way to tell.

---

## 6. 31 unused locals (F841), 7 of them real dead assignments

**Symptom.** flake8 selected on `E9,F63,F7,F82` only, so unused local variables
were invisible. Enabling `F841` produced 31 hits.

**Cause.** 24 were assignment-for-side-effect patterns that were harmless. **7
were real**: a value computed and never used, which means either the computation
was pointless or — worse — **a result was being discarded that the author
intended to use**.

**Fix.** All 7 examined individually; each either wired up or removed.

**The lesson.** A lint rule that is not enabled is a class of bug that cannot be
found. The interesting number is not 31, it is 7 — a rule with a 23% true-positive
rate is still worth enabling if the 7 are real.

---

## 7. The Streamlit legacy UI: ~1,900 lines

**Symptom.** Two user interfaces. Next.js was the one being developed; Streamlit
was still shipped, containerised, routed and documented.

**Cause.** Nothing. This is leftover, not broken.

**Fix.** Deleted: `app.py` (1,202 lines), `mcp_client_helper.py` (230, whose only
consumer was `app.py`), `k8s/web.yaml` (~210), the compose service, the tests for
the deleted client, `streamlit==1.66.1` from requirements, and
`AppConfig.streamlit_port` (read by nothing). Plus the wiring that pointed at it:
the nginx `web_backend` upstream and `/streamlit/` location, the ingress path,
the networkpolicy rule for port 8501, and eight documentation references.

**Proof.** The full suite passes; the frontend build is unaffected; no remaining
reference to port 8501 or `streamlit` in application code.

**The lesson.** Deleting a feature is not one commit in one file. The cost is in
the wiring that still points at it, and that is what makes it survive.

---

## 8. The route split: five steps done, then stopped on purpose

**Symptom.** `api.py` was 2,700+ lines in one file.

**Fix, partial.** Five commits extracted in dependency order: zero-dependency
helpers → the document cluster → request identity → streaming helpers to
`api_utils`.

**Then it stopped**, by decision ("go for A for now"). Recorded here so it is not
mistaken for an oversight. Splitting further touches every route at once, and the
existing tests exercise routes end-to-end rather than by module — safe, but slow,
and not the highest-value work available.

**The lesson worth keeping.** A refactor that is 80% done and *documented as
stopped* is fine. The same refactor 80% done and undocumented is a trap for
whoever reads the file next and assumes the structure is intentional.

---

## The pattern

Six of these eight were **present and unplugged**:

| present | connected to |
|---|---|
| `database_improvements.py` | nothing (but ran at import) |
| 7 feature flags | nothing |
| `validate_fact_key` | nothing |
| `services.py` | only a test importing it |
| Streamlit UI | nothing developing it |
| the route split | stopped at 5 of 6 |

Reading the code confirms the part exists. **Only breaking it shows whether it is
wired.** That is why every finding above carries a test that fails when the fix is
reverted, and why "it's there in the code" is not evidence.
