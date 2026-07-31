# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 3.x.x   | :white_check_mark: |
| 2.x.x   | :x:                |
| 1.x.x   | :x:                |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security issue, please report it responsibly.

### How to Report

**Please do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please report security vulnerabilities by emailing: **security@llama-agent.dev**

Include the following information:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### Response Timeline

- **Initial response**: Within 48 hours
- **Acknowledgment**: Within 72 hours
- **Fix/Patch release**: Within 7-14 days (depending on severity)

### Security Features

This project includes:
- JWT-based authentication
- File upload validation (type, size, content scanning)
- Rate limiting and brute-force protection
- Security headers (CSP, HSTS, X-Frame-Options, etc.)
- Input sanitization (SQL injection, XSS, command injection detection)
- CSRF protection
- Audit logging
- Secrets management (Docker secrets, never in code)

### Security Scanning

- **Bandit**: Static analysis for Python security issues
- **pip-audit**: Dependency vulnerability scanning
- **flake8**: Critical error detection (E9, F63, F7, F82)

## Security Best Practices

1. **Never commit secrets** — use Docker secrets or environment variables
2. **Rotate API keys** regularly
3. **Keep dependencies updated** — run `pip-audit` regularly
4. **Review audit logs** — check `/monitoring/dashboard` for anomalies
5. **Use HTTPS** — nginx is configured with SSL/TLS
