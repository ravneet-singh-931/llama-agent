# 🤖 Llama Agent - Full Stack

[![CI/CD Pipeline](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml/badge.svg)](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml)
[![Docker](https://img.shields.io/badge/Docker-ravneet231%2Fllama--agent-blue?logo=docker)](https://hub.docker.com/r/ravneet231/llama-agent)
[![Python](https://img.shields.io/badge/Python-3.9%20%7C%203.10%20%7C%203.11-blue?logo=python)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104.1-009688?logo=fastapi)](https://fastapi.tiangolo.com/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.28.0-FF4B4B?logo=streamlit)](https://streamlit.io/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

A **full-stack AI agent** with Web UI, REST API, Multi-Agent System, Memory, RAG, and CI/CD!

---

## ✨ Features

### 🌐 Web Interface (Streamlit)
- Beautiful chat UI
- Real-time responses
- Document upload
- Memory visualization

### 🔌 REST API (FastAPI)
- 15+ API endpoints
- Swagger documentation
- CORS support
- Health checks

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

---

## 🚀 Quick Start

### Option 1: Web Interface
```bash
streamlit run app.py
```
Open http://localhost:8501

### Option 2: REST API
```bash
uvicorn api:app --reload
```
Open http://localhost:8000/docs

### Option 3: Multi-Agent System
```bash
python multi_agent.py
```

### Option 4: Docker
```bash
docker pull ravneet231/llama-agent:latest
docker run -p 8501:8501 -p 8000:8000 ravneet231/llama-agent:latest
```

---

## 📁 Project Structure

```
llama-agent/
├── .github/workflows/main.yml    # CI/CD pipeline
├── modules/
│   ├── memory.py                 # Conversation & Fact memory
│   ├── rag.py                    # Document reading & search
│   └── monitoring.py             # Logging & metrics
├── tests/
│   └── test_tools.py             # Unit tests
├── app.py                        # Streamlit Web UI
├── api.py                        # FastAPI REST API
├── multi_agent.py                # Multi-Agent System
├── llama_agent1.py               # Main agent
├── Dockerfile                    # Docker build
├── requirements.txt              # Dependencies
└── README.md                     # Documentation
```

---

## 🔌 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/chat` | POST | Chat with agent |
| `/health` | GET | Health check |
| `/memory` | GET | Get memory |
| `/memory` | DELETE | Clear memory |
| `/facts` | GET | List facts |
| `/facts` | POST | Add fact |
| `/documents` | GET | List documents |
| `/documents/upload` | POST | Upload document |
| `/documents/search` | POST | Search documents |
| `/metrics` | GET | Get metrics |
| `/tools` | GET | List tools |

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
2. 🔒 Security Scanning
3. 🧹 Code Quality
4. 🔗 Integration Tests
5. 🐳 Docker Build
6. 🎪 Deploy Staging
7. 🏭 Deploy Production
8. 📢 Notifications

---

## 📄 License

MIT License
