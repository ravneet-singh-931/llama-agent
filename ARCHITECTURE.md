# Llama Agent - Architecture Diagram

## High-Level Architecture

```
                                    ┌─────────────────────┐
                                    │    User (Browser)    │
                                    │   Chrome/Firefox/etc │
                                    └──────────┬──────────┘
                                               │
                                               │ HTTPS
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          LOAD BALANCER LAYER                                │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                         Nginx (Port 80/443)                          │  │
│  │                                                                       │  │
│  │  • SSL Termination (HTTPS → HTTP)                                     │  │
│  │  • Rate Limiting (100 req/min per IP)                                 │  │
│  │  • Load Balancing (round-robin)                                       │  │
│  │  • Static File Caching                                                │  │
│  │  • CORS Headers                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                               │
                          ┌────────────────────┴────────────────────┐
                          │                                         │
                          ▼                                         ▼
┌─────────────────────────────────────┐       ┌─────────────────────────────────────┐
│          APPLICATION LAYER           │       │          API LAYER                  │
│                                     │       │                                     │
│  ┌───────────────────────────────┐  │       │  ┌───────────────────────────────┐  │
│  │  Next.js UI (Port 3001)       │  │       │  │   FastAPI REST (Port 8000)    │  │
│  │                               │  │       │  │                               │  │
│  │                               │  │       │  │  • REST Endpoints             │  │
│  │  • Chat Interface             │  │       │  │  • WebSocket Server           │  │
│  │  • File Upload                │  │       │  │  • JWT Authentication         │  │
│  │  • Login/Signup               │  │       │  │  • Pydantic Validation        │  │
│  │  • Session Management         │  │       │  │  • Rate Limiting              │  │
│  │  • Real-time Streaming        │  │       │  └───────────────────────────────┘  │
│  └───────────────────────────────┘  │       │  ┌───────────────────────────────┐  │
│                                     │       │  │  MCP Agent Server (Port 8001) │  │
│                                     │       │  │  • SSE transport              │  │
│                                     │       │  │  • 5 specialized agents       │  │
│                                     │       │  └───────────────────────────────┘  │
└─────────────────────────────────────┘       └─────────────────────────────────────┘
                          │                                         │
                          └────────────────────┬────────────────────┘
                                               │
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AI PIPELINE LAYER                                  │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                    Query Router (modules/routing)                    │    │
│  │                                                                       │    │
│  │  User Query ──→ Classify ──→ Route to:                               │    │
│  │                              ├─→ Document Search (if files uploaded)  │    │
│  │                              ├─→ Sub-question Decomposition (complex) │    │
│  │                              └─→ General Answer (simple questions)    │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                    │                                        │
│              ┌─────────────────────┼─────────────────────┐                  │
│              │                     │                     │                  │
│              ▼                     ▼                     ▼                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐         │
│  │  Vector Search    │  │  Sub-questions   │  │  General LLM     │         │
│  │  (ChromaDB)       │  │  (LlamaIndex)    │  │  (Groq API)      │         │
│  │                   │  │                   │  │                   │         │
│  │  • Embed query    │  │  • Break into     │  │  • Direct answer  │         │
│  │  • Find similar   │  │    smaller parts  │  │  • No documents   │         │
│  │  • Cosine sim     │  │  • Search each    │  │  • Quick response │         │
│  └────────┬─────────┘  └────────┬─────────┘  └──────────────────┘         │
│           │                     │                                           │
│           └─────────────────────┼─────────────────────┘                     │
│                                 │                                           │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                      Re-ranking (CrossEncoder)                       │    │
│  │                                                                       │    │
│  │  • Top 18 candidates from search                                   │      │
│  │  • Re-score by relevance to original query                           │    │
│  │  • Return top 6 most relevant chunks                                 │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                 │                                           │
│                                 ▼                                           │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     Answer Generation (Groq API)                     │    │
│  │                                                                       │    │
│  │  Context (relevant chunks) + User Query ──→ Groq LLM ──→ Answer     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                               │
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                            DATA LAYER                                        │
│                                                                             │
│  ┌─────────────────────────┐  ┌─────────────────────────┐                 │
│  │   PostgreSQL (Port 5432) │  │    Redis (Port 6379)    │                 │
│  │                          │  │                          │                 │
│  │  • User Accounts         │  │  • Session Cache         │                 │
│  │  • Chat History          │  │  • Rate Limit Counters   │                 │
│  │  • API Keys              │  │  • WebSocket Pub/Sub     │                 │
│  │  • Document Metadata     │  │  • Query Cache           │                 │
│  └─────────────────────────┘  └─────────────────────────┘                 │
│                                                                             │
│  ┌─────────────────────────┐  ┌─────────────────────────┐                 │
│  │  ChromaDB (Vectors)      │  │   File System            │                 │
│  │                          │  │                          │                 │
│  │  • Document Embeddings   │  │  • Uploaded PDFs         │                 │
│  │  • Semantic Search       │  │  • Text Files            │                 │
│  │  • Cosine Similarity     │  │  • CSV Data              │                 │
│  │  • Persistent Storage    │  │  • Documents/            │                 │
│  └─────────────────────────┘  └─────────────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                               │
                                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       OBSERVABILITY LAYER                                    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                        Monitoring Stack                              │    │
│  │                                                                       │    │
│  │  Prometheus ──→ Grafana (Dashboards)                                 │    │
│  │  • Request metrics      • Visual graphs                              │    │
│  │  • Response times       • Alert rules                                 │    │
│  │  • Error rates          • Custom dashboards                           │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                         Logging Stack                                │    │
│  │                                                                       │    │
│  │  Promtail ──→ Loki ──→ Grafana                                       │    │
│  │  • Ship logs         • Store logs     • Query logs                    │    │
│  │  • From containers   • Searchable     • Visualize                     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                       AI Observability                               │    │
│  │                                                                       │    │
│  │  Phoenix (Arize AI)                                                  │    │
│  │  • Trace every RAG step                                              │    │
│  │  • Debug wrong answers                                               │    │
│  │  • See AI thinking process                                           │    │
│  │  • OpenTelemetry spans                                               │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow

### 1. User Uploads Document

```
User ──→ Nginx ──→ Next.js ──→ FastAPI ──→ PostgreSQL (metadata)
                                    │
                                    ├──→ File System (save file)
                                    │
                                    └──→ ChromaDB (create embeddings)
```

### 2. User Asks Question

```
User ──→ Nginx ──→ Next.js ──→ FastAPI
                                    │
                                    ▼
                              Query Router
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
                    ▼               ▼               ▼
              Vector Search   Sub-questions   General LLM
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                                    ▼
                              Re-ranking
                                    │
                                    ▼
                           Answer Generation
                                    │
                                    ▼
                              Next.js ──→ User
```

### 3. Observability Flow

```
AI Pipeline ──→ PhoenixTracer ──→ OpenTelemetry Spans ──→ Phoenix Dashboard
                    │
                    └──→ Prometheus Metrics ──→ Grafana Dashboard
```

## Container Communication

```
┌──────────────┐
│   Nginx      │──────→ Next.js (3001)
└──────────────┘       FastAPI (8000)
       │ HTTP
       ▼
┌──────────────┐
│   Next.js    │──────→ FastAPI (8000)
│              │       PostgreSQL (5432)
└──────────────┘       Redis (6379)
       │               Phoenix (6006)
       │ HTTP
       ▼
┌──────────────┐
│   FastAPI    │──────→ PostgreSQL (5432)
└──────────────┘       Redis (6379)
       │               Groq API (external)
       │               ChromaDB (shared service)
       │ SSE
       ▼
┌──────────────┐
│ MCP Server   │──────→ Multi-Agent System ──→ Groq API
└──────────────┘
```

Three things about this graph that the arrows do not show, all verified in the
config rather than the picture:

* **`Next.js` has no database client.** It reaches the API through a rewrite
  (`frontend/next.config.ts:70`, `/api/:path*` → `api:8000`) and does nothing else,
  so the arrows from `Next.js` to Postgres/Redis/Phoenix are wrong: it is a proxy,
  not a data client. The API remains the only path to data.
* **There are three doors, not one.** `nginx` publishes `80`/`443`; `frontend`
  publishes `3001`; and `api` publishes **`8000`** (`docker-compose.yml:157`,
  "for direct access"). The observability services are all bound to
  `127.0.0.1` instead. So a request to `host:8000` never passes nginx — it skips
  TLS, and it skips nginx's `limit_req`. It does **not** skip the application's own
  rate limiting, security headers, or CORS: `api.py:745` and `api.py:766` implement
  all three in middleware, which is why bypassing the proxy loses encryption and
  nothing else.
* **The rate limit is not 100 req/min.** `nginx.conf:48` declares
  `rate=10r/s` with `burst=20 nodelay`, applied only inside `location /api/`
  (`nginx.conf:149`). `/ws/` and `/` have no `limit_req` at all, and the upstream is
  `least_conn` with a single uncommented server (`nginx.conf:66-76`) — the replica
  lines are commented out, so no balancing is happening today.

## Security Layers

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: Network Security                                    │
│ • Nginx rate limiting                                        │
│ • CORS restrictions                                          │
│ • SSL/TLS encryption                                         │
│ • Security headers (CSP, HSTS, X-Frame-Options, etc.)       │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 2: Authentication                                      │
│ • JWT 30min, refresh 7d                                      │
│ • Password hashing (bcrypt)                                  │
│ • API key authentication                                     │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 3: Input Validation                                    │
│ • Pydantic models                                            │
│ • File type checking                                         │
│ • Size limits (10MB)                                         │
│ • SQL injection prevention                                   │
│ • Input screening (audit only)                               │
│ • Brute-force protection (IP + username lockout)             │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Layer 4: Secrets Management                                  │
│ • Docker secrets (mounted as files, never in env or images)  │
│ • No secrets in git                                          │
│ • Environment variables                                      │
└─────────────────────────────────────────────────────────────┘
```

## Why the context limits are what they are

Attention is O(n²) in sequence length: doubling the tokens in a request roughly
quadruples the attention work, and every extra token is billed input on the way
in and re-billed as growing context on every subsequent turn. **Context is the
scarcest and most expensive resource in the system.** That single fact explains
constants that otherwise look arbitrary:

| Limit | Value | What it protects |
|---|---|---|
| `llm_guard.input_budget()` | 6000 tokens | Admission control at the API boundary (`LLM_INPUT_BUDGET`) |
| `AGENT_HANDOFF_CHARS` | 12000 chars | How much one agent may hand to the next in a DAG step |
| `_MAX_READ_CHARS` | 6000 chars | One window of a document read (paged, not truncated) |
| `AGENT_TOOL_RESULT_CHARS` | 4000 chars | A single tool result fed back to the model |
| `_with_context` history clip | 200 chars/msg | **Known too aggressive** - see Section 10 finding |
| `_trim_middle()` | head 60 / tail 40 | Keeps the instruction head and the recent tail (lost-in-the-middle) |

Two consequences worth stating explicitly:

- **RAG exists because of this cost.** Retrieving 4 relevant chunks beats
  pasting the corpus, and the retrieval budget (`_MAX_RESULTS`, top-k) is the
  same trade at a smaller scale.
- **Long documents are paged, not amputated.** `read_uploaded_document` takes
  a `start_char` offset and reports the total, so an agent can walk a 40-page
  PDF instead of silently answering from its first three pages.

## Evaluation tiers (what runs when, and why)

The eval suite is split by what a check *costs*, because that decides where it
can run. A gate that needs an API key cannot block a PR; a check that runs in
0.2 s should never wait for a nightly job.

| Tier | What it covers | Needs | Runs |
|---|---|---|---|
| **Offline gate** (`evals/gate.py`) | 53 routing decisions + 18 check fixtures | nothing (stdlib) | every PR, in the `test` job, < 1 s |
| **RAG retrieval** (`evals/rag_eval.py`) | hit@6 on the committed corpus, cross-user leaks | embedding model, no LLM | every PR |
| **Agent goldens** (`evals/run.py`) | 60 cases: real answers, real judge, tool recall, trajectory | `GROQ_API_KEY` + a judge call per case | nightly (`.github/workflows/nightly-evals.yml`) |

The reason the first tier exists at all: routing and answer checks are pure
functions, and they are where regressions actually hide. Reorder a keyword
group in `modules/routing.py` and "write a blog post" routes to `general` --
the answer is still *plausible*, so only the golden comparison catches it. The
goldens cost minutes and money; the routing half costs nothing. Splitting them
means the cheap half runs on every PR instead of never.

The nightly job's value is the **diff**, not the pass rate. A single run says
"87%"; two runs say "case `write-essay-topic` flipped, coding dropped 8 points,
fabrication up 3%". `compare()` reports exactly that, and exits non-zero only
on a real regression -- a missing baseline or a missing key degrades to a
notice so the job cannot go red for reasons unrelated to the product.

## Scalability

### Current (Single Server)

```
┌─────────────────────────────────────────────────────────────┐
│                     Single Server                             │
│                                                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │postgres │ │  redis  │ │  api    │ │  web    │           │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘           │
│                                                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │ nginx   │ │prometheus│ │ grafana │ │ phoenix │           │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘           │
│                                                               │
│  ┌─────────┐ ┌─────────┐                                     │
│  │ frontend│ │ mcp-srv │                                     │
│  └─────────┘ └─────────┘                                     │
└─────────────────────────────────────────────────────────────┘
```

### Future (Multi-Server)

```
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│   Server 1       │  │   Server 2       │  │   Server 3       │
│   (App)          │  │   (Database)     │  │   (Monitoring)   │
│                  │  │                  │  │                  │
│  ┌─────┐ ┌─────┐│  │  ┌─────┐ ┌─────┐│  │  ┌─────┐ ┌─────┐│
│  │ api │ │ web ││  │  │pg   │ │redis││  │  │prom │ │graf ││
│  └─────┘ └─────┘│  │  └─────┘ └─────┘│  │  └─────┘ └─────┘│
└──────────────────┘  └──────────────────┘  └──────────────────┘
         │                     │                     │
         └─────────────────────┼─────────────────────┘
                               │
                        Load Balancer
```

Two things this diagram does not say, both verified against the code:

* **The App server can run more than one `api` replica.** The multi-replica bug this
  diagram used to describe — one replica indexing a document another could not
  search — is fixed: `get_vector_store()` (`modules/vector_store.py:415`) connects to a
  **shared ChromaDB service** over `CHROMA_SERVER_URL` (`docker-compose.yml:175`), so
  every replica reads and writes the same vector index, and the semantic cache lives in
  that same shared collection. Token generations, brute-force state, the token budget
  and WebSocket tickets are all Redis-backed for the same reason. **The one thing that
  is not replica-safe is the fallback**: if `chromadb` is unreachable at startup,
  `get_vector_store()` logs `[WARN]` and returns a pod-local `PersistentClient`, which
  recreates the original bug for that pod's whole lifetime. In a multi-replica
  deployment that fallback should be fatal, not graceful.
* **Server 2 is one `pg` and one `redis`, with no replication and no failover.** So
  "multi-server" here means *separating concerns*, not *high availability*: if Server 2
  dies, every replica loses its data tier at once. The App tier is horizontally
  scalable; the database tier is still a single point of failure, and this diagram
  shows it that way deliberately.

---

## Deliberate limits (decisions, not oversights)

These are things this system does **not** do, with the reason. Each was
considered and rejected for a documented constraint - "we don't have X" is an
oversight, "we chose not to have X because Y" is engineering.

| Not implemented | Why not | Revisit when |
|---|---|---|
| **Mid-run replanning** (`_plan_dag` is fixed once planned) | A replan round is an extra strong-tier call. On the free tier `@team` already runs 84-247 s against a 7,000 input-tokens/min cap, so a replan per failed step could exceed the budget entirely. Instead a failed step degrades to a placeholder and the DAG completes. | We move to a paid tier, or a step failing becomes common enough to measure. |
| **Graph checkpointing** (a crashed `@team` run restarts) | Conversation history is persisted (Postgres + Redis), but in-flight DAG state is not. Adopting LangGraph to get `PostgresSaver` would rewrite ~250 working lines and break the OpenInference/Phoenix spans that `InstrumentedGroqClient` exists to preserve. | The 40-line Redis variant (persist `{level, results}` per level) is worth doing before adopting a framework. |
| **SSE, not streamable-http, for the MCP server** | Agent tools (notably `collaborate`) can run for minutes. A streamable-http POST through nginx and the k8s ingress would hit proxy read timeouts and needs resumption support. SSE is the defensible choice for long-running tools. | The MCP SDK drops SSE, or agent tools get fast enough for a request/response model. |
| **No async task queue / webhooks** (`STREAM_MAX_DURATION=300`, `AGENT_LATENCY_BUDGET=180`, `AGENT_STEP_TIMEOUT=150`) | Work is *intended* to be capped at 180 s, inside the 300 s stream ceiling. **It is not: `AGENT_LATENCY_BUDGET` is checked before each DAG level, not as a deadline**, so a level that starts at 179 s can finish past it — `multi_agent.py:1717` records a measured **309 s** `@team` run that exceeded both the budget and the eval's 300 s case timeout. `AGENT_STEP_TIMEOUT` (per level) is the only real bound. A Redis task store + poll + webhook is ~400 lines and a worker process, so the queue is deferred until the budget is enforced as a deadline rather than a pre-flight check. | The budget becomes a real deadline, or a single task legitimately needs more than ~4 minutes of agent work. |
| **No per-tenant enforcement past the token budget** | `llm_guard` counts tokens per subject (Redis-backed, `AGENT_USER_TOKEN_BUDGET`) and refuses over-budget streams, but nothing is billed and nothing is metered per component. On a free tier there is no invoice to protect. | The deployment bills someone, or a shared key is exposed. |
