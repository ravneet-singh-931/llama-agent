"""`/mcp/tools` — the one route where a *client-supplied name* reaches a server.

Nothing covered it beyond two refusal assertions:

    test_e2e_journey  : anonymous -> 401
    test_multitenancy : anonymous -> 401

So the successful call, the timeout, the error mapping and the circuit breaker
had never run in a test. This file exercises all of them against a fake MCP
client, so no network and no LLM is involved.

The circuit breaker is the interesting part: `_mcp_record_failure()` opens the
breaker after MCP_FAILURE_THRESHOLD consecutive failures and then refuses every
call for MCP_CIRCUIT_COOLDOWN seconds. Before this file, that code had never
executed in any test.
"""
import asyncio
import os

import pytest

import api


PASSWORD = "Str0ng-Passw0rd!x"


@pytest.fixture
def mcp_user(journey, monkeypatch):
    """A logged-in user, the circuit breaker reset, and no real MCP server.

    `call_mcp_tool` is imported into api's namespace, so patching
    ``api.call_mcp_tool`` is what the route actually calls.
    """
    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    monkeypatch.setattr(api, "_MCP_FAILURES", 0, raising=False)
    monkeypatch.setattr(api, "_MCP_OPEN_UNTIL", 0.0, raising=False)
    monkeypatch.setattr(api.app.state, "agent_stats",
                        {"handled": {}, "last_used": {}}, raising=False)

    c = journey.client
    name = f"mcp_{os.urandom(4).hex()}"
    c.cookies.clear()
    r = c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    assert r.status_code in (200, 201), r.text
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200
    return c


def _call(client, name="coder_agent", args=None):
    return client.post("/mcp/tools", json={"name": name, "arguments": args or {}})


# --------------------------------------------------------------- success --

def test_a_successful_call_returns_the_client_result(mcp_user, monkeypatch):
    """The route returns whatever `call_mcp_tool` returned, unchanged.

    Pinned because nothing asserted the response body before: the route is a
    pass-through, and a future refactor that wrapped it would break the
    frontend's `result` field without any test noticing.
    """
    sent = {}

    async def _fake(name, arguments):
        sent["name"], sent["args"] = name, arguments
        return {"tool": name, "isError": False, "result": "def f(): pass"}

    monkeypatch.setattr(api, "call_mcp_tool", _fake)

    r = _call(mcp_user, "coder_agent", {"task": "write a sort"})
    assert r.status_code == 200, r.text
    assert r.json() == {"tool": "coder_agent", "isError": False, "result": "def f(): pass"}
    # The arguments reach the client untouched.
    assert sent == {"name": "coder_agent", "args": {"task": "write a sort"}}


def test_a_known_tool_records_agent_usage(mcp_user, monkeypatch):
    """`coder_agent` counts as a `coder` task on the Agent Team dashboard."""

    async def _fake(name, arguments):
        return {"tool": name, "isError": False, "result": "ok"}

    monkeypatch.setattr(api, "call_mcp_tool", _fake)

    assert _call(mcp_user, "coder_agent").status_code == 200
    assert api.app.state.agent_stats["handled"].get("coder") == 1, (
        f"the dashboard counter did not move: {api.app.state.agent_stats}"
    )


def test_an_unknown_tool_is_forwarded_and_not_counted(mcp_user, monkeypatch):
    """No allowlist: the name is forwarded verbatim, and it is not counted.

    MEASURED, and pinned as the current contract rather than endorsed:
    `_MCP_TOOL_TO_AGENT` is used only to decide which dashboard counter to bump,
    never to validate. `nonexistent_tool` reaches the MCP server. If that is ever
    meant to be an allowlist, this test is the one that should change.
    """
    reached = []

    async def _fake(name, arguments):
        reached.append(name)
        return {"tool": name, "isError": False, "result": "ok"}

    monkeypatch.setattr(api, "call_mcp_tool", _fake)

    r = _call(mcp_user, "nonexistent_tool")
    assert r.status_code == 200, r.text
    assert reached == ["nonexistent_tool"], "the name was not forwarded as-is"
    assert api.app.state.agent_stats["handled"] == {}, (
        "an unknown tool must not move a dashboard counter"
    )


# --------------------------------------------------------------- failures --

def test_a_generic_failure_becomes_a_502(mcp_user, monkeypatch):
    async def _boom(name, arguments):
        raise RuntimeError("mcp exploded")

    monkeypatch.setattr(api, "call_mcp_tool", _boom)

    r = _call(mcp_user)
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == "MCP tool call failed"
    # The internal message must not leak.
    assert "mcp exploded" not in r.text


def test_a_timeout_becomes_a_504(mcp_user, monkeypatch):
    """`asyncio.wait_for` around the call maps to 504, not 502.

    The two are different failures: 504 says the server is slow, 502 says it
    broke. A client retrying on 504 should back off; on 502 it should not.
    """
    async def _slow(name, arguments):
        await asyncio.sleep(30)

    monkeypatch.setattr(api, "call_mcp_tool", _slow)
    monkeypatch.setattr(api, "MCP_TIMEOUT", 0.05, raising=False)

    r = _call(mcp_user)
    assert r.status_code == 504, r.text
    assert r.json()["detail"] == "MCP tool call timed out"


def test_an_http_exception_is_not_rewritten(mcp_user, monkeypatch):
    """`except HTTPException: raise` keeps a deliberate status intact.

    Without that clause a 404 raised by the MCP layer would be flattened into a
    502 and the client would be told the wrong thing.
    """
    async def _http(name, arguments):
        raise api.HTTPException(status_code=404, detail="no such tool on the server")

    monkeypatch.setattr(api, "call_mcp_tool", _http)

    r = _call(mcp_user)
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == "no such tool on the server"


# ------------------------------------------------------- circuit breaker --

def test_the_breaker_opens_after_the_threshold_and_refuses_fast(mcp_user, monkeypatch):
    """Three consecutive failures open the breaker; the next call is a 503.

    The whole point is to fail fast instead of hanging on a dead dependency, so
    the assertion is on the *status* and on the fact that the client is not
    called again.
    """
    calls = {"n": 0}

    async def _boom(name, arguments):
        calls["n"] += 1
        raise RuntimeError("down")

    monkeypatch.setattr(api, "call_mcp_tool", _boom)

    for i in range(api.MCP_FAILURE_THRESHOLD):
        r = _call(mcp_user)
        assert r.status_code == 502, f"failure {i + 1} should be a 502, got {r.status_code}"
    assert calls["n"] == api.MCP_FAILURE_THRESHOLD

    r = _call(mcp_user)
    assert r.status_code == 503, "the breaker did not open"
    assert r.json()["detail"] == "MCP server temporarily unavailable"
    assert calls["n"] == api.MCP_FAILURE_THRESHOLD, (
        "an open breaker must fail fast without calling the client again"
    )


def test_a_success_resets_the_failure_count(mcp_user, monkeypatch):
    """Two failures then a success must not leave the breaker one call from open."""
    state = {"fail": True}

    async def _maybe(name, arguments):
        if state["fail"]:
            raise RuntimeError("down")
        return {"tool": name, "isError": False, "result": "ok"}

    monkeypatch.setattr(api, "call_mcp_tool", _maybe)

    for _ in range(api.MCP_FAILURE_THRESHOLD - 1):
        assert _call(mcp_user).status_code == 502

    state["fail"] = False
    assert _call(mcp_user).status_code == 200
    assert api._MCP_FAILURES == 0, "a success did not reset the failure count"

    # One more failure must not trip it: the count restarted from zero.
    state["fail"] = True
    assert _call(mcp_user).status_code == 502
    assert api._mcp_circuit_open() is False, (
        "the breaker opened from stale failures counted before the success"
    )


# --------------------------------------------------------------- gating ----

def test_the_feature_flag_gates_both_routes(journey, monkeypatch):
    """With `enable_mcp_agents` off, the endpoints answer 404 - not 403, not 500.

    MEASURED ORDERING: authentication runs before the feature flag. An
    anonymous caller gets **401**, not 404, because `Depends(get_current_user)`
    resolves first. That is the right order for a disabled feature (a stranger
    learns nothing either way) but it is not obvious from reading the handler,
    so both cases are pinned here.
    """
    monkeypatch.setattr(api.feature_flags, "is_enabled",
                        lambda name: False if name == "enable_mcp_agents" else True)
    c = journey.client

    # /mcp/status is optional-auth, so the flag is what refuses it.
    audit = c.get("/mcp/status")
    assert audit.status_code == 404, audit.text

    # /mcp/tools is login-only: auth wins, so an anonymous caller sees 401.
    anon = _call(c)
    assert anon.status_code == 401, (
        f"anonymous access to a disabled route should be 401, got {anon.status_code}"
    )


def test_the_flag_refuses_an_authenticated_caller_with_404(mcp_user, monkeypatch):
    """The 404 is what a *logged-in* user sees when the flag is off."""
    monkeypatch.setattr(api.feature_flags, "is_enabled",
                        lambda name: False if name == "enable_mcp_agents" else True)
    r = _call(mcp_user)
    assert r.status_code == 404, r.text
    assert "feature flag" in r.json()["detail"].lower()


def test_mcp_status_maps_a_dead_server_to_503(mcp_user, monkeypatch):
    async def _boom():
        raise RuntimeError("server down")

    monkeypatch.setattr(api, "get_mcp_status", _boom, raising=False)

    r = mcp_user.get("/mcp/status")
    assert r.status_code == 503, r.text
    assert "server down" not in r.text, "the internal error leaked"


def test_agent_stats_are_initialised_when_absent(journey, monkeypatch):
    """With no `agent_stats` on app.state, the first call creates it (1393-1394).

    Included because the coverage report showed those two lines unexecuted while
    the counter demonstrably worked: pytest's `app.state` already had the
    attribute, so the lazy initialisation never ran. Setting it to None exercises
    the real first-request path.
    """
    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    async def _fake(name, arguments):
        return {"tool": name, "isError": False, "result": "ok"}

    monkeypatch.setattr(api, "call_mcp_tool", _fake)
    monkeypatch.setattr(api.app.state, "agent_stats", None, raising=False)

    c = journey.client
    name = f"mcp_{os.urandom(4).hex()}"
    c.cookies.clear()
    c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200

    assert _call(c, "coder_agent").status_code == 200
    stats = getattr(api.app.state, "agent_stats", None)
    assert stats is not None, "agent_stats was never created on a first request"
    assert stats["handled"].get("coder") == 1


def test_the_counter_accumulates_without_redis(journey, monkeypatch):
    """Three dispatches must count as three, with no Redis configured.

    This was a real defect, now fixed: `_record_agent_usage` read through
    `_load_shared_dict`, which without Redis hands back the caller's `default` -
    a brand-new empty dict each call - and then wrote `app.state.agent_stats`
    *from* that empty dict instead of merging into it. The in-memory counter
    therefore capped at 1, and `/agents/stats` reported `tasks_handled: 0`.

    The fix is `_shared_state()`: one place that returns Redis' dict when it has
    one, and otherwise this process's own copy on `app.state`.
    """
    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    async def _fake(name, arguments):
        return {"tool": name, "isError": False, "result": "ok"}

    monkeypatch.setattr(api, "call_mcp_tool", _fake)
    monkeypatch.setattr(api.app.state, "agent_stats",
                        {"handled": {}, "last_used": {}}, raising=False)

    c = journey.client
    name = f"mcp_{os.urandom(4).hex()}"
    c.cookies.clear()
    c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200

    for expected in (1, 2, 3):
        assert _call(c, "coder_agent").status_code == 200
        got = api.app.state.agent_stats["handled"].get("coder")
        assert got == expected, (
            f"the counter did not accumulate: expected {expected}, got {got}"
        )


def test_the_dashboard_reports_the_real_count(journey, monkeypatch):
    """`/agents/stats` must show what actually ran - it showed 0.

    The dashboard is what a user reads. It was reporting `tasks_handled: 0`
    after three successful `coder_agent` calls, because the reader took its own
    fresh empty default rather than the process's stored counters.
    """
    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    async def _fake(name, arguments):
        return {"tool": name, "isError": False, "result": "ok"}

    monkeypatch.setattr(api, "call_mcp_tool", _fake)
    monkeypatch.setattr(api.app.state, "agent_stats",
                        {"handled": {}, "last_used": {}}, raising=False)

    c = journey.client
    name = f"mcp_{os.urandom(4).hex()}"
    c.cookies.clear()
    c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200

    for _ in range(3):
        assert _call(c, "coder_agent").status_code == 200

    data = c.get("/agents/stats").json()
    coder = [a for a in data["agents"] if a["key"] == "coder"][0]
    assert coder["tasks_handled"] == 3, (
        f"the dashboard disagrees with what ran: {coder['tasks_handled']} != 3"
    )
    assert coder["last_used"], "last_used was not recorded for the dashboard"


def test_counters_go_through_redis_when_it_is_available(journey, monkeypatch):
    """With Redis answering, the counters live there - and accumulate.

    The fallback fix must not break the multi-replica case the Redis mirror
    exists for. This fakes `redis_store` and asserts the route *uses* it: the
    value written back must contain the accumulated count, and `/agents/stats`
    must read that same store.
    """
    from modules.security import rate_limiter
    from modules import redis_store
    rate_limiter._requests.clear()

    store = {}

    async def _get(key):
        return store.get(key)

    async def _set(key, value, ttl=0):
        import copy
        store[key] = copy.deepcopy(value)
        return True

    monkeypatch.setattr(redis_store, "get_json", _get)
    monkeypatch.setattr(redis_store, "set_json", _set)
    # A different local dict, so a fallback would be visibly wrong.
    monkeypatch.setattr(api.app.state, "agent_stats",
                        {"handled": {"coder": 99}, "last_used": {}}, raising=False)

    async def _fake(name, arguments):
        return {"tool": name, "isError": False, "result": "ok"}

    monkeypatch.setattr(api, "call_mcp_tool", _fake)

    c = journey.client
    name = f"mcp_{os.urandom(4).hex()}"
    c.cookies.clear()
    c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200

    for _ in range(2):
        assert _call(c, "coder_agent").status_code == 200

    shared = store.get(api._AGENT_STATS_KEY)
    assert shared is not None, "nothing was written to Redis"
    # Redis was empty, so the first call seeded it from the local copy (99) and
    # then counted twice: 101. Seeding on a cold cache is deliberate - it is how
    # a replica that just came up joins the existing total instead of restarting
    # from zero. (My first version of this test asserted 2 and was wrong about
    # the code, not the other way round.)
    assert shared["handled"].get("coder") == 101, (
        f"Redis does not hold the accumulated count: {shared}"
    )

    # Now that Redis holds a value, it wins over the stale local copy: a second
    # dispatch must read 101 from Redis, not 99 from app.state.
    assert _call(c, "coder_agent").status_code == 200
    assert store[api._AGENT_STATS_KEY]["handled"]["coder"] == 102, (
        f"the second call did not continue from Redis: {store[api._AGENT_STATS_KEY]}"
    )

    data = c.get("/agents/stats").json()
    coder = [a for a in data["agents"] if a["key"] == "coder"][0]
    assert coder["tasks_handled"] == 102, (
        f"the dashboard did not read Redis: {coder['tasks_handled']}"
    )


def test_an_unknown_agent_key_is_skipped_by_the_recorder(journey, monkeypatch):
    """A key that is not a real agent must not appear on the dashboard.

    `_record_agent_usage` guards with `if key not in AGENT_KEYS: continue`. That
    line had never executed: every caller passes keys from a fixed map. It is
    the guard that stops a typo (or a future dynamic tool name) from creating a
    phantom agent row, so it is worth its own test rather than a comment.
    """
    import asyncio

    monkeypatch.setattr(api.app.state, "agent_stats",
                        {"handled": {}, "last_used": {}}, raising=False)

    asyncio.get_event_loop_policy().new_event_loop()
    asyncio.run(api._record_agent_usage(["not_an_agent", "coder"]))

    handled = api.app.state.agent_stats["handled"]
    assert "coder" in handled, "the real agent key was not counted"
    assert "not_an_agent" not in handled, (
        f"a phantom agent reached the dashboard: {handled}"
    )
