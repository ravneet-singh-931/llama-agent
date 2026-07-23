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
- **Secrets Management** — API keys never in code

### Monitoring
- **Prometheus + Grafana** — Real-time metrics dashboards
- **Loki + Promtail** — Centralized log aggregation
- **Alert Rules** — 8 automatic alerts for issues

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
- **Main App:** http://localhost:8501
- **Phoenix Dashboard:** http://localhost:6006
- **Grafana Dashboard:** http://localhost:3000 (admin/admin)
- **Prometheus:** http://localhost:9090

### 5. Create an account
- Go to http://localhost:8501
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
│              Nginx (Load Balancer + SSL)                 │
└───────────────────────┬─────────────────────────────────┘
                        │
          ┌─────────────┴─────────────┐
          │                           │
          ▼                           ▼
┌──────────────────┐        ┌──────────────────┐
│  Streamlit UI    │        │   FastAPI REST    │
│  (Port 8501)     │        │   (Port 8000)     │
└────────┬─────────┘        └────────┬─────────┘
         │                           │
         └───────────┬───────────────┘
                     │
         ┌───────────┴───────────────┐
         │                           │
         ▼                           ▼
┌──────────────────┐        ┌──────────────────┐
│   PostgreSQL     │        │      Redis       │
│   (Database)     │        │    (Cache)       │
└──────────────────┘        └──────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│                    AI Pipeline                           │
│                                                         │
│  Query → Router → Search → Re-rank → Answer             │
│                    │                                     │
│                    ▼                                     │
│              ChromaDB (Vectors)                          │
│              Groq API (LLM)                              │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│                 Observability                            │
│                                                         │
│  Prometheus → Grafana (Metrics)                          │
│  Phoenix (AI Traces)                                     │
│  Loki → Promtail (Logs)                                  │
└─────────────────────────────────────────────────────────┘
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Streamlit |
| Backend | FastAPI, Python 3.10 |
| Database | PostgreSQL 15, Redis 7 |
| AI/ML | Groq (Llama 3), ChromaDB, LlamaIndex |
| Monitoring | Prometheus, Grafana, Loki |
| Observability | Phoenix (Arize AI), OpenTelemetry |
| Infrastructure | Docker, Docker Compose, Nginx |
| Security | JWT, Pydantic, Rate Limiting |

## Docker Containers (11 Total)

| Container | Purpose | Port |
|-----------|---------|------|
| `postgres` | Database | 5432 |
| `redis` | Caching | 6379 |
| `api` | REST API | 8000 |
| `web` | Streamlit UI | 8501 |
| `nginx` | Load Balancer | 80, 443 |
| `prometheus` | Metrics | 9090 |
| `grafana` | Dashboards | 3000 |
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
docker-compose up -d --build web

# View logs
docker-compose logs -f web
docker-compose logs -f api

# Check status
docker-compose ps

# Run tests locally
pytest test_advanced_rag.py -v
```

## Project Structure

```
llama-agent/
├── app.py                    # Main Streamlit application
├── api.py                    # FastAPI REST API
├── modules/
│   ├── auth.py               # JWT authentication
│   ├── config.py             # App configuration
│   ├── database.py           # PostgreSQL models
│   ├── memory.py             # Conversation memory
│   ├── monitoring.py         # Logging + metrics
│   ├── security.py           # File validation, rate limiting
│   ├── vector_store.py       # ChromaDB vector search
│   ├── advanced_rag.py       # RAG pipeline + Phoenix tracing
│   └── rag.py                # Legacy RAG (simple)
├── tests/
│   └── test_advanced_rag.py  # 65 unit tests
├── config/
│   ├── grafana/              # Dashboard configs
│   ├── prometheus/           # Metrics + alerts
│   ├── loki/                 # Log aggregation
│   └── promtail/             # Log shipping
├── secrets/                  # API keys (gitignored)
├── docker-compose.yml        # 11 containers
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
| `WebSocket` | `/ws/{token}` | Real-time chat |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | PostgreSQL connection | `postgresql+asyncpg://...` |
| `REDIS_URL` | Redis connection | `redis://redis:6379` |
| `GROQ_API_KEY_FILE` | Path to Groq API key | `/run/secrets/groq_api_key` |
| `JWT_SECRET_KEY_FILE` | Path to JWT secret | `/run/secrets/jwt_secret_key` |
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
