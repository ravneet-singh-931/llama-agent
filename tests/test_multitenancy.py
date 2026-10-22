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
import subprocess
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
                    v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
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
    out = subprocess.run(
        [sys.executable, "-c", "import modules.auth as a; print(a.SECRET_KEY)"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert out.stdout.strip().splitlines()[-1] == "file-secret-value-1234567890"


def test_default_jwt_secret_refused_in_production():
    env = {**os.environ, "ENVIRONMENT": "production"}
    env.pop("JWT_SECRET_KEY", None)
    env.pop("JWT_SECRET_KEY_FILE", None)
    out = subprocess.run(
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
