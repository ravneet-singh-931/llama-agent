# Llama Agent

A production-grade AI assistant that reads your documents and answers questions — with full security, monitoring, and observability.

## What It Does

Upload documents (PDFs, TXTs, CSVs) and ask questions. The AI reads your files and gives you accurate answers based on their content.

```
You: "What's the refund policy?"
AI:  "Based on the uploaded documents, our refund policy allows..."
```

## Features

### Core
- **Document Q&A** — Upload files, ask questions, get answers from your docs
- **Smart CSV Reader** — Understands headers, row counts, sample data
- **Multi-Agent Team** — 5 specialized AI agents for complex queries
- **Real-time Chat** — WebSocket-powered live responses

### Security
- **JWT Authentication** — Secure login/signup
- **File Validation** — Checks types, sizes, scans for threats
- **Rate Limiting** — Prevents abuse
- **Brute-Force Protection** — Auto-locks IPs/usernames after repeated failures
- **Security Headers** — CSP, HSTS, X-Frame-Options, and more on every response
- **Audit Logging** — Login, registration, uploads, and rate-limit events
- **Input Sanitization** — SQL injection / XSS / command injection detection
- **CSRF Protection** — Token-based request validation
- **Secrets Management** — API keys never in code (Docker secrets)

### Monitoring
- **Prometheus + Grafana** — Real-time metrics dashboards (12 panels)
- **Loki + Promtail** — Centralized log aggregation
- **Alert Rules** — 8 automatic alerts for issues
- **Enterprise Dashboard** — `/monitoring/dashboard` (business metrics, costs, SLA, alerts)
- **Cost Tracking** — Groq API spend per model/user/day
- **SLA Monitoring** — Uptime, response-time, and error-rate compliance

### Observability
- **Phoenix (Arize AI)** — Traces every AI step
- **OpenTelemetry** — See exactly what the AI is thinking
- **Debug Dashboard** — Find why answers are wrong

## Quick Start

### Prerequisites
- Docker Desktop installed and running
- Git

### 1. Clone the repo
```bash
git clone https://github.com/ravneet-png/llama-agent.git
cd llama-agent
```

### 2. Add your API key
```bash
# Create secrets directory
mkdir -p secrets

# Add your Groq API key
echo "gsk_YOUR_KEY_HERE" > secrets/groq_api_key.txt

# Create other secrets
echo "your_postgres_password" > secrets/postgres_password.txt
echo "your_jwt_secret" > secrets/jwt_secret_key.txt
echo "admin_password" > secrets/grafana_admin_password.txt
```

### 3. Start everything
```bash
docker-compose up -d
```

### 4. Open the app
- **Main App (Next.js):** http://localhost:3001 (via Nginx: https://localhost)
- **Streamlit UI (legacy):** http://localhost:8501
- **API Docs (Swagger):** http://localhost:8000/docs
- **Grafana Dashboard:** http://localhost:3002 (admin / password in `secrets/grafana_admin_password.txt`)
- **Prometheus:** http://localhost:9090
- **Phoenix Dashboard:** http://localhost:6006

### 5. Create an account
- Go to http://localhost:3001 (or http://localhost:8501)
- Click "Sign Up"
- Create username/password
- Login and start chatting!

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User (Browser)                        │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│              Nginx (Load Balancer + SSL + CDN headers)   │
└───────────────┬──────────────────────┬──────────────────┘
                │                      │
                ▼                      ▼
┌─────────────────────┐        ┌──────────────────────────┐
│  Next.js UI (main)  │        │    Streamlit UI (legacy) │
│  Port 3001/3000     │        │    Port 8501             │
└───────────┬─────────┘        └───────────┬──────────────┘
            │                              │
            ▼                              ▼
┌─────────────────────────────────────────────────────────┐
│               FastAPI REST + WebSocket (8000)            │
│  /chat /chat/stream /ws /auth /documents /monitoring     │
└───────┬───────────────────────────┬─────────────────────┘
        │                           │
        ▼                           ▼
┌──────────────────┐        ┌─────────────────────────────┐
│   PostgreSQL     │        │  MCP Agent Server (SSE)     │
│   (Database)     │        │  Port 8001 → 5 agents → Groq│
└────────┬─────────┘        └──────────────┬──────────────┘
         │                                 │
         ▼                                 ▼
┌─────────────────────────────────────────────────────────┐
│                    AI Pipeline                           │
│                                                         │
│  Query → Router → Search → Re-rank → Answer             │
│                    │                                     │
│                    ▼                                     │
│              ChromaDB (Vectors)   Redis (Cache)         │
│              Groq API (LLM)                             │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│                 Observability                            │
│                                                         │
│  Prometheus → Grafana (Metrics)   Phoenix (AI Traces)   │
│  Loki → Promtail (Logs)                                 │
└─────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Next.js 16 (main UI), Streamlit (legacy) |
| Backend | FastAPI, Python 3.10 |
| Database | PostgreSQL 15, Redis 7, ChromaDB |
| AI/ML | Groq (Llama 3), LlamaIndex, 5-agent team |
| Agents | MCP (Model Context Protocol) over SSE |
| Monitoring | Prometheus, Grafana, Loki, Enterprise dashboard |
| Observability | Phoenix (Arize AI), OpenTelemetry |
| Infrastructure | Docker, Docker Compose, Nginx |
| Security | JWT, brute-force protection, audit logs, CSP headers, CSRF, rate limiting |

## Docker Containers (13 Total)

| Container | Purpose | Port |
|-----------|---------|------|
| `postgres` | Database | 5432 |
| `redis` | Caching | 6379 |
| `api` | REST API + WebSocket | 8000 |
| `mcp-server` | MCP agent server (SSE) | 8001 |
| `frontend` | Next.js UI | 3001 |
| `web` | Streamlit UI (legacy) | 8501 |
| `nginx` | Load Balancer + SSL | 80, 443 |
| `prometheus` | Metrics | 9090 |
| `grafana` | Dashboards | 3002 |
| `loki` | Logs | 3100 |
| `promtail` | Log Shipping | — |
| `node-exporter` | System Metrics | 9100 |
| `phoenix` | AI Observability | 6006 |

## Common Commands

```bash
# Start everything
docker-compose up -d

# Stop everything
docker-compose down

# Rebuild after code changes
docker-compose up -d --build api web mcp-server frontend nginx

# View logs
docker-compose logs -f web
docker-compose logs -f api

# Check status
docker-compose ps

# Run tests locally
pytest tests/ -v -m "not mcp"
```

## Kubernetes Deployment

Kubernetes manifests live in `k8s/` and cover all application components:
API, Streamlit web UI, Next.js frontend, MCP agent server, PostgreSQL,
Redis, secrets, namespace, and ingress.

```bash
# One-command deploy (requires kubectl + a cluster with nginx ingress)
./k8s/deploy.sh

# Or step by step
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml        # fill in real values first!
kubectl apply -f k8s/postgres.yaml k8s/redis.yaml
kubectl apply -f k8s/api.yaml k8s/mcp-server.yaml k8s/web.yaml k8s/frontend.yaml
kubectl apply -f k8s/ingress.yaml
```

> **Note:** Add `llama-agent.local` to your `/etc/hosts` file (pointing at your
> cluster's ingress IP) to reach the app. The ingress routes `/` to the Next.js
> frontend, `/api/*` + `/ws/*` to the API, `/mcp/*` to the MCP server, and
> `/streamlit/*` to the Streamlit UI. Images are pulled from Docker Hub
> (`ravneet231/llama-agent*`) — build and push them first. Real secret values
> must replace the placeholders in `k8s/secrets.yaml`.

## Project Structure

```
llama-agent/
├── app.py                    # Streamlit application (legacy UI)
├── api.py                    # FastAPI REST API (enterprise: security + monitoring wired in)
├── mcp_agents_server.py      # MCP agent server (SSE, 5 specialized agents)
├── multi_agent.py            # Multi-agent orchestrator
├── frontend/                 # Next.js 16 main UI
├── modules/
│   ├── auth.py               # JWT authentication
│   ├── config.py             # App configuration
│   ├── database.py           # PostgreSQL models
│   ├── memory.py             # Conversation memory
│   ├── monitoring.py         # Logging + metrics
│   ├── security.py           # File validation, rate limiting
│   ├── security_hardening.py # Headers, brute-force, audit log, CSRF, sanitization
│   ├── advanced_monitoring.py# Alerts, business metrics, cost tracking, SLA
│   ├── cicd_improvements.py  # Feature flags, A/B testing, deployments
│   ├── database_improvements.py # Migrations, backups, health checks
│   ├── mcp_client.py         # MCP client (SSE → agent server)
│   ├── vector_store.py       # ChromaDB vector search
│   ├── advanced_rag.py       # RAG pipeline + Phoenix tracing
│   └── rag.py                # Legacy RAG (simple)
├── tests/
│   ├── test_enterprise.py    # Enterprise module tests (security, monitoring, flags)
│   └── ...                   # 114+ tests total
├── config/
│   ├── feature_flags.json    # Runtime feature flag configuration
│   ├── grafana/              # Dashboard configs
│   ├── prometheus/           # Metrics + alerts
│   ├── loki/                 # Log aggregation
│   └── promtail/             # Log shipping
├── secrets/                  # API keys (gitignored)
├── docker-compose.yml        # 13 containers
├── Dockerfile                # App container
└── Dockerfile.nginx          # Nginx container
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/auth/register` | Create account |
| `POST` | `/auth/login` | Get JWT token |
| `GET` | `/health` | Health check |
| `POST` | `/chat` | Send message |
| `POST` | `/chat/stream` | SSE streaming chat |
| `WebSocket` | `/ws/{user_id}` | Real-time chat |
| `GET` | `/mcp/status` | MCP server + tools status |
| `POST` | `/mcp/tools` | Invoke MCP agent tool (e.g. `coder_agent`) |
| `GET` | `/monitoring/dashboard` | Enterprise metrics, costs, SLA, alerts |
| `GET` | `/metrics` | Prometheus metrics |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection | `postgresql+asyncpg://...` |
| `REDIS_URL` | Redis connection | `redis://redis:6379` |
| `GROQ_API_KEY_FILE` | Path to Groq API key | `/run/secrets/groq_api_key` |
| `JWT_SECRET_KEY_FILE` | Path to JWT secret | `/run/secrets/jwt_secret_key` |
| `MCP_SERVER_URL` | MCP agent server base URL | `http://mcp-server:8001` |
| `LLM_MODEL` | Groq model used by agents (multi-agent, streaming, WebSocket) | `llama-3.3-70b-versatile` |
| `ENVIRONMENT` | `development` or `production` | `development` |

## Troubleshooting

### App won't start
```bash
# Check if Docker is running
docker info

# Check container logs
docker-compose logs web
docker-compose logs api
```

### Groq API key error
```bash
# Verify key exists
cat secrets/groq_api_key.txt

# Rebuild container
docker-compose up -d --build web
```

### Phoenix not showing traces
```bash
# Check Phoenix is running
docker-compose ps phoenix

# Check web container can reach Phoenix
docker exec llama-agent-web python -c "import urllib.request; print(urllib.request.urlopen('http://phoenix:6006/').status)"
```

### Database connection failed
```bash
# Check PostgreSQL is healthy
docker-compose ps postgres

# Restart database
docker-compose restart postgres
```

## Contributing

1. Fork the repo
2. Create a branch (`git checkout -b feature/amazing`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing`)
5. Open a Pull Request

## License

MIT License - see [LICENSE](LICENSE) for details.

## Author

**Ravneet** - [GitHub](https://github.com/ravneet-png)
