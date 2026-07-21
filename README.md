# 🤖 Llama Agent - Enterprise Edition

[![CI/CD Pipeline](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml/badge.svg)](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml)
[![Docker](https://img.shields.io/badge/Docker-ravneet231%2Fllama--agent-blue?logo=docker)](https://hub.docker.com/r/ravneet231/llama-agent)
[![Python](https://img.shields.io/badge/Python-3.9%20%7C%203.10%20%7C%203.11-blue?logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104.1-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.28.0-FF4B4B?logo=streamlit)](https://streamlit.io/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?logo=postgresql)](https://www.postgresql.org/)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-1.28-326CE5?logo=kubernetes)](https://kubernetes.io/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

A **full-stack AI agent** with Web UI, REST API, WebSocket real-time chat, Authentication, PostgreSQL database, Multi-Agent System, and Kubernetes deployment!

---

## ✨ Features

### 🌐 Web Interface (Streamlit)
- Beautiful chat UI
- Real-time responses
- Document upload
- Memory visualization

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

### 🔐 Authentication System
- JWT token-based auth
- API key authentication
- User registration & login
- Password hashing (bcrypt)
- Role-based access control
- Refresh token rotation

### 🗄️ PostgreSQL Database
- Async database operations (asyncpg)
- SQLAlchemy ORM models
- User, Conversation, Message, Fact, Document tables
- Database migrations (Alembic)
- Connection pooling

### 🤖 Multi-Agent System
- 5 specialized agents
- Automatic task routing
- Agent collaboration
- Team orchestration

### 🧠 Memory System
- Conversation history
- Fact storage
- Search capabilities

### 📄 RAG (Document Reading)
- PDF, Word, Text support
- Document search
- Knowledge base

### 📊 Monitoring & Logging
- Performance metrics
- Error tracking
- Health checks

### ⚙️ Enterprise Architecture
- Environment-based configuration
- Feature flags
- Service dependency injection
- Rate limiting

---

## 🚀 Quick Start

### Option 1: Docker Compose (Recommended)
```bash
# Clone the repo
git clone https://github.com/ravneet-png/llama-agent.git
cd llama-agent

# Set your API key
echo "GROQ_API_KEY=your-key" > .env

# Start everything (PostgreSQL + Redis + API + UI)
docker-compose up -d
```
- API: http://localhost:8000/docs
- UI: http://localhost:8501
- WebSocket: ws://localhost:8765

### Option 2: Web Interface
```bash
streamlit run app.py
```
Open http://localhost:8501

### Option 3: REST API
```bash
uvicorn api:app --reload
```
Open http://localhost:8000/docs

### Option 4: Kubernetes
```bash
# Configure secrets
kubectl create secret generic app-secrets \
  --from-literal=groq-api-key=YOUR_KEY \
  --from-literal=jwt-secret-key=YOUR_SECRET

# Deploy
./k8s/deploy.sh
```

---

## 📁 Project Structure

```
llama-agent/
├── .github/workflows/main.yml    # CI/CD pipeline (8 jobs)
├── k8s/                           # Kubernetes manifests
│   ├── namespace.yaml
│   ├── postgres.yaml
│   ├── redis.yaml
│   ├── api.yaml
│   ├── ingress.yaml
│   ├── secrets.yaml
│   └── deploy.sh
├── modules/
│   ├── __init__.py
│   ├── memory.py                 # Conversation & Fact memory
│   ├── rag.py                    # Document reading & search
│   ├── monitoring.py             # Logging & metrics
│   ├── database.py               # PostgreSQL models & connection
│   ├── auth.py                   # JWT/API Key authentication
│   ├── websocket.py              # WebSocket real-time chat
│   ├── config.py                 # Enterprise configuration
│   └── services.py               # Dependency injection
├── tests/
│   ├── test_tools.py             # Core tests (23 tests)
│   └── test_enterprise.py        # Enterprise tests (17 tests)
├── app.py                        # Streamlit Web UI
├── api.py                        # FastAPI REST API (v3.0)
├── multi_agent.py                # Multi-Agent System
├── llama_agent1.py               # Main agent (14 tools)
├── Dockerfile                    # Docker build
├── docker-compose.yml            # Local dev stack
├── init.sql                      # Database init
├── requirements.txt              # Dependencies
├── pyproject.toml                # Pytest config
└── README.md                     # This file
```

---

## 🔌 API Endpoints

### Authentication
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/auth/register` | POST | Register new user |
| `/auth/login` | POST | Login |
| `/auth/refresh` | POST | Refresh token |
| `/auth/me` | GET | Current user (JWT) |

### Chat
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Chat with agent |
| `/ws/{user_id}` | WS | Real-time WebSocket chat |

### Memory & Facts
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/memory` | GET | Get conversation memory |
| `/memory` | DELETE | Clear memory |
| `/facts` | GET | List all facts |
| `/remember` | POST | Store a fact |
| `/recall` | POST | Recall a fact |

### Documents
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/documents` | GET | List loaded documents |
| `/documents/upload` | POST | Upload document |
| `/documents/search` | POST | Search documents |

### System
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/metrics` | GET | Performance metrics |
| `/tools` | GET | List available tools |
| `/ws/status` | GET | WebSocket status |

---

## 🤖 Multi-Agent System

| Agent | Specialization |
|-------|---------------|
| 🔍 Research Agent | Information gathering |
| 💻 Coder Agent | Code writing/review |
| ✍️ Writer Agent | Content creation |
| 📊 Analyzer Agent | Data analysis |
| 📋 Planner Agent | Strategic planning |

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

## 🧪 Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=modules --cov-report=html

# Run specific test file
pytest tests/test_enterprise.py -v
```

---

## 📄 License

MIT License
