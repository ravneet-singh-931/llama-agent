# Contributing to Llama Agent

Thank you for your interest in contributing to Llama Agent! This document provides guidelines and information for contributors.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)
- [Project Structure](#project-structure)

## Code of Conduct

We expect all contributors to:
- Be respectful and inclusive
- Focus on constructive feedback
- Help create a welcoming environment

## Getting Started

1. **Fork the repository**
   ```bash
   # Click "Fork" on GitHub
   ```

2. **Clone your fork**
   ```bash
   git clone https://github.com/YOUR_USERNAME/llama-agent.git
   cd llama-agent
   ```

3. **Add upstream remote**
   ```bash
   git remote add upstream https://github.com/ravneet-singh-931/llama-agent.git
   ```

4. **Create a branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Setup

### Prerequisites
- Python 3.11+
- Docker Desktop
- Git
- Node.js 20+ (only needed for frontend work)

### Setup Steps

1. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   pip install pytest
   ```

2. **Start the full stack (13 containers)**
   ```bash
   docker-compose up -d
   ```

3. **Run tests**
   ```bash
   pytest tests/ -v -m "not mcp"
   ```

4. **Frontend development (optional)**
   ```bash
   cd frontend
   npm install
   npm run dev        # http://localhost:3001
   ```

### Services after startup

| Service | URL |
| --- | --- |
| Main UI (Next.js, via Nginx) | https://localhost |
| Next.js (direct) | http://localhost:3001 |
| API docs (Swagger) | http://localhost:8000/docs |
| Grafana (admin / password in `secrets/grafana_admin_password.txt`) | http://localhost:3002 |
| Prometheus | http://localhost:9090 |
| Phoenix traces | http://localhost:6006 |

## Making Changes

### 1. Create a feature branch
```bash
git checkout -b feature/amazing-feature
```

### 2. Make your changes
- Follow the [Coding Standards](#coding-standards)
- Add tests for new functionality
- Update documentation if needed

### 3. Test your changes
```bash
# Run all tests (MCP tests are excluded; they need the MCP server running)
pytest tests/ -v -m "not mcp"

# Run a single test file
pytest tests/test_security.py -v

# Check syntax
python -m py_compile your_file.py
```

### 4. Commit your changes
```bash
git add .
git commit -m "feat: Add amazing feature

- Description of what you did
- Why you did it
- Any important notes"
```

### 5. Push to your fork
```bash
git push origin feature/amazing-feature
```

### 6. Create a Pull Request
- Go to the original repository
- Click "New Pull Request"
- Select your branch
- Fill out the PR template

## Testing

### Running Tests

```bash
# Core test suite (no MCP server required)
pytest tests/ -v -m "not mcp"

# Individual suites
pytest tests/test_security.py -v       # Security tests
pytest tests/test_enterprise.py -v     # Enterprise / security-hardening tests
pytest tests/test_e2e.py -v            # End-to-end tests
pytest tests/test_tools.py -v          # Tool tests

# MCP tests (requires the MCP agent server on port 8001)
pytest tests/test_mcp.py -v

# All tests with coverage
pytest --cov=modules --cov-report=html
```

> **Note:** MCP tests talk to a live MCP server. Start the full stack
> (`docker-compose up -d`) before running them.

### Writing Tests

- Place tests in the `tests/` directory
- Name test files `test_*.py`
- Name test functions `test_*`
- Use pytest fixtures for common setup
- Aim for clear, descriptive test names

Example:
```python
def test_rate_limiter_blocks_after_limit():
    """Test: Rate limiter blocks after max requests"""
    from modules.security import RateLimiter
    
    limiter = RateLimiter(max_requests=5, window_seconds=1)
    test_ip = "10.0.0.1"
    
    # First 5 should pass
    for i in range(5):
        is_limited, _ = limiter.is_rate_limited(test_ip)
        assert not is_limited
    
    # 6th should be blocked
    is_limited, _ = limiter.is_rate_limited(test_ip)
    assert is_limited
```

## Pull Request Process

### PR Title Format
```
type(scope): description

Examples:
feat(auth): add OAuth2 login
fix(api): handle null response in chat endpoint
docs(readme): update installation instructions
test(security): add XSS protection tests
```

### PR Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Tests pass locally
- [ ] Added tests for new functionality
- [ ] Tested manually (if applicable)

## Checklist
- [ ] Code follows project style
- [ ] Self-reviewed code
- [ ] Comments added for complex code
- [ ] Documentation updated
- [ ] No new warnings
```

### Review Process

1. **Automated checks** must pass (CI/CD)
2. **At least one review** required
3. **Resolve all comments** before merging
4. **Squash and merge** for clean history

## Coding Standards

### Python Style

- Follow PEP 8
- Use type hints where possible
- Keep functions focused and small
- Write descriptive docstrings

```python
def sanitize_input(text: str) -> str:
    """
    Sanitize user input to prevent injection attacks.
    
    Protects against:
    - SQL injection
    - XSS (Cross-Site Scripting)
    - Command injection
    
    Args:
        text: Raw user input
        
    Returns:
        Sanitized text safe for processing
    """
    if not text:
        return text
    
    # Remove script tags
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.IGNORECASE)
    
    return text.strip()
```

### Naming Conventions

- **Files**: `snake_case.py`
- **Classes**: `PascalCase`
- **Functions**: `snake_case`
- **Variables**: `snake_case`
- **Constants**: `UPPER_SNAKE_CASE`

### Import Order

```python
# 1. Standard library
import os
import sys
from datetime import datetime

# 2. Third-party packages
import fastapi
import pytest
from pydantic import BaseModel

# 3. Local modules
from modules.auth import hash_password
from modules.rag_optimizations import SemanticCache
```

## Project Structure

```
llama-agent/
├── api.py                    # FastAPI REST API (REST + SSE + WebSocket + MCP proxy)
├── mcp_agents_server.py      # MCP agent server (SSE, multi-agent routing)
├── mcp_client_helper.py      # MCP client helper
├── multi_agent.py            # Multi-agent orchestration (Groq)
├── init.sql                  # Postgres init schema
├── Dockerfile                # API/web image
├── Dockerfile.nginx          # Nginx reverse proxy image
├── docker-compose.yml        # 13-service Docker setup
├── frontend/                 # Next.js 16 frontend
│   ├── src/                  # App router pages + components
│   ├── package.json
│   └── Dockerfile
├── modules/
│   ├── auth.py               # Authentication (JWT, API keys)
│   ├── advanced_monitoring.py # Business metrics, alerts, cost/SLA tracking
│   ├── advanced_rag.py       # RAG pipeline (advanced)
│   ├── cicd_improvements.py  # Feature flags, A/B testing
│   ├── config.py             # Configuration
│   ├── database.py           # Database models
│   ├── database_improvements.py  # Migrations, backups, health checks
│   ├── mcp_client.py         # MCP client
│   ├── memory.py             # Conversation memory
│   ├── monitoring.py         # Basic monitoring
│   ├── prometheus_metrics.py # Prometheus metrics
│   ├── rag.py                # RAG pipeline
│   ├── security.py           # Basic security
│   ├── security_hardening.py # Brute-force, audit log, input sanitization, headers
│   ├── secrets.py            # Secret management
│   ├── services.py           # Service helpers
│   ├── vector_store.py       # ChromaDB integration
│   └── websocket.py          # WebSocket support
├── tests/
│   ├── test_e2e.py           # End-to-end tests
│   ├── test_enterprise.py    # Enterprise / security-hardening tests
│   ├── test_mcp.py           # MCP tests (needs running server)
│   ├── test_security.py      # Security tests
│   └── test_tools.py         # Tool tests
├── config/
│   ├── grafana/              # Grafana dashboards
│   ├── prometheus/           # Prometheus config + alerts
│   ├── nginx/                # Nginx config
│   ├── feature_flags.json    # Feature flags
│   └── deployments.json      # Deployment tracking (generated)
├── k8s/                      # Kubernetes manifests
├── migrations/               # Database migrations (generated)
├── backups/                  # Database backups (generated)
├── logs/                     # Logs (audit.log, etc.)
├── secrets/                  # Local secrets (gitignored, never commit)
├── docker-compose.yml        # Docker setup
└── requirements.txt          # Python dependencies
```

## Common Tasks

### Adding a New Feature

1. Create feature branch
2. Add code in appropriate module
3. Add tests in `tests/`
4. Update documentation
5. Create PR

### Fixing a Bug

1. Create bugfix branch
2. Write test that reproduces bug
3. Fix the bug
4. Verify test passes
5. Create PR

### Adding a New API Endpoint

1. Add request/response models in `api.py`
2. Add endpoint function
3. Add authentication (if needed)
4. Add tests
5. Update API documentation

## Getting Help

- **Issues**: Open a GitHub issue
- **Discussions**: Use GitHub Discussions
- **Code Review**: Request review on your PR

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
