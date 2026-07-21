"""
Docker Secrets Helper

Reads secrets from Docker secret files (/run/secrets/<name>)
or falls back to environment variables.

Convention:
  GROQ_API_KEY_FILE=/run/secrets/groq_api_key  --> reads file contents
  GROQ_API_KEY=gsk_xxx                         --> uses env var directly
"""

import os


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
        except Exception:
            pass

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
