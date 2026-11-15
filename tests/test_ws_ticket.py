"""The WebSocket ticket: single-use, short-lived, and never the identity source.

A WebSocket handshake cannot carry an `Authorization` header from a browser, so
the JWT has to travel somewhere visible — a query string. A query string ends up
in proxy logs, browser history and `Referer` headers. The design here avoids that
by issuing a **short-lived, single-use ticket** over HTTPS and putting *that* in
the URL instead.

Two properties make that design safe, and neither was tested before this file:

1. **A ticket works once.** If it can be replayed, it is a password with a
   60-second life sitting in a log line.
2. **Identity comes from the ticket, not the URL.** The path is
   `/ws/{user_id}`, and that `user_id` is deliberately ignored. If anyone ever
   "simplifies" the endpoint by trusting the path, every user can connect as
   every other user, and nothing else in the suite would notice.

The tests below exist to make those two sentences fail loudly if the code changes.
"""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASSWORD = "Str0ng-Passw0rd!x"


@pytest.fixture
def ws_user(journey, monkeypatch):
    """A logged-in user, with the ticket store forced to its in-memory mode.

    Redis is not configured in tests, so the in-memory path is the one under
    test — which is also the path a single-process deployment uses. The Redis
    path is atomic by construction (`GETDEL`); see modules/redis_store.pop_json.
    """
    c = journey.client

    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    import api
    api._ws_tickets.clear()

    name = f"ws_{uuid.uuid4().hex[:8]}"
    c.cookies.clear()
    r = c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    assert r.status_code in (200, 201), r.text
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200

    me = c.get("/auth/me")
    assert me.status_code == 200
    identity = me.json().get("user", me.json())

    yield {"client": c, "username": name, "id": str(identity["id"]),
           "api": api, "journey": journey}
    api._ws_tickets.clear()


def _ticket(user):
    r = user["client"].post("/ws/ticket")
    assert r.status_code == 200, f"ticket issue failed: {r.status_code} {r.text}"
    body = r.json()
    assert "ticket" in body and "expires_in" in body, body
    return body["ticket"]


# --------------------------------------------------------------- issuance --

def test_a_ticket_is_issued_with_a_short_expiry(ws_user):
    body = ws_user["client"].post("/ws/ticket").json()
    assert body["expires_in"] == 60, (
        f"the ticket lifetime is the only thing limiting a leaked ticket; "
        f"got {body['expires_in']}s"
    )
    assert body["ticket"] not in ("", None), body


def test_issuing_a_ticket_requires_authentication(ws_user):
    """Anonymous visitors must not be able to mint tickets."""
    c = ws_user["client"]
    saved = dict(c.cookies)
    c.cookies.clear()
    r = c.post("/ws/ticket")
    assert r.status_code in (401, 403), (
        f"an unauthenticated caller got a ticket: {r.status_code} {r.text}"
    )
    for k, v in saved.items():
        c.cookies.set(k, v)


def test_tickets_are_unique_and_bound_to_the_caller(ws_user):
    first = _ticket(ws_user)
    second = _ticket(ws_user)
    assert first != second, "the same ticket was issued twice"

    store = ws_user["api"]._ws_tickets
    assert store[first]["user_id"] == ws_user["id"], (
        "the ticket is not bound to the user who requested it"
    )


# ------------------------------------------------------------- single use --

def test_a_ticket_can_be_consumed_exactly_once(ws_user):
    """The property that makes a URL-borne credential safe."""
    import asyncio
    ticket = _ticket(ws_user)
    consume = ws_user["api"]._consume_ws_ticket

    first = asyncio.run(consume(ticket))
    assert first is not None, "a freshly issued ticket could not be redeemed"
    second = asyncio.run(consume(ticket))
    assert second is None, "THE TICKET WAS REPLAYED: it can be used more than once"


def test_an_unknown_ticket_is_rejected(ws_user):
    import asyncio
    assert asyncio.run(ws_user["api"]._consume_ws_ticket(str(uuid.uuid4()))) is None


def test_an_expired_ticket_is_rejected(ws_user):
    import asyncio
    ticket = _ticket(ws_user)
    ws_user["api"]._ws_tickets[ticket]["exp"] = 0  # far in the past (monotonic)

    assert asyncio.run(_consume_ws_ticket_safe(ws_user, ticket)) is None


async def _consume_ws_ticket_safe(user, ticket):
    return await user["api"]._consume_ws_ticket(ticket)


# --------------------------------------------------- identity from ticket --

def test_the_websocket_takes_its_identity_from_the_ticket_not_the_path(ws_user):
    """`/ws/<anything>` must resolve to the ticket's user, not the path's.

    This is the endpoint's most important property and the easiest one to break:
    trusting the path segment reads like a harmless simplification and would let
    any user connect as any other.
    """
    from fastapi.testclient import TestClient
    import api

    ticket = _ticket(ws_user)
    victim = str(uuid.uuid4())  # someone else's id in the path

    from modules.websocket import get_ws_handler
    handler = get_ws_handler()

    bound = {}
    original = handler.manager.connect

    async def _record(websocket, user_id, username):
        bound["user_id"] = user_id
        bound["username"] = username
        return await original(websocket, user_id, username)

    handler.manager.connect = _record
    try:
        with TestClient(api.app) as client:
            with client.websocket_connect(f"/ws/{victim}?ticket={ticket}"):
                pass
    finally:
        handler.manager.connect = original

    assert bound, "the websocket handler was never reached"
    assert bound["user_id"] == ws_user["id"], (
        f"the connection was bound to {bound['user_id']!r} - if that is the path "
        f"segment {victim!r}, identity is being taken from the URL"
    )
    assert bound["user_id"] != victim, "THE PATH SEGMENT WON OVER THE TICKET"
    assert bound["username"] == ws_user["username"]


def test_an_invalid_ticket_closes_the_connection_with_1008(ws_user):
    """Fail closed. A rejected handshake must not fall through to anonymous."""
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    import api

    with TestClient(api.app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws/anyone?ticket=not-a-real-ticket"):
                pass
        assert exc.value.code == 1008, (
            f"expected a policy-violation close (1008), got {exc.value.code}"
        )


# ------------------------------------------------------------ the binding --

def test_the_ticket_store_is_purged_of_expired_entries(ws_user):
    """Issuing tickets must not leak memory forever.

    The purge is opportunistic — it runs when a new ticket is issued — so this
    checks that it happens rather than that a background job exists.
    """
    api = ws_user["api"]
    api._ws_tickets.clear()

    stale = "stale-ticket-for-test"
    api._ws_tickets[stale] = {"user_id": ws_user["id"], "username": "x",
                              "exp": 0, "used": False}
    _ticket(ws_user)
    assert stale not in api._ws_tickets, "expired tickets accumulate without bound"
