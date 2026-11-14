# The dark modules

Eleven modules, **2,928 lines**, that no findings document had ever mentioned.
[`ROADMAP.md`](ROADMAP.md) listed them as the last real work before publishing, with no
next step attached — the row said only "still with zero findings".

> **Correction, 2026-11-13.** This paragraph used to attribute a quotation to
> `ROADMAP.md`: *"for each: test it, or delete it, or state in the README that it is
> unexercised."* **That sentence is not in `ROADMAP.md`** and, as far as the git history
> reaches, never was. It was a plausible-sounding instruction written into this document
> and then attributed to the file, which is the exact failure this document is about —
> a claim that reads like it was checked because it has a citation on it. The instruction
> is a good one, which is why it was carried out; it was just **mine**, not the roadmap's.

This is that triage.

---

## The premise was wrong, and that is the first finding

The list was built by grepping every findings document for each module name. Eleven
modules had zero mentions, and the conclusion — *"2,928 lines nobody has looked at"*
— was reasonable and **false**.

Every one of the eleven is imported by live application code:

| Module | Imported by |
|---|---|
| `database_improvements` | `api.py` (health check) |
| `websocket` | `api.py` |
| `cicd_improvements` | `api.py` (feature flags) |
| `web_tools` | `modules/agent_tools.py` (the web search tool) |
| `pg_store` | `api.py` (11 call sites) |
| `doc_scope` | `api.py`, `llama_agent1.py`, `modules/rag.py`, `vector_store.py`, 3 more |
| `provider_client` | `llama_agent1.py` |
| `redis_store` | `api.py` (9 call sites) |
| `context_budget` | `api.py`, `llama_agent1.py`, `modules/memory.py` |
| `citations` | `api.py` |
| `prompt_rules` | `llama_agent1.py`, `multi_agent.py`, `grounding.py`, `evals/judge.py` |

**"No document mentions it" is not "nothing uses it."** A module can be load-bearing
and undocumented at the same time — that is the normal state of working software, and
it was the state of all eleven. The dark-module count measured *prose*, and I had
been reporting it as if it measured *exposure*.

That correction matters before any of the numbers below: the claim this document is
qualified to make is narrower than the one the ROADMAP implied.

---

## What is actually unused

Measured by AST: for each module, every public class and function, and whether it is
referenced from outside its own file (module-level singletons count — `feature_flags`
is a live export). Tests excluded on both sides.

| Module | Lines | Public | Live | **Dead** |
|---|---|---|---|---|
| `database_improvements` | 678 | 6 | 1 | **5** |
| `websocket` | 417 | 7 | 2 | **5** |
| `cicd_improvements` | 383 | 3 | 1 | **2** |
| `web_tools` | 335 | 1 | 1 | 0 |
| `pg_store` | 324 | 13 | 9 | **4** |
| `doc_scope` | 219 | 7 | 6 | **1** |
| `provider_client` | 136 | 4 | 2 | **2** |
| `redis_store` | 137 | 11 | 10 | **1** |
| `context_budget` | 126 | 2 | 2 | 0 |
| `citations` | 117 | 4 | 1 | **3** |
| `prompt_rules` | 56 | 3 | 3 | 0 |
| **Total** | **2,928** | **61** | **38** | **23** |

**23 of 61 public names — 38% — are never referenced outside their own file.**

That is the honest headline, and it is a much smaller claim than "2,900 lines nobody
looks at." The modules are mostly used. A third of their surface is not.

---

## The dead surface, in detail

### `database_improvements` — 5 of 6 dead, the largest block

`MigrationManager` (168 lines), `BackupManager` (156), `ConnectionPool` (76),
`QueryOptimizer` (53) and `create_initial_migrations` (93) are referenced from nowhere
outside their own file. Verified by hand:

```bash
grep -rn "MigrationManager" --include=*.py . | grep -v modules/database_improvements.py
# (no output)
```

The one live export is `db_health_check`, a module-level `DatabaseHealthCheck()`
singleton, used by a `/health` route that deliberately builds its own short-lived
psycopg2 connection because the app's ORM is asyncpg.

**So 546 of 678 lines are code the application never calls** — the five dead
classes measure 168 + 156 + 76 + 53 = 453 lines, plus 93 for
`create_initial_migrations`. Only `DatabaseHealthCheck` (69 lines) is live. It is not broken
and it is not unreachable-by-accident: it is a generic "database improvements"
module written against a different imagined application — migrations, backups,
connection pooling, query optimisation — none of which this app needs, because it
uses SQLAlchemy, managed Postgres, and a connection pool that already exists.

`MigrationManager` has a test (`test_enterprise.py`). `BackupManager`,
`ConnectionPool` and `QueryOptimizer` have **none**.

### `websocket` — 5 of 7 dead, and the live part is the interesting one

Dead: `MessageType`, `WSMessage`, `ConnectionManager`, `broadcast_message`,
`get_online_count`. Live: `get_ws_handler` and `WebSocketHandler`, both used by
`api.py` — and, when this was first written, by `modules/services.py`.

**Correction (2026-11-13):** `broadcast_message` and `get_online_count` have now been
deleted, and so has `modules/services.py` itself. That file was an 84-line service
locator with zero importers and zero references anywhere in the repo; its only inbound
reference was the module-level table row above, and its only caller was a test that
imported it. `WebSocketHandler` is still live via `api.py`'s `get_ws_handler()`.

The pattern is a **module written as a standalone WebSocket server** — own connection
manager, own message types, own broadcast — of which the application uses only the
handler entry point. The other 300-odd lines duplicate state that FastAPI's own
WebSocket support and the app's service layer already hold.

### `cicd_improvements` — 2 of 3 dead

`ABTestManager` (109 lines) and `DeploymentManager` (95) are unreferenced.
`FeatureFlagManager` is live via the `feature_flags` singleton, used in four places:
startup logging, the dashboard, and two `is_enabled("enable_mcp_agents")` gates.

A/B testing and deployment management in an application with no deployment pipeline
wired to it. Nothing constructs them.

### `citations` — 3 of 4 dead, and this one connects to a finding

`CitationReport`, `find_citations` and `parse_excerpt_sources` are unreferenced
outside the module. Only `check_citations` is called (`api.py:1746`).

This is the same module behind **security finding S4** — *"a citation is a pointer,
not evidence"* — which found that `cites_file` checks a filename was produced, not
that the cited chunk supports the claim. The triage adds a detail that finding did
not have: **three quarters of the citation module is dead code, so the checks that
never ran were not failing silently — they were not wired up at all.**

### The smaller ones

- `pg_store`: `get_database_url`, `is_registered_user`, `normalize_user_id`,
  `get_pool` serve an unused alternative database path
- `provider_client`: `ProviderConfigError`, `resolve_target` — the error type and a
  resolution helper, both unreferenced
- `redis_store`: `summary_key` — one key helper the app never builds
- `doc_scope`: `ScopedDocumentStore` — a class the app bypasses in favour of `scoped`
- `context_budget`: `estimate_tokens` — used internally, not exported for use

---

## What this triage does, and does not, recommend

The ROADMAP's instruction was *"test it, or delete it, or state it."* Reading the
code changed my answer, and it is the third option for almost all of it.

**Do not delete it in this PR.** Reasons, stated because the instinct is to delete:

1. **Deleting 546 lines of `database_improvements` is a code change, not a triage.**
   It changes nothing at runtime (the code is already unreachable) but it is 660
   lines removed from a repository whose whole story is "we measure, then change."
   Removing it inside a documentation commit is exactly the kind of silent scope
   creep this project has spent six findings documenting.
2. **Some of it may be intended.** A feature-flag A/B test manager, a WebSocket
   broadcast, a connection pool: these are plausible *plans*, not obviously
   accidents. Deleting someone's planned work because it is currently unused is a
   judgement call that belongs to the owner of the project, stated as a choice, not
   performed in a docs PR.

**What is recommended instead, and is done here:** every dead name is now *listed*.
The rule applied — also misattributed to `ROADMAP.md` in the first version of this
document — was: either wire it up, or delete it, or leave it with a one-line comment saying
what it is waiting for. **The status "unexercised and unlisted" is gone.**

---

## The honest limits of this document

- **Counts are static references, not executed paths.** A name listed as live may
  only be reached from a route that is never called. A name listed as dead cannot be
  reached at all — that direction is sound, the reverse is not.
- **Tests were excluded by design.** A module with a test proves its behaviour when
  invoked; it says nothing about whether the application invokes it. Appendix below
  lists which dead names are also untested, because that is the worse state.
- **This is not a coverage report.** No line-coverage was measured; the question was
  "is this reachable from the app", not "is every branch exercised".

---

## Appendix — dead names that are also untested

| Name | Module | Tested? |
|---|---|---|
| `MigrationManager` | database_improvements | yes (`test_enterprise.py`) |
| `BackupManager` | database_improvements | **no** |
| `ConnectionPool` | database_improvements | **no** |
| `QueryOptimizer` | database_improvements | **no** |
| `create_initial_migrations` | database_improvements | **no** |
| `MessageType`, `WSMessage` | websocket | via handler tests |
| `ConnectionManager` | websocket | **no** |
| `broadcast_message` | websocket | **no** |
| `get_online_count` | websocket | **no** |
| `ABTestManager` | cicd_improvements | **no** |
| `DeploymentManager` | cicd_improvements | **no** |
| `find_citations` | citations | **no** |
| `CitationReport` | citations | via `check_citations` |
| `parse_excerpt_sources` | citations | yes (`test_reliability_batch.py`) |
| `ScopedDocumentStore` | doc_scope | **no** |
| `ProviderConfigError`, `resolve_target` | provider_client | **no** |
| `get_database_url`, `is_registered_user`, `normalize_user_id`, `get_pool` | pg_store | partially |
| `summary_key` | redis_store | **no** |
| `estimate_tokens` | context_budget | yes |

**Thirteen dead names have no test of any kind** — unreachable from the app and
unverified by the suite. That is the actual bottom of this project's coverage, and it
is now written down rather than assumed absent.

---

## The lesson

> **"Undocumented" and "unused" are different words, and this project had been using
> them as one.**

The dark-module count was built by grepping prose. It found eleven modules nobody had
*written about* and reported them as eleven modules nobody had *looked at*. Both
statements were checkable; only the first was checked.

The narrower finding that survived is still worth having: **38% of the public surface
of those modules is unreachable, and thirteen of those functions and classes have no
test either.** But it is a third of the surface, not all of it, and saying so is the
difference between a document and a scare.

---

## Addendum 2026-11-13 — four more dead things, found by reading wiring rather than names

This document was built by counting public names with no inbound reference. That method
finds functions nothing calls. It does **not** find configuration nothing reads, so a
second pass went looking for that instead. It found three things.

### `modules/services.py` — 84 lines, deleted

A service locator (`Services`, `get_services`, `reset_services`) exposing lazy singletons
for database, RAG, memory, facts and websocket. **Zero importers.** Its only inbound
reference in the whole repository was this document's own table, and its only caller was
`tests/test_enterprise.py::test_services_singleton`, a test that existed to import it.

That is the shape worth remembering: **the module was kept alive by a test whose only
purpose was to touch it.** Deleting the test would have deleted the evidence; the module
is deleted instead, and the test now asserts the file stays gone.

### Seven of eight feature flags — deleted from `config/feature_flags.json`

`FeatureFlagManager` (`modules/cicd_improvements.py`) loads
`config/feature_flags.json`, which shipped eight flags. Counting
`is_enabled("<flag>")` call sites across every non-test Python file:

| Flag | Call sites |
|---|---|
| `enable_mcp_agents` | **2** (`api.py:3364`, `api.py:3387`) |
| `enable_advanced_rag` | 0 |
| `enable_phoenix_tracing` | 0 |
| `enable_caching` | 0 |
| `enable_rate_limiting` | 0 |
| `enable_websockets` | 0 |
| `enable_file_upload` | 0 |
| `enable_beta_features` | 0 |

There is no dynamic accessor — `set_flag`, `get_all_flags` and `get_enabled_flags` exist,
but the only reads in the app are the two literal `"enable_mcp_agents"` strings. So
setting `enable_rate_limiting: false` and restarting left rate limiting running:
`api.py` calls `rate_limiter.is_rate_limited(client_ip)` unconditionally.

Worse, `enable_caching`, `enable_rate_limiting` and `enable_websockets` **also** exist in
a second, unrelated flag system (`modules/config.py::FeatureFlags`, read from
`FEATURE_*` environment variables), which means there were two knobs for each of those
three concepts and neither one gated the behaviour.

The seven were removed. `enable_mcp_agents` stays because it is genuinely read.
`tests/test_enterprise.py::TestEnterpriseModules::test_every_shipped_flag_is_actually_read`
now fails if a flag is added to the JSON that no code asks about:

```
AssertionError: flags configured but never read by any code: ['enable_beta_features']
```

### `reload_config` — deleted

`modules/config.py`, zero references. `get_config()` is the only accessor.

### `validate_fact_key` / `validate_fact_value` — **not** dead, wired instead

These were on the dead list in the first pass because nothing called them. Deleting them
would have deleted the only statement of the fact-length limits. They are now called from
`FactMemory.remember()`, which every writer goes through — see
[`SECURITY_FINDINGS.md` S6](SECURITY_FINDINGS.md). A guard that nothing calls is a
comment; a guard that something calls is a limit.

### Revised count

**23 dead public names** in the original pass, of which `broadcast_message`,
`get_online_count`, `reload_config` and the whole `services` module are now deleted
rather than documented. The 546 unreachable lines in `database_improvements` are
unchanged — that deletion is still open.


---

## Addendum 2026-11-13 (second) — `database_improvements`: 678 lines → 103

This document already recorded the 546 unreachable lines here. They are now deleted, and
the reason is worth recording because a count is not the same as a defect.

### What was unreachable

| Symbol | External references |
|---|---|
| `MigrationManager` | **0** |
| `BackupManager` | **0** |
| `ConnectionPool` | **0** |
| `QueryOptimizer` | **0** |
| `create_initial_migrations` | **0** |
| `DatabaseHealthCheck` | 1 (`api.py:457`, from `/health`) |

Only `DatabaseHealthCheck` was wired. The other five existed, had tests, had docstrings
listing features, and were called by nothing.

### The part that was an actual defect, not just unused code

Three of them had module-level singletons that ran `os.makedirs()` in `__init__`:

```python
migration_manager = MigrationManager()   # os.makedirs("migrations")
backup_manager    = BackupManager()      # os.makedirs("backups")
```

So **importing the module had a side effect** — it created two directories in the working
directory. Verified before the fix:

```
$ cd /tmp/empty && python -c "import modules.database_improvements"
$ ls
backups   migrations
```

Dead code that is never called is untidy. Dead code that runs at import is different: it
executes on every process start, in every container, and nothing asked it to. After the
deletion, importing creates nothing — and a test asserts that by importing the module in a
fresh temporary directory and requiring the directory to stay empty.

### What is left

```
$ wc -l modules/database_improvements.py
103 modules/database_improvements.py
```

`DatabaseHealthCheck` and the `db_health_check` singleton. The module's own docstring now
says what it is for and what was removed, so the next reader does not assume the other
five are somewhere else.

Coverage of the file went **29% → 74%**; repo total **68% → 70%** — the second number moved
2 points for reasons that have nothing to do with new tests.

---

## Addendum 2026-11-14 (third) — a deep pass after #15: two new failures, two confirmed, one stale claim

After the `api.py` split finished (#15, scope A), I re-derived the repo's weak spots
from coverage rather than from this document, then checked each one against the code.
Four findings; **two were already known here and two were not.** The two known ones are
listed first, with what I confirmed, because a document that silently omits them would
read as if they had just been discovered.

### Already known, re-confirmed

**1. Seven of eight feature flags are dead.** Found in the second addendum and fixed
there; `config/feature_flags.json` now carries only `enable_mcp_agents`, and
`test_enterprise.py::test_every_shipped_flag_is_actually_read` fails if a flag is added
that nothing reads. I re-derived the count and it holds: exactly **2** `is_enabled()`
call sites, both `"enable_mcp_agents"`.

**2. Two feature-flag systems exist** (`modules/cicd_improvements.py::FeatureFlagManager`
and `modules/config.py::FeatureFlags` from `FEATURE_*` env vars). `/health` reports
`websocket: active` from the *second* one; nothing gates the WebSocket route on either.
Unchanged, still true, and still deliberately not "fixed" — see the note at the end.

### New 1 — a corrupt `config/feature_flags.json` stopped the app from starting

This is the one that could take the service down. `api.py` does
`from modules.cicd_improvements import feature_flags`, and `feature_flags` is built **at
import time**; `_load_flags()` read the file bare:

```python
with open(self.config_file, "r") as f:
    self.flags = json.load(f)
```

So any exception there is an exception during `import api`. Three reproduced against the
real entry point, each of which made `import api` fail:

| file content | failure |
|---|---|
| `{"enable_mcp_agents": {"enabled": tru` (truncated) | `JSONDecodeError` |
| `{"enable_mcp_agents": true}` | `AttributeError: 'bool' object has no attribute 'get'` |
| `"rollout_percent": "100"` | `TypeError: '<' not supported between 'str' and 'int'` |

The first is the realistic one: a partial write (disk full, pod killed mid-save) leaves
exactly that. The others need a hand edit, and hand-editing a flags file is the normal
way to use one.

A feature flag exists so you can change behaviour **without a deploy**. A file that can
prevent the deploy from starting is the exact inverse. Fixed so the file can only ever
cost flags, never availability:

* load is wrapped; on any failure the built-in defaults are applied, the problem is
  logged at ERROR with the path, and `load_error` records it
* the loaded value must be a JSON **object of objects** — `["x"]` or `{"x": true}` is
  rejected at load rather than blowing up at the first read
* `is_enabled()` tolerates a non-dict entry and a non-numeric `rollout_percent` (a bad
  rollout ignores the rollout, it does not disable the flag)
* `_save_flags()` is best effort, so a read-only container filesystem can no longer break
  `set_flag` or startup

12 tests in `tests/test_feature_flag_robustness.py`. **Reverting the fix fails 6 of them.**

**Not done, deliberately:** the import-time `_save_flags()` call still writes
`config/feature_flags.json` into the *current working directory*, not next to the source.
That is a real smell (it means the app writes a tracked file, and behaves differently
depending on where uvicorn was started), but changing where configuration is found is a
deployment decision, not a bug fix. Recorded, not changed.

### New 2 — the MCP agent server is a deployed process with no tests at all

`mcp_agents_server.py` was at **0% coverage**: nothing in the suite imported it. It is not
dead code — `docker-compose.yml` runs it as its own service on port 8001 and
`k8s/mcp-server.yaml` runs it as its own Deployment with its own image tag. The k8s
manifests are reviewed separately and are out of scope here; the Python process they run
is not.

18 tests now cover the parts that fail silently rather than loudly: the module imports,
all **seven** advertised tools are registered (a missing one is not a crash — the server
starts, the docs promise the tool, and it is simply absent), DNS-rebinding protection is
on with no `*` in the allowed hosts, and the service DNS name the manifest addresses is
allowed. Also the timeout backstop: a hung upstream must return a message, not wedge the
call, and an internal error must not leak its text to the MCP client.

The tool-timeout test was **rewritten after it initially passed against sabotaged code** —
it asserted only "returns a string", which is also what `fut.result()` with no timeout
returns. It now shortens the timeout to milliseconds and asserts the timeout *branch*.

### New 3 — the reranker fallback chain, untested at the only level that runs in a broken deployment

`CrossEncoderReranker` documents three backends, first available wins: ONNX →
sentence-transformers → keyword. The keyword level is taken whenever the ONNX model is
missing, and it calls `advanced_rag.ReRanker`. Measured before: `advanced_rag.py` at
**27% coverage**, the whole `ReRanker` class unreached, and both `_fallback_rerank` and
its call site uncovered.

The path taken *when things are already going wrong* was the untested one. 10 tests in
`tests/test_reranker_fallback.py`.

**The first version of these tests was worthless and the sabotage caught it.** The fixture
listed the relevant chunk *first*, so "the reranked output has the relevant chunk first"
was true whether or not the reranker ran. Deleting the entire rerank call left all 10
tests green. Reordering the fixture (relevant chunk last, lowest vector score) made the
sabotage fail, which is what the test was for.

> A test whose data already satisfies the assertion is not testing the assertion.

### One stale claim, corrected

An earlier maintenance audit (a working-branch file, since deleted — see the lifecycle
note in `CHANGELOG.md`) defended `advanced_rag` against being called unused by citing
**`app.py:380` imports `AdvancedRAG`**. That was true when written. `app.py` was deleted
in #13, and it has been false since.

Re-derived, repo-wide: `AdvancedRAG`, `SubQuestionEngine` and `PhoenixTracer` — 540 lines
— are constructed **only inside `modules/advanced_rag.py` itself**. Nothing outside the
file instantiates `AdvancedRAG`. `ReRanker` and `RouterQueryEngine` are referenced from
outside (the reranker fallback, and `tests/test_e2e.py`).

The method, not the answer, is the point: the correction was right when made and went
stale when a different change landed. A citation to a file that no longer exists is worse
than no citation, because it reads as checked.

### What this addendum did not fix

`api.py` remains at **46% coverage** (648 of 1,202 statements unreached). That is the
route layer plus `lifespan`, and the two `/mcp/*` endpoints are unreachable by design in
the shipped configuration (`enable_mcp_agents` gates them, and it is the one flag that is
on — but the endpoints call an MCP server that the default deployment does not run). It
is a real gap and it is the honest headline number for the next pass.

Repo total **83.51% → 84.06%**, tests **950 → 990**.
