"""
Regression tests for multi-tenant isolation and the RAG/auth fixes.

Covers:
- heap_top_k never compares dicts on tied scores (doc Q&A crash)
- chunk_score fallback order (relevance -> rerank -> similarity -> bm25)
- owner isolation in SimpleRAG / KeywordStore / VectorStore / OptimizedRAG
  (search, context, per-document context, chunks, caches, same-name files)
- agent tools read through the common interface, scoped to the caller
- multi-agent answer cache is per-owner
- refresh tokens are not access tokens; JWT secret honours *_FILE secrets
- DATABASE_URL gets the password from POSTGRES_PASSWORD(_FILE)
- API: guest cookies isolate anonymous visitors' documents, WebSocket
  ignores the path user_id, protected endpoints require auth
"""

import hashlib
import os
import subprocess  # nosec B404 - tests spawn the current interpreter only
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return str(p)


class _NoLLM:
    def complete(self, *a, **k):
        return ""


def _chroma_client(tmp_path):
    """Real ChromaDB PersistentClient with an offline hash embedding."""
    chromadb = pytest.importorskip("chromadb")
    from chromadb.api.types import EmbeddingFunction

    class HashEF(EmbeddingFunction):
        def __init__(self):
            pass

        def __call__(self, input):
            out = []
            for t in input:
                v = [0.0] * 64
                for w in t.lower().split():
                    v[int(hashlib.md5(w.encode(), usedforsecurity=False).hexdigest(), 16) % 64] += 1.0
                v[0] += 0.01  # never a zero vector
                out.append(v)
            return out

        @staticmethod
        def name():
            return "hash-ef"

        def get_config(self):
            return {}

        @staticmethod
        def build_from_config(c):
            return HashEF()

    inner = chromadb.PersistentClient(path=str(tmp_path / "chroma"))

    class Client:
        def get_or_create_collection(self, name, metadata=None):
            return inner.get_or_create_collection(
                name=name, metadata=metadata, embedding_function=HashEF()
            )

        def delete_collection(self, name):
            inner.delete_collection(name)

        def __getattr__(self, item):
            return getattr(inner, item)

    return Client()


# ---------------------------------------------------------------------------
# heap_top_k / chunk_score
# ---------------------------------------------------------------------------


def test_heap_top_k_ties_on_dicts_do_not_crash():
    from api import heap_top_k

    items = [{"id": i, "relevance_score": 2} for i in range(10)]
    top = heap_top_k(items, key=lambda c: c["relevance_score"], k=3)
    assert [c["id"] for c in top] == [0, 1, 2]  # stable: earliest wins ties


def test_heap_top_k_orders_largest_first():
    from api import heap_top_k

    items = [{"s": s} for s in [1, 5, 3, 5, 2]]
    assert [c["s"] for c in heap_top_k(items, key=lambda c: c["s"], k=3)] == [5, 5, 3]
    assert heap_top_k(items, key=lambda c: c["s"], k=0) == []


def test_chunk_score_fallback_order():
    from api import chunk_score

    assert chunk_score({"relevance_score": 3, "similarity": 0.9}) == 3
    assert chunk_score({"rerank_score": 1.5, "similarity": 0.9}) == 1.5
    assert chunk_score({"similarity": 0.7, "bm25_score": 0.2}) == 0.7
    assert chunk_score({"bm25_score": 0.2}) == 0.2
    assert chunk_score({}) == 0.0


# ---------------------------------------------------------------------------
# SimpleRAG / KeywordStore isolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", ["simple", "keyword"])
def test_store_owner_isolation(tmp_path, factory):
    if factory == "simple":
        from modules.rag import SimpleRAG as Store
    else:
        from modules.vector_store import KeywordStore as Store
    store = Store()
    a = _write(tmp_path / "a", "report.txt", "alice secret pricing is 49 dollars")
    b = _write(tmp_path / "b", "report.txt", "bob secret pricing is 99 dollars")
    store.add_document(a, owner="alice")
    store.add_document(b, owner="bob")

    assert store.list_documents(owner="alice") == ["report.txt"]
    assert store.list_documents(owner="carol") == []
    alice_hits = store.search("secret pricing", 5, owner="alice")
    assert alice_hits and all("49" in h["content"] for h in alice_hits)
    assert "99" not in store.get_context("pricing", 5, owner="alice")
    assert "No documents" in store.get_context("pricing", 5, owner="carol")
    chunks = store.get_document_chunks("report", owner="bob")
    assert chunks and "99" in chunks[0]["content"]
    assert "49" in store.get_context_for_document("pricing", "report.txt", 4, owner="alice")
    assert store.get_context_for_document("pricing", "report.txt", 4, owner="carol") == ""

    # Re-adding replaces (no duplicate chunks)
    store.add_document(a, owner="alice")
    assert len(store.get_document_chunks("report.txt", owner="alice")) == len(chunks)


def test_simple_rag_answer_cache_is_owner_scoped(tmp_path):
    from modules.rag import SimpleRAG

    rag = SimpleRAG()
    rag.cache_answer("q", "alice-answer", owner="alice")
    assert rag.get_cached_answer("q", owner="alice") == "alice-answer"
    assert rag.get_cached_answer("q", owner="bob") is None
    rag.add_document(_write(tmp_path, "x.txt", "hello"), owner="alice")
    assert rag.get_cached_answer("q", owner="alice") is None  # invalidated


# ---------------------------------------------------------------------------
# VectorStore / OptimizedRAG (real ChromaDB, offline embeddings)
# ---------------------------------------------------------------------------


def test_vector_store_owner_isolation(tmp_path):
    from modules.vector_store import VectorStore

    store = VectorStore(client=_chroma_client(tmp_path))
    a = _write(tmp_path / "a", "plan.txt", "alice plan pricing costs 49 dollars monthly")
    b = _write(tmp_path / "b", "plan.txt", "bob plan pricing costs 99 dollars monthly")
    store.add_document(a, owner="alice")
    store.add_document(b, owner="bob")

    # Same filename for two owners: neither overwrote the other
    assert store.collection.count() == 2
    assert store.list_documents(owner="alice") == ["plan.txt"]
    hits = store.search("pricing", 5, owner="alice")
    assert len(hits) == 1 and "49" in hits[0]["content"]
    assert store.search("pricing", 5, owner="carol") == []
    assert "No documents" in store.get_context("pricing", 5, owner="carol")
    assert "99" in store.get_context_for_document("pricing", "plan", 4, owner="bob")
    assert store.get_document_chunks("plan.txt", owner="carol") == []

    store.add_document(a, owner="alice")  # re-upload replaces
    assert store.collection.count() == 2
    assert store.delete_document("plan.txt", owner="bob") == 1
    assert store.list_documents(owner="bob") == []


def test_optimized_rag_isolation_and_cache(tmp_path):
    from modules.rag_optimizations import OptimizedRAG
    from modules.vector_store import VectorStore

    store = VectorStore(client=_chroma_client(tmp_path))
    rag = OptimizedRAG(store, llm_client=_NoLLM())
    a1 = _write(tmp_path / "a", "one.txt", "Alpha pricing basic plan costs 9 dollars. " * 5)
    a2 = _write(tmp_path / "a", "two.txt", "Alpha pricing premium plan costs 29 dollars. " * 5)
    b1 = _write(tmp_path / "b", "one.txt", "Bravo confidential salary data 123456. " * 5)
    rag.add_document(a1, owner="alice")
    rag.add_document(a2, owner="alice")
    rag.add_document(b1, owner="bob")

    hits = rag.search("pricing plan", 6, owner="alice")
    assert len(hits) >= 2
    assert all("Bravo" not in h["content"] for h in hits)
    assert sorted(rag.list_documents(owner="alice")) == ["one.txt", "two.txt"]
    assert "29" in rag.get_context_for_document("pricing", "two.txt", 4, owner="alice")
    assert rag.get_document_chunks("one.txt", owner="bob")[0]["content"].count("Bravo")

    rag.cache_answer("what is the price?", "ALICE ANSWER", owner="alice")
    assert rag.get_cached_answer("what is the price?", owner="alice") == "ALICE ANSWER"
    assert rag.get_cached_answer("what is the price?", owner="bob") is None
    rag.add_document(a1, owner="alice")  # upload invalidates alice's cache
    assert rag.get_cached_answer("what is the price?", owner="alice") is None


def test_retrieve_context_with_tied_scores_no_crash(tmp_path, monkeypatch):
    """The production doc-QA path used to raise TypeError on tied scores
    and answer 'No documents have been uploaded yet'."""
    import api
    from modules.doc_scope import scoped
    from modules.rag import SimpleRAG

    rag = SimpleRAG()
    for i in range(3):
        rag.add_document(_write(tmp_path, f"d{i}.txt", "pricing plan details"), owner="u1")
    ctx = api._retrieve_context(scoped(rag, "u1"), "pricing plan")
    assert ctx.count("--- Source:") == 3


# ---------------------------------------------------------------------------
# Agent tools + multi-agent cache scoping
# ---------------------------------------------------------------------------


def test_agent_tools_read_scoped_through_interface(tmp_path):
    from modules.agent_tools import read_uploaded_document, set_document_store, list_uploaded_documents
    from modules.rag_optimizations import OptimizedRAG
    from modules.request_context import bind_owner, reset_owner
    from modules.vector_store import VectorStore

    rag = OptimizedRAG(VectorStore(client=_chroma_client(tmp_path)), llm_client=_NoLLM())
    rag.add_document(_write(tmp_path / "a", "notes.txt", "alice private notes"), owner="alice")
    set_document_store(rag)
    try:
        tok = bind_owner("alice")
        assert "alice private notes" in read_uploaded_document("notes.txt")
        reset_owner(tok)
        tok = bind_owner("bob")
        assert "not found" in read_uploaded_document("notes.txt")
        assert "No documents" in list_uploaded_documents()
        reset_owner(tok)
    finally:
        set_document_store(None)


def test_multi_agent_cache_is_per_owner():
    import multi_agent
    from modules.request_context import bind_owner, reset_owner

    tok = bind_owner("alice")
    multi_agent._agent_cache_put("exact same task text", "coder", "ALICE RESULT")
    assert multi_agent._semantic_lookup("exact same task text", "coder") == "ALICE RESULT"
    reset_owner(tok)
    tok = bind_owner("bob")
    assert multi_agent._semantic_lookup("exact same task text", "coder") is None
    reset_owner(tok)


# ---------------------------------------------------------------------------
# Auth / secrets
# ---------------------------------------------------------------------------


def test_refresh_token_is_not_an_access_token():
    import asyncio

    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials

    from modules.auth import get_current_user, jwt_manager

    refresh = jwt_manager.create_refresh_token({"sub": "00000000-0000-0000-0000-000000000001"})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=refresh)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(get_current_user(request=None, credentials=creds))
    assert exc.value.status_code == 401


def test_jwt_secret_read_from_file_secret(tmp_path):
    secret_file = tmp_path / "jwt"
    secret_file.write_text("file-secret-value-1234567890")
    env = {**os.environ, "JWT_SECRET_KEY_FILE": str(secret_file)}
    env.pop("JWT_SECRET_KEY", None)
    out = subprocess.run(  # nosec B603 - fixed argv, sys.executable, no shell
        [sys.executable, "-c", "import modules.auth as a; print(a.SECRET_KEY)"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.stdout.strip().splitlines()[-1] == "file-secret-value-1234567890"


def test_default_jwt_secret_refused_in_production():
    env = {**os.environ, "ENVIRONMENT": "production"}
    env.pop("JWT_SECRET_KEY", None)
    env.pop("JWT_SECRET_KEY_FILE", None)
    out = subprocess.run(  # nosec B603 - fixed argv, sys.executable, no shell
        [sys.executable, "-c", "import modules.auth"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.returncode != 0


def test_database_url_password_injection(tmp_path, monkeypatch):
    from modules.secrets import resolve_database_url

    pw = tmp_path / "pg"
    pw.write_text("s3cr@t/pw")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://postgres@postgres:5432/llama_agent")
    monkeypatch.setenv("POSTGRES_PASSWORD_FILE", str(pw))
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    url = resolve_database_url()
    assert url == "postgresql+asyncpg://postgres:s3cr%40t%2Fpw@postgres:5432/llama_agent"


# ---------------------------------------------------------------------------
# API: guest isolation, WebSocket identity, protected endpoints
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api
    from modules.rag import SimpleRAG

    api.app.state.rag = SimpleRAG()
    monkeypatch.setattr(api, "UPLOAD_ROOT", str(tmp_path / "uploads"))
    return api, TestClient


def test_guests_are_isolated(api_client):
    api, TestClient = api_client
    alice, bob = TestClient(api.app), TestClient(api.app)

    r = alice.post("/documents/upload", files={"file": ("secret.txt", b"alice data", "text/plain")})
    assert r.status_code == 200, r.text
    assert "guest_id" in alice.cookies

    assert alice.get("/documents").json()["documents"] == ["secret.txt"]
    assert bob.get("/documents").json()["documents"] == []

    gid = alice.cookies["guest_id"]
    assert os.path.isfile(os.path.join(api.UPLOAD_ROOT, f"guest-{gid}", "secret.txt"))


def test_documents_read_rejects_paths_outside_own_uploads(api_client):
    api, TestClient = api_client
    c = TestClient(api.app)
    for bad in ["data/facts.json", "../../etc/passwd", "/etc/passwd", "uploads/other/x.txt"]:
        r = c.post("/documents/read", params={"file_path": bad})
        assert r.status_code in (400, 404), (bad, r.status_code)


def test_websocket_ignores_path_user_id(api_client):
    api, TestClient = api_client
    from modules.websocket import get_ws_handler

    c = TestClient(api.app)
    with c.websocket_connect("/ws/victim-user-id") as ws:
        welcome = ws.receive_json()
        assert "online_users" not in welcome["data"]
        assert "victim-user-id" not in get_ws_handler().manager.active_connections
    status = c.get("/ws/status").json()
    assert "online_users" not in status


def test_protected_endpoints_require_auth(api_client):
    api, TestClient = api_client
    c = TestClient(api.app)
    assert c.post("/mcp/tools", json={"name": "team_status", "arguments": {}}).status_code == 401
    assert c.get("/metrics/json").status_code == 401
    assert c.get("/monitoring/dashboard").status_code == 401


def test_expired_access_cookie_is_silently_refreshed(api_client):
    api, TestClient = api_client
    from modules.auth import jwt_manager

    c = TestClient(api.app)
    refresh = jwt_manager.create_refresh_token({"sub": "00000000-0000-0000-0000-000000000002"})
    c.cookies.set("refresh_token", refresh)
    r = c.get("/documents")
    assert r.status_code == 200
    new_access = r.cookies.get("access_token")
    assert new_access
    assert jwt_manager.verify_token(new_access)["type"] == "access"


def test_prometheus_metrics_hidden_behind_proxy(api_client, monkeypatch):
    api, TestClient = api_client
    c = TestClient(api.app)
    monkeypatch.delenv("METRICS_ALLOW_PROXIED", raising=False)
    # Direct scrape (Prometheus on the internal network): allowed.
    assert c.get("/metrics").status_code == 200
    # Through Nginx / ingress / Next.js rewrite (adds X-Forwarded-For): hidden.
    assert c.get("/metrics", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 404
    monkeypatch.setenv("METRICS_ALLOW_PROXIED", "1")
    assert c.get("/metrics", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200


def test_hybrid_bm25_adds_keyword_hits_for_same_owner_only():
    from modules.rag_optimizations import HybridSearcher

    docs = [
        "alpha report quarterly revenue",            # alice, vector hit
        "error code ZX-4471 means disk quota",       # alice, keyword-only
        "error code ZX-4471 secret bob runbook",     # bob, keyword-only
    ]
    metas = [
        {"file": "a.txt", "owner": "alice", "chunk_index": 0},
        {"file": "b.txt", "owner": "alice", "chunk_index": 0},
        {"file": "c.txt", "owner": "bob", "chunk_index": 0},
    ]

    class FakeCollection:
        def get(self, include=None):
            return {"documents": docs, "metadatas": metas}

    class FakeStore:
        collection = FakeCollection()

        def search(self, query, top_k=5, owner=None):
            return [{"content": docs[0], "file": "a.txt", "owner": "alice",
                     "chunk_index": 0, "similarity": 0.4}]

    hs = HybridSearcher(vector_store=FakeStore(), reranker=None)
    res = hs.search("ZX-4471", top_k=3, owner="alice")
    contents = [r["content"] for r in res]
    assert docs[1] in contents          # keyword-only hit surfaced
    assert docs[2] not in contents      # other tenant never leaks
    assert all(r.get("owner") in ("alice",) for r in res)


def test_llm_usage_is_costed_and_route_labels_are_templates(api_client):
    api, TestClient = api_client
    from modules.prometheus_metrics import record_llm_usage, cost_summary

    # The spend is read back from the Prometheus counter -- the same series
    # Grafana reads -- rather than from a second in-process ledger.
    before = cost_summary()["total_cost"]
    cost = record_llm_usage("openai/gpt-oss-20b", 1_000_000, 1_000_000)
    assert abs(cost - (0.075 + 0.30)) < 1e-9
    assert cost_summary()["total_cost"] - before >= cost - 1e-9

    c = TestClient(api.app)
    c.get("/conversations/some-random-id-123")
    body = c.get("/metrics").text
    assert "some-random-id-123" not in body
    assert 'llama_agent_llm_cost_usd_total{component="other",model="openai/gpt-oss-20b"}' in body


def test_aiter_in_thread_does_not_block_event_loop():
    import asyncio
    import time as _t
    import api

    def slow_tokens():
        for i in range(3):
            _t.sleep(0.15)  # blocking network read in the real Groq stream
            yield f"t{i}"

    async def main():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        tk = asyncio.create_task(ticker())
        out = [t async for t in api._aiter_in_thread(slow_tokens)]
        tk.cancel()
        return out, ticks

    out, ticks = asyncio.run(main())
    assert out == ["t0", "t1", "t2"]
    assert ticks > 10  # loop kept running while tokens were produced


def _sse_text(body: str):
    import json as _json
    events, text = [], ""
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        data = line[6:]
        if data.strip() == "[DONE]":
            break
        if data.startswith("{"):
            try:
                events.append(_json.loads(data))
                continue
            except ValueError:
                pass
        text += data
    return events, text


def test_stream_agent_override_and_team(api_client, monkeypatch):
    api, TestClient = api_client
    import multi_agent

    calls = []

    class FakeAgent:
        def __init__(self, key):
            self.name = key.title()
            self.key = key

        def execute_stream(self, task, critique=False, reflect=False):
            calls.append((self.key, task))
            yield {"type": "token", "content": f"[{self.key}]"}

        def critique(self, task, text):
            return ""

    class FakeOrchestrator:
        def __init__(self):
            self.agents = {k: FakeAgent(k) for k in api.AGENT_KEYS}

        def collaborate_stream(self, task, critique=None, reflect=None, parallel=None):
            calls.append(("team", task))
            for k in ("research", "writer"):
                yield {"type": "agent_start", "agent": k.title(), "step": k}
                yield {"type": "token", "content": f"<{k}>"}
                yield {"type": "agent_done", "agent": k.title(), "step": k, "content": k}
            yield {"type": "final", "content": "FINAL"}

    monkeypatch.setattr(multi_agent, "MultiAgentOrchestrator", FakeOrchestrator)
    monkeypatch.setenv("GROQ_API_KEY", os.getenv("GROQ_API_KEY") or "test-key")
    from modules.memory import ConversationMemory, FactMemory
    monkeypatch.setattr(api.app.state, "memory", ConversationMemory(), raising=False)
    monkeypatch.setattr(api.app.state, "facts", FactMemory(), raising=False)
    c = TestClient(api.app)

    # Explicit agent from the UI wins even without routing keywords.
    r = c.post("/chat/stream", json={"message": "hello there friend", "agent": "coder",
                                     "use_critic": False})
    events, text = _sse_text(r.text)
    assert "[coder]" in text
    assert events[0]["type"] == "config" and events[0]["agent"] == "coder"

    # "@team" runs the whole collaboration, with agent_start/agent_done events.
    r = c.post("/chat/stream", json={"message": "@team plan a product launch", "use_critic": False})
    events, text = _sse_text(r.text)
    assert ("team" in [k for k, _ in calls])
    assert "plan a product launch" in [t for k, t in calls if k == "team"][0]
    assert "<research>" in text and "<writer>" in text
    assert [e["type"] for e in events].count("agent_start") == 2
    assert api.detect_team_prefix("@team") == (False, "@team")


def test_register_db_unreachable_returns_503_and_logs(api_client, monkeypatch, caplog):
    """DB DNS/connection failures surface as a clear 503 (and in stderr logs),
    not an opaque 500 that is only written to the audit log file."""
    import socket

    api, TestClient = api_client

    async def boom(**_kw):
        raise socket.gaierror(-3, "Temporary failure in name resolution")

    monkeypatch.setattr(api.user_manager, "register", boom)
    c = TestClient(api.app)
    with caplog.at_level("ERROR", logger="llama_agent.api"):
        r = c.post("/auth/register", json={
            "username": "newuser1", "email": "n1@example.com", "password": "Str0ng!Passw0rd",
        })
    assert r.status_code == 503
    assert "Database unavailable" in r.json()["detail"]
    assert any("registration failed: database unavailable" in m for m in caplog.messages)


def test_register_unexpected_bug_still_500(api_client, monkeypatch):
    api, TestClient = api_client

    async def bug(**_kw):
        raise KeyError("oops")

    monkeypatch.setattr(api.user_manager, "register", bug)
    r = TestClient(api.app).post("/auth/register", json={
        "username": "newuser2", "email": "n2@example.com", "password": "Str0ng!Passw0rd",
    })
    assert r.status_code == 500


def test_is_db_unavailable_walks_cause_chain():
    import api

    class OperationalError(Exception):
        pass

    try:
        try:
            raise ConnectionRefusedError("refused")
        except ConnectionRefusedError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert api._is_db_unavailable(outer)
    assert api._is_db_unavailable(OperationalError("x"))
    assert not api._is_db_unavailable(ValueError("bug"))


def test_chat_options_exposes_server_dsa_defaults(api_client, monkeypatch):
    api, TestClient = api_client
    monkeypatch.setenv("AGENT_CRITIQUE", "1")
    monkeypatch.setenv("AGENT_REFLECT", "0")
    monkeypatch.setenv("AGENT_PARALLEL_STEPS", "1")
    r = TestClient(api.app).get("/chat/options")
    assert r.status_code == 200
    assert r.json()["defaults"] == {"critic": True, "reflection": False, "parallel": True}
    assert set(r.json()["applies_to"]) == {"critic", "reflection", "parallel"}


def test_stream_config_reports_what_actually_ran(api_client, monkeypatch):
    """Chips show the route's real behaviour; flags reach the agents; tool
    markers go to the reasoning view, not the answer text; an empty answer
    becomes a visible message."""
    api, TestClient = api_client
    import multi_agent

    seen = {}

    class FakeAgent:
        def __init__(self, key):
            self.name = key.title() + " Agent"
            self.key = key

        def execute_stream(self, task, critique=False, reflect=False):
            seen["agent"] = (critique, reflect)
            yield {"type": "tool", "content": "⚙️ web_search"}
            yield {"type": "reasoning", "stage": "critic", "content": "ok"}
            if "empty" not in task:
                yield {"type": "token", "content": "answer"}

        def critique(self, task, text):
            raise AssertionError("agent already reviewed; no second critic pass")

    class FakeOrchestrator:
        def __init__(self):
            self.agents = {k: FakeAgent(k) for k in api.AGENT_KEYS}

        def collaborate_stream(self, task, critique=None, reflect=None, parallel=None):
            seen["team"] = (critique, reflect, parallel)
            yield {"type": "reasoning", "stage": "parallel", "content": "2 agents"}
            yield {"type": "final", "content": "TEAM"}

    monkeypatch.setattr(multi_agent, "MultiAgentOrchestrator", FakeOrchestrator)
    from modules.memory import ConversationMemory, FactMemory
    monkeypatch.setattr(api.app.state, "memory", ConversationMemory(), raising=False)
    monkeypatch.setattr(api.app.state, "facts", FactMemory(), raising=False)
    c = TestClient(api.app)

    # Direct reply (fact command): nothing ran -> no chips.
    events, _ = _sse_text(c.post("/chat/stream", json={
        "message": "remember color = blue", "use_critic": True}).text)
    cfg = [e for e in events if e["type"] == "config"]
    assert len(cfg) == 1
    assert (cfg[0]["critic"], cfg[0]["reflection"], cfg[0]["parallel"]) == (False, False, False)
    assert cfg[0]["requested"]["critic"] is True

    # Specialist: critic/reflect forwarded, never parallel, clean text.
    events, text = _sse_text(c.post("/chat/stream", json={
        "message": "hello", "agent": "coder", "use_critic": True,
        "use_reflection": True, "use_parallel": True}).text)
    cfg = [e for e in events if e["type"] == "config"][-1]
    assert (cfg["critic"], cfg["reflection"], cfg["parallel"]) == (True, True, False)
    assert seen["agent"] == (True, True)
    assert text == "answer"  # no "⚙️ web_search" glued into the answer
    assert {"type": "reasoning", "stage": "tool", "content": "⚙️ web_search"} in events
    assert any(e.get("content") == "Routing to Coder Agent" for e in events)

    # Empty specialist answer -> visible warning instead of a blank bubble.
    _, text = _sse_text(c.post("/chat/stream", json={
        "message": "empty please", "agent": "coder"}).text)
    assert "empty answer" in text

    # Team: all three flags pass through and show as chips.
    events, _ = _sse_text(c.post("/chat/stream", json={
        "message": "@team launch", "use_critic": False,
        "use_reflection": True, "use_parallel": False}).text)
    assert seen["team"] == (False, True, False)
    cfg = [e for e in events if e["type"] == "config"][-1]
    assert (cfg["critic"], cfg["reflection"], cfg["parallel"]) == (False, True, False)
    assert {"type": "reasoning", "stage": "parallel", "content": "2 agents"} in events
