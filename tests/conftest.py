"""Shared fixtures for the whole suite.

`journey` used to live inside tests/test_e2e_journey.py. It is the only fixture
that boots the *real* application - lifespan, middleware, cookies, uploads,
auth - against in-memory SQLite with the language model faked at its narrowest
point. Several test files now need that; a fixture that good should not be
copy-pasted, because the copies drift and then two files are testing two
different applications.

The `__all__`-style export below is what makes it visible to other files.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DOC_TEXT = (
    "Acme Corp financial summary.\n"
    "The Rule of 40 is growth rate plus free cash flow margin.\n"
    "Revenue was 12 million and churn was 4 percent.\n"
)
ANSWER = ("Acme's financial summary states the Rule of 40 is growth rate plus free "
          "cash flow margin (acme.txt, chunk 0).")


class _FakeLLM:
    """Minimal Groq-shaped client. Records calls so we can assert the model ran."""

    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        text = "" if kw.get("max_tokens") == 120 else ANSWER
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=text), finish_reason="stop")])


@pytest.fixture
def journey(tmp_path, monkeypatch):
    """A booted app with a temp upload dir, a fake LLM, and no real network."""
    monkeypatch.setenv("GROQ_API_KEY", "test-key-for-e2e")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret-for-e2e-only-not-real")
    monkeypatch.setenv("ENVIRONMENT", "development")

    # Booting the real lifespan installs a global OpenTelemetry TracerProvider
    # (api.py calls phoenix.otel.register()). OTel allows that ONCE per process,
    # so any later test that wants its own in-memory exporter silently gets the
    # global one instead and collects zero spans. That is not hypothetical: it
    # broke test_provider_client::test_swapping_the_vendor_keeps_the_llm_span
    # in the full suite while passing in isolation. The exporter is stubbed out
    # here so this file exercises the app without claiming process-wide tracing.
    import phoenix.otel as _photel
    monkeypatch.setattr(_photel, "register", lambda *a, **k: None, raising=False)

    import api
    from fastapi.testclient import TestClient
    from modules.rag import SimpleRAG
    from modules import doc_answers

    # Isolate storage: uploads and the fact store must not touch the repo.
    monkeypatch.setattr(api, "UPLOAD_ROOT", str(tmp_path / "uploads"))
    os.makedirs(str(tmp_path / "uploads"), exist_ok=True)

    # Fresh, per-owner RAG so this test sees only its own document.
    api.app.state.rag = SimpleRAG()
    api.app.state.facts = None
    api.app.state.fact_memory = None

    # Real auth against in-memory SQLite: registration/login must actually work,
    # but the test must not need a Postgres to run. `user_manager` resolves its
    # db through the module-level singleton (see test_auth.py), so that is the
    # one thing pointed at the temporary engine.
    import asyncio as _asyncio
    import modules.database as _dbmod
    from modules.database import Base
    from modules.auth import UserManager
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from contextlib import asynccontextmanager

    class _FakeDB:
        def __init__(self, factory):
            self._f = factory

        @asynccontextmanager
        async def session(self):
            async with self._f() as sess:
                yield sess
                await sess.commit()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False})

    async def _build():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        m = UserManager()
        m.db = _FakeDB(async_sessionmaker(engine, expire_on_commit=False))
        return m

    manager = _asyncio.run(_build())
    # The routes do not call get_db_manager() themselves - they use the shared
    # `user_manager` object, which resolves the DB through the module-level
    # singleton. Point the singleton at the temporary engine AND give the
    # already-constructed user_manager that DB, since it captured one at import.
    import api as _api_mod
    import modules.auth as _auth_mod
    _orig_get_db = _dbmod.get_db_manager
    _dbmod.get_db_manager = _api_mod.get_db_manager = lambda: manager.db
    _orig_user_db = _auth_mod.user_manager.db
    _auth_mod.user_manager.db = manager.db
    _orig_api_user_db = _api_mod.user_manager.db
    _api_mod.user_manager.db = manager.db

    fake = _FakeLLM()
    monkeypatch.setattr(doc_answers, "_get_groq_client", lambda key: fake)
    # The process-wide answer cache would leak answers between tests.
    doc_answers._doc_answer_cache._store.clear()

    async def _fake_agent(message, **kw):
        return f"echo: {message}"

    # run_agent_stream is a plain *sync* generator: the streaming route drives it
    # through `aiter_in_thread`, which iterates it on a worker thread. An
    # `async def` fake yields an async generator, and `for item in <async
    # generator>` is a TypeError - so this fixture made every test that reached
    # the general streaming path fail, and silently left that path untested.
    def _fake_stream(message, **kw):
        yield "echo: "
        yield message

    import llama_agent1
    monkeypatch.setattr(llama_agent1, "run_agent", _fake_agent)
    monkeypatch.setattr(llama_agent1, "run_agent_stream", _fake_stream)

    try:
        with TestClient(api.app) as client:
            yield SimpleNamespace(api=api, client=client, llm=fake, tmp=tmp_path)
    finally:
        _dbmod.get_db_manager = _api_mod.get_db_manager = _orig_get_db
        _auth_mod.user_manager.db = _orig_user_db
        _api_mod.user_manager.db = _orig_api_user_db
        _asyncio.run(engine.dispose())
        doc_answers._doc_answer_cache._store.clear()
