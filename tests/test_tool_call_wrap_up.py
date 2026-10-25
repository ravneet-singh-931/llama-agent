"""v3.5.2: the wrap-up after the tool-round cap must survive a rejected tool call.

Found by the agent eval (team-todo-landing): the research agent used all its
tool rounds, was asked for the final answer with no tools offered, tried
another tool call anyway, and Groq failed the stream midway with
"Tool choice is none, but model called a tool". The exception killed the step.
These tests reproduce the provider error itself - no prompt-specific logic.
"""
import os
import types

import pytest

TOOL_ERR = "Tool choice is none, but model called a tool"


def _chunk(text):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=text))])


def _response(text):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])


class _Client:
    """create(stream=True) plays `stream_tokens` then raises `stream_error`;
    create() without stream returns `rescue_text` (and records the messages)."""

    def __init__(self, stream_tokens=(), stream_error=None, rescue_text="rescued answer"):
        self.stream_tokens, self.stream_error, self.rescue_text = stream_tokens, stream_error, rescue_text
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls.append(kw)
        if kw.get("stream"):
            def gen():
                for t in self.stream_tokens:
                    yield _chunk(t)
                if self.stream_error:
                    raise self.stream_error
            return gen()
        return _response(self.rescue_text)


def _tool_history(messages):
    """What the tool loop returns when it hits the round cap: the original
    messages + an assistant tool call + its result, and no final answer."""
    return messages + [
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "RESULT: cafe rents average Rs 80,000"},
    ]


def _tokens(out):
    return "".join(o["content"] for o in out if o["type"] == "token").strip()


@pytest.fixture
def research(monkeypatch):
    os.environ.setdefault("GROQ_API_KEY", "test-key")
    from multi_agent import ResearchAgent
    monkeypatch.setattr("modules.agent_tools.run_tool_rounds",
                        lambda c, m, msgs, tools, **kw: (_tool_history(msgs), ["⚙️ web_search"], None))
    return ResearchAgent()


def test_stream_rescues_when_wrap_up_stream_rejects_a_tool_call(research):
    client = _Client(stream_error=RuntimeError(TOOL_ERR))
    research.client = client
    out = list(research._stream_with_tools("open a cafe"))
    assert _tokens(out) == "rescued answer"
    rescue = [c for c in client.calls if not c.get("stream")]
    assert len(rescue) == 1  # straight to the plain rescue, no repeat of the failing request
    msgs = rescue[0]["messages"]
    assert all(m["role"] in ("system", "user") for m in msgs)   # no tool-call turns
    assert not any(m.get("tool_calls") for m in msgs)
    assert "cafe rents average Rs 80,000" in msgs[-1]["content"]  # results kept as text


def test_stream_keeps_partial_answer_when_stream_fails_midway(research):
    client = _Client(stream_tokens=["Partial ", "answer."], stream_error=RuntimeError(TOOL_ERR))
    research.client = client
    out = list(research._stream_with_tools("open a cafe"))
    assert _tokens(out) == "Partial answer."
    assert not [c for c in client.calls if not c.get("stream")]  # no rescue call


def test_stream_empty_answer_falls_back_to_plain_messages(research):
    """Existing rescue (empty stream) first retries the normal messages; if that
    gives nothing it now also tries the plain version."""
    client = _Client(stream_tokens=[], rescue_text="")
    research.client = client
    out = list(research._stream_with_tools("open a cafe"))
    rescue = [c for c in client.calls if not c.get("stream")]
    assert len(rescue) == 2
    assert any(m["role"] == "tool" for m in rescue[0]["messages"])
    assert not any(m["role"] == "tool" for m in rescue[1]["messages"])
    assert "empty answer" in _tokens(out).lower()  # both empty -> the usual notice


# ---------- non-streaming wrap-up (run_tool_loop) ----------

class _NonStreamClient:
    def __init__(self, first_error):
        self.first_error, self.calls = first_error, []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls.append(kw)
        if len(self.calls) == 1 and self.first_error:
            raise self.first_error
        return _response("wrapped answer")


def test_run_tool_loop_retries_wrap_up_without_tool_history(monkeypatch):
    import modules.agent_tools as at
    monkeypatch.setattr(at, "run_tool_rounds",
                        lambda c, m, msgs, tools, **kw: (_tool_history(msgs), [], None))
    client = _NonStreamClient(RuntimeError(TOOL_ERR))
    msgs, _ = at.run_tool_loop(client, "m", [{"role": "system", "content": "S"},
                                             {"role": "user", "content": "task"}])
    assert msgs[-1] == {"role": "assistant", "content": "wrapped answer"}
    retry = client.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["system", "user"]
    assert retry[1]["content"].startswith("task")


def test_run_tool_loop_other_errors_still_propagate(monkeypatch):
    """Rate limits etc. must reach the caller's strong->fast fallback."""
    import modules.agent_tools as at
    monkeypatch.setattr(at, "run_tool_rounds",
                        lambda c, m, msgs, tools, **kw: (_tool_history(msgs), [], None))
    with pytest.raises(RuntimeError, match="429"):
        at.run_tool_loop(_NonStreamClient(RuntimeError("429 rate_limit_exceeded")), "m",
                         [{"role": "user", "content": "task"}])


def test_is_tool_call_error():
    from modules.agent_tools import is_tool_call_error
    assert is_tool_call_error(RuntimeError(TOOL_ERR))
    e = RuntimeError("bad request")
    e.body = {"error": {"code": "tool_use_failed"}}
    assert is_tool_call_error(e)
    assert not is_tool_call_error(RuntimeError("429 rate_limit_exceeded"))


# ---------- eval: provider-failure notices are not graded ----------

def test_eval_does_not_grade_the_apps_rate_limit_notice():
    from evals.run import run_case
    notice = ("⚠️ I've hit today's free-tier AI usage limit, so I can't answer right now. "
              "Please try again in about 3 minutes.")
    judged = []
    r = run_case({"id": "x", "category": "general", "prompt": "p", "checks": {}},
                 executor=lambda prompt, **kw: {"route": "general", "answer": notice, "reasoning": []},
                 judge_fn=lambda *a: judged.append(a) or {"overall": 1.0})
    assert judged == []           # the grader never sees the notice
    assert r["graded"] is False and r["passed"] is False
    assert "not judged" in r["judge"]["error"]


def test_eval_still_grades_normal_answers():
    from evals.run import run_case
    r = run_case({"id": "x", "category": "general", "prompt": "p", "checks": {}},
                 executor=lambda prompt, **kw: {"route": "general", "answer": "22.2 C", "reasoning": []},
                 judge_fn=lambda *a: {"overall": 5.0})
    assert r["graded"] is True and r["passed"] is True
