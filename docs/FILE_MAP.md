# File Map

What each file is for, in one or two lines. Every description below was taken
from the module's own module-level docstring or from reading its definitions —
not from memory. Where a file is large, the line count is given so you can judge
whether to read it.

The README has a shorter tree (`## 📁 Project Structure`). This file exists
because that section lists **10 of the 39 modules in `modules/`** (still true); the other 29 —
including `rag.py`, `pg_store.py`, `redis_store.py`, `pii.py`, `grounding.py`,
`routing.py` and `websocket.py` — are missing from it. Verified by checking each
filename against the README tree.

---

## Top level

| file | lines | what it is |
|---|---:|---|
| `api.py` | 2881 | The FastAPI app. All 35 routes, auth, chat, documents, memory, metrics, and the WebSocket. The biggest file; being split slowly (see `modules/api_utils.py`, `doc_answers.py`, `doc_identity.py`, `routing.py`, which were extracted from it). |
| `multi_agent.py` | 1991 | The five specialist agents and the orchestrator that routes to them. Second-largest file. |
| `llama_agent1.py` | 865 | The Groq/llama-index wrapper and the streaming agent loop. |
| `mcp_agents_server.py` | 342 | The MCP **server** — exposes the agents as MCP tools over SSE. |
| `docker-compose.yml` | — | 13 services (app, nginx, postgres, redis, chromadb, mcp-server, frontend, prometheus, grafana, loki, promtail, node-exporter, phoenix). |
| `Dockerfile` / `Dockerfile.nginx` | — | App image and proxy image. |
| `alembic.ini`, `migrations/` | — | Database migrations. |
| `init.sql` | — | Initial schema. |
| `pyproject.toml`, `requirements.txt` | — | Dependencies; `pyproject.toml` also holds pytest/flake8 config. |
| `evals/` | — | Offline evaluation suite (`run.py`, 1428 lines) and its checks. |

**Start here if you are new:** `docs/ARCHITECTURE.md` for the shape, then
`api.py` around the `lifespan` (line 521) and one endpoint, then `modules/rag.py`
(353 lines — the small, readable RAG) before `rag_optimizations.py` (1704).

---

## `modules/` — all 39 files

### Retrieval and answering
| file | lines | what it does |
|---|---:|---|
| `rag.py` | 353 | The baseline RAG: read documents, chunk, store, answer. Small on purpose. |
| `rag_optimizations.py` | 1704 | The upgraded pipeline: retrieval and answer-checking steps on top of ChromaDB. `OptimizedRAG` lives here. |
| `advanced_rag.py` | 854 | Four extra retrieval strategies (rearranging, sub-questions, routing, tracing). The `OptimizedRAG` fallback path builds on this. |
| `vector_store.py` | 448 | ChromaDB integration. |
| `doc_answers.py` | 742 | Document questions end to end: routing, retrieval, answering, citation checking. Extracted from `api.py`. |
| `doc_scope.py` | — | Per-owner document scoping, so one user cannot retrieve another's document. Every store implements it. |
| `doc_identity.py` | — | Who is asking: request identity, guest cookies, document ownership. Extracted from `api.py`. |
| `citations.py` | — | Checks an answer cites documents it was actually given. |
| `grounding.py` | 256 | Numeric grounding: are the figures in an answer backed by sources? |
| `context_budget.py` | — | Turns a message list into a bounded history block that fits the prompt. |
| `llm_guard.py` | 670 | Keeps every model call inside the provider's rate limits. |

### Agents and tools
| file | lines | what it does |
|---|---:|---|
| `agent_tools.py` | 652 | The real tools the agent team advertises (web search, calculator, …). |
| `web_tools.py` | 337 | Web search with a provider priority order. |
| `mcp_client.py` | — | The MCP **client**: connects to the MCP agent server over SSE. |
| `routing.py` | 312 | Intent routing for the multi-agent chat path. Extracted verbatim from `api.py`. |
| `prompt_rules.py` | — | Shared prompt rules for every LLM entry point. |
| `model_tiers.py` | — | Two model tiers, trading quality against latency and cost. |

### Identity, security, privacy
| file | lines | what it does |
|---|---:|---|
| `auth.py` | 641 | JWT tokens, API keys, password hashing, `get_current_user`. |
| `security.py` | 443 | Input validation, file-upload protection, rate limiting, sanitisation. |
| `security_hardening.py` | 655 | Headers, brute-force, audit log, CSRF. |
| `pii.py` | — | Redacts high-risk identifiers before text is persisted or shipped. |
| `secrets.py` | — | Docker-secret reader, with env/file fallback. |
| `request_context.py` | — | Per-request identity carried in `contextvars`, so deep code knows who is asking. |
| `silent.py` | — | One helper for every `except: pass`, so each states what it loses. |

### Storage and state
| file | lines | what it does |
|---|---:|---|
| `database.py` | 441 | The SQLAlchemy layer (Postgres in production, SQLite in tests). |
| `pg_store.py` | 324 | Durable persistence for conversations and messages. |
| `redis_store.py` | — | Redis caching layer; optional, with in-process fallback. |
| `memory.py` | 666 | `ConversationMemory` (chat history) and `FactMemory` (`/remember`, `/recall`). |
| `database_improvements.py` | — | Database health checks. |
| `recording.py` | 476 | Records and replays the eval suite's non-deterministic inputs, so evals are comparable. |

### Observability
| file | lines | what it does |
|---|---:|---|
| `advanced_monitoring.py` | 419 | Alerts, business metrics, cost tracking, SLOs. |
| `prometheus_metrics.py` | 659 | Prometheus middleware and the `/metrics` payload. |
| `monitoring.py` | — | Tracks what the agent did, errors, performance. |

### Config and plumbing
| file | lines | what it does |
|---|---:|---|
| `config.py` | — | Configuration management. |
| `api_utils.py` | 261 | Small helpers that had accumulated in `api.py`; moved out because none depends on the app. |
| `websocket.py` | 398 | Real-time chat over WebSocket. |
| `cicd_improvements.py` | 460 | Feature flags. |
| `provider_client.py` | 136 | Chooses the SDK/HTTP client for a given model name (`build_client`, `provider_for_model`) — the one module the earlier index missed. |
| `__init__.py` | — | Package marker with a short index of what the package contains. |

---

## `frontend/`

Next.js 16. 34 `.tsx` files, 6 routes:

| route | file |
|---|---|
| `/` | `src/app/page.tsx` |
| `/login` | `src/app/login/page.tsx` |
| `/agents` | `src/app/agents/page.tsx` |
| `/dashboard` | `src/app/dashboard/page.tsx` |
| `/documents` | `src/app/documents/page.tsx` |
| `/settings` | `src/app/settings/page.tsx` |

Six component groups under `src/components/`: `chat`, `landing`, `layout`,
`markdown`, `providers`, `ui`.

**No frontend tests exist** — out of scope by the owner's decision (backend and
LLMOps presentation). Recorded, not hidden — see `COVERAGE_API.md`.

---

## `tests/`

**62 test modules**, not 23 (the README says 23; it is out of date). No source
tree under `tests/`. `conftest.py` holds the `journey` fixture — the booted app
used by nearly every integration test, defined at line 45.

---

## `config/`

`nginx/` (SSL + proxy), `prometheus/` (scrape + alert rules), `grafana/`
(provisioned dashboards), `loki/`, `promtail/`. `k8s/` holds the Kubernetes
manifests and is **out of scope for this audit**, by the owner's decision.
