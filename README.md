# 🤖 Llama Agent - Enterprise Edition

[![CI/CD Pipeline](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml/badge.svg)](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml)
[![Python](https://img.shields.io/badge/Python-3.10-blue?logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104.1-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.28.0-FF4B4B?logo=streamlit)](https://streamlit.io/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?logo=postgresql)](https://www.postgresql.org/)
[![Docker](https://img.shields.io/badge/Docker-10%20Containers-blue?logo=docker)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

A **full-stack AI agent** with Web UI, REST API, WebSocket real-time chat, Authentication, Chat History, File Upload, Multi-Agent System via MCP, and a complete DevOps stack with 10 Docker containers!

---

## ✨ Features

### 🌐 Web Interface (Streamlit)
- Beautiful dark-themed chat UI
- Streaming AI responses with `st.write_stream()`
- **User Login & Sign Up** — accounts stored in PostgreSQL
- **Chat History** — conversations saved, loaded, and deleted
- **File Upload** — drag-and-drop CSV, PDF, TXT, MD, PY, JSON, DOCX
- **Smart CSV Reader** — extracts headers, row count, and sample data
- **Markdown Rendering** — AI responses show bold, code blocks, headers
- Memory visualization
- MCP Multi-Agent team controls

### 🔐 Authentication System
- User registration & login with PostgreSQL
- Password hashing (bcrypt)
- Guest mode (no history saved)
- JWT token-based auth for API
- API key authentication

### 🗄️ PostgreSQL Database
- Synchronous database operations (psycopg2)
- SQLAlchemy ORM models
- Tables: users, conversations, messages, facts, documents, api_keys
- Connection pooling
- Docker secrets for credentials

### 📄 RAG (Document Reading)
- Smart CSV reader: headers + row count + sample rows
- PDF, Word, Text, Markdown, Python, JSON support
- Document search with keyword matching
- Automatic context injection into AI prompts
- When documents are loaded, AI reads them directly

### 🤖 Multi-Agent System (MCP)
- **5 specialized agents** via Model Context Protocol
- Automatic task routing based on query content
- Agent collaboration for complex tasks
- Real-time agent status

| Agent | Specialization |
|-------|---------------|
| 🔍 Research Agent | Information gathering |
| 💻 Coder Agent | Code writing/review |
| ✍️ Writer Agent | Content creation |
| 📊 Analyzer Agent | Data analysis |
| 📋 Planner Agent | Strategic planning |

### 🔌 REST API (FastAPI)
- 20+ API endpoints
- Swagger documentation at `/docs`
- CORS support
- Health checks

### 📡 WebSocket Real-time Chat
- Bidirectional live communication
- Typing indicators
- Connection management
- Message broadcasting
- Auto-reconnection

### 🧠 Memory System
- Conversation history (file-based)
- Fact storage (remember/recall)
- Search capabilities

### 📊 DevOps Stack (10 Docker Containers)

| Service | Port | Purpose |
|---------|------|---------|
| **PostgreSQL** | 5432 | Database (users, conversations, messages) |
| **Redis** | 6379 | Caching layer |
| **FastAPI** | 8000 | REST API + WebSocket |
| **Streamlit** | 8501 | Web UI |
| **Nginx** | 80/443 | SSL termination + load balancing + CDN headers |
| **Prometheus** | 9090 | Metrics collection |
| **Grafana** | 3000 | Monitoring dashboards |
| **Loki** | 3100 | Log aggregation |
| **Promtail** | 9080 | Log shipping from all containers |
| **Node Exporter** | 9100 | System metrics (CPU, RAM, disk) |

### 🔒 Security
- Docker secrets management (API keys, DB passwords, JWT secrets)
- SSL/TLS via Nginx
- Rate limiting
- Password hashing with bcrypt
- `.gitignore` protects all secrets from accidental commits

---

## 🚀 Quick Start

### Option 1: Docker Compose (Recommended)
```bash
# Clone the repo
git clone https://github.com/ravneet-png/llama-agent.git
cd llama-agent

# Set your Groq API key
echo "GROQ_API_KEY=your-key" > .env

# Start everything (10 containers)
docker-compose up -d
```

| Service | URL |
|---------|-----|
| **Web UI** | http://localhost:8501 |
| **API Docs** | http://localhost:8000/docs |
| **Grafana** | http://localhost:3000 (admin/admin) |
| **Prometheus** | http://localhost:9090 |

### Option 2: Local Development
```bash
# Install dependencies
pip install -r requirements.txt

# Start PostgreSQL and Redis
docker-compose up -d postgres redis

# Run Streamlit
streamlit run app.py

# Run API (separate terminal)
uvicorn api:app --reload
```

---

## 📁 Project Structure

```
llama-agent/
├── .github/workflows/main.yml    # CI/CD pipeline
├── config/
│   ├── nginx/nginx.conf          # SSL + Load Balancer + CDN
│   ├── prometheus/prometheus.yml # Metrics scraping
│   ├── grafana/                  # Dashboards + provisioning
│   ├── loki/loki-config.yml      # Log aggregation
│   └── promtail/                 # Log shipping
├── k8s/                          # Kubernetes manifests
├── modules/
│   ├── __init__.py
│   ├── auth.py                   # JWT/API Key auth
│   ├── config.py                 # Enterprise configuration
│   ├── database.py               # PostgreSQL models & connection
│   ├── memory.py                 # Conversation & Fact memory
│   ├── monitoring.py             # Logging & metrics
│   ├── prometheus_metrics.py     # Prometheus metrics
│   ├── rag.py                    # Document reading + Smart CSV reader
│   ├── secrets.py                # Docker secrets reader
│   ├── services.py               # Dependency injection
│   └── websocket.py              # WebSocket module
├── secrets/                      # Docker secrets (NEVER commit real keys)
├── tests/
│   ├── test_tools.py             # Core tests
│   ├── test_enterprise.py        # Enterprise tests
│   └── test_mcp.py               # MCP integration tests
├── app.py                        # Streamlit Web UI (login, chat history, file upload)
├── api.py                        # FastAPI REST API
├── llama_agent1.py               # Single agent with streaming
├── multi_agent.py                # Multi-Agent System
├── mcp_agents_server.py          # MCP Server (5 specialized agents)
├── mcp_client_helper.py          # MCP Client
├── Dockerfile                    # Main app Docker build
├── docker-compose.yml            # 10-service DevOps stack
├── init.sql                      # Database init
├── requirements.txt              # Python dependencies
├── pyproject.toml                # Pytest config
├── .gitignore                    # Protects secrets, caches, logs
└── README.md                     # This file
```

---

## 🧪 Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=modules --cov-report=html

# Run specific test files
pytest tests/test_tools.py -v
pytest tests/test_enterprise.py -v
pytest test_mcp.py -v
```

---

## 🔄 CI/CD Pipeline

8 automated jobs:
1. 🧪 Matrix Testing (Python 3.9, 3.10, 3.11)
2. 🔒 Security Scanning (pip-audit + bandit)
3. 🧹 Code Quality (flake8)
4. 🔗 Integration Tests
5. 🐳 Docker Build & Push
6. 🎪 Deploy Staging
7. 🏭 Deploy Production
8. 📢 Notifications

---

## 📄 License

MIT License
