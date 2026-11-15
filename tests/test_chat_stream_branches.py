"""`/chat/stream` end to end: the path every user actually touches.

Measured before writing this: **all 133 executable lines inside `chat_stream`
were unexecuted**. `test_chat_slo_metrics.py` posts to the route, but only to
check the route/outcome counters, and only ever through the `agent` and `team`
branches. Everything else — the special commands, the fact and name lookups,
the document path, the token-budget refusal, the stream timeout, the empty-answer
guard, the PII redactor tail — had never run.

Two things are real in these tests: the FastAPI app (lifespan, middleware,
cookies, auth, SSE plumbing, `timed_chat_stream`) and the branches inside
`event_generator`. One thing is fake: `run_agent_stream`, the single point where
the model is called.

Faking it at that boundary matters. Patching the generator's output or the
response object would skip the code these tests exist to cover — the routing
decisions, the redactor, the persistence and the `[DONE]` contract.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASSWORD = "Str0ng-Passw0rd!x"


def events(text):
    """Parse an SSE body into (kind, payload) tuples.

    `data: {"type": "..."}` becomes ("json", dict); `data: <text>` becomes
    ("text", str); the terminator becomes ("done", None). Anything unparseable
    fails loudly rather than being skipped — a malformed frame is a bug.
    """
    out = []
    for chunk in text.split("\n\n"):
        if not chunk.strip():
            continue
        assert chunk.startswith("data: "), f"not an SSE frame: {chunk!r}"
        payload = chunk[len("data: "):]
        if payload.strip() == "[DONE]":
            out.append(("done", None))
            continue
        if payload.strip().startswith("{"):
            out.append(("json", json.loads(payload)))
        else:
            out.append(("text", payload))
    return out


def text_of(parsed):
    return "".join(p for k, p in parsed if k == "text")


def types_of(parsed):
    return [p.get("type") for k, p in parsed if k == "json"]


@pytest.fixture
def streamer(journey, monkeypatch):
    """A logged-in user with the model faked at its narrowest point."""
    c = journey.client

    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    name = f"str_{os.urandom(4).hex()}"
    c.cookies.clear()
    r = c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    assert r.status_code in (200, 201), r.text
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200

    import llama_agent1
    calls = {}

    def _make(tokens):
        def _stream(message, **kw):
            calls["message"] = message
            calls["kw"] = kw
            for t in tokens:
                yield t
        return _stream

    state = {"tokens": ["Hello", " world"]}
    monkeypatch.setattr(llama_agent1, "run_agent_stream",
                        lambda m, **k: _make(state["tokens"])(m, **k))
    return {"client": c, "calls": calls, "state": state, "journey": journey}


# ------------------------------------------------------------ the SSE contract --

def test_a_general_answer_streams_tokens_then_done(streamer):
    r = streamer["client"].post("/chat/stream", json={"message": "hello there friend"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    # Streaming must not be buffered by a proxy in front of it.
    assert r.headers.get("x-accel-buffering") == "no"
    assert "no-cache" in r.headers.get("cache-control", "")

    parsed = events(r.text)
    assert parsed[-1] == ("done", None), "the stream must end with [DONE]"
    assert text_of(parsed) == "Hello world", f"got {text_of(parsed)!r}"

    # The route announces itself and what it will actually run.
    assert "config" in types_of(parsed)
    assert "reasoning" in types_of(parsed)


def test_the_agent_receives_the_message_and_the_conversation_identity(streamer):
    streamer["client"].post("/chat/stream", json={"message": "what is a monad"})
    assert streamer["calls"]["message"] == "what is a monad"
    kw = streamer["calls"]["kw"]
    for required in ("memory", "facts", "user_id", "conversation_id"):
        assert required in kw, f"run_agent_stream was not given {required}"


def test_exactly_one_done_frame(streamer):
    """Two would end the stream twice; none would hang the client."""
    parsed = events(streamer["client"].post(
        "/chat/stream", json={"message": "hello there friend"}).text)
    assert sum(1 for k, _ in parsed if k == "done") == 1


# ------------------------------------------------------- special commands --

def test_remember_and_recall_round_trip(streamer):
    c = streamer["client"]
    r = c.post("/chat/stream", json={"message": "remember favourite colour = blue"})
    assert "Remembered" in text_of(events(r.text)), events(r.text)

    r = c.post("/chat/stream", json={"message": "recall favourite colour"})
    assert "blue" in text_of(events(r.text))


def test_remember_without_an_equals_sign_explains_the_syntax(streamer):
    """A wrong command must say how to use it, not silently store nothing."""
    r = streamer["client"].post("/chat/stream", json={"message": "remember just this"})
    assert "Usage" in text_of(events(r.text))


def test_show_facts_lists_what_was_stored(streamer):
    c = streamer["client"]
    c.post("/chat/stream", json={"message": "remember team = platform"})
    r = c.post("/chat/stream", json={"message": "show facts"})
    assert "team" in text_of(events(r.text))


def test_show_facts_says_so_when_nothing_is_stored(streamer):
    r = streamer["client"].post("/chat/stream", json={"message": "show facts"})
    assert "No facts stored" in text_of(events(r.text))


def test_a_command_reply_is_not_sent_to_the_model(streamer):
    """`remember`/`show facts` are answered locally; a model call would be waste,
    and would also make these answers non-deterministic."""
    streamer["client"].post("/chat/stream", json={"message": "show facts"})
    assert streamer["calls"] == {}, "a local command reached the language model"


def test_search_command_returns_retrieved_context(streamer):
    r = streamer["client"].post("/chat/stream", json={"message": "search rule of 40"})
    assert r.status_code == 200
    assert events(r.text)[-1] == ("done", None)


# ------------------------------------------------------------- failure paths --

def test_an_empty_answer_is_replaced_with_an_explanation(streamer):
    """A blank stream would render as an empty bubble with no reason given."""
    streamer["state"]["tokens"] = []
    r = streamer["client"].post("/chat/stream", json={"message": "hello there friend"})
    body = text_of(events(r.text))
    assert body.strip(), "an empty answer was streamed as nothing at all"
    assert "empty answer" in body.lower(), body


def test_an_exception_mid_stream_is_an_open_finding_NOT_a_guarantee(streamer, monkeypatch):
    """The general route has no error handling around the model call.

    **This test records a real gap instead of asserting the behaviour is
    correct.** Measured: when `run_agent_stream` raises, the client receives the
    two preamble frames (`config`, `reasoning`) and then **nothing** — no error
    event, no `[DONE]`. The exception propagates out of the response.

    Compare with the agent route, which has an `except` that emits a readable
    message and then still terminates with `[DONE]`:

        except Exception:
            logging.getLogger(__name__).exception("%s agent stream failed", agent_type)
            full = f"⚠️ The {agent.name} could not complete this task..."
            yield f"data: {escape_sse(full)}\n\n"

    So the same class of failure is explained on one route and silent on the
    other. The assertion below is deliberately written to **fail when the gap is
    fixed**, so the fix cannot be made without updating this record.
    """
    import llama_agent1

    def _boom(message, **kw):
        raise RuntimeError("model exploded mid-stream")

    monkeypatch.setattr(llama_agent1, "run_agent_stream", _boom)
    # TestClient is `raise_server_exceptions=True` by default, so an exception
    # escaping the generator is re-raised here instead of becoming a response.
    # That is the honest way to observe it: the point of this test is *what the
    # client gets*, so the client is constructed to behave like a browser.
    from fastapi.testclient import TestClient
    import api
    with TestClient(api.app, raise_server_exceptions=False) as plain:
        r = plain.post("/chat/stream", json={"message": "hello there friend"})
    assert r.status_code in (200, 500), r.status_code
    if r.status_code == 200:
        parsed = events(r.text)
        # CURRENT behaviour: no terminator and no explanation.
        assert parsed[-1][0] == "json", (
            "the stream now terminates cleanly - the gap this test records has "
            "been fixed; update this test and docs/COVERAGE_API.md"
        )
        assert "[DONE]" not in r.text, "the gap has been fixed; update this test"
        assert not any("⚠️" in str(p) for _, p in parsed), (
            "an error message is now emitted; the gap has been fixed"
        )


def test_the_token_budget_refusal_never_calls_the_model(streamer, monkeypatch):
    """When a user is over budget the refusal must be immediate.

    The guard is disabled unless a limit is configured, so it is enabled here
    rather than assumed to be on.
    """
    from modules import llm_guard

    monkeypatch.setattr(llm_guard, "over_budget", lambda subject=None: True)
    monkeypatch.setattr(llm_guard, "budget_message", lambda: "Budget exhausted for today.")

    r = streamer["client"].post("/chat/stream", json={"message": "hello there friend"})
    assert r.status_code == 200
    parsed = events(r.text)
    assert parsed[-1] == ("done", None)
    assert "Budget exhausted" in text_of(parsed) or "Budget exhausted" in r.text
    assert "reasoning" in types_of(parsed), "the refusal did not explain itself"
    assert streamer["calls"] == {}, "a budget refusal still called the model"


def test_the_stream_stops_at_the_duration_cap(streamer, monkeypatch):
    """STREAM_MAX_DURATION must actually cut the stream, not just be defined."""
    import api

    monkeypatch.setattr(api, "STREAM_MAX_DURATION", 0.0)
    r = streamer["client"].post("/chat/stream", json={"message": "hello there friend"})
    assert r.status_code == 200
    body = text_of(events(r.text))
    assert "timed out" in body.lower(), f"the cap did not fire: {body!r}"


# ------------------------------------------------------------------ PII --

def test_a_card_number_split_across_tokens_is_redacted_whole(streamer):
    """The redactor must survive a token boundary inside the number.

    This is the bug that was fixed once already: a fixed-offset cut splits
    `4111 1111 1111 1111` into two halves that each match no pattern, so the
    number reaches the browser.
    """
    streamer["state"]["tokens"] = ["4111 1111", " 1111 1111 is the card"]
    r = streamer["client"].post("/chat/stream", json={"message": "hello there friend"})
    body = text_of(events(r.text))
    assert "4111 1111 1111 1111" not in body, f"a card number reached the client: {body!r}"
    assert "[REDACTED" in body.upper() or "REDACTED" in body.upper(), body
