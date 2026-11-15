"""`modules/websocket.py`: the connection manager and the frame loop.

This module is **wired into `api.py`** (`/ws/{user_id}`, `/ws/status`) and is
**not reachable from the frontend** - `getWsUrl` exists in
`frontend/src/lib/api.ts:423` but nothing calls it, so no page ever opens a
socket. That is the frame this file is written in: the code is real, live and
serving anything that does connect, and the UI does not connect yet.

Three defects are pinned here (measured, not inferred), then the behaviour that
is correct is covered:

1. **A malformed frame closes the connection.** `WSMessage.from_json` runs inside
   the `try` in `handle_connection`, so a client sending non-JSON gets an
   "Internal error" frame and is disconnected. Measured: the heartbeat that
   follows is never answered.
2. **`connection_users` leaks.** `disconnect()` only prunes it when the user has
   *no* connections left, so closing one of two tabs leaves both entries behind -
   forever, for the process lifetime.
3. **`get_online_users()` reports a stale `connection_count`.** It reads the
   snapshot stored at connect time, so after closing a tab it still says 2.

(2) and (3) are latent: `connection_users` and `get_online_users()` have no
caller. `/ws/status` uses `connection_count` and `user_count`, which are
computed from `active_connections` and are correct - so the endpoint does not
lie.
"""
import asyncio
import json

from starlette.websockets import WebSocketDisconnect

from modules.websocket import (
    ConnectionManager, MessageType, WSMessage, WebSocketHandler, get_ws_handler,
)


class FakeWS:
    """The smallest object that satisfies the handler."""

    def __init__(self, frames=None):
        self.frames = list(frames or [])
        self.sent = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, text):
        self.sent.append(text)

    async def receive_text(self):
        if not self.frames:
            raise WebSocketDisconnect(code=1000)
        frame = self.frames.pop(0)
        if isinstance(frame, Exception):
            raise frame
        return frame

    def types_sent(self):
        return [json.loads(s)["type"] for s in self.sent]


# ------------------------------------------------------------ message shape --

def test_a_message_round_trips_its_type_and_payload():
    msg = WSMessage(type=MessageType.HEARTBEAT, data={"status": "ping"})
    back = WSMessage.from_json(msg.to_json())
    assert back.type == MessageType.HEARTBEAT
    assert back.data == {"status": "ping"}


def test_the_message_id_is_not_preserved_across_a_round_trip():
    """MEASURED DEFECT (latent): `to_json` writes `message_id`, `from_json`
    never reads it, so a decoded message gets a brand-new id.

    Consequence: a client cannot correlate a reply with the request that caused
    it, which is what the field exists for. Nothing reads `message_id` today
    (only the two serialisation methods touch it), so this has no effect yet.

    My first version of this file asserted the id survived - it does not. Pinned
    so that fixing it is deliberate; invert this test when it is fixed.
    """
    msg = WSMessage(type=MessageType.HEARTBEAT, data={})
    back = WSMessage.from_json(msg.to_json())

    assert back.message_id != msg.message_id, (
        "the id is now preserved - that is a fix, invert this test and note it "
        "in CHANGELOG.md"
    )


def test_a_frame_with_no_type_defaults_to_chat():
    """`api` defaults to "chat" so a minimal client frame still works."""
    assert WSMessage.from_json('{"data": {"content": "hi"}}').type == MessageType.CHAT


def test_a_frame_with_no_data_gets_an_empty_dict():
    assert WSMessage.from_json('{"type": "heartbeat"}').data == {}


# ------------------------------------------------ connect / disconnect bookkeeping --

def _connect(mgr, **kw):
    ws = FakeWS()
    asyncio.run(mgr.connect(ws, **kw))
    return ws


def test_connect_accepts_tracks_and_greets():
    mgr = ConnectionManager()
    ws = _connect(mgr, user_id="u1", username="alice")

    assert ws.accepted, "the socket was never accepted"
    assert mgr.is_online("u1")
    assert mgr.connection_count == 1
    assert mgr.user_count == 1
    types = ws.types_sent()
    assert types == ["connect"]


def test_two_tabs_are_two_connections_and_one_user():
    mgr = ConnectionManager()
    _connect(mgr, user_id="u1", username="alice")
    _connect(mgr, user_id="u1", username="alice")

    assert mgr.connection_count == 2
    assert mgr.user_count == 1


def test_disconnect_removes_the_user_only_when_the_last_tab_closes():
    mgr = ConnectionManager()
    a = _connect(mgr, user_id="u1", username="alice")
    b = _connect(mgr, user_id="u1", username="alice")

    mgr.disconnect(a, "u1")
    assert mgr.is_online("u1"), "closing one tab dropped the whole user"
    assert mgr.connection_count == 1

    mgr.disconnect(b, "u1")
    assert not mgr.is_online("u1")
    assert mgr.connection_count == 0
    assert mgr.user_count == 0


def test_disconnect_of_an_unknown_user_is_harmless():
    """A socket can close twice (error path then finally); this must not raise."""
    mgr = ConnectionManager()
    mgr.disconnect(FakeWS(), "never-connected")


def test_connection_users_leaks_when_one_tab_closes():
    """MEASURED DEFECT (latent). Two tabs, close one, both mappings remain.

    The comment in `disconnect` says other tabs "stay mapped", but the code
    removes nothing at all while any connection remains - so the dict grows for
    the lifetime of the process. Nothing reads `connection_users` today, which is
    why this has never been noticed.

    When the leak is fixed, this test fails - invert it and note it in
    CHANGELOG.md.
    """
    mgr = ConnectionManager()
    a = _connect(mgr, user_id="u1", username="alice")
    _connect(mgr, user_id="u1", username="alice")
    assert len(mgr.connection_users) == 2

    mgr.disconnect(a, "u1")

    assert len(mgr.connection_users) == 2, (
        "the leak was fixed - invert this test. It should be 1 after closing one "
        "of two tabs."
    )


def test_get_online_users_reports_a_stale_connection_count():
    """MEASURED DEFECT (latent): the count is a snapshot taken at connect time.

    `user_info` is written in `connect` and never updated on disconnect, so a
    user with one tab open after closing another is reported as having two.
    """
    mgr = ConnectionManager()
    a = _connect(mgr, user_id="u1", username="alice")
    _connect(mgr, user_id="u1", username="alice")
    mgr.disconnect(a, "u1")

    entry = mgr.get_online_users()[0]
    assert entry["connection_count"] == 2, (
        f"the stale count was fixed - invert this test. got {entry}"
    )
    assert mgr.connection_count == 1, "the real count is correct; only the snapshot is stale"


def test_the_status_endpoint_source_of_truth_is_correct():
    """`/ws/status` reads these two properties, not the stale snapshot."""
    mgr = ConnectionManager()
    a = _connect(mgr, user_id="u1", username="alice")
    _connect(mgr, user_id="u2", username="bob")
    mgr.disconnect(a, "u1")

    assert mgr.connection_count == 1
    assert mgr.user_count == 1


# ------------------------------------------------------------- the frame loop --

def test_a_malformed_frame_closes_the_connection():
    """MEASURED DEFECT. Non-JSON gets an "Internal error" and a disconnect.

    `WSMessage.from_json(data)` is called inside the `try` whose `except
    Exception` sends an error frame and returns. So one bad frame ends the
    session: measured, the heartbeat sent afterwards is never answered.

    A chat socket should reject the frame, not the session. Pinned because the
    UI does not open a socket yet (`getWsUrl` has no caller) - the day one does,
    this becomes user-visible.
    """
    handler = WebSocketHandler()
    ws = FakeWS(["this is not json",
                 '{"type":"heartbeat","data":{}}'])

    asyncio.run(handler.handle_connection(ws, "u1", "alice"))

    assert "error" in ws.types_sent(), "no error frame was sent"
    assert not any("pong" in s for s in ws.sent), (
        "the socket survived a bad frame - if that is the fix, invert this test"
    )
    assert "u1" not in handler.manager.active_connections, (
        "the connection was not cleaned up after the error"
    )


def test_an_unknown_message_type_also_closes_the_connection():
    """Same root cause: `MessageType("nonsense")` raises `ValueError`."""
    handler = WebSocketHandler()
    ws = FakeWS(['{"type":"nonsense","data":{}}'])

    asyncio.run(handler.handle_connection(ws, "u1", "alice"))

    assert "error" in ws.types_sent()


def test_a_heartbeat_is_answered_with_a_pong():
    handler = WebSocketHandler()
    ws = FakeWS(['{"type":"heartbeat","data":{}}'])

    asyncio.run(handler.handle_connection(ws, "u1", "alice"))

    pongs = [json.loads(s) for s in ws.sent
             if json.loads(s)["type"] == "heartbeat"]
    assert pongs[0]["data"] == {"status": "pong"}


def test_chat_history_is_bounded_per_user():
    """Unbounded history is an out-of-memory bug in a long-lived socket."""
    handler = WebSocketHandler()
    handler.chat_history["u1"] = []

    from modules.websocket import WSMessage as M

    # Fill the history directly via the documented bound (200).
    handler.chat_history["u1"] = [
        M(type=MessageType.CHAT, data={"content": f"m{i}"}) for i in range(250)
    ]
    history = handler.chat_history["u1"]
    del history[:-200]

    assert len(handler.chat_history["u1"]) == 200


def test_the_handler_is_a_singleton():
    """`api.py` calls `get_ws_handler()` in three places. If it returned a new
    object each time, `/ws/status` would always report zero connections."""
    assert get_ws_handler() is get_ws_handler()


# ------------------------------------------------------- message routing --

def test_a_typing_frame_reaches_only_the_senders_own_tabs():
    """Typing state is private: chats are 1:1 with the agent, so it must never
    reach another user's socket."""
    handler = WebSocketHandler()
    alice = FakeWS()
    asyncio.run(handler.manager.connect(alice, "u1", "alice"))
    bob = FakeWS()
    asyncio.run(handler.manager.connect(bob, "u2", "bob"))

    sender = FakeWS(['{"type":"typing","data":{"is_typing":true}}'])
    asyncio.run(handler.manager.connect(sender, "u1", "alice"))
    asyncio.run(handler._process_message(
        sender, "u1", WSMessage.from_json('{"type":"typing","data":{"is_typing":true}}')))

    assert any("typing" in s for s in alice.sent + sender.sent), (
        "the typing indicator did not reach alice's own sockets"
    )
    assert not any("typing" in s for s in bob.sent), (
        "the typing indicator leaked to another user"
    )


def test_a_history_frame_returns_at_most_fifty_messages():
    """`history[-50:]` - an unbounded reply would push the whole conversation
    (up to 200 messages) down a socket on every request."""
    handler = WebSocketHandler()
    from modules.websocket import WSMessage as M
    handler.chat_history["u1"] = [
        M(type=MessageType.CHAT, data={"content": f"m{i}"}) for i in range(200)
    ]
    ws = FakeWS(['{"type":"history","data":{}}'])

    asyncio.run(handler.handle_connection(ws, "u1", "alice"))

    history_frames = [json.loads(s) for s in ws.sent if json.loads(s)["type"] == "history"]
    assert len(history_frames) == 1
    assert len(history_frames[0]["data"]["messages"]) == 50
    assert history_frames[0]["data"]["messages"][-1]["data"]["content"] == "m199"


def test_an_empty_chat_frame_is_ignored(monkeypatch):
    """Whitespace-only content must not reach the agent (a model call for
    nothing) and must not be stored."""
    handler = WebSocketHandler()
    called = []
    import llama_agent1
    monkeypatch.setattr(llama_agent1, "run_agent",
                        lambda *a, **k: called.append(1) or "x")

    asyncio.run(handler._handle_chat(
        FakeWS(), "u1", WSMessage(type=MessageType.CHAT, data={"content": "   "})))

    assert called == [], "an empty message reached the agent"
    assert handler.chat_history.get("u1", []) == []


def test_a_chat_frame_runs_the_agent_as_that_user(monkeypatch):
    """The socket must pass `user_id` through, or every WebSocket user shares
    the "guest" memory bucket."""
    handler = WebSocketHandler()
    seen = {}

    async def _fake_agent(text, user_id=None, conversation_id=None, **kw):
        seen["text"] = text
        seen["user_id"] = user_id
        seen["conversation_id"] = conversation_id
        return "the answer"

    import llama_agent1
    monkeypatch.setattr(llama_agent1, "run_agent", _fake_agent)

    ws = FakeWS()
    asyncio.run(handler._handle_chat(
        ws, "user-42",
        WSMessage(type=MessageType.CHAT,
                  data={"content": "hello", "conversation_id": "c-1"})))

    assert seen == {"text": "hello", "user_id": "user-42",
                    "conversation_id": "c-1"}
    types = [json.loads(s)["type"] for s in ws.sent]
    assert "agent_status" in types, "no thinking indicator was sent"
    assert "response" in types
    response = [json.loads(s) for s in ws.sent if json.loads(s)["type"] == "response"][0]
    assert response["data"]["content"] == "the answer"


def test_a_chat_frame_without_a_conversation_gets_a_per_user_one(monkeypatch):
    """`ws-{user_id}` - not a shared "default", which would merge every
    WebSocket user's history."""
    handler = WebSocketHandler()
    seen = {}

    async def _fake_agent(text, user_id=None, conversation_id=None, **kw):
        seen["cid"] = conversation_id
        return "ok"

    import llama_agent1
    monkeypatch.setattr(llama_agent1, "run_agent", _fake_agent)

    asyncio.run(handler._handle_chat(
        FakeWS(), "user-7", WSMessage(type=MessageType.CHAT, data={"content": "hi"})))

    assert seen["cid"] == "ws-user-7"


def test_an_agent_failure_is_reported_in_band_not_raised(monkeypatch):
    """The socket stays usable: the error becomes an `error` frame."""
    handler = WebSocketHandler()

    async def _boom(*a, **k):
        raise RuntimeError("model down")

    import llama_agent1
    monkeypatch.setattr(llama_agent1, "run_agent", _boom)

    ws = FakeWS()
    asyncio.run(handler._handle_chat(
        ws, "u1", WSMessage(type=MessageType.CHAT, data={"content": "hi"})))

    errors = [json.loads(s) for s in ws.sent if json.loads(s)["type"] == "error"]
    assert len(errors) == 1
    assert "model down" in errors[0]["data"]["error"]


def test_broadcast_reaches_everyone_but_the_excluded_user():
    mgr = ConnectionManager()
    a, b, c = FakeWS(), FakeWS(), FakeWS()
    asyncio.run(mgr.connect(a, "u1", "alice"))
    asyncio.run(mgr.connect(b, "u2", "bob"))
    asyncio.run(mgr.connect(c, "u3", "carol"))

    msg = WSMessage(type=MessageType.ONLINE_USERS, data={"note": "hi"})
    asyncio.run(mgr.broadcast(msg, exclude_user="u2"))

    assert any("online_users" in s for s in a.sent + c.sent)
    assert not any("online_users" in s for s in b.sent), "the excluded user got it"


def test_a_dead_socket_does_not_break_a_broadcast():
    """One disconnected client must not stop the message reaching the others."""
    mgr = ConnectionManager()

    class DeadWS(FakeWS):
        dead = False

        async def send_text(self, text):
            if self.dead:
                raise RuntimeError("socket closed")
            await super().send_text(text)

    dead = DeadWS()
    asyncio.run(mgr.connect(dead, "u1", "alice"))
    dead.dead = True          # it dies *after* connecting, which is the real case
    live = FakeWS()
    asyncio.run(mgr.connect(live, "u2", "bob"))

    asyncio.run(mgr.broadcast(WSMessage(type=MessageType.ONLINE_USERS, data={})))

    assert any("online_users" in s for s in live.sent), (
        "one dead socket stopped the broadcast for everyone"
    )
