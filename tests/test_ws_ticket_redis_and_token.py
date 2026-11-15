"""The two ticket paths `tests/test_ws_ticket.py` cannot reach.

That file (9 tests) covers the **in-memory** ticket store and the "identity comes
from the ticket, not the path" rule. It cannot cover:

1. the **Redis** branch — `pop_json` and the cross-replica store; and
2. the **token** branch of the WebSocket handshake (`?token=`), which is the
   fallback for clients that cannot call `POST /ws/ticket` first.

Both are exercised here against a fake `redis_store`, so no Redis is needed.

The replay question is the reason this matters. A WebSocket ticket is a
credential carried in a **URL**, which lands in logs and browser history; the
whole design is that it is single-use and lasts 60 seconds. `pop_json` is
`GETDEL` (atomic read-and-delete), so a second use must find nothing. That is
asserted here rather than assumed from the docstring.
"""
import os

import pytest

import api
from modules import redis_store


PASSWORD = "Str0ng-Passw0rd!x"


@pytest.fixture
def ws_user(journey, monkeypatch):
    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    # A fake Redis that only exists inside one test.
    store = {}

    async def _set_json(key, value, ttl=0):
        store[key] = value
        return True

    async def _get_json(key):
        return store.get(key)

    async def _pop_json(key):
        return store.pop(key, None)

    monkeypatch.setattr(redis_store, "is_configured", lambda: True)
    monkeypatch.setattr(redis_store, "set_json", _set_json)
    monkeypatch.setattr(redis_store, "get_json", _get_json)
    monkeypatch.setattr(redis_store, "pop_json", _pop_json)

    c = journey.client
    name = f"ws_{os.urandom(4).hex()}"
    c.cookies.clear()
    r = c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    assert r.status_code in (200, 201), r.text
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200
    return {"client": c, "store": store, "username": name}


# ------------------------------------------------------------ Redis path --

def test_a_ticket_is_written_to_redis_when_it_is_configured(ws_user):
    """With Redis configured, the ticket lives there - not in this process.

    That is what lets a ticket issued by one replica be redeemed by another, so
    a silent fallback to the in-memory dict would break the load-balanced
    deployment without any visible error.
    """
    r = ws_user["client"].post("/ws/ticket")
    assert r.status_code == 200, r.text
    ticket = r.json()["ticket"]

    key = api._WS_TICKET_PREFIX + ticket
    assert key in ws_user["store"], "the ticket was not written to Redis"
    assert ws_user["store"][key]["username"] == ws_user["username"]
    assert ticket not in api._ws_tickets, (
        "the ticket was ALSO kept in memory; with Redis configured it must not be"
    )


def test_a_redis_ticket_can_be_consumed_exactly_once(ws_user):
    """`pop_json` is atomic, so the second redemption must find nothing.

    This is the replay check the in-memory tests cannot make: it is the Redis
    branch that a multi-replica deployment actually uses.

    Driven with `asyncio.run` like the rest of this suite (no anyio plugin
    dependency), so the coroutine really executes rather than being skipped by a
    missing async marker.
    """
    import asyncio

    r = ws_user["client"].post("/ws/ticket")
    ticket = r.json()["ticket"]

    first = asyncio.run(api._consume_ws_ticket(ticket))
    assert first is not None, "the first redemption failed"
    assert first["username"] == ws_user["username"]

    second = asyncio.run(api._consume_ws_ticket(ticket))
    assert second is None, "the ticket was replayable through the Redis store"

    third = asyncio.run(api._consume_ws_ticket(ticket))
    assert third is None


def test_an_unknown_ticket_is_rejected_even_with_redis_configured(ws_user):
    ws_user["client"].cookies.clear()
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect) as exc:
        with ws_user["client"].websocket_connect("/ws/ignored?ticket=not-a-ticket") as ws:
            ws.receive_text()
    assert exc.value.code == 1008


# ------------------------------------------------------------ token path --

def test_the_websocket_accepts_a_valid_access_token(ws_user):
    """`?token=<access>` is the documented fallback to the ticket flow.

    The path segment is still ignored - the token decides who you are - so
    connecting as `/ws/someone-else` with your own token must give YOUR identity,
    not theirs.
    """
    token = ws_user["client"].cookies.get("access_token")
    assert token

    with ws_user["client"].websocket_connect(
            f"/ws/somebody-elses-id?token={token}") as ws:
        first = ws.receive_json()
    payload = first.get("data", first)
    who = str(payload)
    assert ws_user["username"] in who, (
        f"the socket did not take the identity from the token: {first}"
    )
    assert "somebody-elses-id" not in who, (
        "the socket trusted the user_id in the path"
    )


def test_an_invalid_token_closes_the_connection_with_1008(ws_user):
    """The socket must be CLOSED, not left open holding a dead credential.

    Bounded on purpose, twice over. Measured: with the close removed, a plain
    `ws.receive_text()` HANGS (a run sat until the 300s command timeout), and
    `asyncio.wait_for` does not help because starlette's receive is blocking.

    The read happens in a worker thread with a 5s ceiling, and whatever the
    outcome the socket is closed from the main thread afterwards - otherwise a
    still-open connection keeps the TestClient's portal alive and the whole
    suite hangs on teardown even though the assertion already failed.
    """
    import queue
    import threading

    from starlette.websockets import WebSocketDisconnect

    outcome = queue.Queue()
    holder = {}

    def _attempt():
        conn = ws_user["client"].websocket_connect("/ws/x?token=garbage.token.here")
        holder["conn"] = conn
        try:
            with conn as ws:
                ws.receive_text()
            outcome.put(("open", None, None))
        except WebSocketDisconnect as e:
            outcome.put(("closed", e.code, e.reason))
        except Exception as e:  # noqa: BLE001 - reported, not swallowed
            outcome.put(("error", type(e).__name__, str(e)))

    t = threading.Thread(target=_attempt, daemon=True)
    t.start()
    try:
        state, code, reason = outcome.get(timeout=5.0)
    except queue.Empty:
        pytest.fail(
            "the socket was neither closed nor readable within 5s - an invalid "
            "token was left connected instead of being refused with 1008"
        )
    finally:
        conn = holder.get("conn")
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - only unblocking a dead socket
                pass
        t.join(timeout=2.0)

    assert state == "closed", (
        f"an invalid token left the socket {state}: {code} {reason}"
    )
    assert code == 1008, f"closed with {code}, expected 1008"
    assert "invalid" in (reason or "").lower()


def test_a_cookie_token_authenticates_the_socket(ws_user):
    """The browser cannot set headers on a WebSocket handshake, so the access
    token cookie is a legitimate credential there - and it is read from the
    cookie rather than the query string."""
    with ws_user["client"].websocket_connect("/ws/x") as ws:
        first = ws.receive_json()
    assert ws_user["username"] in str(first.get("data", first))
