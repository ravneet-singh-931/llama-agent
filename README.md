# Llama Agent

[![CI](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml/badge.svg)](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
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

## 🚀 Llama Agent — Production-Grade Multi-Agent AI Assistant

A full-stack, multi-agent AI assistant with **end-to-end observability**. Upload documents (PDF, TXT, CSV, DOCX), chat with a team of five specialized agents (`@Research`, `@Coder`, `@Writer`, `@Planner`, `@Analyzer`), and get answers grounded in your files — every LLM call traced, every span captured.

> **Built to be observed.** Every request flows through a single trace: `POST /chat/stream` → `GroqLLM.chat` → tool calls → HTTP send/receive — visible live in Phoenix.

> 🔒 **Secure by default, observable by design.** Enterprise-grade auth (JWT, rate limiting, brute-force lockout, CSRF, CSP/HSTS headers, audit logging) and full LLM observability (Phoenix traces, Prometheus metrics, Grafana dashboards, Loki logs) — with all infrastructure locked down and **never exposed publicly**.

```
You:  "summarize the CRM file"
AI:   "This document is a Word document (CRM-3.docx) discussing investment and
       capital allocation strategies, involving financial analysis and business
       metrics (CRM-3.docx, chunk 0)..."

You:  "@Research Agent tell me a fun fact about Japan"
AI:   "Japan has a vending machine culture, with over 5.5 million vending
       machines across the country — offering drinks, snacks, and even live crabs."
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
- **Document RAG** — upload files, retrieve with ChromaDB, cite every claim with file + chunk
- **OptimizedRAG** — production RAG pipeline with 7 optimizations: hybrid search (BM25 + dense vectors), a cross-encoder reranker, contextual retrieval with semantic chunking, query decomposition, corrective RAG (CRAG), conversational retrieval, and a semantic cache. Enabled by `USE_OPTIMIZED_RAG=true` (on by default in the deployed image).
- **Two-Pass Retrieval** — thin answers trigger query reformulation and a second search
- **Streaming Chat** — SSE streaming with agent/tool transcript markers
- **LLM Observability** — every Groq call (agents *and* RAG) emits `GroqLLM.chat` spans to Phoenix via llama-index instrumentation
- **Full DevOps Stack** — Prometheus, Grafana, Loki, Promtail, node-exporter, nginx (SSL + LB)
- **Enterprise Security** — JWT auth, rate limiting, brute-force lockout, CSRF, audit logging, Docker secrets

---

## 🧠 OptimizedRAG

The deployed API uses **OptimizedRAG** — a drop-in replacement for the basic RAG
pipeline that layers seven production-grade optimizations on top of ChromaDB:

1. **Hybrid Search** — BM25 keyword + dense-vector retrieval, fused
2. **Cross-Encoder Reranker** — re-ranks candidates by true relevance
3. **Contextual Retrieval** — LLM-generated context per chunk (semantic chunking)
4. **Query Decomposition** — splits complex questions into sub-questions
5. **Corrective RAG (CRAG)** — self-checks retrieval and retries on weak context
6. **Conversational Retrieval** — history-aware answers across turns
7. **Semantic Cache** — near-duplicate questions return the cached answer (skips the LLM)

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
1. **Add secrets** — open `k8s/secrets.yaml` and replace every `REPLACE_ME_*` placeholder
   with a real value (Groq key, JWT secret, Postgres password, Grafana password, Tavily key).
   > 🔐 These are placeholders only; real values are supplied at deploy time and never committed.
2. **Deploy:**
   ```bash
   ./k8s/deploy.sh
   ```
3. **Point your `hosts` file** at the ingress IP (e.g. `127.0.0.1 llama-agent.local`).
4. **Open the app:**
   | Service | URL |
   |---------|-----|
   | **Main App (Next.js)** | http://llama-agent.local/ |
   | **API Docs (Swagger)** | http://llama-agent.local/api/docs |
   | **Streamlit UI (legacy)** | http://llama-agent.local/streamlit/ |

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
   echo "change_me_grafana" > secrets/grafana_admin_password.txt
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
   | **Streamlit UI (legacy)** | http://localhost:8501 |
   | **Nginx (SSL/LB)** | https://localhost |

### 3️⃣ Create an account
1. Open the app → **Sign Up**
2. Create a username/password, log in, and start chatting
3. Try: `@Research Agent tell me a fun fact about Japan` or `summarize the CRM file`

---

## 🛡️ Secure-by-default

The committed `docker-compose.yml` publishes **only** the user-facing ports
(`3001`, `8000`, `8501`, `80/443`). Infrastructure ports (Postgres, Redis,
Phoenix, Grafana, Prometheus, Loki, node-exporter, MCP) are **internal to the
Docker network** — cloned copies are secure out of the box.

The Kubernetes deployment is locked down the same way: the ingress exposes only the
app routes (`/`, `/api`, `/ws`, `/mcp`, `/streamlit`); observability (Phoenix, Prometheus,
Grafana, Loki) runs internally and is **never published** to the public.

For local debugging, the owner can add a **gitignored** override (this file is
**not** committed and never ships to users):

```yaml
# docker-compose.override.yml (local only — DO NOT COMMIT)
services:
  phoenix:
    ports: ["6006:6006"]
  grafana:
    ports: ["3002:3000"]
  prometheus:
    ports: ["9090:9090"]
```

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User (Browser)                        │
└───────────────────────┬─────────────────────────────────┘
                        │  HTTPS
                        ▼
┌─────────────────────────────────────────────────────────┐
│              Nginx (SSL + Load Balancer + CDN headers)   │
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
                        │  Groq (LLMs)   │  llama-3.3-70b (strong)
                        │                │  llama-3.1-8b (fast)
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
| **Backend** | FastAPI, Python 3.10, asyncpg |
| **Database** | PostgreSQL 15 (+ pgvector), Redis 7, ChromaDB |
| **AI/ML** | Groq (Llama 3.3-70b / 3.1-8b), LlamaIndex |
| **Agents** | 5 specialized agents (Research, Coder, Writer, Planner, Analyzer) via MCP/SSE |
| **Observability** | Phoenix (Arize), OpenTelemetry, OpenInference |
| **Monitoring** | Prometheus, Grafana, Loki, Promtail, node-exporter |
| **Infrastructure** | Docker Compose (14 services), Nginx, Kubernetes (k8s/) |
| **Security** | JWT, Docker secrets, rate limiting, brute-force lockout, CSRF, CSP headers, audit logging |

---

## 🐳 Docker Services (14)

| Container | Service | Purpose | Port |
|-----------|---------|---------|------|
| `llama-agent-db` | `postgres` | PostgreSQL 15 (+pgvector) | internal |
| `llama-agent-redis` | `redis` | Caching + WebSocket pub/sub + shared fact store | internal |
| `llama-agent-chromadb` | `chromadb` | Shared ChromaDB vector store (all API replicas) | internal |
| `llama-agent-api` | `api` | FastAPI REST + WebSocket + SSE chat | **8000** |
| `llama-agent-mcp` | `mcp-server` | MCP agent server (SSE, 5 agents) | internal |
| `llama-agent-web` | `web` | Streamlit UI (legacy) | **8501** |
| `llama-agent-frontend` | `frontend` | Next.js 16 UI | **3001** |
| `llama-agent-nginx` | `nginx` | SSL + Load Balancer + CDN headers | **80, 443** |
| `llama-agent-prometheus` | `prometheus` | Metrics collection + alert rules | internal |
| `llama-agent-grafana` | `grafana` | Monitoring dashboards | internal |
| `llama-agent-loki` | `loki` | Log aggregation | internal |
| `llama-agent-promtail` | `promtail` | Log shipping → Loki | internal |
| `llama-agent-node-exporter` | `node-exporter` | System metrics | internal |
| `llama-agent-phoenix` | `phoenix` | LLM observability (Arize Phoenix) | internal |

---

## 🔐 Security

| Feature | Description |
|---------|-------------|
| 🔑 **JWT Authentication** | Secure signup/login with token-based auth |
| 🔐 **Docker Secrets** | API keys mounted at `/run/secrets/*` — never in code or images |
| 📁 **File Validation** | Upload type/size checks + threat scanning |
| 🚦 **Rate Limiting** | Per-IP/per-user limits with configurable thresholds |
| 🛡️ **Brute-Force Protection** | Automatic IP/username lockout after repeated failures |
| 🛡️ **Security Headers** | CSP, HSTS, X-Frame-Options, X-Content-Type-Options |
| 🧼 **Input Sanitization** | SQL injection / XSS / command-injection detection |
| 🛡️ **CSRF Protection** | Token-based request validation |
| 📝 **Audit Logging** | Login, registration, uploads, rate-limit events |
| 🔒 **Secure-by-default** | Infra ports never published to the host in committed config |

---

## ✅ Verification

I verified the core flows (chat, agent routing, RAG, upload, MCP, monitoring) end-to-end in the browser, and confirmed the **multi-replica state fix**: `remember` on one replica is visible to `list facts` on another, and document `search` / `list documents` return consistent results across both API replicas (shared Redis + shared ChromaDB server). The 14 tools / 5 agents are covered by the passing test suite (`257 passed, 0 failed`).

## 🔐 Security & Cloud Hardening

The app ships with enterprise-grade app-layer security (JWT, rate limiting,
brute-force lockout, CSRF, CSP/HSTS headers, audit logging, Docker secrets).
On top of that, the `k8s/` manifests are now **production-state ready** — the 9
cloud-hardening points below are implemented as committed, validated manifests.
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

All 9 points are implemented as committed, validated Kubernetes manifests in
`k8s/`. "Scaffold" means the manifest/config is present and correct, but it
activates only once *you* supply the corresponding cloud account or credential
(nothing is provisioned for you, and no real certs/keys are issued by this
repo).

| # | Hardening point | What's in the repo | Status | You must provide |
|---|-----------------|--------------------|--------|------------------|
| 1 | **TLS / HTTPS** | `k8s/cert-manager.yaml` (Let's Encrypt staging/prod + self-signed issuers); every ingress has a `tls:` block + `cert-manager.io/cluster-issuer` | ✅ manifest | A public FQDN (Let's Encrypt can't issue for `.local`); install cert-manager (`CERT_MANAGER=true`) |
| 2 | **SSO / OIDC** | `k8s/oauth2-proxy.yaml` + `oauth2-proxy-ingress.yaml`; Grafana native OAuth env (optional, disabled by default) | 🔧 scaffold | OIDC client ID/secret/issuer → `oauth2-proxy-secret`; enable `OIDC_ENABLED=true` |
| 3 | **Rate-limit / WAF** | `limit-rps`/`limit-rpm`/`limit-connections` + ModSecurity/OWASP CRS annotations on all ingresses | ✅ manifest | ingress-nginx controller built with ModSecurity enabled |
| 4 | **Pin image tags** | `k8s/api.yaml` + `k8s/mcp-server.yaml` use `:v7` + `imagePullPolicy: IfNotPresent`; CI pushes `:v7` | ✅ manifest | CI builds `:v7` on main (already wired); optionally pin to a digest |
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

The CI pipeline (`.github/workflows/main.yml`) builds & pushes `:v7` (app +
MCP server) and, when a base64 `KUBE_CONFIG` secret is present, applies the
core manifests to staging then production (GitHub Environments gate prod).

---

Every turn is a **full trace** in Phoenix:

- **Multi-agent turns** → `POST /chat/stream` → **2 × `GroqLLM.chat`** LLM spans (research + answer)
- **Document/RAG turns** → `POST /chat/stream` → **1 × `GroqLLM.chat`** LLM span (grounded answer)
- **HTTP layer** → `http send` / `http receive` children capture the full request/response

In addition:
- **Prometheus + Grafana** — pre-provisioned dashboards + 8 alert rules
- **Loki + Promtail** — centralized log aggregation
- **Enterprise dashboard** — `GET /monitoring/dashboard` (business metrics, costs, SLA, alerts)
- **Cost tracking** — Groq spend per model/user/day
- **`GET /metrics`** and **`GET /metrics/json`** — Prometheus / JSON metric endpoints

---

## 🔌 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/auth/register` | Create account |
| `POST` | `/auth/login` | Get JWT token |
| `GET` | `/health` | Health check |
| `POST` | `/chat` | Send message |
| `POST` | `/chat/stream` | SSE streaming chat |
| `WebSocket` | `/ws/{user_id}` | Real-time chat |
| `GET` | `/mcp/status` | MCP server + tools status |
| `POST` | `/mcp/tools` | Invoke an MCP agent tool (e.g. `coder_agent`) |
| `GET` | `/documents` | Uploaded-document listing |
| `GET` | `/monitoring/dashboard` | Business metrics, costs, SLA, alerts |
| `GET` | `/metrics` | Prometheus metrics |
| `GET` | `/metrics/json` | Metrics as JSON |

---

## ⚙️ Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GROQ_API_KEY_FILE` | Path to Groq API key (Docker secret) | `/run/secrets/groq_api_key` |
| `JWT_SECRET_KEY_FILE` | Path to JWT secret (Docker secret) | `/run/secrets/jwt_secret_key` |
| `TAVILY_API_KEY_FILE` | Path to Tavily key (Docker secret) | `/run/secrets/tavily_api_key` |
| `DATABASE_URL` | PostgreSQL async connection | `postgresql+asyncpg://postgres:...` |
| `REDIS_URL` | Redis connection (also the shared fact store) | `redis://redis:6379` |
| `CHROMA_SERVER_URL` | ChromaDB server for the shared vector index (all replicas) | unset → local `PersistentClient` |
| `LLM_MODEL_STRONG` | Strong model (agents, one-shot answers, RAG) | `llama-3.3-70b-versatile` |
| `LLM_MODEL_FAST` | Fast model (streaming chat, reformulation) | `llama-3.1-8b-instant` |
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
├── tests/                    # 200+ tests (257 passing, pytest)
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
```

**Current status:** 265 passing, 17 deselected (`-m "not mcp"`), across 9 test
modules — covering security hardening, rate limiting, monitoring, RAG,
multi-agent routing/streaming, the Phoenix span-instrumentation path, and the
Point 9 multi-provider LLM chain/fallback logic.

---

## ☸️ Kubernetes Deployment

Kubernetes manifests live in `k8s/` and cover API, web, frontend, MCP server,
PostgreSQL, Redis, secrets, namespace, and ingress.

```bash
./k8s/deploy.sh            # one-command deploy (kubectl + nginx ingress)

# or step by step
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml          # replace placeholders with real values!
kubectl apply -f k8s/postgres.yaml k8s/redis.yaml
kubectl apply -f k8s/api.yaml k8s/mcp-server.yaml k8s/web.yaml k8s/frontend.yaml
kubectl apply -f k8s/ingress.yaml
```

> **Note:** add `llama-agent.local` → ingress IP to your `hosts` file. Ingress routes
> `/` → Next.js, `/api/*` + `/ws/*` → API, `/mcp/*` → MCP, `/streamlit/*` → Streamlit.
> Images come from Docker Hub (`ravneet231/llama-agent*`) — build & push first, and
> **replace all placeholders in `k8s/secrets.yaml`** before applying.

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
