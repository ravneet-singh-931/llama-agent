"""
Tests for CHANGELOG 3.3.6 - free-tier LLMOps guard (modules/llm_guard.py)
and the agent prompt fixes that ship with it.

- Token estimation + context compaction that preserves structure.
- ITPM sliding window (local + Redis buckets), proactive strong->fast
  routing, bounded wait when every tier is saturated.
- InstrumentedGroqClient: guard applied on every call, eager streaming
  (errors raise inside the caller's try/except), 413 shrink-and-retry.
- Guard notes surface as "llmops" reasoning events in the agent stream.
- Tool-result / search-snippet caps, bigger hand-offs, no team meta-talk,
  grounded research prompt.

All offline. Run with: pytest tests/test_llm_guard.py -v
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("GROQ_API_KEY", "test-key")

from modules import llm_guard  # noqa: E402
from modules.model_tiers import resolve_model, should_fallback  # noqa: E402

STRONG = resolve_model("strong")
FAST = resolve_model("fast")


@pytest.fixture(autouse=True)
def _fresh_guard(monkeypatch):
    for var in ("LLM_INPUT_BUDGET", "LLM_INPUT_BUDGET_STRONG", "LLM_INPUT_BUDGET_FAST",
                "LLM_ITPM_LIMIT_STRONG", "LLM_ITPM_LIMIT_FAST",
                "LLM_PROACTIVE_DOWNGRADE", "LLM_GUARD_MAX_WAIT_S"):
        monkeypatch.delenv(var, raising=False)
    llm_guard.set_window(llm_guard.TokenWindow())
    llm_guard.drain_notes()
    yield
    llm_guard.set_window(None)
    llm_guard.drain_notes()


def _big(n):
    return ("lorem ipsum dolor sit amet " * (n // 27 + 1))[:n]


# ============ ESTIMATION + COMPACTION ============

def test_estimate_tokens_counts_messages_and_tools():
    msgs = [{"role": "user", "content": "x" * 330}]
    assert llm_guard.estimate_tokens(msgs) == 100 + 6
    tools = [{"type": "function", "function": {"name": "web_search"}}]
    assert llm_guard.estimate_tokens(msgs, tools) > 106


def test_fit_messages_noop_when_under_budget():
    msgs = [{"role": "user", "content": "hi"}]
    out, trimmed = llm_guard.fit_messages(msgs, 6000)
    assert out is msgs and trimmed == 0


def test_fit_messages_trims_tool_results_first_and_keeps_structure():
    msgs = [
        {"role": "system", "content": "SYSTEM " * 200},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "web_search", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": _big(20000)},
        {"role": "tool", "tool_call_id": "c2", "content": _big(20000)},
    ]
    original = [dict(m) for m in msgs]
    out, trimmed = llm_guard.fit_messages(msgs, 3000)
    assert msgs == original                      # caller's list untouched
    assert trimmed > 0
    assert llm_guard.estimate_tokens(out) <= 3000
    assert out[0]["content"] == msgs[0]["content"]   # system prompt intact
    assert out[1]["content"] == "question"           # latest user turn intact
    assert [m["role"] for m in out] == [m["role"] for m in msgs]
    assert out[3]["tool_call_id"] == "c1"
    assert "trimmed to fit" in out[3]["content"]


def test_fit_messages_compacts_large_handoff_in_last_message():
    msgs = [{"role": "system", "content": "sys"},
            {"role": "user", "content": "STEP\n" + _big(40000) + "\nEND-OF-TASK"}]
    out, trimmed = llm_guard.fit_messages(msgs, 4000)
    assert trimmed > 0 and llm_guard.estimate_tokens(out) <= 4000
    assert out[1]["content"].startswith("STEP")         # head kept
    assert out[1]["content"].endswith("END-OF-TASK")    # tail kept


def test_input_budget_env_overrides(monkeypatch):
    assert llm_guard.input_budget(STRONG) == 6000
    monkeypatch.setenv("LLM_INPUT_BUDGET", "5000")
    monkeypatch.setenv("LLM_INPUT_BUDGET_FAST", "4000")
    assert llm_guard.input_budget(STRONG) == 5000
    if FAST != STRONG:
        assert llm_guard.input_budget(FAST) == 4000


def test_is_request_too_large_matches_groq_413():
    err = Exception("Error code: 413 - {'error': {'message': 'Request too large for model "
                    "`qwen/qwen3.8-27b` ... on input tokens per minute (ITPM): Limit 7000, "
                    "Requested 8107'}}")
    assert llm_guard.is_request_too_large(err)
    assert not llm_guard.is_request_too_large(Exception("connection reset"))
    # ...and the tier fallback treats it as "use the fast model".
    assert should_fallback(err, "strong")


# ============ ITPM WINDOW + ROUTING ============

def test_token_window_slides_after_60s():
    w = llm_guard.TokenWindow()
    w.record("m", 5000, now=1000.0)
    w.record("m", 1000, now=1030.0)
    assert w.used("m", now=1031.0) == 6000
    assert w.used("m", now=1065.0) == 1000


class _FakeRedis:
    def __init__(self):
        self.data = {}

    def mget(self, keys):
        return [self.data.get(k) for k in keys]

    def pipeline(self):
        outer = self

        class _P:
            def incrby(self, k, n):
                outer.data[k] = outer.data.get(k, 0) + n

            def expire(self, k, ttl):
                pass

            def execute(self):
                pass
        return _P()


def test_token_window_shares_usage_through_redis():
    r = _FakeRedis()
    pod_a, pod_b = llm_guard.TokenWindow(r), llm_guard.TokenWindow(r)
    pod_a.record("m", 4000, now=1000.0)
    assert pod_b.used("m", now=1005.0) == 4000      # visible to other replicas
    assert pod_b.used("m", now=1100.0) == 0
    assert any(k.startswith("llmops:itpm:m:") for k in r.data)


@pytest.mark.skipif(STRONG == FAST, reason="needs distinct tiers")
def test_prepare_call_routes_to_fast_when_strong_window_is_full():
    llm_guard.get_window().record(STRONG, 6500)
    msgs = [{"role": "user", "content": "x" * 3300}]   # ~1000 tokens
    model, out, est = llm_guard.prepare_call(STRONG, msgs)
    assert model == FAST
    assert any("switched to the fast model" in n for n in llm_guard.drain_notes())


@pytest.mark.skipif(STRONG == FAST, reason="needs distinct tiers")
def test_prepare_call_keeps_strong_when_there_is_room():
    msgs = [{"role": "user", "content": "hello"}]
    model, _, _ = llm_guard.prepare_call(STRONG, msgs)
    assert model == STRONG and llm_guard.drain_notes() == []


@pytest.mark.skipif(STRONG == FAST, reason="needs distinct tiers")
def test_prepare_call_proactive_downgrade_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROACTIVE_DOWNGRADE", "0")
    monkeypatch.setenv("LLM_GUARD_MAX_WAIT_S", "0")
    llm_guard.get_window().record(STRONG, 6900)
    model, _, _ = llm_guard.prepare_call(STRONG, [{"role": "user", "content": "x" * 3300}])
    assert model == STRONG


@pytest.mark.skipif(STRONG == FAST, reason="needs distinct tiers")
def test_prepare_call_waits_when_every_tier_is_saturated(monkeypatch):
    window = llm_guard.get_window()
    window.record(STRONG, 6900)
    window.record(FAST, 7900)
    slept = []

    def fake_sleep(s):
        slept.append(s)
        if len(slept) == 3:   # the window slides: capacity frees up
            llm_guard.set_window(llm_guard.TokenWindow())
            window._local.clear()

    monkeypatch.setattr(llm_guard, "_sleep", fake_sleep)
    model, _, _ = llm_guard.prepare_call(STRONG, [{"role": "user", "content": "x" * 3300}])
    assert model == STRONG            # fast had no room either -> stay on strong
    assert len(slept) == 3
    assert any("waited 3s" in n for n in llm_guard.drain_notes())


def test_prepare_call_compacts_and_notes():
    msgs = [{"role": "user", "content": "q"},
            {"role": "tool", "tool_call_id": "a", "content": _big(60000)},
            {"role": "user", "content": "go"}]
    model, out, est = llm_guard.prepare_call(FAST, msgs)
    assert est <= llm_guard.input_budget(model)
    assert any("Context compacted" in n for n in llm_guard.drain_notes())


# ============ INSTRUMENTED CLIENT ============

class _Chunk:
    def __init__(self, raw):
        self.raw = raw


class _Resp:
    raw = "RESPONSE"


def _install_fake_groq(monkeypatch, behaviour):
    """Replace llama_agent1.GroqLLM with a recorder; behaviour(model, msgs)
    returns a list of stream items or raises."""
    calls = []

    class FakeGroqLLM:
        def __init__(self, model, api_key):
            self.model = model

        def stream_chat(self, messages, **kw):
            calls.append(("stream", self.model, messages))
            items = behaviour(self.model, messages)   # raises EAGERLY (like Groq)
            return iter([_Chunk(i) for i in items])

        def chat(self, messages, **kw):
            calls.append(("chat", self.model, messages))
            behaviour(self.model, messages)
            return _Resp()

    fake_mod = types.ModuleType("llama_agent1")
    fake_mod.GroqLLM = FakeGroqLLM
    monkeypatch.setitem(sys.modules, "llama_agent1", fake_mod)
    return calls


def test_instrumented_stream_raises_inside_create(monkeypatch):
    """Errors must surface from create(), not later during iteration, so the
    agents' strong->fast try/except actually catches them."""
    from multi_agent import InstrumentedGroqClient

    def boom(model, msgs):
        raise RuntimeError("429 rate_limit_exceeded")

    _install_fake_groq(monkeypatch, boom)
    client = InstrumentedGroqClient(api_key="k")
    with pytest.raises(RuntimeError):
        client.chat.completions.create(
            model=FAST, messages=[{"role": "user", "content": "hi"}], stream=True)


def test_instrumented_stream_yields_raw_chunks_and_records_window(monkeypatch):
    from multi_agent import InstrumentedGroqClient
    _install_fake_groq(monkeypatch, lambda m, msgs: ["a", "b"])
    client = InstrumentedGroqClient(api_key="k")
    out = client.chat.completions.create(
        model=FAST, messages=[{"role": "user", "content": "x" * 330}], stream=True)
    assert list(out) == ["a", "b"]
    assert llm_guard.get_window().used(FAST) > 0


def test_instrumented_413_shrinks_and_retries_once(monkeypatch):
    from multi_agent import InstrumentedGroqClient
    sizes = []

    def behaviour(model, msgs):
        size = sum(len(m.content or "") for m in msgs)
        sizes.append(size)
        if len(sizes) == 1:
            raise RuntimeError("Error code: 413 - Request too large for model")
        return ["ok"]

    _install_fake_groq(monkeypatch, behaviour)
    client = InstrumentedGroqClient(api_key="k")
    msgs = [{"role": "user", "content": "q"},
            {"role": "tool", "tool_call_id": "a", "content": _big(15000)},
            {"role": "user", "content": "go"}]
    out = client.chat.completions.create(model=FAST, messages=msgs, stream=True)
    assert list(out) == ["ok"]
    assert len(sizes) == 2 and sizes[1] < sizes[0]
    assert any("Request too large" in n for n in llm_guard.drain_notes())


@pytest.mark.skipif(STRONG == FAST, reason="needs distinct tiers")
def test_instrumented_client_uses_guard_chosen_model(monkeypatch):
    from multi_agent import InstrumentedGroqClient
    calls = _install_fake_groq(monkeypatch, lambda m, msgs: ["x"])
    llm_guard.get_window().record(STRONG, 6990)
    client = InstrumentedGroqClient(api_key="k")
    client.chat.completions.create(
        model=STRONG, messages=[{"role": "user", "content": "x" * 3300}])
    assert calls[0][0] == "chat" and calls[0][1] == FAST


# ============ AGENT STREAM SURFACES GUARD NOTES ============

def test_agent_stream_emits_llmops_reasoning_events(monkeypatch):
    from multi_agent import ResearchAgent
    agent = ResearchAgent()

    def fake_rounds(client, model, messages, tools, **kw):
        llm_guard.note("Context compacted 9,000 → 5,900 tokens")
        return messages, ["⚙️ web_search"], "final answer"

    monkeypatch.setattr("modules.agent_tools.run_tool_rounds", fake_rounds)
    llm_guard.note("stale note from a previous request")
    out = list(agent._stream_with_tools("t", critique=False, reflect=False))
    notes = [o for o in out if o.get("stage") == "llmops"]
    assert [n["content"] for n in notes] == ["Context compacted 9,000 → 5,900 tokens"]
    assert out.index(notes[0]) < next(i for i, o in enumerate(out) if o["type"] == "token")


# ============ SIZE CAPS ============

def test_tool_results_are_capped(monkeypatch):
    from modules import agent_tools
    monkeypatch.setattr(agent_tools, "_TOOL_RESULT_CHARS", 100)
    monkeypatch.setattr(agent_tools, "_dispatch", lambda n, a: "y" * 5000)
    out = agent_tools.dispatch_tool("web_search", {"query": "q"})
    assert out.startswith("y" * 100) and "truncated at 100" in out
    assert len(out) < 200


def test_search_snippets_are_capped(monkeypatch):
    from modules import web_tools
    monkeypatch.setattr(web_tools, "_SNIPPET_CHARS", 50)
    out = web_tools._format_results(
        [{"title": "T", "url": "https://a.com", "content": "word " * 400}])
    assert "word word" in out and out.count("word") < 20 and " ..." in out


# ============ PROMPTS ============

def _step_prompt(monkeypatch, dep_len):
    from multi_agent import MultiAgentOrchestrator
    orch = MultiAgentOrchestrator()
    step = {"id": "s2", "agent": "writer", "instruction": "summarize",
            "depends_on": ["s1"]}
    return orch._build_step_prompt(step, {"s1": "R" * dep_len}, "overall task")


def test_step_prompt_forbids_team_meta_talk_and_keeps_big_handoffs(monkeypatch):
    monkeypatch.delenv("AGENT_HANDOFF_CHARS", raising=False)
    prompt = _step_prompt(monkeypatch, 10000)
    assert "never mention" in prompt and "other agents" in prompt
    assert "agent team" not in prompt
    assert "R" * 10000 in prompt             # old 2000-char cut is gone


def test_handoff_limit_is_configurable(monkeypatch):
    monkeypatch.setenv("AGENT_HANDOFF_CHARS", "3000")
    prompt = _step_prompt(monkeypatch, 10000)
    assert "R" * 3000 in prompt and "R" * 3001 not in prompt


def test_research_prompt_requires_grounded_cited_facts():
    from multi_agent import ResearchAgent
    p = ResearchAgent._task_prompt.format(task="todo app market")
    assert "GROUNDING" in p and "(unverified)" in p and "Never invent" in p
