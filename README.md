# 🤖 Llama Agent

[![CI/CD Pipeline](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml/badge.svg)](https://github.com/ravneet-png/llama-agent/actions/workflows/main.yml)
[![Docker](https://img.shields.io/badge/Docker-ravneet231%2Fllama--agent-blue?logo=docker)](https://hub.docker.com/r/ravneet231/llama-agent)
[![Python](https://img.shields.io/badge/Python-3.9%20%7C%203.10%20%7C%203.11-blue?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Code Style](https://img.shields.io/badge/code%20style-black-black)](https://github.com/psf/black)
[![Security](https://img.shields.io/badge/security-bandit-yellow)](https://bandit.readthedocs.io/)

A powerful AI agent built with **LlamaIndex** and **Groq API** that can perform calculations, text analysis, unit conversion, and more!

---

## ✨ Features

| Tool | Description |
|------|-------------|
| 🧮 Calculator | Evaluate mathematical expressions |
| 📅 DateTime | Get current date and time |
| 📊 Text Analyzer | Word count, character count, sentence count |
| 🔄 Unit Converter | km/miles, kg/pounds, celsius/fahrenheit |
| 🔤 String Operations | uppercase, lowercase, reverse, capitalize |
| 📝 Notes | Simple note-taking tool |

---

## 🚀 Quick Start

### Option 1: Run with Docker (Recommended)

```bash
docker pull ravneet231/llama-agent:latest
docker run -e GROQ_API_KEY=your_api_key ravneet231/llama-agent:latest
```

### Option 2: Run locally

```bash
# Clone the repo
git clone https://github.com/ravneet-png/llama-agent.git
cd llama-agent

# Install dependencies
pip install -r requirements.txt

# Set your API key
export GROQ_API_KEY=your_api_key_here

# Run the agent
python llama_agent1.py
```

---

## 🔧 Setup

1. Get a free API key from [Groq](https://console.groq.com)
2. Set it as an environment variable:
   ```bash
   export GROQ_API_KEY=your_key_here
   ```
3. Run the agent!

---

## 🧪 Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage report
pytest tests/ -v --cov=. --cov-report=term
```

---

## 📦 Project Structure

```
llama-agent/
├── .github/
│   ├── workflows/
│   │   └── main.yml          # Advanced CI/CD pipeline
│   └── environments.yml      # Deployment environments
├── tests/
│   └── test_tools.py         # Unit tests
├── Dockerfile                # Docker build instructions
├── .dockerignore             # Docker ignore file
├── pyproject.toml            # Pytest configuration
├── requirements.txt          # Python dependencies
└── llama_agent1.py           # Main agent code
```

---

## 🔄 CI/CD Pipeline

Every push to GitHub automatically:

| Job | What it does |
|-----|-------------|
| 🧪 Matrix Test | Tests on Python 3.9, 3.10, 3.11 |
| 🔒 Security Scan | Checks for vulnerable packages |
| 🧹 Code Quality | Linting and formatting checks |
| 🐳 Docker Build | Builds and pushes to Docker Hub |
| 🎪 Deploy Staging | Auto-deploys to staging server |
| 🏭 Deploy Production | Manual approval required |
| 📢 Notifications | Alerts on success/failure |

---

## 🛡️ Security

This project uses:
- **Bandit** for security scanning
- **Safety** for dependency vulnerability checks
- **GitHub Secrets** for API key protection

---

## 📊 Code Coverage

View coverage reports at [Codecov](https://codecov.io/gh/ravneet-png/llama-agent)

---

## 📄 License

MIT License - feel free to use and modify!
