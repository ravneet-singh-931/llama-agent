# 🤖 Llama Agent - Full Stack

[![CI/CD Pipeline](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml/badge.svg)](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml)
[![Docker](https://img.shields.io/badge/Docker-ravneet231%2Fllama--agent-blue?logo=docker)](https://hub.docker.com/r/ravneet231/llama-agent)
[![Python](https://img.shields.io/badge/Python-3.9%20%7C%203.10%20%7C%203.11-blue?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

A **full-stack AI agent** with Memory, RAG, Monitoring, and CI/CD pipeline!

---

## ✨ Features

### 🧠 Memory System
- **Conversation Memory** - Remembers past conversations
- **Fact Memory** - Stores important facts about you
- **Search** - Finds relevant past conversations

### 📄 RAG (Retrieval Augmented Generation)
- **Read Documents** - PDF, Word, Text, Markdown
- **Search Documents** - Find relevant information
- **Knowledge Base** - Build a knowledge base from files

### 🔧 14 Tools
| Tool | Description |
|------|-------------|
| 🧮 Calculator | Mathematical calculations |
| 📅 DateTime | Current date and time |
| 📊 Text Analyzer | Word/character/sentence count |
| 🔄 Unit Converter | km/miles, kg/pounds, etc. |
| 🔤 String Operations | uppercase, lowercase, reverse |
| 🧠 Remember Fact | Store important facts |
| 🔍 Recall Fact | Recall stored facts |
| 📋 List Facts | View all stored facts |
| 📄 Read Document | Load documents |
| 🔍 Search Documents | Search loaded documents |
| 📚 List Documents | View loaded documents |
| 📊 Memory Stats | View memory statistics |
| 🗑️ Clear Memory | Clear all memory |
| 🏥 Health Check | Agent health and performance |

### 📊 Monitoring & Logging
- **Interaction Logging** - Tracks all conversations
- **Error Tracking** - Logs and tracks errors
- **Performance Metrics** - Response times, uptime
- **Health Checks** - Agent health status

---

## 🚀 Quick Start

### Option 1: Docker (Recommended)

```bash
docker pull ravneet231/llama-agent:latest
docker run -e GROQ_API_KEY=your_key ravneet231/llama-agent:latest
```

### Option 2: Local

```bash
git clone https://github.com/ravneet-png/llama-agent.git
cd llama-agent
pip install -r requirements.txt
export GROQ_API_KEY=your_key
python llama_agent1.py
```

---

## 📁 Project Structure

```
llama-agent/
├── .github/
│   └── workflows/
│       └── main.yml              # CI/CD pipeline
├── modules/
│   ├── memory.py                 # Conversation & Fact memory
│   ├── rag.py                    # Document reading & search
│   └── monitoring.py             # Logging & metrics
├── tests/
│   └── test_tools.py             # Unit tests
├── logs/                         # Auto-created logs
├── llama_agent1.py               # Main agent
├── Dockerfile                    # Docker build
├── requirements.txt              # Dependencies
└── README.md                     # This file
```

---

## 🔄 CI/CD Pipeline

Every push automatically:
1. 🧪 Tests on Python 3.9, 3.10, 3.11
2. 🔒 Security scanning
3. 🧹 Code quality checks
4. 🔗 Integration tests
5. 🐳 Docker build & push
6. 🎪 Deploy to staging
7. 🏭 Deploy to production
8. 📢 Notifications on failure

---

## 📊 Monitoring

View agent metrics:
```python
from modules.monitoring import AgentLogger
logger = AgentLogger()
print(logger.get_metrics())
```

Logs are saved in `logs/` directory:
- `agent.log` - All interactions
- `interactions.json` - Structured interaction data
- `errors.json` - Error tracking
- `metrics.json` - Performance metrics

---

## 🧠 Memory

The agent remembers:
- Past conversations (last 50 messages)
- Important facts you tell it
- Document contents you load

---

## 📄 RAG (Document Reading)

Load documents:
```python
from modules.rag import SimpleRAG
rag = SimpleRAG()
rag.add_document("resume.pdf")
results = rag.search("What skills does the candidate have?")
```

Supported formats: PDF, DOCX, TXT, MD, PY, JSON, CSV

---

## 📄 License

MIT License
