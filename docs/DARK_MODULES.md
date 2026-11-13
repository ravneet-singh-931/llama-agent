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
| `websocket` | `api.py`, `modules/services.py` |
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
`modules/services.py`.

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
