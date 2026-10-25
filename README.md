# Llama Agent

[![CI](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml/badge.svg)](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009639.svg?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Next.js](https://img.shields.io/badge/Next.js-16+-000000.svg?logo=nextdotjs)](https://nextjs.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.40+-FF4B4B.svg?logo=streamlit)](https://streamlit.io/)
[![Groq](https://img.shields.io/badge/Groq-API-00F5A8.svg?logo=groq)](https://groq.com/)
[![Phoenix](https://img.shields.io/badge/Phoenix-Observability-43B58B.svg)](https://arize.com/phoenix/)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-000000.svg?logo=opentelemetry)](https://opentelemetry.io/)
[![Prometheus](https://img.shields.io/badge/Prometheus-Monitoring-00C0F0.svg?logo=prometheus)](https://prometheus.io/)
[![Grafana](https://img.shields.io/badge/Grafana-Dashboards-F46842.svg?logo=grafana)](https://grafana.com/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ed.svg?logo=docker)](https://www.docker.com/)

---

## 🚀 Llama Agent — Multi-Agent AI Assistant with RAG and LLMOps

A full-stack, multi-agent AI assistant with **end-to-end observability**. Upload documents (PDF, TXT, CSV, DOCX), chat with a team of five specialized agents (`@Research`, `@Coder`, `@Writer`, `@Planner`, `@Analyzer`), and get answers grounded in your files — every LLM call traced, every span captured.

> **Scope, honestly:** a portfolio project that runs on a single-node local Kubernetes
> cluster (kind, on one PC) with a GitHub Actions CI/CD pipeline (tests → image build →
> staging → production), on the **Groq free tier**. See [Known limits](#-known-limits).

> **Built to be observed.** Every request flows through a single trace: `POST /chat/stream` → `GroqLLM.chat` → tool calls → HTTP send/receive — visible live in Phoenix.

> 🔒 **Secure by default, observable by design.** App-layer auth (JWT, rate limiting, brute-force lockout, CSRF, CSP/HSTS headers, audit logging) and full LLM observability (Phoenix traces, Prometheus metrics, Grafana dashboards, Loki logs) — with all infrastructure locked down and **never exposed publicly**.

```
You:  "summarize the CRM file"
AI:   "This document is a Word document (CRM-3.docx) discussing investment and
       capital allocation strategies, involving financial analysis and business
       metrics (CRM-3.docx, chunk 0)..."

You:  "@Research Agent what happened in Formula 1 this week?"
AI:   (searches the web with Tavily, answers with dated sources)
```

---

## 📚 Documentation

| Document | What it covers |
|----------|---------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Full system design, data flow, and decision records |
| [API_DOCUMENTATION.md](API_DOCUMENTATION.md) | Every REST/WebSocket endpoint with payloads and examples |
| [SECURITY.md](SECURITY.md) | Threat model, security posture, and responsible disclosure |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Contribution workflow and development guide |
| [CHANGELOG.md](CHANGELOG.md) | Version history and notable changes |

---

## ✨ Highlights

- **Multi-Agent Orchestration** — five specialized agents + team mode, keyword-routed (`@Research Agent ...`) or automatic
- **Document RAG** — upload files, retrieve with ChromaDB; answers cite the file + chunk they came from
- **OptimizedRAG** — hybrid search (BM25 + dense vectors), an ONNX cross-encoder reranker, semantic chunking, a corrective-RAG answer check (CRAG) and a versioned semantic cache for repeated questions; contextual chunk summaries (`RAG_CONTEXTUAL=1`) and query decomposition (`RAG_DECOMPOSE=1`) are opt-in. Enabled by `USE_OPTIMIZED_RAG=true` (on in Compose and k8s).
- **Two-Pass Retrieval** — thin answers trigger query reformulation and a second search
- **Streaming Chat** — SSE streaming with agent/tool transcript markers
- **LLMOps on the Groq free tier** — token budgeting and context compaction, rate-limit-aware model routing shared via Redis, 413 shrink-and-retry (`modules/llm_guard.py`)
- **Grounding guardrail** — figures in agent answers are checked against the retrieved sources; unverified ones are marked before the next agent sees them (`modules/grounding.py`)
- **Evaluation suite** — 30 varied agent cases, rule-based checks + LLM judge + grounding rate, baseline comparison; plus a **RAG eval** on a committed sample corpus: retrieval hit@k/MRR for vector vs hybrid vs hybrid + reranker and a cross-user leak check (CI), and document-answer checks (on demand) (`evals/`, see `evals/README.md`)
- **LLM Observability** — every Groq call (agents *and* RAG) emits `GroqLLM.chat` spans to Phoenix via llama-index instrumentation
- **Full DevOps Stack** — Prometheus, Grafana, Loki, Promtail, node-exporter, nginx (SSL + LB)
- **App security** — JWT auth, rate limiting, brute-force lockout, CSRF, audit logging, Docker secrets

---

## 🧠 OptimizedRAG

The deployed API uses **OptimizedRAG** — a drop-in replacement for the basic RAG
pipeline that adds the steps below on top of ChromaDB. Items 4 and 6b are off in the
chat path. `python -m evals.rag_eval retrieval` measures what hybrid search and the
reranker add over plain vector search (see `evals/README.md`).


1. **Hybrid Search** — BM25 keyword (`rank_bm25`) + dense-vector retrieval; keyword-only
   hits (IDs, error codes, names) are added as candidates, always restricted to the
   requesting user's documents. The BM25 index is rebuilt on upload/delete and every
   `RAG_BM25_TTL` seconds (so other replicas' uploads become searchable).
2. **Cross-encoder reranker** — `ms-marco-MiniLM-L-6-v2` (int8 ONNX, ~23 MB) run with
   `onnxruntime` + `tokenizers` (both already come with chromadb — **no PyTorch**). The
   Docker build downloads it at a pinned revision with hash checks
   (`scripts/download_reranker.py`, `RERANKER_MODEL_DIR`) and fails if the model doesn't
   rank a relevant passage first. The startup log says which reranker is in use
   (`🔎 Reranker: onnx (...)`); without the model it falls back to a keyword reranker.
3. **Contextual Retrieval** — **off by default** (`RAG_CONTEXTUAL=1` to enable): a short
   summary of each document, written by the fast model, is prepended to each semantic chunk.
   Until v3.4.8 the summary call had a 100-token budget that the reasoning model used up on
   hidden thinking, so most documents got no summary (the RAG eval showed 1 of 7). The budget
   is fixed (`RAG_SUMMARY_MAX_TOKENS`), but whether summaries improve retrieval is not
   measured yet, so the default stays on plain chunks, the setup the RAG eval measures. The
   upload result says which was used (`contextual retrieval` vs `plain chunks`).
4. **Query Decomposition** — splits complex questions into sub-questions. **Off by default**
   (`RAG_DECOMPOSE=1` to enable).
5. **Corrective RAG (CRAG)** — on by default: the fast model checks every document answer
   against the excerpts it was written from. An unsupported answer triggers a second
   retrieval with a reformulated query; if it still can't be verified it is shown with a
   note and not cached. Verdicts are counted in `llama_agent_rag_crag_verdicts_total`.
6. **Follow-ups** — in chat, a follow-up ("what about the pricing column?") is combined with
   the conversation's last document question before retrieval. (6b) `OptimizedRAG.retrieve`
   can also rewrite follow-ups with the LLM when given chat history (`RAG_QUERY_REWRITE`),
   but the chat path does not pass history to it, so this rewrite is not used there.
7. **Semantic Cache** — a repeated question returns the cached answer (per user;
   invalidated when that user's documents change). A hit needs cosine distance ≤ 0.05
   (`RAG_CACHE_MAX_DISTANCE`) **and** the same numbers, years and quarters in both questions.
   The RAG eval found that the old, looser limit (0.3) answered "How much does the Team plan
   cost?" with the cached Team-plan *rate limit*, and a Q2 2025 question with a Q3 2026
   answer. A missed hit only costs tokens; a wrong hit is a wrong answer. Entries carry an answer-format version
   (`SemanticCache.ANSWER_VERSION`), so answers from an older prompt are never served;
   errors, cut-off and unverified answers are never cached.

It is activated with the `USE_OPTIMIZED_RAG=true` environment variable (already set
in `k8s/api.yaml` and `docker-compose.yml`). The HTTP API (`POST /documents/ask`)
and the document-upload flow use it automatically — no code changes needed.

---

## 🚀 Quick Start

### Prerequisites
- 🐳 **Docker Desktop** (or Docker Engine + Compose v2)
- ☸️ **Kubernetes** — Docker Desktop's built-in k8s (enable in settings) with [ingress-nginx](https://kubernetes.github.io/ingress-nginx/), or a kind cluster
- 🔑 A **Groq API key** ([console.groq.com](https://console.groq.com/)) — free tier works
- (Optional) A **Tavily API key** for web-search powered agents

### 1️⃣ Clone
```bash
git clone https://github.com/ravneet-png/llama-agent.git
cd llama-agent
```

### 2️⃣ Deploy — choose your path

#### ☸️ Kubernetes (this repo's live deployment)
1. **Add secrets** — create the same `secrets/*.txt` files as in the Docker Compose path
   below (Groq key, JWT secret, Postgres password, Grafana password, optional Tavily key).
   `deploy.sh` turns them into Kubernetes Secrets and **refuses to deploy** if a file is
   missing, empty, or still a placeholder. (`k8s/secrets.yaml` is only a template.)
   With `ESO_ENABLED=true` the Secrets come from AWS Secrets Manager instead.
2. **Deploy:**
   ```bash
   ./k8s/deploy.sh
   ```
3. **Point your `hosts` file** at the ingress IP (e.g. `127.0.0.1 llama-agent.local`).
4. **Open the app:**
   | Service | URL |
   |---------|-----|
   | **Main App (Next.js)** | https://llama-agent.local/ |
   | **API Docs (Swagger)** | https://llama-agent.local/api/docs |
   | **Streamlit UI (legacy)** | https://llama-agent.local/streamlit/ |

#### 🐳 Docker Compose (alternative — not running by default here)
1. **Add your secrets** (never committed):
   ```bash
   mkdir -p secrets

   # Groq API key (required)
   echo "gsk_YOUR_KEY_HERE" > secrets/groq_api_key.txt

   # Tavily API key (optional; used by research/analyzer agents)
   echo "tvly_YOUR_KEY_HERE" > secrets/tavily_api_key.txt

   # Postgres password + JWT secret (generate strong random values!)
   python -c "import secrets; print(secrets.token_hex(32))" > secrets/postgres_password.txt
   python -c "import secrets; print(secrets.token_hex(32))" > secrets/jwt_secret_key.txt
   python -c "import secrets; print(secrets.token_urlsafe(16))" > secrets/grafana_admin_password.txt
   ```
   > 🔐 The `secrets/` directory is **gitignored** — Compose mounts these as Docker secrets at
   > `/run/secrets/<name>`, so keys never appear in code, images, or env listings.
2. **Start the stack:**
   ```bash
   docker compose up -d
   ```
3. **Open the app** (only after the command above — Compose is stopped by default in this
   repo's live setup, where Kubernetes is the active deployment):
   | Service | URL |
   |---------|-----|
   | **Main App (Next.js)** | http://localhost:3001 |
   | **API Docs (Swagger)** | http://localhost:8000/docs |
   | **Streamlit UI (legacy)** | http://localhost:8501/streamlit/ |
   | **Nginx (SSL/LB)** | https://localhost (self-signed cert generated on first start; put real certs in `config/nginx/ssl/`) |

### 3️⃣ Create an account
1. Open the app → **Sign Up**
2. Create a username/password, log in, and start chatting
3. Try: `@Research Agent tell me a fun fact about Japan` or `summarize the CRM file`

---

## 🛡️ Secure-by-default

The committed `docker-compose.yml` publishes the user-facing ports
(`3001`, `8000`, `8501`, `80/443`). The monitoring UIs are bound to **localhost only**
(`127.0.0.1`), so you can open them on your own machine but nobody else can:
Grafana http://localhost:3002, Prometheus http://localhost:9090,
Phoenix http://localhost:6006. Postgres, Redis, Loki, node-exporter and the MCP server
are **internal to the Docker network**.

The Kubernetes deployment is locked down the same way: the ingress exposes only the
app routes (`/`, `/api`, `/ws`, `/streamlit`). The MCP server is internal-only,
`/metrics` refuses proxied requests, and the observability UIs sit behind basic-auth
(or SSO with `OIDC_ENABLED=true`).

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User (Browser)                        │
└───────────────────────┬─────────────────────────────────┘
                        │  HTTPS
                        ▼
┌─────────────────────────────────────────────────────────┐
│        Nginx (SSL + Load Balancer + security headers)    │
└───────┬──────────────┬──────────────┬───────────────────┘
        │              │              │
        ▼              ▼              ▼
┌──────────────┐ ┌─────────────┐ ┌───────────────────────┐
│ Next.js UI   │ │ Streamlit   │ │ FastAPI (8000)        │
│ :3001        │ │ UI :8501    │ │ /chat /chat/stream    │
│              │ │ (legacy)    │ │ /ws /auth /documents  │
└──────────────┘ └─────────────┘ │ /monitoring /metrics  │
                                 └───────┬───────────────┘
        ┌───────────────────────────────┬┘───────────┬────────────┐
        ▼                               ▼            ▼            ▼
┌──────────────┐ ┌──────────────────┐ ┌─────────────┐ ┌─────────────┐
│  PostgreSQL  │ │  MCP Agent Svc   │ │  Redis      │ │  ChromaDB   │
│  (state)     │ │  :8001 (SSE)     │ │  (cache/WS) │ │  (vectors)  │
│              │ │  5 agents → Groq │ │             │ │             │
└──────────────┘ └──────────────────┘ └─────────────┘ └─────────────┘
                                 ▼
                        ┌────────────────┐
                        │  Groq (LLMs)   │  qwen/qwen3.8-27b (strong)
                        │                │  openai/gpt-oss-20b (fast)
                        └────────────────┘
        ┌───────────────────────────────┬───────────────────────────────┐
        ▼                               ▼                               ▼
┌──────────────┐                 ┌──────────────┐                 ┌──────────────┐
│  Phoenix     │                 │  Prometheus  │                 │  Loki        │
│  LLM traces  │                 │  → Grafana   │                 │  ← Promtail  │
│  (6006)      │                 │  (metrics)   │                 │  (logs)      │
└──────────────┘                 └──────────────┘                 └──────────────┘
```

**Observability flow:** FastAPI + llama-index are auto-instrumented with
OpenTelemetry; all traces are exported to Phoenix. Every LLM call — whether from
the multi-agent orchestrator or the document RAG pipeline — is routed through
`GroqLLM` (a llama-index wrapper) so it emits a proper `GroqLLM.chat` **LLM span**
with latency, model, and I/O, parented under the originating `POST /chat/stream` span.

---

## 🗄️ Shared State (multi-replica safe)

The API runs **2+ replicas** behind a load balancer, so any state that lives
only inside one container would be invisible to the others. Two stores are
therefore backed by shared infrastructure:

| State | Shared backend | Fallback (single-replica / degraded) |
|-------|---------------|--------------------------------------|
| **Facts** (`remember` / `recall` / `show facts`) | **Redis** (`REDIS_URL`) — one hash `llama:facts` every replica reads/writes | pod-local JSON file (`memory/fact_memory.json`) when Redis is unreachable |
| **Vector index** (document `search` / `list documents`) | **ChromaDB server** (`CHROMA_SERVER_URL`) — one index shared by every replica | pod-local ChromaDB (`PersistentClient`) when the server is unreachable |

- `FactMemory` (`modules/memory.py`) connects to Redis on startup; if
  `REDIS_URL` is unset or down it transparently falls back to the JSON file,
  so local dev and the test suite (no Redis) are unaffected.
- `get_vector_store()` (`modules/vector_store.py`) uses `chromadb.HttpClient`
  when `CHROMA_SERVER_URL` is set, otherwise a local `PersistentClient`.
- The ReAct agent's document/fact tools are rebound at API startup to the
  same shared instances, so the agent and the REST API never diverge.

**Kubernetes:** a `chromadb` Deployment + Service (`k8s/chromadb.yaml`) is
deployed and `CHROMA_SERVER_URL=http://chromadb-service:8000` is set in
`k8s/api.yaml`. **Docker Compose:** a `chromadb` service is included and
`CHROMA_SERVER_URL=http://chromadb:8000` is wired into the `api` service.
Both keep ChromaDB cluster-internal (no published host port).

---

## 💻 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Frontend** | Next.js 16 (main UI), Streamlit (legacy) |
| **Backend** | FastAPI, Python 3.11, asyncpg |
| **Database** | PostgreSQL 15, Redis 7, ChromaDB |
| **AI/ML** | Groq (`qwen/qwen3.8-27b` strong, `openai/gpt-oss-20b` fast), LlamaIndex, onnxruntime (reranker) |
| **Agents** | 5 specialized agents (Research, Coder, Writer, Planner, Analyzer) via MCP/SSE |
| **Observability** | Phoenix (Arize), OpenTelemetry, OpenInference |
| **Monitoring** | Prometheus, Grafana, Loki, Promtail, node-exporter |
| **Infrastructure** | Docker Compose (14 services), Nginx, Kubernetes (k8s/) |
| **Security** | JWT, Docker secrets, rate limiting, brute-force lockout, CSRF, CSP headers, audit logging |

---

## 🐳 Docker Services (14)

| Container | Service | Purpose | Port |
|-----------|---------|---------|------|
| `llama-agent-db` | `postgres` | PostgreSQL 15 | internal |
| `llama-agent-redis` | `redis` | Caching + WebSocket pub/sub + shared fact store | internal |
| `llama-agent-chromadb` | `chromadb` | Shared ChromaDB vector store (all API replicas) | internal |
| `llama-agent-api` | `api` | FastAPI REST + WebSocket + SSE chat | **8000** |
| `llama-agent-mcp` | `mcp-server` | MCP agent server (SSE, 5 agents) | internal |
| `llama-agent-web` | `web` | Streamlit UI (legacy) | **8501** |
| `llama-agent-frontend` | `frontend` | Next.js 16 UI | **3001** |
| `llama-agent-nginx` | `nginx` | SSL + Load Balancer + security headers | **80, 443** |
| `llama-agent-prometheus` | `prometheus` | Metrics collection + alert rules | 127.0.0.1:9090 |
| `llama-agent-grafana` | `grafana` | Monitoring dashboards | 127.0.0.1:3002 |
| `llama-agent-loki` | `loki` | Log aggregation | internal |
| `llama-agent-promtail` | `promtail` | Log shipping → Loki | internal |
| `llama-agent-node-exporter` | `node-exporter` | System metrics | internal |
| `llama-agent-phoenix` | `phoenix` | LLM observability (Arize Phoenix) | 127.0.0.1:6006 |

---

## 🔐 Security

| Feature | Description |
|---------|-------------|
| 🔑 **JWT Authentication** | HttpOnly-cookie JWTs (short-lived access + refresh), bearer tokens for scripts |
| 👥 **Multi-user isolation** | Per-user (and per-guest) history, facts, documents, RAG caches and WebSocket sessions |
| 🔐 **Docker Secrets** | API keys mounted at `/run/secrets/*` — never in code or images |
| 📁 **File Validation** | Upload extension/size checks, filename sanitizing, path confinement |
| 🚦 **Rate Limiting** | Per-IP/per-user limits with configurable thresholds |
| 🛡️ **Brute-Force Protection** | Automatic IP/username lockout after repeated failures |
| 🛡️ **Security Headers** | CSP, HSTS, X-Frame-Options, X-Content-Type-Options |
| 🧼 **Input Sanitization** | Regex sanitizer (script tags, event handlers, null bytes) + a prompt-injection heuristic — applied on non-streaming `/chat` only, not on `/chat/stream` |
| 🛡️ **CSRF Protection** | `SameSite=Lax` HttpOnly auth cookies + same-origin API |
| 📝 **Audit Logging** | Login, registration, uploads, rate-limit events |
| 🔒 **Secure-by-default** | Infra ports never published to the host in committed config |

---

## ✅ Verification

I checked the core flows (chat, agent routing, RAG, upload, MCP, monitoring) by hand in the browser, and confirmed the **multi-replica state fix**: `remember` on one replica is visible to `list facts` on another, and document `search` / `list documents` return consistent results across both API replicas (shared Redis + shared ChromaDB server). The 5 agents, the ReAct agent's 15 tools and the MCP server's 7 tools are covered by the automated test suite (see [Tests](#-tests)); the MCP tests are excluded from the default run (`-m "not mcp"`).

## 🔐 Security & Cloud Hardening

The app ships with app-layer security (JWT, rate limiting,
brute-force lockout, CSRF, CSP/HSTS headers, audit logging, Docker secrets).
On top of that, the 9 cloud-hardening points below are written as committed `k8s/`
manifests. Only some are active in the local lab (no cert-manager, ESO or SSO installed),
and CI does not schema-validate the manifests.
Items that need *your* cloud accounts/credentials are scaffolded (config +
manifests) so activation is a config step, not a rewrite. See
[Cloud Hardening (9 Points)](#☁️-cloud-hardening-9-points) for the full table
and exactly what to fill in.

> The legacy shared basic-auth creds (`admin`/`<monitoring-password>`) still
> protect the monitoring UIs in the **local lab** (no cert-manager/SSO
> installed). That password exists only in the live k8s secret + gitignored
> `secrets/grafana_admin_password.txt` — never committed to the repo. In cloud
> deployments, TLS + SSO (Point 1 & 2) replace this.

---

## ☁️ Cloud Hardening (9 Points)

All 9 points are written as committed Kubernetes manifests in `k8s/`. "Scaffold" means the manifest/config is present and correct, but it
activates only once *you* supply the corresponding cloud account or credential
(nothing is provisioned for you, and no real certs/keys are issued by this
repo).

| # | Hardening point | What's in the repo | Status | You must provide |
|---|-----------------|--------------------|--------|------------------|
| 1 | **TLS / HTTPS** | `k8s/cert-manager.yaml` (Let's Encrypt staging/prod + self-signed issuers); every ingress has a `tls:` block + `cert-manager.io/cluster-issuer` | ✅ manifest | A public FQDN (Let's Encrypt can't issue for `.local`); install cert-manager (`CERT_MANAGER=true`) |
| 2 | **SSO / OIDC** | `k8s/oauth2-proxy.yaml` + `oauth2-proxy-ingress.yaml`; Grafana native OAuth env (optional, disabled by default) | 🔧 scaffold | OIDC client ID/secret/issuer → `oauth2-proxy-secret`; enable `OIDC_ENABLED=true` |
| 3 | **Rate-limit / WAF** | `limit-rps`/`limit-rpm`/`limit-connections` + ModSecurity/OWASP CRS annotations on all ingresses | ✅ manifest | ingress-nginx controller built with ModSecurity enabled |
| 4 | **Pin image tags** | CI pushes an immutable `:<git sha>` tag; `k8s/apply-app.sh` renders it into the app Deployments, so each deploy runs exactly that commit's image | ✅ manifest | Nothing (optionally pin to a digest) |
| 5 | **Managed/HA datastores + backups** | `k8s/postgres-backup-cronjob.yaml` (daily `pg_dump` → PVC); PDBs for api/postgres/redis/chromadb/mcp | ✅ manifest | For true HA, use managed Postgres/Redis (see below); backups run as-is |
| 6 | **Secret manager** | `k8s/external-secrets.yaml` (ESO `ClusterSecretStore` + `ExternalSecret`s for AWS SM, GCP alt commented) | 🔧 scaffold | Cloud secret-manager backend + creds; enable `ESO_ENABLED=true` |
| 7 | **HPA / PDB / quotas / NetworkPolicies** | `k8s/api-hpa.yaml`, `k8s/poddisruptionbudgets.yaml`, `k8s/resourcequota.yaml`, `k8s/networkpolicy.yaml` | ✅ manifest | metrics-server (for HPA); enable `NETWORK_POLICY=true` to enforce |
| 8 | **CI → cloud deploy** | `.github/workflows/main.yml` deploy jobs run real `kubectl apply` (guarded by `KUBE_CONFIG`) | ✅ manifest | A base64 `KUBE_CONFIG` repo secret + pre-created runtime Secrets |
| 9 | **Paid / multi-provider LLM** | `modules/model_tiers.py` provider chain + fallback; `LLMConfig.provider_chain`; offline tests | 🔧 scaffold | Paid provider API keys (OpenAI/Anthropic); set `LLM_PROVIDER_CHAIN` |

### Activating the scaffolded points

**TLS (1):** install cert-manager, then set the ingress host to your public
FQDN and keep `cert-manager.io/cluster-issuer: letsencrypt-prod` (or
`selfsigned` for the `.local` lab). For the local lab, HTTP still works even
without cert-manager (the `tls:` secret is just absent).

**SSO/OIDC (2):** create `oauth2-proxy-secret` with your IdP's
`oauth-client-id`, `oauth-client-secret`, `oauth-cookie-secret`, and
`oidc-issuer-url`, then run `OIDC_ENABLED=true ./k8s/deploy.sh`. This deploys
oauth2-proxy, the callback ingress, swaps the observability ingresses from
basic-auth to external-auth, and flips Grafana's native OAuth on.

**Secret manager (6):** install ESO, create the IAM/cloud credential Secret
referenced by the `ClusterSecretStore` (use IRSA/Workload Identity in prod),
pre-create the remote secrets (`llama-agent/app`, `llama-agent/db`,
`llama-agent/oauth2-proxy`), then `ESO_ENABLED=true ./k8s/deploy.sh` (this
skips the static `k8s/secrets.yaml` so ESO owns the Secrets).

**Managed datastores (5):** for production HA, point `DATABASE_URL` /
`REDIS_URL` at managed Postgres (RDS / Cloud SQL) and Redis (ElastiCache /
Memorystore) instead of the in-cluster Deployments, and keep the backup
CronJob as an extra safety net (or rely on the managed service's native PITR).

**Multi-provider LLM (9):** set `LLM_PROVIDER_CHAIN=groq,openai,anthropic` and
supply the provider keys. The chain + fallback resolver is wired and tested;
the actual non-Groq client construction is the one follow-up that needs your
key + a test pass (Groq stays the instrumented, tracing-preserving default).

### Deploy flags

`./k8s/deploy.sh [env]` reads these env vars (all default **off** so the local
lab keeps working without extra operators):

```
CERT_MANAGER=true ESO_ENABLED=true OIDC_ENABLED=true NETWORK_POLICY=true \
  ./k8s/deploy.sh production
```

The CI pipeline (`.github/workflows/main.yml`) builds & pushes the app, MCP server and
frontend images (`:latest`, `:v7`, `:<git sha>`) and, when a base64 `KUBE_CONFIG` secret is
present, deploys the `:<git sha>` images to staging then production.

---

Every turn is a **full trace** in Phoenix:

- **Agent turns** → `POST /chat/stream` → one LLM span per model call (tool rounds,
  critique and revision included)
- **Document/RAG turns** → `POST /chat/stream` → spans for the answer and the helper calls
  (reformulation, CRAG check)
- **HTTP layer** → `http send` / `http receive` children capture the full request/response

In addition:
- **Prometheus + Grafana** — a pre-provisioned dashboard (18 panels) + 9 alert rules (no Alertmanager, so alerts are not delivered anywhere)
- **Loki + Promtail** — centralized log aggregation
- **Monitoring dashboard** — `GET /monitoring/dashboard` (costs, business metrics, SLA, alerts). In-memory and per replica; the business/SLA numbers count only the non-streaming `/chat` endpoint, not `/chat/stream` (which the main UI uses), and no downtime is ever recorded, so its uptime figure is not meaningful. Use Prometheus/Grafana for real traffic.
- **Cost tracking** — Groq spend per model/user/day
- **`GET /metrics`** and **`GET /metrics/json`** — Prometheus / JSON metric endpoints

---

## 🔌 API Endpoints

Full reference: [API_DOCUMENTATION.md](API_DOCUMENTATION.md) (or `/docs` on the API).

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/auth/register`, `/auth/login` | – | Create account / log in (sets HttpOnly cookies) |
| `POST` | `/auth/refresh`, `/auth/logout` | – | Renew access cookie / log out |
| `GET` | `/health` | – | Health check |
| `POST` | `/chat`, `/chat/stream` | optional | Chat (SSE stream); `agent` = research/coder/writer/analyzer/planner/team |
| `POST` | `/ws/ticket` → `WebSocket /ws/x?ticket=…` | login | Real-time chat |
| `GET/POST` | `/documents`, `/documents/upload`, `/documents/ask` | optional | Per-user RAG |
| `GET/POST/DELETE` | `/conversations…`, `/facts…`, `/memory` | optional | Per-user history & facts |
| `GET` | `/mcp/status` · `POST` `/mcp/tools` | optional · login | MCP server status / run a tool |
| `GET` | `/metrics` | internal | Prometheus (404 through proxies) |
| `GET` | `/metrics/json`, `/monitoring/dashboard` | admin | Metrics JSON, costs, SLA, alerts |

"optional" endpoints work for guests (each browser gets its own private `guest_id`).
Admin endpoints need `users.is_admin = true`
(`UPDATE users SET is_admin = true WHERE username = '...';`).

---

## ⚙️ Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GROQ_API_KEY_FILE` | Path to Groq API key (Docker secret) | `/run/secrets/groq_api_key` |
| `JWT_SECRET_KEY_FILE` | Path to JWT secret (Docker secret) | `/run/secrets/jwt_secret_key` |
| `TAVILY_API_KEY_FILE` | Path to Tavily key (Docker secret) | `/run/secrets/tavily_api_key` |
| `DATABASE_URL` | PostgreSQL async connection — **without** a password; it is injected from `POSTGRES_PASSWORD(_FILE)` | `postgresql+asyncpg://postgres@postgres:5432/llama_agent` |
| `POSTGRES_PASSWORD_FILE` / `POSTGRES_PASSWORD` | Postgres password (Docker secret / k8s Secret) | `/run/secrets/postgres_password` |
| `REDIS_URL` | Redis connection (also the shared fact store) | `redis://redis:6379` |
| `CHROMA_SERVER_URL` | ChromaDB server for the shared vector index (all replicas) | unset → local `PersistentClient` |
| `LLM_MODEL_STRONG` | Strong model (agents, one-shot answers, RAG) | `qwen/qwen3.8-27b` |
| `LLM_MODEL_FAST` | Fast model (streaming chat, reformulation, 429 fallback) | `openai/gpt-oss-20b` |
| `JWT_EXPIRE_MINUTES` / `JWT_REFRESH_EXPIRE_DAYS` | Access / refresh token lifetime | `30` / `7` |
| `USE_OPTIMIZED_RAG` | Hybrid BM25+vector search, reranker, CRAG, semantic cache | `true` (Compose/k8s) |
| `RAG_CONTEXTUAL` / `RAG_SUMMARY_MAX_TOKENS` | Contextual chunk summaries at upload (not measured yet) / their token budget (the fast model reasons first) | `0` / `1024` |
| `RAG_CACHE_MAX_DISTANCE` | Semantic cache: max cosine distance for a repeated question (numbers must also match) | `0.05` |
| `RAG_CRAG` / `RAG_DECOMPOSE` / `RAG_QUERY_REWRITE` | Corrective-RAG answer check / sub-question decomposition / follow-up rewrite | `1` / `0` / `1` |
| `RAG_CRAG_MAX_TOKENS` / `RAG_CRAG_CONTEXT_CHARS` | CRAG check budget (the fast model reasons first) / excerpt characters it sees | `1024` / `12000` |
| `RERANKER_MODEL_DIR` / `RERANKER_THREADS` | ONNX cross-encoder location (set in the image) / onnxruntime threads | `/opt/models/reranker` / `2` |
| `DOC_ANSWER_MAX_TOKENS` | Token budget for a document answer or comparison (min 512) | `2048` |
| `RAG_BM25_TTL` | Seconds before the BM25 keyword index is rebuilt (picks up other replicas' uploads) | `300` |
| `GROQ_PRICING` | JSON price overrides for cost tracking, e.g. `{"qwen/qwen3.8-27b":{"input":0.2,"output":0.6}}` (USD / 1M tokens) | built-in Groq list prices |
| `METRICS_ALLOW_PROXIED` | `1` = serve `/metrics` even through a proxy (off: proxied requests get 404) | `0` |
| `SSL_CN` | Common name for the auto-generated self-signed Nginx cert | `localhost` |
| `MCP_SERVER_URL` | MCP agent server base URL (SSE) | `http://mcp-server:8001/sse` (k8s: `http://llama-agent-mcp-server-service:8001/sse`) |
| `ENVIRONMENT` | `development` or `production` | `production` |
| `LLM_PROVIDER_CHAIN` | **(Point 9)** Ordered providers to try, comma-separated; Groq appended as fallback | `groq` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | **(Point 9)** Paid provider keys for the multi-provider chain | unset |
| `GROQ_MODEL_STRONG` / `GROQ_MODEL_FAST` | Per-provider model overrides (OpenAI/Anthropic variants also supported) | provider defaults |
| `OIDC_ISSUER_URL` / `OAUTH_CLIENT_ID` / `OAUTH_CLIENT_SECRET` | **(Point 2)** OIDC IdP config for oauth2-proxy SSO (in `oauth2-proxy-secret`) | unset |
| `CERT_MANAGER` / `ESO_ENABLED` / `OIDC_ENABLED` / `NETWORK_POLICY` | **(deploy flags)** Opt-in operators/policies for `./k8s/deploy.sh` | `false` |

---

## 📁 Project Structure

```
llama-agent/
├── api.py                    # FastAPI REST API (auth, chat, RAG, monitoring)
├── llama_agent1.py           # GroqLLM llama-index wrapper + streaming agent
├── multi_agent.py            # Multi-agent orchestrator (5 agents) + Phoenix-aware client
├── mcp_agents_server.py      # MCP agent server (SSE, 5 specialized agents)
├── app.py                    # Streamlit application (legacy UI)
├── frontend/                 # Next.js 16 main UI
├── modules/
│   ├── auth.py               # JWT authentication
│   ├── security.py           # File validation, rate limiting, sanitization
│   ├── security_hardening.py # Headers, brute-force, audit log, CSRF
│   ├── advanced_monitoring.py# Alerts, business metrics, cost tracking, SLA
│   ├── agent_tools.py        # Tool loop (run_tool_rounds / run_tool_loop)
│   ├── vector_store.py       # ChromaDB vector search
│   ├── advanced_rag.py       # RAG pipeline + Phoenix tracing
│   ├── memory.py             # Per-user conversation memory
│   ├── mcp_client.py         # MCP client (SSE → agent server)
│   └── secrets.py            # Docker-secret reader (env/file fallback)
├── tests/                    # pytest suite (23 modules)
├── config/
│   ├── nginx/                # SSL + reverse proxy config
│   ├── prometheus/           # Metrics + alert rules
│   ├── grafana/              # Provisioned dashboards
│   ├── loki/                 # Log aggregation
│   └── promtail/             # Log shipping
├── k8s/                      # Kubernetes manifests + deploy.sh
├── docker-compose.yml        # 14 services (secure-by-default)
├── Dockerfile                # App container
└── Dockerfile.nginx          # Nginx container
```

---

## 🧪 Tests

```bash
# Unit + integration tests (MCP server tests excluded by default)
pytest tests/ -q -m "not mcp"

# Run everything
pytest tests/ -q

# Agent quality evaluation against the live models (run inside the API pod)
#   python -m evals.run --tags smoke        # see evals/README.md
```

**Current status (v3.4.8):** 502 passed, 4 skipped, 17 deselected (`-m "not mcp"`), across
23 test modules. The 4 skipped tests need the reranker model file, which CI downloads
(`RERANKER_TEST_MODEL_DIR`); the 17 deselected are the MCP server tests. The suite covers security hardening, rate limiting, monitoring, RAG (including the
reranker and document answers), multi-agent routing/streaming, the LLM guard, grounding,
the Phoenix span-instrumentation path, the Point 9 multi-provider chain/fallback logic, and
multi-tenancy (`test_multitenancy.py`: tenant isolation, guest identity, WS tickets, metrics
exposure, hybrid search, cost tracking, streaming). These are unit/integration tests with
the LLM stubbed; answer quality is measured separately by the eval suite (`evals/`).

---

## ☸️ Kubernetes Deployment

Kubernetes manifests live in `k8s/` and cover API, web, frontend, MCP server,
PostgreSQL, Redis, secrets, namespace, and ingress.

```bash
./k8s/deploy.sh            # one-command deploy (kubectl + nginx ingress)

```

`deploy.sh` applies the namespace first, installs optional operators (cert-manager / ESO)
and waits for their CRDs, creates Secrets from `secrets/*.txt`, then rolls out infra →
app → ingress → monitoring → backups/HPA/PDB → NetworkPolicies.

> **Note:** add `llama-agent.local` → ingress IP to your `hosts` file. Ingress routes
> `/` → Next.js, `/api/*` → API (prefix stripped), `/ws/*` → API WebSocket,
> `/streamlit/*` → Streamlit. MCP stays internal.
> Images come from Docker Hub (`ravneet231/llama-agent*`). CI pushes `:latest`, `:v7` and an
> immutable `:<git sha>`; the CD job renders `:<sha>` into the manifests (`k8s/apply-app.sh`)
> so every deploy is one rollout (and `kubectl rollout undo` works).
>
> **Upgrading an existing cluster:** uploads moved from a PVC to the shared ChromaDB
> index, so the old `uploads-pvc` can be removed: `kubectl -n llama-agent delete pvc uploads-pvc`.
>
> **Staging vs production:** give the GitHub `staging` and `production` environments
> their own `KUBE_CONFIG` (and app secrets) as *environment* secrets, so the two deploy
> jobs never target the same cluster.

> ⚠️ **Port 80 clash:** the Compose stack's `nginx` service also binds host port
> 80/443, which conflicts with the Kubernetes ingress. Run **either** Compose
> **or** k8s — don't start Compose's `nginx` while the k8s ingress is up, or one
> will fail to bind the port. (The API, frontend, db, and redis Compose services
> are fine to run alongside k8s; only `nginx` clashes.)

---

## 🔧 Common Commands

```bash
# Start / stop
docker compose up -d
docker compose down

# Rebuild after code changes
docker compose up -d --build api web frontend

# Logs
docker compose logs -f api
docker compose logs -f frontend

# Status
docker compose ps

# Run tests
pytest tests/ -q -m "not mcp"
```

---

## 🔧 Troubleshooting

### App won't start
```bash
docker info                      # is the daemon running?
docker compose ps                # which services are up?
docker compose logs api          # what failed?
```

### Groq API key error
```bash
# Key file must exist (gitignored, so clones need it)
ls secrets/groq_api_key.txt
# Rebuild the container after adding it
docker compose up -d --build api
```

### Phoenix not showing traces
```bash
docker compose ps phoenix        # is it running?
# (Owner override only) is the port exposed?
curl http://localhost:6006/health
# Check the API can reach it
docker compose logs api | grep -i phoenix
```

### Database connection failed
```bash
docker compose ps postgres redis
docker compose logs postgres
```

---

## 🧭 Known limits

- **One machine.** Staging and production are namespaces on a single-node kind cluster on my
  PC, deployed by a self-hosted GitHub Actions runner; there is no cloud HA.
- **Groq free tier.** Rate limits (7,000 input tokens/min on the strong model) are handled by
  a token budget, model fallback and retries, but `@team` mode (several agents in a row) can
  still hit the limit and return a partial answer.
- **Evaluation is small.** The agent eval has 30 cases and runs on demand, not in CI. The
  RAG eval uses a small made-up corpus (6 documents, 44 questions): good for catching
  regressions in this pipeline, not a benchmark against other systems.
- **No alert delivery.** Prometheus alert rules exist, but no Alertmanager is deployed.
- **Streaming endpoint gaps.** `/chat/stream` (used by the main UI) skips the input
  sanitizer, the prompt-injection heuristic and the in-app business/SLA metrics;
  Prometheus request metrics and Phoenix traces do cover it.

---

## 🤝 Contributing

1. Fork the repo
2. Create a branch (`git checkout -b feature/amazing`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push (`git push origin feature/amazing`)
5. Open a Pull Request

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

---

## 📄 License

[MIT](LICENSE) © Ravneet — see [LICENSE](LICENSE) for details.

---

## ⭐ Star this repo if you find it helpful!
