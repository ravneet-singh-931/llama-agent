# Changelog

All notable changes to Llama Agent will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.1.0] - 2026-07-23

### Added

#### Testing
- End-to-end tests (`tests/test_e2e.py`)
  - Authentication flow tests
  - Chat flow tests
  - RAG pipeline tests
  - Security flow tests
  - Memory flow tests
  - Monitoring flow tests
- Security tests (`tests/test_security.py`)
  - SQL injection tests
  - XSS protection tests
  - Input validation tests
  - Authentication security tests
  - Rate limiting tests
  - File upload security tests
  - CORS tests

#### Security
- Security hardening module (`modules/security_hardening.py`)
  - Input sanitization (SQL injection, XSS, command injection)
  - Filename sanitization (path traversal, special characters)
  - Brute force protection
  - Security headers (HSTS, CSP, X-Frame-Options)
  - Audit logging
  - CSRF protection

#### Performance
- Caching module (`modules/cache.py`)
  - In-memory cache with TTL
  - Redis cache support
  - Response caching
  - Query result caching
  - Embedding caching
  - Cache statistics

#### Monitoring
- Advanced monitoring module (`modules/advanced_monitoring.py`)
  - Custom alerts (email, Slack, webhook)
  - Business metrics tracking
  - Cost tracking (Groq API spending)
  - SLA monitoring
  - Alert deduplication
  - Monitoring dashboard

#### Database
- Database improvements module (`modules/database_improvements.py`)
  - Migration manager (Alembic-like)
  - Backup strategy
  - Connection pooling
  - Query optimizer
  - Database health checks
  - Initial migrations (users, chat_history, documents, api_keys)

#### CI/CD
- CI/CD improvements module (`modules/cicd_improvements.py`)
  - Feature flags manager
  - A/B testing support
  - Deployment manager
  - Rollback capability
  - Deployment history

#### Documentation
- CHANGELOG.md (this file)
- CONTRIBUTING.md
- Updated README.md with new features

### Changed
- Enhanced GitHub Actions workflow with better error handling
- Improved test coverage

## [3.0.0] - 2026-07-23

### Added
- Phoenix (Arize AI) observability
- OpenTelemetry integration
- ChromaDB vector store
- Advanced RAG features (re-ranking, sub-question decomposition, router engine)
- Security hardening (file validation, rate limiting)
- Monitoring dashboards (Prometheus, Grafana)
- Loki + Promtail log aggregation
- 11 Docker containers
- CI/CD pipeline (GitHub Actions)
- REST API (FastAPI)
- WebSocket support
- JWT authentication
- API documentation

### Changed
- Replaced Langfuse with Phoenix for observability
- Updated Docker networking for Phoenix connectivity

### Fixed
- ChromaDB ONNX embedding model download in Docker
- Phoenix traces not showing (network isolation issue)
- Web container network configuration

## [2.0.0] - 2026-07-22

### Added
- ChromaDB vector store for semantic search
- Advanced RAG features
- Re-ranking with CrossEncoder
- Sub-question decomposition
- Router query engine

### Changed
- Improved document chunking
- Enhanced search accuracy

## [1.0.0] - 2026-07-21

### Added
- Initial release
- Streamlit chat interface
- Groq API integration
- File upload support
- Basic RAG pipeline
- PostgreSQL database
- Redis caching
- Docker setup

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| 3.1.0 | 2026-07-23 | Production hardening (tests, security, monitoring) |
| 3.0.0 | 2026-07-23 | Phoenix observability + advanced RAG |
| 2.0.0 | 2026-07-22 | ChromaDB + advanced features |
| 1.0.0 | 2026-07-21 | Initial release |
