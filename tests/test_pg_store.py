"""
Offline-safe tests for the PostgreSQL + Redis persistence layer.

No live Postgres/Redis is required: connection creation is monkeypatched to
verify id normalization, the Redis read-through cache, and the graceful
degradation behaviour that keeps chat working when the databases are down.
"""

import asyncio
import json
import uuid

from modules import pg_store, redis_store
from modules.memory import ConversationMemory


# ---------------- id normalization (pure logic) ----------------


def test_normalize_conversation_id_uuid_passthrough():
    raw = str(uuid.uuid4())
    assert pg_store.normalize_conversation_id(raw) == raw


def test_normalize_conversation_id_legacy_string_is_valid_uuid():
    cid = pg_store.normalize_conversation_id("default_abc-123")
    # Must parse as a real UUID so it can go into the Postgres UUID column.
    assert str(uuid.UUID(cid)) == cid


def test_normalize_conversation_id_deterministic():
    assert (pg_store.normalize_conversation_id("diag_http_test")
            == pg_store.normalize_conversation_id("diag_http_test"))


def test_normalize_conversation_id_distinct_inputs():
    assert (pg_store.normalize_conversation_id("one")
            != pg_store.normalize_conversation_id("two"))


def test_normalize_user_id_uuid_passthrough():
    raw = str(uuid.uuid4())
    assert pg_store.normalize_user_id(raw) == raw


def test_normalize_user_id_deterministic_and_distinct():
    assert pg_store.normalize_user_id("guest") == pg_store.normalize_user_id("guest")
    assert pg_store.normalize_user_id("guest") != pg_store.normalize_user_id("other")


def test_is_registered_user_detects_real_uuids_only():
    assert pg_store.is_registered_user(str(uuid.uuid4())) is True
    assert pg_store.is_registered_user("guest") is False
    assert pg_store.is_registered_user("") is False
    assert pg_store.is_registered_user(None) is False


def test_pg_add_message_skips_unregistered_users_without_db(monkeypatch):
    """Anonymous/derived users must not attempt FK-violating writes."""
    called = {"n": 0}

    async def boom():
        called["n"] += 1
        raise AssertionError("pool must not be touched for unregistered users")

    monkeypatch.setattr(pg_store, "get_pool", boom)
    assert _run(pg_store.add_message("guest", "c", "user", "hello")) is None
    assert _run(pg_store.ensure_conversation("guest", "c")) is None
    assert called["n"] == 0


def test_pg_add_message_still_attempts_real_users(monkeypatch):
    """Registered UUID users keep the normal persistence path (pool is hit)."""
    called = {"n": 0}

    async def boom():
        called["n"] += 1
        raise ConnectionRefusedError("no database")

    monkeypatch.setattr(pg_store, "get_pool", boom)
    uid = str(uuid.uuid4())
    assert _run(pg_store.add_message(uid, "c", "user", "hello")) is None
    assert called["n"] == 1


# ---------------- memory store contract (used by the mirror) ----------------


def test_memory_create_conversation_returns_uuid():
    mem = ConversationMemory(memory_file="tests/_tmp_memory_test.json")
    try:
        conv = mem.create_conversation("u1", title="New chat")
        assert str(uuid.UUID(conv["id"])) == conv["id"]  # real UUID
    finally:
        mem.clear()


def test_memory_add_message_returns_resolved_id():
    mem = ConversationMemory(memory_file="tests/_tmp_memory_test.json")
    try:
        cid = mem.add_message("user", "hello", user_id="u1", conversation_id="legacy-id")
        # The message was written under a real conversation key we can read back.
        assert mem.get_messages("u1", cid) and mem.get_messages("u1", cid)[0]["content"] == "hello"
    finally:
        mem.clear()


# ---------------- redis read-through cache (fake client) ----------------


class _FakeRedis:
    """Minimal async stand-in for redis.asyncio.Redis."""

    def __init__(self, fail=False):
        self.store = {}
        self.fail = fail

    async def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        if self.fail:
            raise ConnectionError("redis down")
        self.store[key] = value
        return True

    async def delete(self, *keys):
        if self.fail:
            raise ConnectionError("redis down")
        for k in keys:
            self.store.pop(k, None)
        return 0

    async def aclose(self):
        pass


def _run(coro):
    return asyncio.run(coro)


def test_redis_roundtrip(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_store, "_get_client", lambda: fake)
    key, val = "k1", [{"role": "user", "content": "hi"}]

    async def go():
        assert await redis_store.set_json(key, val, ttl=60) is True
        assert await redis_store.get_json(key) == val

    _run(go())


def test_redis_miss_returns_none(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_store, "_get_client", lambda: fake)
    assert _run(redis_store.get_json("missing")) is None


def test_redis_down_degrades_gracefully(monkeypatch):
    fake = _FakeRedis(fail=True)
    monkeypatch.setattr(redis_store, "_get_client", lambda: fake)

    async def go():
        assert await redis_store.get_json("x") is None          # cache miss, no raise
        assert await redis_store.set_json("x", 1) is False      # write fails, no raise
        assert await redis_store.invalidate_user("u") is True   # best-effort delete

    _run(go())


def test_invalidate_user_clears_list_key(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_store, "_get_client", lambda: fake)
    key = redis_store.list_key("user-1")

    async def go():
        await redis_store.set_json(key, [{"id": "c1"}])
        assert await redis_store.get_json(key) == [{"id": "c1"}]
        await redis_store.invalidate_user("user-1")
        assert await redis_store.get_json(key) is None

    _run(go())


def test_invalidate_conversation_clears_message_cache(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(redis_store, "_get_client", lambda: fake)
    cid = str(uuid.uuid4())
    key = redis_store.messages_key(cid)

    async def go():
        await redis_store.set_json(key, [{"role": "user", "content": "hi"}])
        await redis_store.invalidate_conversation(cid)
        assert await redis_store.get_json(key) is None

    _run(go())


# ---------------- postgres store graceful degradation ----------------


def test_pg_add_message_degrades_when_db_down(monkeypatch):
    async def boom():
        raise ConnectionRefusedError("no database")

    monkeypatch.setattr(pg_store, "get_pool", boom)
    assert _run(pg_store.add_message("u", "c", "user", "hello")) is None


def test_pg_list_degrades_when_db_down(monkeypatch):
    async def boom():
        raise ConnectionRefusedError("no database")

    monkeypatch.setattr(pg_store, "get_pool", boom)
    assert _run(pg_store.list_conversations("u")) is None


def test_pg_get_messages_degrades_when_db_down(monkeypatch):
    async def boom():
        raise ConnectionRefusedError("no database")

    monkeypatch.setattr(pg_store, "get_pool", boom)
    assert _run(pg_store.get_messages("u", "c")) is None


def test_pg_delete_degrades_when_db_down(monkeypatch):
    async def boom():
        raise ConnectionRefusedError("no database")

    monkeypatch.setattr(pg_store, "get_pool", boom)
    assert _run(pg_store.delete_conversation("u", "c")) is False


# ---------------- cache key helpers ----------------


def test_cache_key_names():
    assert redis_store.list_key("u") == "llama:conv:list:u"
    assert redis_store.messages_key("c") == "llama:conv:msgs:c"
