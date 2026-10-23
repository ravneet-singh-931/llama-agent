"""
Tests for CHANGELOG 3.4.0 - grounding guardrail, context isolation, Tavily
news/time filters, default-DAG fix.

All offline. Run with: pytest tests/test_grounding.py -v
"""

import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("GROQ_API_KEY", "test-key")

from modules import grounding  # noqa: E402

SOURCES = ["The task management software market was valued at USD 1.27 billion in "
           "2025 and is expected to reach USD 2.66 billion by 2031, a CAGR of 13.1%. "
           "Todoist Pro costs $5 per month billed yearly. " * 3]


# ============ CLAIM EXTRACTION ============

def test_extracts_significant_figures_only():
    text = ("1. Market: USD 1.27 billion (2025), CAGR 13.1%.\n"
            "2. Pro costs $5/month; 3 plans; launched at 4:23 pm on 360iresearch.com; "
            "qwen3 model; version 3.14; 30–55 % of users; 120 signups.")
    claims = grounding.extract_claims(text)
    assert "1.27 billion" in claims and "13.1%" in claims and "$5" in claims
    assert "3.14" in claims and "30–55 %" in claims and "120" in claims
    for noise in ("1", "2", "3", "2025", "4", "23", "360", "qwen3"):
        assert noise not in claims


def test_code_blocks_are_ignored():
    text = "Price $99.\n```css\nbody{width:1200px;padding:2rem}\n```"
    assert grounding.extract_claims(text) == ["$99"]


def test_check_reports_unsupported_and_rate():
    text = "Market USD 1.27 billion, CAGR 13.1%, and 55% of adults prefer web apps."
    r = grounding.check(text, SOURCES)
    assert r.checked and r.total == 3 and r.supported == 2
    assert r.unsupported == ["55%"]
    assert "2/3 figures" in r.summary("answer") and "55%" in r.summary()


def test_no_sources_means_unchecked():
    r = grounding.check("55% of adults", ["tiny"])
    assert not r.checked and "no sources" in r.summary()
    assert grounding.run_sources("task", []) == []          # nothing looked up


def test_number_formats_normalise():
    r = grounding.check("Revenue was $1,200 and growth 7.50%.",
                        ["revenue 1200 dollars, growth of 7.5 percent " * 10])
    assert r.unsupported == []


def test_annotate_marks_prose_not_code_and_is_idempotent():
    text = "55% prefer web.\n```python\nx = 55\n```\nAgain 55% here."
    once = grounding.annotate(text, ["55%"])
    assert once.count("55% (unverified)") == 2 and "x = 55\n" in once
    assert grounding.annotate(once, ["55%"]) == once


# ============ CONTEXT ISOLATION ============

def _orch():
    from multi_agent import MultiAgentOrchestrator
    return MultiAgentOrchestrator()


def test_intermediate_step_does_not_see_the_user_request():
    orch = _orch()
    step = {"id": "s2", "agent": "coder", "depends_on": [],
            "instruction": "Write a landing page for a todo app"}
    p = orch._build_step_prompt(step, {}, "research the market AND write code", is_final=False)
    assert p.startswith("YOUR TASK: Write a landing page for a todo app")
    assert "research the market" not in p


def test_final_step_sees_request_and_source_material():
    orch = _orch()
    step = {"id": "s3", "agent": "writer", "depends_on": ["s1"], "instruction": "Summarize"}
    p = orch._build_step_prompt(step, {"s1": "findings"}, "the request", is_final=True)
    assert "The user's original request (context): the request" in p
    assert "Source material 1:" in p and "findings" in p
    assert "agent" not in p.lower().replace("agent team", "")  # no team framing


def test_final_step_ids():
    from multi_agent import MultiAgentOrchestrator
    steps = [{"id": "a", "depends_on": []}, {"id": "b", "depends_on": []},
             {"id": "c", "depends_on": ["a", "b"]}]
    assert MultiAgentOrchestrator._final_step_ids(steps) == {"c"}


def test_default_dag_summary_waits_for_every_step():
    steps = _orch()._default_dag("write code and write an article")
    summary = steps[-1]
    assert summary["agent"] == "analyzer"
    assert set(summary["depends_on"]) == {s["id"] for s in steps[:-1]}


def test_planner_requires_self_contained_instructions(monkeypatch):
    orch = _orch()
    seen = {}

    class _R:
        class choices:  # noqa: N801
            pass

    def fake_chat(messages, **kw):
        seen["prompt"] = messages[-1]["content"]
        raise RuntimeError("stop")

    monkeypatch.setattr(orch.agents["planner"], "_chat", fake_chat)
    orch._plan_dag("anything")
    assert "SELF-CONTAINED" in seen["prompt"]
    assert "todo" not in seen["prompt"].lower()      # no example-specific wording


# ============ HAND-OFF FACT CHECK (team stream) ============

def test_team_marks_unsupported_figures_before_the_next_agent(monkeypatch):
    orch = _orch()
    steps = [
        {"id": "s1", "agent": "research", "depends_on": [], "instruction": "research"},
        {"id": "s2", "agent": "coder", "depends_on": [], "instruction": "code"},
        {"id": "s3", "agent": "writer", "depends_on": ["s1", "s2"], "instruction": "summarize"},
    ]
    monkeypatch.setattr(orch, "_plan_dag", lambda task: steps)
    prompts = {}

    def research_stream(task, critique=False, reflect=False, sources=None):
        sources.extend(SOURCES)
        yield {"type": "token", "content": "Market USD 1.27 billion, CAGR 13.1%."}

    def coder_stream(task, critique=False, reflect=False, sources=None):
        yield {"type": "token", "content": "55% of adults prefer web apps.\n```html\n<p>hi</p>\n```"}

    def writer_stream(task, critique=False, reflect=False, sources=None):
        prompts["writer"] = task
        yield {"type": "token", "content": "Summary: USD 1.27 billion market."}

    monkeypatch.setattr(orch.agents["research"], "execute_stream", research_stream)
    monkeypatch.setattr(orch.agents["coder"], "execute_stream", coder_stream)
    monkeypatch.setattr(orch.agents["writer"], "execute_stream", writer_stream)

    out = list(orch.collaborate_stream("task", critique=False, reflect=False, parallel=False))
    assert "55% (unverified)" in prompts["writer"]
    assert "1.27 billion (unverified)" not in prompts["writer"]
    assert "leave them out or keep the marker" in prompts["writer"]
    notes = [o["content"] for o in out if o.get("stage") == "grounding"]
    assert any("Coder Agent → Writer Agent" in n and "55%" in n for n in notes)
    assert any(n.startswith("Fact check (final answer") for n in notes)


def test_standalone_agent_reports_fact_check(monkeypatch):
    from multi_agent import ResearchAgent
    agent = ResearchAgent()

    def fake_rounds(client, model, messages, tools, **kw):
        msgs = messages + [{"role": "tool", "tool_call_id": "c1", "content": SOURCES[0]}]
        return msgs, ["⚙️ web_search"], "Market is USD 1.27 billion; 42% churn."

    monkeypatch.setattr("modules.agent_tools.run_tool_rounds", fake_rounds)
    monkeypatch.delenv("AGENT_FACTCHECK_MARK", raising=False)
    out = list(agent._stream_with_tools("t"))
    notes = [o["content"] for o in out if o.get("stage") == "grounding"]
    assert notes and "1/2 figures" in notes[0] and "42%" in notes[0]
    text = "".join(o["content"] for o in out if o["type"] == "token")
    assert "(unverified)" not in text                  # report only by default

    monkeypatch.setenv("AGENT_FACTCHECK_MARK", "1")
    out = list(agent._stream_with_tools("t"))
    text = "".join(o["content"] for o in out if o["type"] == "token")
    assert "42% (unverified)" in text


def test_orchestrated_agent_fills_sources_and_skips_own_check(monkeypatch):
    from multi_agent import ResearchAgent
    agent = ResearchAgent()

    def fake_rounds(client, model, messages, tools, **kw):
        msgs = messages + [{"role": "tool", "tool_call_id": "c1", "content": "RESULT"}]
        return msgs, [], "answer 42%"

    monkeypatch.setattr("modules.agent_tools.run_tool_rounds", fake_rounds)
    sources = []
    out = list(agent._stream_with_tools("t", sources=sources))
    assert sources == ["RESULT"]
    assert not [o for o in out if o.get("stage") == "grounding"]


# ============ TAVILY TOPIC / TIME RANGE ============

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_tavily_passes_topic_time_range_and_shows_dates(monkeypatch):
    from modules import web_tools
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["body"] = json.loads(req.data.decode())
        return _Resp(json.dumps({"results": [
            {"title": "AI news", "url": "https://x.com/a", "content": "c",
             "published_date": "2026-10-21T08:00:00"}]}).encode())

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(web_tools.urllib.request, "urlopen", fake_urlopen)
    out = web_tools.web_search("ai news", topic="news", time_range="week")
    assert seen["body"]["topic"] == "news" and seen["body"]["time_range"] == "week"
    assert seen["body"]["include_published_date"] is True
    assert "(published 2026-10-21T08:00)" in out

    web_tools.web_search("python", topic="bogus", time_range="decade")
    assert "topic" not in seen["body"] and "time_range" not in seen["body"]


def test_tool_schema_and_dispatch_forward_filters(monkeypatch):
    from modules import agent_tools, web_tools
    props = web_tools.WEB_SEARCH_TOOL["function"]["parameters"]["properties"]
    assert props["topic"]["enum"] == ["general", "news", "finance"]
    assert props["time_range"]["enum"] == ["day", "week", "month", "year"]
    seen = {}
    monkeypatch.setattr(agent_tools, "web_search",
                        lambda q, topic="", time_range="": seen.update(q=q, t=topic, r=time_range) or "ok")
    agent_tools.dispatch_tool("web_search", {"query": "q", "topic": "news", "time_range": "day"})
    assert seen == {"q": "q", "t": "news", "r": "day"}
