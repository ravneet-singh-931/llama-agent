"""
The critic and the reviser see the evidence (v3.4.2).

Regression: an eval run showed the Research agent's sourced answer about
this week's AI news being "corrected" into "I cannot provide news, I don't
have real-time access". The critic and reviser judged recent facts against
their training data because they never saw the tool results or the date.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

SOURCE = "Example Labs released Model-Z on 21 October 2026 (example.com/news)."


def _fake_chat(captured, reply):
    def _chat(messages, **kw):
        captured.append(messages)
        msg = SimpleNamespace(content=reply)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])
    return _chat


def test_critic_gets_date_and_sources(monkeypatch):
    from multi_agent import ResearchAgent
    from modules.prompt_rules import today_str
    agent = ResearchAgent()
    captured = []
    monkeypatch.setattr(agent, "_chat", _fake_chat(captured, "OK"))

    assert agent.critique("AI news this week?", "Model-Z launched.", sources=[SOURCE]) == ""
    system, user = captured[0][0]["content"], captured[0][1]["content"]
    assert today_str() in system
    assert "do not recognise" in system
    assert SOURCE in user

    captured.clear()
    agent.critique("q", "a")  # no sources -> no empty evidence block
    assert "Sources the agent retrieved" not in captured[0][1]["content"]


def test_reviser_gets_sources_and_must_not_refuse(monkeypatch):
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    captured = []
    monkeypatch.setattr(agent, "_chat", _fake_chat(captured, "revised"))

    assert agent._revise("q", "draft", "- add dates", sources=[SOURCE]) == "revised"
    last = captured[0][-1]["content"]
    assert SOURCE in last
    assert "never replace the answer with a refusal" in last


def test_evidence_is_clipped(monkeypatch):
    from multi_agent import _evidence_block
    monkeypatch.setenv("AGENT_REVIEW_SOURCE_CHARS", "50")
    block = _evidence_block(["x" * 500])
    assert block.count("x") == 50 and "[...]" in block
    assert _evidence_block([]) == "" and _evidence_block(["", "  "]) == ""


def test_stream_review_receives_tool_results(monkeypatch):
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    msgs_with_tool = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "tool", "content": SOURCE},
    ]
    monkeypatch.setattr("modules.agent_tools.run_tool_rounds",
                        lambda c, m, msgs, tools, **kw: (msgs_with_tool, [], "draft answer"))
    seen = {}

    def crit(task, ans, sources=None):
        seen["critic"] = sources
        return "- missing dates" if ans == "draft answer" else ""

    def rev(task, ans, c, sources=None):
        seen["revise"] = sources
        return "revised answer"

    monkeypatch.setattr(agent, "critique", crit)
    monkeypatch.setattr(agent, "_revise", rev)
    out = list(agent._stream_with_tools("AI news?", critique=True, reflect=True))
    assert "".join(o["content"] for o in out if o["type"] == "token").strip() == "revised answer"
    assert seen["critic"] == [SOURCE] and seen["revise"] == [SOURCE]


def test_non_stream_reflect_passes_sources(monkeypatch):
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    seen = []
    monkeypatch.setattr(agent, "critique",
                        lambda t, a, sources=None: seen.append(sources) or ("- fix" if a == "draft" else ""))
    monkeypatch.setattr(agent, "_revise", lambda t, a, c, sources=None: "better")
    assert agent._maybe_critique_reflect("q", "draft", True, True, sources=[SOURCE]) == "better"
    assert seen and all(s == [SOURCE] for s in seen)
