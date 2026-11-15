"""The `@team` route inside `/chat/stream`: the body, not just the counters.

`test_chat_slo_metrics.py` asserts that posting `@team ...` moves the
`route="team"` counter. That is a metric test: the whole streaming body — the
per-agent headers, `agent_start`/`agent_done` frames, tool events going to the
reasoning view instead of the answer, the failure path, the empty-team guard and
persistence — ran unexecuted.

The team route is the most complex branch in `api.py`, and it is the one a user
reaches deliberately (`@team build me a plan`), so it is worth more than a counter.
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


def json_of(parsed):
    return [p for k, p in parsed if k == "json"]


@pytest.fixture
def team_route(journey, monkeypatch):
    c = journey.client

    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    name = f"tm_{os.urandom(4).hex()}"
    c.cookies.clear()
    r = c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    assert r.status_code in (200, 201), r.text
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": PASSWORD}).status_code == 200

    import multi_agent
    state = {"events": [], "task": None}

    class Team:
        def __init__(self):
            self.agents = {"coder": _Named("Coder"), "analyzer": _Named("Analyzer"),
                           "writer": _Named("Writer")}

        def collaborate_stream(self, task, critique=None, reflect=None, parallel=None):
            state["task"] = task
            for ev in state["events"]:
                yield ev

    class _Named:
        def __init__(self, name):
            self.name = name

    monkeypatch.setattr(multi_agent, "MultiAgentOrchestrator", Team)
    monkeypatch.setenv("AGENT_CRITIQUE", "0")

    yield {"client": c, "state": state}
    rate_limiter._requests.clear()


def _post(user, message, **kw):
    return user["client"].post("/chat/stream", json={"message": message, **kw})


# ------------------------------------------------------------------ body --

def test_the_team_streams_agent_frames_and_a_final_answer(team_route):
    team_route["state"]["events"] = [
        {"type": "agent_start", "agent": "Writer", "step": "write"},
        {"type": "token", "content": "Draft."},
        {"type": "agent_done", "agent": "Writer", "step": "write", "content": "Draft."},
        {"type": "final", "content": "FINAL ANSWER"},
    ]
    r = _post(team_route, "@team plan a launch")
    assert r.status_code == 200
    parsed = events(r.text)
    assert parsed[-1] == ("done", None)

    kinds = [p.get("type") for p in json_of(parsed)]
    assert "agent_start" in kinds, "the team never announced its first agent"
    assert "agent_done" in kinds

    body = text_of(parsed)
    assert "Draft." in body
    assert "### Writer" in body, f"the per-agent header is missing: {body!r}"


def test_the_team_route_persists_the_final_answer(team_route):
    """The sidebar reads the stored message, so the `final` text must be saved."""
    team_route["state"]["events"] = [
        {"type": "token", "content": "partial"},
        {"type": "final", "content": "FINAL ANSWER"},
    ]
    _post(team_route, "@team plan a launch")
    r = team_route["client"].get("/memory")
    assert r.status_code == 200
    recent = json.dumps(r.json())
    assert "FINAL ANSWER" in recent, "the team's final answer was not persisted"


def test_tool_events_from_the_team_go_to_reasoning_not_the_answer(team_route):
    team_route["state"]["events"] = [
        {"type": "tool", "content": "searching the web"},
        {"type": "token", "content": "answer"},
    ]
    parsed = events(_post(team_route, "@team look it up").text)
    assert text_of(parsed).strip() == "answer", (
        f"tool activity leaked into the answer: {text_of(parsed)!r}"
    )
    assert any(p.get("stage") == "tool" for p in json_of(parsed)), (
        "the tool event was dropped instead of forwarded"
    )


def test_a_failing_team_is_reported_and_still_terminates(team_route, monkeypatch):
    """The team route has an `except`; assert it produces a readable message."""
    import multi_agent

    class Broken:
        def __init__(self):
            self.agents = {"coder": _Named("Coder")}

        def collaborate_stream(self, *a, **k):
            raise RuntimeError("team died")

    class _Named:
        def __init__(self, name):
            self.name = name

    monkeypatch.setattr(multi_agent, "MultiAgentOrchestrator", Broken)
    r = _post(team_route, "@team plan a launch")
    assert r.status_code == 200
    parsed = events(r.text)
    assert parsed[-1] == ("done", None), "a failed team did not terminate the stream"
    assert "could not complete" in text_of(parsed).lower(), text_of(parsed)


def test_an_empty_team_answer_is_replaced_with_an_explanation(team_route):
    team_route["state"]["events"] = []
    body = text_of(events(_post(team_route, "@team plan a launch").text))
    assert "empty answer" in body.lower(), body


def test_the_team_task_has_the_team_prefix_stripped(team_route):
    """`@team X` must reach the orchestrator as `X`."""
    team_route["state"]["events"] = [{"type": "final", "content": "ok"}]
    _post(team_route, "@team build a launch plan")
    task = team_route["state"]["task"] or ""
    new_message = task.split("User's new message:")[-1].strip()
    assert new_message == "build a launch plan", (
        f"the @team prefix was not stripped from the task: {new_message!r}"
    )


def test_only_agents_that_actually_ran_are_recorded(team_route, monkeypatch):
    """`used_agents` is built from agent_start events, not from every configured agent.

    Recording an agent that never ran would inflate its usage metrics.
    """
    import api
    recorded = []
    original = api._record_agent_usage

    async def _spy(names):
        recorded.append(list(names))
        return await original(names)

    monkeypatch.setattr(api, "_record_agent_usage", _spy)
    team_route["state"]["events"] = [
        {"type": "agent_start", "agent": "Writer", "step": "write"},
        {"type": "final", "content": "done"},
    ]
    _post(team_route, "@team x")
    assert recorded, "no agent usage was recorded"
    assert "writer" in recorded[0], recorded
    assert "coder" not in recorded[0], (
        f"an agent that never ran was recorded as used: {recorded}"
    )
