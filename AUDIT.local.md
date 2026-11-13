> ⚠️ **TEMPORARY FILE — DO NOT TREAT AS PROJECT DOCUMENTATION.**
> Committed on a working branch only to keep the audit findings from being lost between
> sessions. **It is to be deleted once the fixes are done.** It is not part of the
> seven-part findings structure and should never be linked from the README or docs/.
> If you are reading this after the work is finished: delete it.

# Complete audit — over-engineering + deep logic/wiring/correctness

**Audited:** 2026-11-13 against `69f8f42`. **Read-only — nothing changed.**
**Method:** AST parsing, import-graph analysis, env-var reconciliation, full coverage run,
Prometheus-rule validation, plus manual reading of every path that carries traffic.
Not impressions. Every number below came from a command, not memory.

**Status:** audit complete, nothing fixed yet. This file is the record so the findings
survive the chat. **One file, not two** — the over-engineering notes and the deep audit
are merged here, nothing dropped, including the optional items and my own mistakes.

**Verdict:** the project is **partially over-engineered — not by being too clever but by
additive accretion: a second version was added and the first was never removed.** And
separately: **the test suite is 68% coverage, with the main chat route, the WebSocket and
most of authentication in the untested third.**

Read "What to do next" at the bottom first if you only want the decision.

---

# PART 1 — The verification bugs (highest severity)

## 🔴 1.1 The CI integration job does not install what the app installs

`.github/workflows/main.yml` lines 275–279:

```yaml
pip install groq llama-index-core llama-index-llms-groq
pip install chromadb pydantic fastapi uvicorn PyJWT bcrypt
pip install python-dotenv pypdf python-docx pandas
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
pip install pytest sqlalchemy psycopg2-binary asyncpg redis "mcp<2" prometheus-client "fastmcp<4"
```

Hand-listed and **unpinned**. Compare the real test job (lines 38–42), which says so in a
comment:

```yaml
# Pinned: install exactly what the Docker image ships (requirements.txt)
pip install -r requirements.txt
```

`requirements.txt` pins `mcp==1.30.0`. The integration job installs **the newest `mcp<2`**.

| Job | Installs | Can it verify the shipped artifact? |
|---|---|---|
| `test` | `mcp==1.30.0` (from requirements.txt) | **yes** |
| `integration` | newest `mcp<2` | **no** |

**So the job whose purpose is "every module imports" is not testing the versions that
ship.** It can stay green while the image breaks.

That is not over-engineering. **It is a verification bug** — and in a project whose thesis
is "a claim you can check", a green check that does not test the artifact is the worst
class of defect available.

**Fix (one line):** replace lines 275–279 with `pip install -r requirements.txt`.

## 🔴 1.2 `test_cors_headers_present` asserts nothing — and the README cites it

`tests/test_security.py:402`

```python
def test_cors_headers_present(self):
    """Test: CORS headers are configured"""
    # This would normally test the actual API
    # For unit test, we verify the configuration

    from api import app

    # Check middleware is added
    middleware = [m for m in app.middleware.__dict__.get("user_middleware", [])]
    # CORS middleware should be present
    print("  ✅ CORS middleware configured")
```

It builds a list, **discards it**, prints a checkmark, and passes. `middleware` is never
asserted on. Delete `CORSMiddleware` from `api.py` and this test still passes.

And the README cites it as the proof (README.md:757):

> `| 🌐 **CORS** | Fixed origin list when ENVIRONMENT=production | \`test_security.py::TestCORS\` |`

Its sibling `test_cors_origins_restricted` **does** assert (on `config.environment`), so the
class is not a complete loss — but the test the README points at is decoration.

*Same shape as the boot bug: the check and the thing checked were configured to agree.*

---

# PART 2 — Code coverage is 68%, and the holes are the important ones

First time this has ever been measured. Full suite with `--cov=modules --cov=api`:

```
TOTAL   7330 statements   2379 missed   68%
833 passed, 6 skipped, 17 deselected
```

## Ranked worst → best

| Coverage | Module | Missed |
|---|---|---|
| **27%** | `advanced_rag.py` | 210 / 288 |
| **29%** | `database_improvements.py` | 188 / 263 |
| **36%** | `mcp_client.py` | 18 / 28 |
| **44%** | `cicd_improvements.py` | 78 / 140 |
| **51%** | **`api.py`** | **805 / 1630** |
| **53%** | **`auth.py`** | **102 / 215** |
| **55%** | `pg_store.py` | 58 / 129 |
| **60%** | `websocket.py` | 62 / 156 |
| 64% | `rag.py` | 68 / 190 |
| 66% | `memory.py` | 134 / 396 |
| 68% | `security_hardening.py` | 73 / 225 |
| 72% | `security.py` | 59 / 211 |
| 74% | `redis_store.py` | 18 / 70 |
| 76% | `vector_store.py` | 57 / 238 |
| 79% | `llm_guard.py` | 76 / 369 |
| 80% | `rag_optimizations.py` | 143 / 702 |
| 80% | `doc_scope.py` | 26 / 128 |
| 81% | `secrets.py` | 6 / 31 |
| 83% | `prompt_rules.py` | 4 / 24 |
| 87% | `prometheus_metrics.py` | 38 / 287 |
| 87% | `web_tools.py` | 17 / 135 |
| 90% | `pii.py`, `advanced_monitoring.py` | 7, 13 |
| 91% | `agent_tools.py`, `recording.py` | 22, 20 |
| 94% | `database.py` | 7 / 120 |
| 95% | `grounding.py`, `routing.py` | 6, 7 |
| 97% | `provider_client.py` | 1 / 34 |
| 98% | `config.py`, `context_budget.py`, `model_tiers.py` | 2, 1, 1 |
| **100%** | `citations.py`, `request_context.py` | 0 |

## Why this matters more than the over-engineering findings

**`api.py` at 51% is the file that serves every request.** 805 untested statements.
Look at what is missing: **`2154–2369` is the entire `/chat` route body.** `1130–1170`
is the WebSocket endpoint. `980–1024` is login.

**`auth.py` at 53%** is security-critical: `208–289` missed is the password-hashing /
verification block, `303–416` is token issue/verify/refresh.

So the honest statement is not "833 tests, everything is covered". It is:

> **833 tests, 68% of statements, and the untested third contains the main chat route,
> the WebSocket, and most of authentication.**

That is a real finding, and it is the same shape as everything else in this project — the
number that was quoted (833) was true, and the number that mattered (68%) was not quoted.

> **Note, no false claim to correct:** the README's "90% coverage" refers to *eval case
> coverage* (graded / total cases), not code coverage. Different metric, correctly used.
> There is no code-coverage number anywhere in the docs — that is the gap.

---

# PART 3 — Over-engineering findings table

Findings ordered by how much they matter. *Row 7 has since been **superseded** by a more
thorough count — see the correction immediately below the table.*

| # | Finding | Size | Severity |
|---|---|---|---|
| 1 | CI integration job installs unpinned deps | 5 lines | **high** — false green |
| 2 | `test_cors_headers_present` asserts nothing | 1 test | **high** — and the README cites it |
| 3 | 546 of 678 lines in `database_improvements` never called | 546 lines | medium |
| 4 | Two full UIs (Next.js + Streamlit "legacy") | 1,200 lines | medium |
| 5 | Three monitoring modules | 1,348 lines | medium |
| 6 | `api.py` holds ~6 concerns | 3,412 lines | medium |
| 7 | ~~75 env vars, 39 documented~~ → **111 read, 76 undocumented** | config surface | medium |
| 8 | 23 of 61 public names unused (13 untested) | dead surface | medium |
| 9 | Two MCP clients (root sync + `modules/` async) | 230 + ~100 | low |
| 10 | Metrics counted twice (9 `business_metrics` + 51 Prometheus) | duplication | low |
| 11 | Four tool-loop methods in `BaseAgent` (2 + 2 wrappers) | 2 methods' worth | low |
| 12 | compose + 31 k8s manifests describe the same system | 89 resources | low — justified |
| 13 | `advanced_` naming: `advanced_rag` + `rag`, `advanced_monitoring` + `monitoring` | naming | low — explains #4/#5 |
| 14 | Two `*_improvements` modules, both largely unused | 1,061 lines | low — a category |

**Correction on row 7:** the first pass grepped README + docs and found 39 documented of 75
read. A second, full pass over every `os.getenv`/`os.environ` read across `*.py`,
`modules/`, `evals/` found the real figure — **111 distinct variables, 76 undocumented
(68%)**. The earlier 75/39 undercounted. Full breakdown in PART 4.

---

# PART 4 — Wiring: checked, and it holds

The surprise of this audit: **I tried to break the wiring and mostly could not.**

## 4.1 Env vars: 111 read, 76 undocumented (68%)

- **111** distinct variables read in code
- **76** of them appear nowhere in the README

Grouped: `AGENT_*` 19, `EVAL_*` 8, `MCP_*` 6, `LLM_*` 5, `FEATURE_*` 5, `GROQ_*` 4,
`DB_*` 3, `RATE_*` 3, `POSTGRES_*` 3, then singles.

Live hidden knobs — `AGENT_PARALLEL`, `AGENT_REFLECT`, `AGENT_CRITIQUE`,
`AGENT_LATENCY_BUDGET`, `STREAM_MAX_DURATION`, `MCP_TIMEOUT`: these change runtime
behaviour and are documented nowhere a user would look.

**This is the strongest documentation finding in the whole audit.** It is not that the docs
are wrong — they are unusually accurate. It is that **68% of the configuration surface is
invisible.**

## 4.2 Docker secrets vs k8s Secrets — correct on both

Compose uses `GROQ_API_KEY_FILE` / `JWT_SECRET_KEY_FILE`; k8s uses `GROQ_API_KEY` /
`JWT_SECRET_KEY` as Secret refs. The divergence looked suspicious, so I traced it:

`modules/secrets.py:32` checks `if os.getenv(f"{env_var}_FILE")` first, then the plain var.
**Both deploy targets resolve.** Not a bug.

## 4.3 Tests importing missing symbols — 29 flagged, 0 real

My first check reported 29 broken `from modules import X` lines. All false: they are
**submodule** imports (`from modules import web_tools`), and `hasattr(modules, 'web_tools')`
is False only until the submodule is imported. Verified directly.

## 4.4 `evals/checks.py` — 5 checks flagged unused, all wired

`inline_code_spans`, `check_excludes`, `check_length`, `check_min_length`, `check_route`
are never referenced outside `checks.py` by name — but `evaluate()` dispatches them
internally at lines 620–633. False alarm.

## 4.5 Prometheus alert rules — all 24 real

`config/prometheus/alerts.yml`, 24 rules. I extracted every metric name referenced and
checked it against `modules/prometheus_metrics.py`:

- `llama_agent_chat_seconds_bucket` / `llama_agent_response_time_seconds_bucket` /
  `..._count` flagged as "missing" — **false positive**: the Prometheus client appends
  `_bucket` / `_count` / `_sum` automatically for a `Histogram`. Declared at
  `prometheus_metrics.py:51` and `:163`.
- `node_cpu_seconds_total`, `node_filesystem_*` — produced by **node-exporter**, which
  **is** defined in `docker-compose.yml:400–401` (`prom/node-exporter:v1.7.0`) **and**
  scraped (`prometheus.yml` job `node-exporter`, target `node-exporter:9100`). **Wiring
  correct.**
- `pg_stat_activity_count`, `pg_up`, `redis_memory_used_bytes`, `redis_memory_max_bytes`,
  `redis_up` — **no postgres-exporter or redis-exporter exists in compose or in any k8s
  manifest.** But those rules are **commented out** with an honest explanation:

```yaml
# These rules reference metrics (pg_up, pg_stat_activity_count) that
# ...                                   (alerts.yml:178, 194)
# These rules reference metrics (redis_up, redis_memory_used_bytes) that
# ...                                   (alerts.yml:205, 221)
```

**That is the right way to handle it** — the rule is documented as unrunnable rather than
shipped silently. Credit where due: this is the opposite of the CI job's failure mode.

## 4.6 `scripts/` — audited; one file, correctly wired

`scripts/download_reranker.py` is the only file. It is **not** dead:

- invoked at `.github/workflows/main.yml:48`
  (`python scripts/download_reranker.py /tmp/reranker-model`)
- documented at `README.md:509` and `CHANGELOG.md:998`
- its output is consumed by `modules/rag_optimizations.py:387`

The earlier draft of this audit said "scripts/ not audited in detail" — that gap is now
closed, and the answer is **clean**.

## 4.7 `config/` — provisioning-as-code, correct

11 files: `feature_flags.json`, grafana (dashboard + 2 provisioning files), loki,
nginx (config + 2 ssl scripts), prometheus (alerts + scrape config), promtail.
All referenced, all valid YAML/conf. See PART 8 for the one problem inside `feature_flags.json`
— which is a *content* problem, not a wiring problem.

---

# PART 5 — Correctness findings

## 🟡 5.1 Blocking file write inside `async def` (bounded, so minor)

`api.py:3027`, in `upload_document()`:

```python
with open(file_path, "wb") as f:
    f.write(content)
```

This is inside `async def`, so it blocks the event loop for every concurrent request.

**Severity: low**, because the upload is capped at `MAX_FILE_SIZE = 10 MB`
(`modules/security.py:25`), and a 10 MB write is sub-millisecond on SSD. Worth noting, not
worth a PR on its own. **The irony:** `rag.add_document()` on the very next line *is*
correctly wrapped in `asyncio.to_thread`.

## 🟡 5.2 32 `except Exception: pass` in live paths

Concentrated in `prometheus_metrics.py` (9), `api.py` (5), `rag_optimizations.py` (4).
Many are deliberate ("telemetry must never fail a tool call" — one even carries
`# nosec B110`). But `api.py:1760` swallows a citation-recording failure silently, which
means a broken citation path would be **invisible**.

## ✅ 5.3 Clean

| Check | Result |
|---|---|
| Mutable default arguments | **0** |
| Unused imports (`F401`) | **0** |
| Unused variables (`F841`) | **0** |
| Duplicate imports (`F811`) | **0** |
| Broken test imports | **0** (29 flagged, all submodule imports) |
| Eval checks unwired | **0** (5 flagged, all dispatched by `evaluate()`) |
| Prometheus alerts on non-existent metrics | **0** (5 flagged; 2 histograms, 3 commented out) |
| Dockerfile | correct, installs `requirements.txt`, drops to non-root |
| Secret resolution | correct on both compose and k8s |

---

# PART 6 — Dead code, confirmed not suspected

## 6.1 Genuinely dead: 0 references anywhere in the repo

| Symbol | File |
|---|---|
| **`modules/services.py` — whole file, 84 lines** | a service locator nothing imports |
| `broadcast_message` | `websocket.py` |
| `get_online_count` | `websocket.py` |
| `validate_fact_key`, `validate_fact_value` | `security.py` |
| `reload_config` | `config.py` |

`services.py` is the standout: **84 lines, zero importers, zero references.** It shows 65%
coverage only because a test imports it directly — **a test for code nothing uses.** The
test is what keeps it alive.

## 6.2 50 top-level functions never referenced outside their own file

Of those, the ones verified as truly unreachable are listed above. The rest — `_luhn_ok`,
`_card_sub`, `_is_private_host`, `_search_tavily`, … — are private helpers called
internally, **not dead**. My naive check counts them because it only looks outside the
defining file.

## 6.3 Already known (from `DARK_MODULES.md`), still true

546 of 678 lines in `database_improvements` unreachable — now **confirmed independently by
coverage: 29%**.

## 6.4 23 of 61 public names unused, 13 of those untested

A separate, older measure (public API surface) from the function-level count above. Both
are real; they measure different things and do not contradict.

---

# PART 7 — Per-folder verdict

| Folder | Files | Lines | Verdict |
|---|---|---|---|
| **root `*.py`** | 6 | 8,039 | `api.py` 3,412 lines / 51% cov is the weak point. `mcp_client_helper.py` + `modules/mcp_client.py` duplicate the MCP client. `mcp_client_helper.py` is not imported by any module — only by the integration check. |
| **`modules/`** | 36 | 13,529 | Mostly healthy. Three genuinely weak: `advanced_rag` 27%, `database_improvements` 29%, `cicd_improvements` 44%. `services.py` dead. `auth.py` 53% is the concerning one. |
| **`tests/`** | 40 | 12,668 | 833 tests, 68% coverage. One vacuous test (`test_cors_headers_present`). Strong where it counts: `config`, `context_budget`, `model_tiers` ≥98%, `grounding`, `routing` ≥95%. |
| **`evals/`** | 6 | 2,948 | **Best-built area.** All 21 checks wired. Record/replay is genuinely good engineering. |
| **`k8s/`** | 31 yaml | — | Re-audited in #45; one false claim fixed. 8 checks now documented in `docs/KUBERNETES_CHECKS.md`. |
| **`frontend/src`** | 39 ts/tsx | — | 6 pages, 34 components. No images (so no `alt` gap). Only 3 `aria-*` in the whole UI — accessibility is thin, but CI typechecks/lints/builds it. |
| **`config/`** | 11 files | — | Provisioning-as-code, all referenced, all valid. One dead-flags problem inside `feature_flags.json` (PART 8). |
| **`scripts/`** | 1 | — | ✅ Audited this round. `download_reranker.py` is called by CI, documented twice, consumed by `rag_optimizations.py:387`. Clean. |
| **`.github/`** | 2 workflows | — | 🔴 `integration` job installs unpinned deps — still the highest-severity finding (1.1). |

---

# PART 8 — NEW this round: two feature-flag systems, and 7 of 8 flags do nothing

Found while auditing `config/`. There are **two independent feature-flag systems**, both
live, both read at runtime:

| | System A | System B |
|---|---|---|
| **Defined in** | `modules/config.py:148` `class FeatureFlags` | `modules/cicd_improvements.py:32` `FeatureFlagSystem` |
| **Source** | **environment variables** | **`config/feature_flags.json`** |
| **Flags** | `enable_websockets`, `enable_auth`, `enable_rate_limiting`, `enable_metrics`, `enable_caching` | `enable_advanced_rag`, `enable_phoenix_tracing`, `enable_caching`, `enable_rate_limiting`, `enable_websockets`, `enable_file_upload`, `enable_mcp_agents`, `enable_beta_features` |
| **Read at** | `api.py:569` (`enable_auth`), `api.py:860–861` | `api.py:3280`, `api.py:3364`, `api.py:3387` |

## The serious half: 7 of the 8 JSON flags are never read

Counted every `is_enabled("<flag>")` call site across all non-test Python:

| Flag in `config/feature_flags.json` | `is_enabled()` call sites |
|---|---|
| `enable_mcp_agents` | **2** ← the only live one |
| `enable_advanced_rag` | **0** |
| `enable_phoenix_tracing` | **0** |
| `enable_caching` | **0** |
| `enable_rate_limiting` | **0** |
| `enable_websockets` | **0** |
| `enable_file_upload` | **0** |
| `enable_beta_features` | **0** |

I checked for a dynamic accessor that would hide usage — there is none. The only reads in
the repo are the two literal `is_enabled("enable_mcp_agents")` calls at `api.py:3364` and
`:3387`.

**So a user can set `enable_rate_limiting: false` in that JSON, restart, and rate limiting
still runs** — `api.py:696` calls `rate_limiter.is_rate_limited(client_ip)`
unconditionally. Same for the cache (`rag_optimizations.py:1597`, `api.py:2053`) and
uploads.

Three of those names also exist in System A on a *different* switch
(`enable_caching`, `enable_rate_limiting`, `enable_websockets`), so there are two knobs for
the same concept, **and neither one is the knob that actually gates the behaviour.**

**This is the same defect class as finding 1.2** — a control that reads as functional and
is not. It is arguably worse, because a *config file* is the thing an operator trusts most.

## Also worth noting

`FEATURE_AUTH=false` does not disable auth. `api.py:569` uses it only to decide whether to
initialise the database; per-route enforcement is via 5 explicit `Depends(...)` guards and
is not gated by the flag. The flag name promises more than it does.

**Fix:** either wire the 7 flags to real branches (hours), or delete the dead ones and keep
`enable_mcp_agents` (10 min). Deleting is the honest option — 7 flags that assert nothing
are worse than 1 flag that works.

---

# PART 9 — Master ranked list, all rounds merged

| # | Finding | Severity | Effort |
|---|---|---|---|
| 1 | CI `integration` job installs unpinned deps — green check tests a different `mcp` | **high** | 5 min |
| 2 | `test_cors_headers_present` asserts nothing; README cites it as CORS proof | **high** | 15 min |
| 3 | **Code coverage 68%; `/chat`, WebSocket, and most of `auth.py` untested** | **high** | weeks |
| 4 | **76 of 111 env vars undocumented (68%)** | **high** | 2 h |
| 5 | **7 of 8 feature flags in `config/feature_flags.json` never read** | **high** | 10 min |
| 6 | `services.py` — 84 lines, zero references | medium | 2 min |
| 7 | 546 unreachable lines in `database_improvements` (29% cov confirms) | medium | 1–2 h |
| 8 | Two full UIs (Next.js + Streamlit "legacy") | medium | decision |
| 9 | Three monitoring modules, 1,348 lines | medium | 2–3 h |
| 10 | `api.py` 3,412 lines / 6 concerns / 51% coverage | medium | days |
| 11 | `auth.py` 53% coverage — password hashing + token paths missed | medium | days |
| 12 | Two MCP clients (`mcp_client_helper.py` + `modules/mcp_client.py`) | low | 1 h |
| 13 | 32 `except Exception: pass` in live paths | low | 1 h |
| 14 | Blocking `open().write()` in `async def` (bounded by 10 MB) | low | 10 min |
| 15 | 4 confirmed-dead functions + `services.py` | low | 10 min |
| 16 | Metrics counted twice; 3 ways to view them | low | — |
| 17 | compose + 31 k8s manifests | low | justified |
| 18 | `advanced_` naming explains #8/#9 | low | naming |
| 19 | Two `*_improvements` modules, 1,061 lines, one 29% dead | low | category |

---

# PART 10 — Checked and CLEAN — do not re-audit these

| Check | Result |
|---|---|
| `F401` unused imports | **0** |
| `F841` unused variables | **0** |
| `F811` duplicate imports | **0** (fixed in #47) |
| Mutable default arguments | **0** |
| Blocking I/O in async (other than 5.1) | **0** found |
| Modules never imported | **1** — `modules/__init__.py`, 6 lines, normal |
| Dead test files | **0** |
| `BaseAgent` + 5 subclasses | ✅ **good design** — base 625 lines, each agent 40–70 |
| Duplicate tool definitions | **0** — all in `agent_tools.py` (18 tools, one registry) |
| Dockerfile | installs `requirements.txt`, drops to non-root |
| requirements pinned | **90 of 92** (only `pypdf`, `pillow` are ranges) |
| `advanced_rag.py` used? | ✅ **yes** — imported by `app.py:380,903`; `ReRanker` by `rag_optimizations.py:556` |
| MCP wired up? | ✅ yes — `api.py` `/mcp/status` does a real SSE handshake |
| compose ↔ k8s secret resolution | ✅ both resolve via `secrets.py:32` |
| Prometheus alert rules | ✅ all 24 valid; 5 flagged = 2 histogram false positives + 3 honestly commented out |
| node-exporter | ✅ defined in compose:400, scraped as job `node-exporter` |
| `scripts/download_reranker.py` | ✅ used by CI:48, documented, output consumed |
| `config/` provisioning files | ✅ all referenced, valid |
| Eval checks | ✅ 21 defined, 21 dispatched |
| 4 of the 5 non-asserting tests | ✅ valid — implicit assertions via raised exceptions |

### The 5 "no assertion" tests, classified

My first detector flagged 5. Only **one** is actually vacuous — the other four assert by
letting an exception escape:

| Test | Real? |
|---|---|
| `test_record_chat_never_raises` | ✅ raises if it raises |
| `test_real_endpoints_are_allowed` | ✅ `_assert_http` raises if refused |
| `test_resolve_unknown_tier_raises` | ✅ `try/except/else: raise` |
| `test_questions_file_is_valid_jsonl` | ✅ `json.loads` raises |
| **`test_cors_headers_present`** | ❌ **prints and passes** |

---

# PART 11 — My own error log — keep this, it is the point

**Round 1: 5 claims, 1 wrong.**

> "Four RAG modules … `advanced_rag.py` is a third path the app doesn't choose between."

**False.** `app.py:380` imports `AdvancedRAG`; `rag_optimizations.py:556` imports its
`ReRanker`. I inferred "unused" from "not in `api.py`" — **the exact mistake
`DARK_MODULES.md` documents at length.** I had read that document and still made it.

**Round 2: 4 new claims, all held, but missed the two biggest** (the two UIs, and the
`advanced_` naming pattern).

**Round 3: 6 new claims, all held**, plus 1 new finding in round 4 (`test_cors`).

**Round 4 (this one): 6 new claims — and 3 of my first checks were wrong.**

| My check | Claimed | Truth |
|---|---|---|
| Broken test imports | 29 broken | **0** — all submodule imports (`from modules import web_tools`) |
| Eval checks unused | 5 unwired | **0** — all dispatched inside `evaluate()` |
| Prometheus alert metrics | 8 missing | **0** — 2 are histogram `_bucket`/`_count` suffixes; 3 are commented out; 3 belong to node-exporter which is scraped |
| Env var count (earlier) | 75 read | **111** read — first pass undercounted by 36 |
| Functions "unused" | 50 dead | **5 dead** — the rest are internal helpers |

Held up this round: the coverage numbers, `services.py` dead, the two feature-flag systems
and the 7 unread flags, `scripts/` clean, the vacuous test.

**The lesson, restated:** *AST-level checks over-report. Every claim needs a second,
different check before it goes in the record.* Roughly **half** the raw flags this round
were false positives, and they were all false in the same direction — **toward "the project
is more broken than it is."**

**Every systematic sweep found what the previous one missed.** Four sweeps, four rounds of
new findings, and every round also corrected a number from the round before.

---

# PART 12 — The pattern behind all of it

> **A second version was added, and the first was never removed.**

- `rag.py` → `rag_optimizations.py` → `advanced_rag.py`
- `monitoring.py` → `advanced_monitoring.py` → `prometheus_metrics.py`
- Streamlit → Next.js (Streamlit still shipped, labelled "legacy")
- compose → k8s (both maintained)
- `database_improvements` → never wired up
- 111 env vars → 76 undocumented
- Two MCP clients, two tool-loop wrappers, two metric systems, **two feature-flag systems**
- Two verification mechanisms that verify nothing (CI deps, CORS test)

None of it is wrong code. Each addition was reasonable when made. **Nobody went back and
deleted the previous one.** That is easier to fix than "too clever" — it is deletion, not
redesign.

---

# PART 13 — What to do next

The question was: *should I do this, and what first?* Answered explicitly, item by item.

## Do now — ~55 minutes, all small, three of them fix a false claim

| Order | Task | Effort | Why this position |
|---|---|---|---|
| **1** | **Fix the CI integration job** → `pip install -r requirements.txt` | 5 min | Makes every green check suspect. Fix before anything else. |
| **2** | **Fix `test_cors_headers_present`** to actually assert the middleware | 15 min | The README cites a test that cannot fail. |
| **3** | **Delete the 7 unread feature flags** from `config/feature_flags.json` (keep `enable_mcp_agents`) | 10 min | Config that lies is worse than no config. Zero risk — nothing reads them. |
| **4** | **Delete `services.py`** and the 4 dead functions | 10 min | Zero references. Delete, don't refactor. |
| **5** | **`asyncio.to_thread` around the upload write** | 10 min | Small, and it matches what the next line already does. |

**Recommendation: do 1–5 now.** They are ~55 minutes, none needs a refactor, and three of
them fix claims that are currently **false**.

## Do next — half a day

| Order | Task | Effort | Why |
|---|---|---|---|
| 6 | **Document the 76 env vars** — or delete the unused ones | 2 h | Highest documentation value available. 68% of the config surface is invisible. |
| 7 | **Delete the 546 dead lines** in `database_improvements` | 1–2 h | Biggest single cut. Needs the suite green after. |
| 8 | **Add `pytest --cov` with a floor of 68%** to CI | 30 min | So it cannot silently fall. |

## Decide separately — do not start these casually

| Task | Effort | Position |
|---|---|---|
| Streamlit: fix it or delete it | decision | "Legacy" that still deploys is two UIs. Pick one. |
| Merge `monitoring.py` + `advanced_monitoring.py` | 2–3 h | 690 lines of overlap. |
| Raise `auth.py` coverage | days | Genuinely worth it — password + token paths. |
| Split `api.py` | days | **Last. Riskiest. Least immediate gain.** |

**Do not start with splitting `api.py`.** A 3,412-line file is the change most likely to
break something for the least immediate benefit.

---

# PART 14 — If any of this becomes a document in the repo

It should **not** be a new findings document. The seven-part structure is complete and this
audit is a *different kind of thing* — a maintenance review, not part of the build.

Best homes:

- **Findings 1.1 (CI deps), 1.2 (CORS test), and PART 8 (dead flags)** → append to
  `docs/KUBERNETES_CHECKS.md` as checks 9, 10, 11. All three are "the verification did not
  verify" findings, which is that document's exact theme.
- **The coverage number (PART 2)** → the README's **Known limits**, one honest line:
  *"the suite covers 68% of statements; the `/chat` route, the WebSocket and much of
  `auth.py` are not covered."*
- **Dead lines, two UIs, module sizes (PART 3)** → a short "the codebase has accreted; here
  is where" paragraph in the README.

That keeps the seven-part structure intact and still puts every number somewhere a reader
can find it.

---

# PART 15 — The honest one-line summary

> The project is **better wired than its size suggests** — 111 env vars all resolve, both
> deploy targets work, every eval check is dispatched, all 24 alert rules are real, zero
> dead imports. But **the three largest files sit at 27%, 29% and 51% coverage**, the
> untested part of `api.py` is **the main chat route**, and **7 of the 8 feature flags in
> the shipped config file are never read by any code.**

Two sentences, both measured today, neither of which had been measured before.

---

*Every number in this file came from a command run against `69f8f42`. Nothing was edited,
committed, or pushed. Where a check of mine was wrong, the wrong version is in PART 11
alongside the correction.*

---

**Lifecycle:** this file is temporary. It lives on branch
`arena/8a7490b4-llama-agent` purely so the findings survive between sessions. When the
fixes in PART 13 are done, delete it with `git rm AUDIT.local.md`. It is **not** a findings
document for the project and must not be referenced from `README.md` or `docs/`.
