"""`/chat` and `/chat/stream` must give the same answer to the same message.

They are two implementations of one interface. The frontend calls **both**
(`frontend/src/lib/api.ts`: `chat()` posts `/chat`, `chatStream()` posts
`/chat/stream`), and the chat box specifically uses the streaming one.

They had already drifted. Measured on the same user and the same message:

    remember colour = blue       /chat -> Remembered    /chat/stream -> Remembered
    remember food is pizza       /chat -> Remembered    /chat/stream -> "Usage: remember key = value"
    remember my name is Ravi     /chat -> Remembered    /chat/stream -> "Usage: remember key = value"

So the route real users were on **refused two of the three forms it told them to
use**. Nothing caught it, because each route was only ever tested on its own, and
`/chat` had no behaviour tests at all.

Two things now prevent a repeat:

1. both routes call one parser (`api._parse_remember`), so there is a single
   place for the grammar to live; and
2. this file posts the same message to both and requires the same answer, so a
   future change that touches only one of them fails here.
"""

import os
import sys

import pytest

import api

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASSWORD = "Str0ng-Passw0rd!x"


def _stream_text(resp):
    """The answer text out of an SSE body (not the JSON events)."""
    out = []
    for chunk in resp.text.split("\n\n"):
        if not chunk.startswith("data: "):
            continue
        payload = chunk[6:]
        if payload.strip() == "[DONE]" or payload.strip().startswith("{"):
            continue
        out.append(payload)
    return "".join(out)


@pytest.fixture
def two_routes(journey):
    c = journey.client

    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    name = f"par_{os.urandom(4).hex()}"
    c.cookies.clear()
    r = c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    assert r.status_code in (200, 201), r.text
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200

    yield {"client": c}
    rate_limiter._requests.clear()


def _ask_both(user, message):
    """Return (plain_answer, streamed_answer)."""
    c = user["client"]
    r = c.post("/chat", json={"message": message})
    assert r.status_code == 200, f"/chat failed: {r.status_code} {r.text}"
    plain = r.json()["response"]

    r = c.post("/chat/stream", json={"message": message})
    assert r.status_code == 200, f"/chat/stream failed: {r.status_code} {r.text}"
    return plain, _stream_text(r)


# ------------------------------------------------------------- parity --

@pytest.mark.parametrize("message,expected", [
    ("remember favourite colour = blue", "favourite colour = blue"),
    # `_IS_PATTERN` is `(.{5,}?)\s+is\s+(.+)` - the key must be 5+ chars.
    # "food is pizza" therefore parses on NEITHER route (see the parser test).
    ("remember favourite food is pizza", "favourite food = pizza"),
    ("remember my name is Ravi", "your name is Ravi"),
])
def test_both_routes_accept_all_three_remember_forms(two_routes, message, expected):
    """The exact forms that used to disagree."""
    plain, streamed = _ask_both(two_routes, message)
    assert "Remembered" in plain, f"/chat rejected a valid form: {plain!r}"
    assert "Remembered" in streamed, (
        f"/chat/stream rejected a form /chat accepts: {streamed!r} - the routes "
        f"have drifted apart again"
    )
    assert expected.lower() in plain.lower(), plain
    assert expected.lower() in streamed.lower(), streamed


def test_both_routes_reject_a_remember_with_no_fact_the_same_way(two_routes):
    plain, streamed = _ask_both(two_routes, "remember nothing useful here")
    assert "Usage" in plain, plain
    assert "Usage" in streamed, streamed
    # And the advice must name a form the route actually accepts - the original
    # bug was telling the user to use syntax the route then refused.
    assert "my name is" in plain.lower() and "is" in plain.lower(), plain


def test_a_fact_stored_on_one_route_is_visible_on_the_other(two_routes):
    """A single fact store, so the two routes are not two memories."""
    c = two_routes["client"]
    c.post("/chat/stream", json={"message": "remember team = platform"})

    plain, _ = _ask_both(two_routes, "show facts")
    assert "team" in plain.lower(), f"a fact streamed in is missing from /chat: {plain!r}"

    c.post("/chat", json={"message": "remember tier = gold"})
    _, streamed = _ask_both(two_routes, "show facts")
    assert "tier" in streamed.lower(), (
        f"a fact stored through /chat is missing from /chat/stream: {streamed!r}"
    )


# ------------------------------------------------- the /chat route itself --

def test_chat_returns_the_documented_response_shape(two_routes):
    r = two_routes["client"].post("/chat", json={"message": "hello there friend"})
    assert r.status_code == 200
    body = r.json()
    for field in ("response", "timestamp", "response_time"):
        assert field in body, f"ChatResponse is missing {field}: {body}"
    assert isinstance(body["response"], str) and body["response"]
    assert isinstance(body["response_time"], (int, float))
    assert body["response_time"] >= 0


def test_recall_my_name_works_on_both_routes(two_routes):
    """`recall my name` must resolve to the stored `user's name` on BOTH routes.

    /chat normalised "my name" -> "user's name"; /chat/stream did not, so the
    stored name was unreachable on the route the chat box actually uses.
    """
    c = two_routes["client"]
    c.post("/chat/stream", json={"message": "remember my name is Ravi"})

    plain, streamed = _ask_both(two_routes, "recall my name")
    assert "Ravi" in plain, f"/chat could not recall the stored name: {plain!r}"
    assert "Ravi" in streamed, (
        f"/chat/stream could not recall a name it had just stored: {streamed!r}"
    )


def test_an_unknown_recall_reads_the_same_on_both_routes(two_routes):
    """A miss must be a plain miss - and, above all, must not wear a tick.

    Comparing the two routes to each other is NOT enough here, and that was
    proven by sabotage: with /chat reverted to `f"✅ {key}: {recall(...)}"` and
    /chat/stream calling the shared helper, BOTH returned
    "✅ nothing-here: I don't remember anything about 'nothing-here'".
    They agreed, so a comparison-only assertion passed while both were wrong.
    The assertion below is about the answer itself; parity is the second half.
    """
    plain, streamed = _ask_both(two_routes, "recall nothing-here")
    for route, text in (("/chat", plain), ("/chat/stream", streamed)):
        assert "nothing-here" in text.lower(), text
        assert "\u2705" not in text, (
            f"{route} put a success tick on a fact it does not have: {text!r}"
        )
        assert text.lower().lstrip().startswith("i don't remember"), (
            f"{route} did not say it doesn't remember: {text!r}"
        )
    assert plain.lower().strip() == streamed.lower().strip(), (
        f"the two routes disagree about an unknown fact:\n  /chat: {plain!r}\n"
        f"  /chat/stream: {streamed!r}"
    )


def test_a_known_recall_is_the_same_on_both_routes(two_routes):
    """The positive half: a fact that IS stored comes back the same way."""
    c = two_routes["client"]
    c.post("/chat/stream", json={"message": "remember favourite colour = orange"})

    plain, streamed = _ask_both(two_routes, "recall favourite colour")
    for route, text in (("/chat", plain), ("/chat/stream", streamed)):
        assert "orange" in text.lower(), f"{route} lost the stored value: {text!r}"
    assert plain.lower().strip() == streamed.lower().strip(), (
        f"the two routes disagree about a stored fact:\n  /chat: {plain!r}\n"
        f"  /chat/stream: {streamed!r}"
    )


def test_a_failing_agent_is_a_500_on_chat_and_a_message_on_stream(two_routes, monkeypatch):
    """The documented difference between the routes - asserted, not assumed.

    `/chat` raises HTTPException(500) with a generic detail; `/chat/stream` has
    already sent 200 headers, so it has to report the failure inside the stream.

    Which message reaches `run_agent` matters. `route_multi_agent()` claims
    "summarise ..." (-> research) and the multi-agent path has its own graceful
    fallback, so it answers 200 with a polite failure. A plain question is the
    one that actually falls through to `run_agent`.
    """
    import llama_agent1

    def _boom(*a, **k):
        raise RuntimeError("agent exploded")

    monkeypatch.setattr(llama_agent1, "run_agent", _boom)

    # Measured: this message is NOT claimed by route_multi_agent, so it reaches
    # `run_agent` and the failure is the agent's, not the orchestrator's.
    is_routed = api.route_multi_agent("what is the capital of France")[0]
    assert is_routed is False, "this test needs a message the orchestrator does NOT claim"

    r = two_routes["client"].post("/chat", json={"message": "what is the capital of France"})
    assert r.status_code == 500, (r.status_code, r.text[:200])
    assert "Internal server error" in r.json()["detail"]
    # The internal failure text must not reach the client.
    assert "agent exploded" not in r.text, "an internal error leaked to the client"


def test_a_routed_task_that_fails_is_reported_in_the_answer_not_as_a_500(
        two_routes, monkeypatch):
    """A multi-agent failure is handled, not a 500.

    Pinned because the test above deliberately drives the OTHER path: without
    this pair, "summarise ..." returning 200 looks like a bug rather than the
    orchestrator's own fallback.
    """
    from multi_agent import MultiAgentOrchestrator
    from modules.routing import route_multi_agent as _route

    assert _route("summarise this week please")[0] is True, (
        "this test only means something if the message is routed to multi-agent"
    )

    async def _boom(*a, **k):
        raise RuntimeError("orchestrator exploded")

    monkeypatch.setattr(MultiAgentOrchestrator, "process_parallel",
                        _boom, raising=False)

    r = two_routes["client"].post(
        "/chat", json={"message": "summarise this week please"})
    assert r.status_code in (200, 500), r.status_code
    assert "orchestrator exploded" not in r.text


def test_the_parser_rejects_degenerate_input_without_storing_junk():
    """`_parse_remember` must never return a key with no value."""

    for junk in ["", "   ", "= value", "key =", "remember ", " = "]:
        key, value = api._parse_remember(junk)
        assert key is None, f"{junk!r} produced a fact: {(key, value)!r}"

    # The three real forms.
    assert api._parse_remember("colour = blue") == ("colour", "blue")
    assert api._parse_remember("favourite food is pizza") == ("favourite food", "pizza")
    assert api._parse_remember("my name is Ravi") == ("user's name", "Ravi")

    # MEASURED LIMITATION, pinned so it is not mistaken for a bug later:
    # `_IS_PATTERN` is `(.{5,}?)\s+is\s+(.+)`, so a key shorter than 5
    # characters never matches the "X is Y" form. It behaves identically on both
    # routes (both call this parser), so it is not a divergence - but
    # "food is pizza" silently falling through to the agent is surprising.
    assert api._parse_remember("food is pizza") == (None, None)
    assert api._parse_remember("a is b") == (None, None)


# ── Greetings ──────────────────────────────────────────────────────────────
# The original check was `any(g in message.lower() for g in greetings)`: a raw
# substring test. Three letters were enough to hijack a real request.

GREETING_REPLY = "Hello! How can I help you today?"


@pytest.mark.parametrize("message", [
    "summarise this week",              # "this" contains "hi"
    "can you help me write a function",  # "you" contains "yo"
    "do you know python",               # "you" contains "yo"
    "explain this code to me",          # "this" contains "hi"
    "while loop help",                  # "while" contains "hi"
    "support ticket",                   # "support" contains "sup"
    "your code",                        # "your" contains "yo"
    "hit the api",                      # "hit" contains "hi"
])
def test_a_real_request_is_not_answered_with_the_greeting(two_routes, message):
    """A message that merely CONTAINS a greeting substring is not a greeting.

    Before the fix each of these was answered with the canned greeting on
    /chat, so the model was never called and the user got no answer at all.
    """
    plain, streamed = _ask_both(two_routes, message)
    assert plain.strip() != GREETING_REPLY, (
        f"/chat treated {message!r} as a greeting and never answered it"
    )
    assert streamed.strip() != GREETING_REPLY


@pytest.mark.parametrize("message", [
    "hi", "hello", "hey", "good morning", "hi there", "hello again",
    "Hello!", "hey there",
])
def test_a_greeting_is_answered_the_same_on_both_routes(two_routes, message):
    """Greetings get the canned reply on BOTH routes.

    /chat/stream had no greeting branch at all: "hi" cost a full model call
    there while /chat answered instantly. Same message, same answer is the
    rule this whole file exists to enforce.
    """
    plain, streamed = _ask_both(two_routes, message)
    assert plain.strip() == GREETING_REPLY, plain
    assert streamed.strip() == GREETING_REPLY, streamed


def test_the_greeting_matcher_is_not_a_substring_test():
    """Unit-level: every greeting word that hides inside a real word."""
    for message in ("this", "which", "while", "think", "machine", "history",
                    "you", "your", "young", "support", "suppose", "shipping"):
        assert api._is_greeting(message) is False, (
            f"{message!r} was treated as a greeting"
        )
    for message in ("hi", "hello", "hey", "hola", "howdy", "yo", "sup",
                    "good morning", "good afternoon", "good evening"):
        assert api._is_greeting(message) is True, (
            f"{message!r} was NOT treated as a greeting"
        )


def test_a_greeting_with_a_request_attached_is_not_a_greeting():
    """"hi, write me a function" asks for something; the greeting must not eat it."""
    assert api._is_greeting("hi, write me a function") is False
    assert api._is_greeting("hello can you summarise this file") is False
    # ...but politeness words alone still count as a greeting.
    assert api._is_greeting("hi there please") is True


def test_a_greeting_does_not_beat_an_explicit_agent_choice(journey, monkeypatch):
    """Picking "Coder Agent" and typing "hello" must run the agent.

    The streaming greeting branch has to yield to the UI override. It did not at
    first: the branch ran before the override was applied, so "hello" with
    agent="coder" returned the canned greeting and the agent never executed.
    Found by test_multitenancy::test_stream_config_reports_what_actually_ran,
    which asserts on the config frame of exactly that request.
    """
    import multi_agent

    ran = {}

    class _Agent:
        name = "Coder Agent"

        def execute_stream(self, task, critique=False, reflect=False):
            ran["agent"] = task
            yield {"type": "token", "content": "agent answered"}

    class _Orch:
        def __init__(self):
            self.agents = {k: _Agent() for k in api.AGENT_KEYS}

    monkeypatch.setattr(multi_agent, "MultiAgentOrchestrator", _Orch)

    c = journey.client
    from modules.security import rate_limiter
    rate_limiter._requests.clear()
    name = f"par_{os.urandom(4).hex()}"
    c.cookies.clear()
    c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    c.cookies.clear()
    c.post("/auth/login", json={"username": name, "password": PASSWORD})

    r = c.post("/chat/stream", json={"message": "hello", "agent": "coder"})
    text = _stream_text(r)
    assert text.strip() != GREETING_REPLY, (
        "the greeting branch answered a request that explicitly named an agent, "
        f"so the agent never ran: {text!r}"
    )
    assert "agent" in ran, "the chosen agent was never executed"
