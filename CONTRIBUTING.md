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
   git remote add upstream https://github.com/ravneet-png/llama-agent.git
   ```

4. **Create a branch**
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Setup

### Prerequisites
- Python 3.10+
- Docker Desktop
- Git

### Setup Steps

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install pytest
   ```

2. **Start Docker services**
   ```bash
   docker-compose up -d postgres redis
   ```

3. **Run tests**
   ```bash
   pytest test_advanced_rag.py -v
   ```

4. **Start the app**
   ```bash
   streamlit run app.py
   ```

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
# Run all tests
pytest test_advanced_rag.py -v
pytest tests/test_e2e.py -v
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
# Unit tests
pytest test_advanced_rag.py -v

# End-to-end tests
pytest tests/test_e2e.py -v

# Security tests
pytest tests/test_security.py -v

# All tests with coverage
pytest --cov=modules --cov-report=html
```

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
from modules.cache import ResponseCache
```

## Project Structure

```
llama-agent/
├── app.py                    # Main Streamlit application
├── api.py                    # FastAPI REST API
├── modules/
│   ├── auth.py               # Authentication
│   ├── cache.py              # Caching layer
│   ├── cicd_improvements.py  # Feature flags, A/B testing
│   ├── config.py             # Configuration
│   ├── database.py           # Database models
│   ├── database_improvements.py  # Migrations, backups
│   ├── advanced_monitoring.py # Business metrics, alerts
│   ├── memory.py             # Conversation memory
│   ├── monitoring.py         # Basic monitoring
│   ├── security.py           # Basic security
│   ├── security_hardening.py # Advanced security
│   ├── vector_store.py       # ChromaDB integration
│   └── advanced_rag.py       # RAG pipeline
├── tests/
│   ├── test_e2e.py           # End-to-end tests
│   └── test_security.py      # Security tests
├── config/
│   ├── grafana/              # Grafana dashboards
│   ├── prometheus/           # Prometheus config
│   └── feature_flags.json    # Feature flags
├── migrations/               # Database migrations
├── backups/                  # Database backups
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
