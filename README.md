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
- 🔑 A **Groq API key** ([console.groq.com](https://console.groq.com/)) — free tier works
- (Optional) A **Tavily API key** for web-search powered agents

### 1️⃣ Clone
```bash
git clone https://github.com/ravneet-png/llama-agent.git
cd llama-agent
```

### 2️⃣ Add your secrets (never committed)
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

> 🔐 **Security note:** the `secrets/` directory is **gitignored** (`secrets/` in `.gitignore`).
> Compose mounts these files as **Docker secrets** at `/run/secrets/<name>` — keys never
> appear in code, images, or environment listings. See [SECURITY.md](SECURITY.md).

### 3️⃣ Start the stack
```bash
docker compose up -d
```

### 4️⃣ Open the app

**Kubernetes deployment** (this repo's live setup — ingress on `llama-agent.local`):

| Service | URL |
|---------|-----|
| **Main App (Next.js)** | http://llama-agent.local/ |
| **API Docs (Swagger)** | http://llama-agent.local/api/docs |
| **Streamlit UI (legacy)** | http://llama-agent.local/streamlit/ |
| **Grafana** | http://grafana.llama-agent.local |
| **Prometheus** | http://prometheus.llama-agent.local |
| **Loki** | http://loki.llama-agent.local |
| **Phoenix** | http://phoenix.llama-agent.local |

> Requires `llama-agent.local` (and the `*.llama-agent.local` subdomains) in your
> `hosts` file pointing at the ingress IP. See
> [Kubernetes Deployment](#️-kubernetes-deployment).

**Docker Compose deployment** (ports published on `localhost`):

| Service | URL |
|---------|-----|
| **Main App (Next.js)** | http://localhost:3001 |
| **API Docs (Swagger)** | http://localhost:8000/docs |
| **Streamlit UI (legacy)** | http://localhost:8501 |
| **Nginx (SSL/LB)** | https://localhost |

> Monitoring UIs (Phoenix `:6006`, Grafana `:3002`, Prometheus `:9090`) are **not exposed
> by default** — see [Secure-by-default](#-secure-by-default) below.

### 5️⃣ Create an account
1. Open the app URL for your deployment:
   - **Kubernetes:** http://llama-agent.local/ → **Sign Up**
   - **Compose:** http://localhost:3001 → **Sign Up**
2. Create a username/password, log in, and start chatting
3. Try: `@Research Agent tell me a fun fact about Japan` or `summarize the CRM file`

---

## 🛡️ Secure-by-default

The committed `docker-compose.yml` publishes **only** the user-facing ports
(`3001`, `8000`, `8501`, `80/443`). Infrastructure ports (Postgres, Redis,
Phoenix, Grafana, Prometheus, Loki, node-exporter, MCP) are **internal to the
Docker network** — cloned copies are secure out of the box.

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
| **Infrastructure** | Docker Compose (13 services), Nginx, Kubernetes (k8s/) |
| **Security** | JWT, Docker secrets, rate limiting, brute-force lockout, CSRF, CSP headers, audit logging |

---

## 🐳 Docker Services (13)

| Container | Service | Purpose | Port |
|-----------|---------|---------|------|
| `llama-agent-db` | `postgres` | PostgreSQL 15 (+pgvector) | internal |
| `llama-agent-redis` | `redis` | Caching + WebSocket pub/sub | internal |
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

## 📊 Observability

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
| `REDIS_URL` | Redis connection | `redis://redis:6379` |
| `LLM_MODEL_STRONG` | Strong model (agents, one-shot answers, RAG) | `llama-3.3-70b-versatile` |
| `LLM_MODEL_FAST` | Fast model (streaming chat, reformulation) | `llama-3.1-8b-instant` |
| `MCP_SERVER_URL` | MCP agent server base URL (SSE) | `http://mcp-server:8001/sse` (k8s: `http://llama-agent-mcp-server-service:8001/sse`) |
| `ENVIRONMENT` | `development` or `production` | `production` |

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
├── tests/                    # 200+ tests (217 passing, pytest)
├── config/
│   ├── nginx/                # SSL + reverse proxy config
│   ├── prometheus/           # Metrics + alert rules
│   ├── grafana/              # Provisioned dashboards
│   ├── loki/                 # Log aggregation
│   └── promtail/             # Log shipping
├── k8s/                      # Kubernetes manifests + deploy.sh
├── docker-compose.yml        # 13 services (secure-by-default)
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

**Current status:** 217 passing, 17 deselected (`-m "not mcp"`), across 8 test
modules — covering security hardening, rate limiting, monitoring, RAG,
multi-agent routing/streaming, and the Phoenix span-instrumentation path.

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

> **Note:** add `llama-agent.local` (and `grafana`/`prometheus`/`loki`/`phoenix`.llama-agent.local)
> → ingress IP to your `hosts` file. Ingress routes `/` → Next.js, `/api/*` + `/ws/*` → API,
> `/mcp/*` → MCP, `/streamlit/*` → Streamlit; the monitoring hostnames route to
> Grafana / Prometheus / Loki / Phoenix respectively. Images come from Docker Hub
> (`ravneet231/llama-agent*`) — build & push first, and **replace all placeholders in
> `k8s/secrets.yaml`** before applying.

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
