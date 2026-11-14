"""
Docker Secrets Helper

Reads secrets from Docker secret files (/run/secrets/<name>)
or falls back to environment variables.

Convention:
  GROQ_API_KEY_FILE=/run/secrets/groq_api_key  --> reads file contents
  GROQ_API_KEY=gsk_xxx                         --> uses env var directly
"""

import os

from modules.silent import degrade

def get_secret(env_var: str, fallback_var: str = None, default: str = None) -> str:
    """
    Get a secret value, checking multiple sources in order:
    1. Docker secret file (e.g., GROQ_API_KEY_FILE=/run/secrets/groq_api_key)
    2. Direct environment variable (e.g., GROQ_API_KEY=gsk_xxx)
    3. Fallback env var
    4. Default value

    Args:
        env_var: Primary env var name (e.g., "GROQ_API_KEY")
        fallback_var: Alternative env var name
        default: Default value if nothing found

    Returns:
        The secret value, or default
    """
    # 1. Check for Docker secret file (env_var + "_FILE")
    file_path = os.getenv(f"{env_var}_FILE")
    if file_path and os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return f.read().strip()
        except Exception as _e:
            degrade("docker secret file present but unreadable (falling back to env var)", _e)

    # 2. Check primary env var
    value = os.getenv(env_var)
    if value:
        return value

    # 3. Check fallback env var
    if fallback_var:
        value = os.getenv(fallback_var)
        if value:
            return value

    # 4. Return default
    return default


DEFAULT_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/llama_agent"


def resolve_database_url(default: str = DEFAULT_DATABASE_URL) -> str:
    """Return the DATABASE_URL with the real Postgres password applied.

    Sources, in order:
      1. DATABASE_URL_FILE / DATABASE_URL (full URL; Docker/K8s secret aware)
      2. default

    If POSTGRES_PASSWORD_FILE / POSTGRES_PASSWORD is set, that password is
    injected into the URL, replacing any password already in it. This keeps
    the app in sync with the password the Postgres server was initialised
    with (the same secret file), so the two can never drift apart.
    """
    from urllib.parse import quote, urlsplit, urlunsplit

    url = get_secret("DATABASE_URL", default=default) or default
    password = get_secret("POSTGRES_PASSWORD")
    if not password:
        return url

    parts = urlsplit(url)
    if "@" in parts.netloc:
        userinfo, hostinfo = parts.netloc.rsplit("@", 1)
        user = userinfo.split(":", 1)[0]
    else:
        user, hostinfo = "postgres", parts.netloc
    netloc = f"{user}:{quote(password, safe='')}@{hostinfo}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
