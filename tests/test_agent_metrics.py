"""Per-agent metrics: LLM spend/tokens by component, and agent run time/outcome.

- llama_agent_llm_tokens_total / llama_agent_llm_cost_usd_total carry a
  `component` label (research, coder, ..., chat, doc_answer, rag, router, judge).
- llama_agent_agent_run_seconds{agent,outcome} times each specialist run.
"""
import sys
import types

import pytest
from prometheus_client import REGISTRY

FAST = "openai/gpt-oss-20b"


def _val(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


# ---------- record_llm_usage: component label ----------

def test_record_llm_usage_labels_component():
    from modules.prometheus_metrics import record_llm_usage
    before = _val("llama_agent_llm_tokens_total", model=FAST, kind="input", component="coder")
    cost_before = _val("llama_agent_llm_cost_usd_total", model=FAST, component="coder")
    record_llm_usage(FAST, 1000, 100, component="coder")
    assert _val("llama_agent_llm_tokens_total", model=FAST, kind="input",
                component="coder") == before + 1000
    assert _val("llama_agent_llm_cost_usd_total", model=FAST, component="coder") > cost_before


def test_unknown_component_is_bucketed_as_other():
    """Label values are a fixed set - a typo or new caller can't create
    unbounded series."""
    from modules.prometheus_metrics import record_llm_usage
    before = _val("llama_agent_llm_tokens_total", model=FAST, kind="output", component="other")
    record_llm_usage(FAST, 1, 7, component="something-new")
    assert _val("llama_agent_llm_tokens_total", model=FAST, kind="output",
                component="other") == before + 7
    assert REGISTRY.get_sample_value(
        "llama_agent_llm_tokens_total",
        {"model": FAST, "kind": "output", "component": "something-new"}) is None


def test_record_llm_usage_default_component_is_other():
    from modules.prometheus_metrics import record_llm_usage
    before = _val("llama_agent_llm_tokens_total", model=FAST, kind="input", component="other")
    record_llm_usage(FAST, 3, 0)
    assert _val("llama_agent_llm_tokens_total", model=FAST, kind="input",
                component="other") == before + 3


# ---------- record_agent_run ----------

def test_record_agent_run_observes_and_never_raises():
    from modules.prometheus_metrics import record_agent_run
    before = _val("llama_agent_agent_run_seconds_count", agent="writer", outcome="ok")
    record_agent_run("writer", 4.2, "ok")
    assert _val("llama_agent_agent_run_seconds_count", agent="writer", outcome="ok") == before + 1
    # bad input is absorbed, unknown outcome -> "error"
    before_err = _val("llama_agent_agent_run_seconds_count", agent="writer", outcome="error")
    record_agent_run("writer", "not-a-number", "ok")  # swallowed
    record_agent_run("writer", 1.0, "weird")
    assert _val("llama_agent_agent_run_seconds_count", agent="writer",
                outcome="error") == before_err + 1


# ---------- GroqLLM passes its component ----------

def test_groqllm_records_usage_with_its_component(monkeypatch):
    import llama_agent1
    import modules.prometheus_metrics as pm
    seen = []
    monkeypatch.setattr(pm, "record_llm_usage",
                        lambda m, i, o, component="other": seen.append((m, i, o, component)))
    llm = llama_agent1.GroqLLM(model=FAST, api_key="x", component="coder")
    llm._record_usage({"prompt_tokens": 10, "completion_tokens": 5})
    assert seen == [(FAST, 10, 5, "coder")]
    assert llama_agent1.GroqLLM(model=FAST, api_key="x").component == "other"


def test_chat_llms_are_labelled_chat():
    import llama_agent1
    assert llama_agent1.llm.component == "chat"
    assert llama_agent1.stream_llm.component == "chat"


# ---------- InstrumentedGroqClient sets the component ----------

def test_instrumented_client_sets_component_on_llm(monkeypatch):
    from multi_agent import InstrumentedGroqClient

    class FakeGroqLLM:  # same constructor as the real one's required fields
        def __init__(self, model, api_key):
            self.model = model

        def chat(self, messages, **kw):
            return types.SimpleNamespace(raw="R")

    fake_mod = types.ModuleType("llama_agent1")
    fake_mod.GroqLLM = FakeGroqLLM
    monkeypatch.setitem(sys.modules, "llama_agent1", fake_mod)
    client = InstrumentedGroqClient(api_key="k", component="router")
    client.chat.completions.create(model=FAST, messages=[{"role": "user", "content": "hi"}])
    assert client._llms[FAST].component == "router"


def test_agents_get_their_component_name():
    from multi_agent import (AnalyzerAgent, CoderAgent, PlannerAgent, ResearchAgent,
                             WriterAgent)
    names = {cls().component for cls in
             (ResearchAgent, CoderAgent, WriterAgent, AnalyzerAgent, PlannerAgent)}
    assert names == {"research", "coder", "writer", "analyzer", "planner"}
    agent = CoderAgent()
    assert agent.client._component == "coder"


# ---------- run outcome / timing ----------

@pytest.fixture
def runs(monkeypatch):
    import modules.prometheus_metrics as pm
    seen = []
    monkeypatch.setattr(pm, "record_agent_run",
                        lambda agent, seconds, outcome: seen.append((agent, outcome)))
    return seen


def test_run_outcome_rules():
    from multi_agent import _run_outcome
    assert _run_outcome("Here is the answer.") == "ok"
    assert _run_outcome("") == "failed"
    assert _run_outcome("   ") == "failed"
    assert _run_outcome("I couldn't complete that task right now. Please try again.") == "failed"
    assert _run_outcome("⚠️ The model returned an empty answer. Please try again") == "failed"
    assert _run_outcome("⚠️ I've hit today's free-tier AI usage limit, "
                        "so I can't answer right now.") == "failed"


def test_execute_with_tools_records_ok_failed_error(monkeypatch, runs):
    from multi_agent import CoderAgent
    agent = CoderAgent()
    monkeypatch.setattr(agent, "_execute_with_tools_impl", lambda task, **kw: "def f(): pass")
    assert agent._execute_with_tools("t") == "def f(): pass"
    monkeypatch.setattr(agent, "_execute_with_tools_impl",
                        lambda task, **kw: "I couldn't complete that task right now.")
    agent._execute_with_tools("t")

    def boom(task, **kw):
        raise RuntimeError("x")
    monkeypatch.setattr(agent, "_execute_with_tools_impl", boom)
    with pytest.raises(RuntimeError):
        agent._execute_with_tools("t")
    assert runs == [("coder", "ok"), ("coder", "failed"), ("coder", "error")]


def test_execute_with_tools_records_cached(monkeypatch, runs):
    import multi_agent
    agent = multi_agent.WriterAgent()
    monkeypatch.setattr(multi_agent, "_semantic_lookup", lambda task, name: "cached answer")
    assert agent._execute_with_tools("t") == "cached answer"
    # next run on the same thread is not marked cached
    monkeypatch.setattr(agent, "_execute_with_tools_impl", lambda task, **kw: "fresh")
    agent._execute_with_tools("t")
    assert runs == [("writer", "cached"), ("writer", "ok")]


def _events(*tokens, fail=False):
    for t in tokens:
        yield {"type": "token", "content": t}
        yield {"type": "reasoning", "content": "x"}
    if fail:
        raise RuntimeError("boom")


def test_timed_events_passes_events_and_records_outcome(runs):
    from multi_agent import _timed_events
    out = list(_timed_events("research", _events("Hello ", "world")))
    assert [e["content"] for e in out if e["type"] == "token"] == ["Hello ", "world"]
    list(_timed_events("research", _events()))  # no answer tokens
    list(_timed_events("research", _events("I couldn't complete that task right now.")))
    assert runs == [("research", "ok"), ("research", "failed"), ("research", "failed")]


def test_timed_events_error_and_cancelled(runs):
    from multi_agent import _timed_events
    with pytest.raises(RuntimeError):
        list(_timed_events("analyzer", _events("a", fail=True)))
    inner = _events("a", "b", "c")
    gen = _timed_events("analyzer", inner)
    next(gen)
    gen.close()  # client disconnected mid-stream
    assert inner.gi_frame is None  # inner generator closed too
    assert runs == [("analyzer", "error"), ("analyzer", "cancelled")]


def test_stream_with_tools_is_timed(monkeypatch, runs):
    from multi_agent import PlannerAgent
    agent = PlannerAgent()
    monkeypatch.setattr(agent, "_stream_with_tools_impl",
                        lambda task, **kw: _events("Step 1", "Step 2"))
    out = list(agent._stream_with_tools("plan it"))
    assert len(out) == 4
    assert runs == [("planner", "ok")]


# ---------- v3.5.1: one scrape target per API pod + series start at 0 ----------

def test_agent_run_series_exist_at_zero_from_startup():
    """Prometheus can't see the first increment of a series born at 1, and the
    failure-rate panel needs the failed/error series to exist at 0."""
    import modules.prometheus_metrics  # noqa: F401 - creates the series
    for agent in ("research", "coder", "writer", "analyzer", "planner"):
        for outcome in ("ok", "failed", "cached", "error", "cancelled"):
            assert REGISTRY.get_sample_value(
                "llama_agent_agent_run_seconds_count",
                {"agent": agent, "outcome": outcome}) is not None, (agent, outcome)


def _k8s_docs(path):
    import yaml
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def test_prometheus_scrapes_every_api_pod_via_headless_service():
    import yaml
    docs = _k8s_docs("k8s/api.yaml")
    services = {d["metadata"]["name"]: d for d in docs if d["kind"] == "Service"}
    headless = services["llama-agent-api-headless"]
    assert headless["spec"]["clusterIP"] == "None"
    assert headless["spec"]["selector"] == services["llama-agent-api-service"]["spec"]["selector"]
    assert headless["spec"]["ports"][0]["port"] == 8000

    cm = next(d for d in _k8s_docs("k8s/prometheus.yaml")
              if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "prometheus-config")
    jobs = {j["job_name"]: j for j in yaml.safe_load(cm["data"]["prometheus.yml"])["scrape_configs"]}
    api = jobs["llama-agent-api"]
    assert "static_configs" not in api  # the load-balanced Service mixes replicas
    sd = api["dns_sd_configs"][0]
    assert sd["names"] == ["llama-agent-api-headless.llama-agent.svc.cluster.local"]
    assert sd["type"] == "A" and sd["port"] == 8000


def _app_exprs():
    """Every dashboard query / alert rule over the app's own metrics."""
    import json
    import yaml
    with open("config/grafana/dashboards/llama-agent.json") as f:
        dash = json.load(f)
    exprs = [t["expr"] for p in dash["panels"] for t in p.get("targets", [])]
    cm = next(d for d in _k8s_docs("k8s/prometheus.yaml") if d["kind"] == "ConfigMap")
    for path_rules in (yaml.safe_load(cm["data"]["alerts.yml"]),
                       yaml.safe_load(open("config/prometheus/alerts.yml"))):
        exprs += [r["expr"] for g in path_rules["groups"] for r in g["rules"]]
    return [e for e in exprs if "llama_agent_" in e]


def test_app_queries_and_alerts_aggregate_across_pods():
    """With one target per pod, a bare series query shows one line per pod
    (and a per-pod alert threshold). Every query over llama_agent_* metrics
    must add the pods up."""
    exprs = _app_exprs()
    assert len(exprs) > 20
    for e in exprs:
        assert any(a in e for a in ("sum(", "sum by", "sum without", "min(", "max(")), e
