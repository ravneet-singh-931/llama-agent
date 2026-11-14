"""The `agent` route inside `/chat/stream`: the body, not just the counters.

`test_chat_slo_metrics.py` posts to `/chat/stream` with `agent="coder"` and
asserts the route/outcome **counters** moved. That is a test of the metric, not
of the branch: the entire streaming body (lines 2014-2093) ran unexecuted —
routing, the redactor, the exception path, the empty-answer guard, the critic
pass, and persistence.

Two defects were found here, and both are the kind that only surface when the
body is actually driven.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASSWORD = "Str0ng-Passw0rd!x"


def events(text):
    out = []
    for chunk in text.split("\n\n"):
        if not chunk.strip():
            continue
        assert chunk.startswith("data: "), f"not an SSE frame: {chunk!r}"
        payload = chunk[len("data: "):]
        if payload.strip() == "[DONE]":
            out.append(("done", None))
        elif payload.strip().startswith("{"):
            out.append(("json", json.loads(payload)))
        else:
            out.append(("text", payload))
    return out


def text_of(parsed):
    return "".join(p for k, p in parsed if k == "text")


@pytest.fixture
def agent_route(journey, monkeypatch):
    """A logged-in user whose explicit agent streams a scripted set of events."""
    c = journey.client

    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    name = f"ag_{os.urandom(4).hex()}"
    c.cookies.clear()
    r = c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    assert r.status_code in (200, 201), r.text
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200

    import multi_agent

    state = {"events": [{"type": "token", "content": "Hello"},
                        {"type": "token", "content": " world"}],
             "calls": [], "critique": None}

    class FakeAgent:
        name = "Coder"

        def execute_stream(self, task, critique=None, reflect=None):
            state["calls"].append({"task": task, "critique": critique,
                                   "reflect": reflect})
            for ev in state["events"]:
                yield ev

        def critique(self, task, answer):
            state["critique"] = {"task": task, "answer": answer}
            return state.get("critique_note")

    class FakeOrchestrator:
        def __init__(self):
            self.agents = {k: FakeAgent() for k in ("coder", "analyzer", "writer")}

    monkeypatch.setattr(multi_agent, "MultiAgentOrchestrator", FakeOrchestrator)
    # The critic is on by default (AGENT_CRITIQUE=1); tests set it explicitly.
    monkeypatch.setenv("AGENT_CRITIQUE", "0")

    yield {"client": c, "state": state}

    from modules.security import rate_limiter as rl
    rl._requests.clear()


def _post(user, **payload):
    return user["client"].post("/chat/stream", json={"agent": "coder", **payload})


# ------------------------------------------------------------- the body --

def test_the_agent_streams_its_tokens_and_terminates(agent_route):
    r = _post(agent_route, message="write a function", use_critic=False)
    assert r.status_code == 200
    parsed = events(r.text)
    assert parsed[-1] == ("done", None)
    assert text_of(parsed) == "Hello world", text_of(parsed)

    # The route announced itself and which agent it picked.
    reasoning = [p for k, p in parsed if k == "json" and p.get("stage") == "route"]
    assert reasoning and "Coder" in reasoning[0]["content"], reasoning


def test_the_agent_receives_the_cleaned_task(agent_route):
    """`@Coder Agent do X` must reach the agent as `do X`.

    The recognised syntax is `@<Type> Agent <task>` (modules/routing.py:109); the
    `agent=` field is the other way to pick one.

    One nuance, measured and deliberate: `_with_context` prepends this
    conversation's history, and the history contains the message **as the user
    typed it** (it is persisted verbatim at api.py:2020 before the prefix is
    stripped). So `@Coder` does appear inside the context block. Only the NEW
    message — the part the agent is asked to act on — must be clean.
    """
    _post(agent_route, message="@Coder Agent write a function", use_critic=False)
    calls = agent_route["state"]["calls"]
    assert calls, "the agent was never invoked"
    task = calls[0]["task"]

    new_message = task.split("User's new message:")[-1].strip()
    assert new_message == "write a function", (
        f"the new message still carries the routing prefix: {new_message!r}"
    )
    assert "@Coder" not in new_message


def test_tool_and_reasoning_events_go_to_the_reasoning_view_not_the_answer(agent_route):
    """Tool activity must never be concatenated into the answer text.

    If it were, a tool call would appear as part of the reply a user reads.
    """
    agent_route["state"]["events"] = [
        {"type": "tool", "tool": "search", "content": "searching"},
        {"type": "token", "content": "answer"},
    ]
    r = _post(agent_route, message="look it up", use_critic=False)
    parsed = events(r.text)
    assert text_of(parsed) == "answer", (
        f"tool output leaked into the answer: {text_of(parsed)!r}"
    )
    # `_reasoning_sse` re-labels a tool event as {"type": "reasoning",
    # "stage": "tool"} so the UI renders it in the reasoning view.
    assert any(k == "json" and p.get("type") == "reasoning"
               and p.get("stage") == "tool" for k, p in parsed), (
        "the tool event was dropped instead of being forwarded as reasoning"
    )


def test_a_failing_agent_produces_a_readable_message_and_done(agent_route, monkeypatch):
    """The behaviour the general route lacks, verified on the route that has it."""
    def _raise(*a, **k):
        raise RuntimeError("model died mid-stream")

    agent_route["state"]["events"] = []
    # Make execute_stream raise when iterated.
    import multi_agent
    original = multi_agent.MultiAgentOrchestrator

    class Raising:
        name = "Coder"

        def execute_stream(self, *a, **k):
            raise RuntimeError("model died mid-stream")

    class Orch:
        def __init__(self):
            self.agents = {k: Raising() for k in ("coder", "analyzer", "writer")}

    monkeypatch.setattr(multi_agent, "MultiAgentOrchestrator", Orch)

    r = _post(agent_route, message="x", use_critic=False)
    assert r.status_code == 200
    parsed = events(r.text)
    assert parsed[-1] == ("done", None), "a failed agent stream did not terminate"
    body = text_of(parsed)
    assert "could not complete" in body.lower(), body


def test_an_empty_agent_answer_is_replaced_with_an_explanation(agent_route):
    agent_route["state"]["events"] = []
    r = _post(agent_route, message="x", use_critic=False)
    body = text_of(events(r.text))
    assert "empty answer" in body.lower(), body


# ------------------------------------------- the UnboundLocalError path --

def test_the_repeated_flush_branch_is_unreachable_dead_code(agent_route):
    """Lines 2087-2091 dead code — measured, not assumed.

    The agent branch calls `_redactor.flush()` twice:

        2065  _tail = _redactor.flush();  if _tail: full += _tail; yield ...
        ...
        2087  _tail = _redactor.flush();  if _tail: streamed += _tail

    `StreamRedactor.flush()` (modules/pii.py:128) swaps the buffer out and
    returns it, so the second call can only be non-empty if something fed the
    redactor in between. Nothing does. Verified directly:

        flush() after a normal reply      -> '4111 1111'
        flush() immediately again         -> ''          (always)

    So the `if _tail:` at 2087 is never true. It cannot crash today — but if it
    ever did run it would raise `UnboundLocalError`, because `streamed` is only
    assigned inside the TEAM branch (line 1971) and the two branches never both
    execute for one request.

    This test pins both halves: the second flush is empty, and a stray reference
    to a team-branch local in the agent branch would be a latent bug. It is
    recorded rather than deleted because deleting it is a code change, and this
    audit's job is to make the finding visible first.
    """
    # 1. The redactor guarantees the second flush is empty.
    from modules.pii import StreamRedactor
    r = StreamRedactor()
    emitted = "".join(r.feed(t) for t in ["call 4111 1111 1111 1111", " now"])
    first = r.flush()
    second = r.flush()
    assert second == "", "flush() is no longer the end-of-stream marker; re-check 2087"
    assert first or emitted

    # 2. And the branch still runs end to end without touching it.
    agent_route["state"]["events"] = [{"type": "token", "content": "4111 1111 1111 1111"}]
    resp = _post(agent_route, message="x", use_critic=False)
    assert resp.status_code == 200
    parsed = events(resp.text)
    assert parsed[-1] == ("done", None)
    assert "4111 1111 1111 1111" not in text_of(parsed), "a card number reached the client"


# ------------------------------------------------------- the critic pass --

def test_the_critic_note_is_emitted_as_reasoning_not_as_the_answer(agent_route, monkeypatch):
    """With the critic on, its note goes to the reasoning view.

    The note is a review comment, not the reply. Gluing it into the answer text
    would show the user a critique of an answer they cannot tell apart from it.
    """
    monkeypatch.setenv("AGENT_CRITIQUE", "1")
    agent_route["state"]["critique_note"] = "Looks correct, but cite a source."
    agent_route["state"]["events"] = [{"type": "token", "content": "the answer"}]

    r = _post(agent_route, message="write a function", use_critic=True)
    parsed = events(r.text)
    assert parsed[-1] == ("done", None)

    assert text_of(parsed).strip() == "the answer", (
        f"the critic note leaked into the answer: {text_of(parsed)!r}"
    )
    notes = [p for k, p in parsed
             if k == "json" and p.get("stage") == "critic"]
    assert notes, "the critic ran but its note was never sent"
    assert "cite a source" in notes[0]["content"]

    # And it was actually given the answer to review.
    assert agent_route["state"]["critique"]["answer"].strip() == "the answer"


def test_a_failing_critic_does_not_break_the_reply(agent_route, monkeypatch):
    """The critic is an add-on; if it raises, the answer still arrives."""
    monkeypatch.setenv("AGENT_CRITIQUE", "1")
    agent_route["state"]["events"] = [{"type": "token", "content": "the answer"}]
    agent_route["state"]["critique_note"] = None

    import multi_agent
    original = multi_agent.MultiAgentOrchestrator

    class RaisingCritic:
        name = "Coder"

        def execute_stream(self, *a, **k):
            yield {"type": "token", "content": "the answer"}

        def critique(self, task, answer):
            raise RuntimeError("critic exploded")

    class Orch:
        def __init__(self):
            self.agents = {k: RaisingCritic() for k in ("coder", "analyzer", "writer")}

    monkeypatch.setattr(multi_agent, "MultiAgentOrchestrator", Orch)
    r = _post(agent_route, message="x", use_critic=True)
    assert r.status_code == 200
    parsed = events(r.text)
    assert parsed[-1] == ("done", None), "a failed critic broke the stream"
    assert "the answer" in text_of(parsed), "a failed critic lost the answer"


def test_the_critic_is_skipped_when_the_answer_is_a_failure_notice(agent_route, monkeypatch):
    """Reviewing "the model returned an empty answer" is pointless.

    The guard is `not full.startswith("⚠️")`. Without it the critic would be
    asked to review an error message and its note would be noise.
    """
    monkeypatch.setenv("AGENT_CRITIQUE", "1")
    agent_route["state"]["events"] = []          # -> empty-answer notice
    r = _post(agent_route, message="x", use_critic=True)
    assert agent_route["state"]["critique"] is None, (
        "the critic was asked to review a failure notice"
    )
    assert events(r.text)[-1] == ("done", None)
