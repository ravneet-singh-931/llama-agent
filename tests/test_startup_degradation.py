"""What happens when startup does not go perfectly.

The promise in the code is that the app **still starts** when the database is
unreachable — it degrades to memory-only and says so. That promise is what lets a
fresh clone boot with no secrets and reach `/health` to find out what is wrong.

It was only half tested. `test_boot_without_key.py` covers "no LLM key". The
degradation paths below had no test at all, and they are the ones that run on the
worst day: the database is down, Redis is missing, a document cannot be
re-indexed.

Each test below drives a real startup with one thing broken, and asserts the two
halves of the promise:

1. the app comes up and serves traffic, and
2. it does not claim the broken thing worked.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASSWORD = "Str0ng-Passw0rd!x"


def _boot(monkeypatch, tmp_path, **env):
    """Boot the real app and yield a live client."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key-for-startup")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-for-startup-only")
    monkeypatch.setenv("ENVIRONMENT", "development")
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    # Same reason as tests/conftest.py: booting the lifespan installs a global
    # OTel TracerProvider, which OTel allows once per process.
    import phoenix.otel as _photel
    monkeypatch.setattr(_photel, "register", lambda *a, **k: None, raising=False)

    # The rate limiter is a process-wide singleton keyed on client IP, and every
    # request in the suite shares the TestClient's address. Without resetting it
    # this file's requests fail with 429 depending on how many ran before it -
    # which is the limiter working, just not on the thing under test.
    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    import api
    from fastapi.testclient import TestClient

    monkeypatch.setattr(api, "UPLOAD_ROOT", str(tmp_path / "uploads"))
    os.makedirs(str(tmp_path / "uploads"), exist_ok=True)
    return api, TestClient


# ------------------------------------------------- the database is down --

def test_the_app_starts_even_when_the_database_is_unreachable(monkeypatch, tmp_path):
    """Startup must survive a dead database.

    This is the difference between "cannot start" and "running memory-only", and
    the app is explicitly designed for the second. If `init_db()` ever stopped
    raising into a caught handler, a database outage would become a boot loop.
    """
    api, TestClient = _boot(monkeypatch, tmp_path)

    async def _broken_init(self):
        raise ConnectionError("database is unreachable")

    from modules.database import DatabaseManager
    monkeypatch.setattr(DatabaseManager, "init_db", _broken_init)

    with TestClient(api.app) as client:
        r = client.get("/health")
        assert r.status_code in (200, 503), r.status_code
        assert client.get("/").status_code < 500


def test_the_app_starts_when_the_database_manager_cannot_be_built(monkeypatch, tmp_path):
    """`get_db_manager()` itself can fail — before `init_db()` is ever called."""
    api, TestClient = _boot(monkeypatch, tmp_path)

    def _no_manager():
        raise RuntimeError("no database configuration available")

    monkeypatch.setattr(api, "get_db_manager", _no_manager)

    with TestClient(api.app) as client:
        assert client.get("/health").status_code in (200, 503)


def test_a_database_failure_does_not_claim_the_database_is_initialised(monkeypatch, tmp_path, capsys):
    """The log must not say "✅ Database initialized" when it was not.

    This is the same class of defect as the CORS test that printed a checkmark:
    a success message that is not evidence of success. It is worse at startup,
    because "✅ Database initialized" is the line an operator trusts.
    """
    api, TestClient = _boot(monkeypatch, tmp_path)

    async def _broken_init(self):
        raise ConnectionError("database is unreachable")

    from modules.database import DatabaseManager
    monkeypatch.setattr(DatabaseManager, "init_db", _broken_init)

    with TestClient(api.app):
        pass
    out = capsys.readouterr().out
    assert "Database not available" in out, "the failure was not reported at all"
    assert "✅ Database initialized" not in out, (
        "startup claimed the database was initialised after it failed"
    )


# ------------------------------------------------ the UI reaches the API --

def test_a_fresh_boot_serves_the_endpoints_the_frontend_calls(monkeypatch, tmp_path):
    """The app is usable in the degraded state, not merely running.

    `/health` returning 200 while every real endpoint 500s would satisfy a naive
    "it booted" check and none of the users.
    """
    api, TestClient = _boot(monkeypatch, tmp_path)

    async def _broken_init(self):
        raise ConnectionError("database is unreachable")

    from modules.database import DatabaseManager
    monkeypatch.setattr(DatabaseManager, "init_db", _broken_init)

    with TestClient(api.app) as client:
        name = f"boot_{os.urandom(4).hex()}"
        # Registration needs the database; without it this may fail, and that is
        # an acceptable answer. What is NOT acceptable is a crash or a hung
        # request, so both are checked.
        r = client.post("/auth/register", json={
            "username": name, "email": f"{name}@example.com", "password": PASSWORD})
        assert r.status_code < 500 or r.status_code == 503, r.status_code
        assert client.get("/documents").status_code in (200, 401)
        assert client.get("/conversations").status_code in (200, 401)


# --------------------------------------------- optimized RAG fallback --

def test_optimized_rag_falls_back_to_simple_rag_when_it_cannot_load(monkeypatch, tmp_path):
    """`USE_OPTIMIZED_RAG=true` with a broken stack must not stop startup.

    The fallback prints a warning and continues on SimpleRAG. Untested before
    this, which matters because this is the path a deployment with a missing
    model takes.
    """
    import modules.rag_optimizations as _ragopt

    def _boom(*a, **k):
        raise RuntimeError("vector store unavailable")

    monkeypatch.setattr(_ragopt, "OptimizedRAG", _boom, raising=False)
    api, TestClient = _boot(monkeypatch, tmp_path, USE_OPTIMIZED_RAG="1")

    with TestClient(api.app) as client:
        assert client.get("/health").status_code in (200, 503)
        # It degraded to the simple implementation rather than dying.
        assert type(api.app.state.rag).__name__ == "SimpleRAG", (
            f"expected a SimpleRAG fallback, got {type(api.app.state.rag).__name__}"
        )
