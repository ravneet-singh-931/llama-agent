"""Chat answer-time metrics and SLOs (v3.5.3).

Before v3.5.3 the HTTP middleware stopped its timer when `call_next` returned,
i.e. when the response HEADERS were ready. For /chat/stream that is ~0.1 s, so
a 60 s agent answer was recorded as fast and the latency alerts could never
fire. These tests pin the fix (time to the end of the body), the per-route
chat histogram the SLOs are built on, and the SLO rules / dashboard panels.
"""
import asyncio
import json
import os

import pytest
import yaml
from prometheus_client import REGISTRY

from modules import prometheus_metrics as pm


def _val(name, labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def _chat_count(route, outcome):
    return _val("llama_agent_chat_seconds_count", {"route": route, "outcome": outcome})


# ---------------------------------------------------------------- middleware

def test_streamed_response_time_is_measured_to_the_end_of_the_body():
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.middleware("http")(pm.prometheus_middleware)

    @app.get("/slow-stream")
    async def slow_stream():
        async def body():
            for i in range(3):
                await asyncio.sleep(0.15)
                yield f"data: {i}\n\n"
        return StreamingResponse(body(), media_type="text/event-stream")

    labels = {"method": "GET", "endpoint": "/slow-stream"}
    before_sum = _val("llama_agent_response_time_seconds_sum", labels)
    before_n = _val("llama_agent_response_time_seconds_count", labels)
    active = _val("llama_agent_active_connections", {})

    r = TestClient(app).get("/slow-stream")
    assert r.status_code == 200 and r.text.count("data:") == 3

    assert _val("llama_agent_response_time_seconds_count", labels) == before_n + 1
    # Old code recorded ~0.0 s here (headers only); the body takes >= 0.45 s.
    assert _val("llama_agent_response_time_seconds_sum", labels) - before_sum >= 0.4
    # Active connections stay up for the whole stream and come back down.
    assert _val("llama_agent_active_connections", {}) == active


def test_middleware_counts_a_request_whose_handler_raises():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.middleware("http")(pm.prometheus_middleware)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    active = _val("llama_agent_active_connections", {})
    before = _val("llama_agent_errors_total", {"error_type": "RuntimeError"})
    with pytest.raises(RuntimeError):
        TestClient(app).get("/boom")
    assert _val("llama_agent_errors_total", {"error_type": "RuntimeError"}) == before + 1
    assert _val("llama_agent_active_connections", {}) == active


def test_timed_body_finishes_once_even_if_dropped_unfinished():
    calls = []

    async def body():
        yield b"a"
        yield b"b"

    async def main():
        t = pm._TimedBody(body(), lambda: calls.append(1))
        assert await t.__anext__() == b"a"
        t.__del__()  # what GC does when the server stops iterating early
        t.__del__()
    asyncio.run(main())
    assert calls == [1]


# ---------------------------------------------------------------- chat stream wrapper

def _run(events, meta):
    async def main():
        return [c async for c in pm.timed_chat_stream(events, meta)]
    return asyncio.run(main())


def _sse(*chunks, delay=0.0):
    async def gen():
        for c in chunks:
            if delay:
                await asyncio.sleep(delay)
            yield c
    return gen()


def test_every_chat_series_exists_at_zero_on_startup():
    for route in pm.CHAT_ROUTES:
        assert REGISTRY.get_sample_value(
            "llama_agent_chat_first_token_seconds_count", {"route": route}) is not None
        for outcome in pm.CHAT_OUTCOMES:
            assert REGISTRY.get_sample_value(
                "llama_agent_chat_seconds_count", {"route": route, "outcome": outcome}) is not None


def test_slo_thresholds_are_histogram_buckets():
    # A threshold between two buckets can't be read exactly from the histogram.
    assert 10 in pm.CHAT_BUCKETS and 90 in pm.CHAT_BUCKETS


def test_answer_chunks_skip_events_and_done():
    assert pm.is_answer_chunk("data: Hello\n\n")
    assert pm.is_answer_chunk("data: {not json\n\n")
    assert pm.is_answer_chunk('data: {"answer": 1}\n\n')  # JSON text, no event type
    assert not pm.is_answer_chunk('data: {"type": "reasoning", "content": "x"}\n\n')
    assert not pm.is_answer_chunk("data: [DONE]\n\n")
    assert not pm.is_answer_chunk("data: \n\n")
    assert not pm.is_answer_chunk(b"data: bytes")


def test_stream_passes_through_and_records_time_route_and_first_text():
    chunks = ['data: {"type": "config"}\n\n', 'data: {"type": "reasoning"}\n\n',
              "data: Hello\n\n", "data:  world\n\n", "data: [DONE]\n\n"]
    meta = {"route": "command", "start": None}
    n = _chat_count("general", "ok")
    ft = _val("llama_agent_chat_first_token_seconds_count", {"route": "general"})
    s = _val("llama_agent_chat_seconds_sum", {"route": "general", "outcome": "ok"})

    async def main():
        out = []
        async for c in pm.timed_chat_stream(_sse(*chunks, delay=0.05), meta):
            meta["route"] = "general"  # the handler picks the route mid-stream
            out.append(c)
        return out
    assert asyncio.run(main()) == chunks

    assert _chat_count("general", "ok") == n + 1
    assert _val("llama_agent_chat_first_token_seconds_count", {"route": "general"}) == ft + 1
    assert _val("llama_agent_chat_seconds_sum", {"route": "general", "outcome": "ok"}) - s >= 0.2


@pytest.mark.parametrize("notice", [
    "⚠️ The Research Agent could not complete this task. Please try again.",
    "⚠️ The agent team could not complete this task.",
    "⚠️ Stream timed out after 300s.",
    "⚠️ The AI model is unavailable right now. Please try again in a moment.",
])
def test_failure_notice_instead_of_an_answer_is_failed(notice):
    n = _chat_count("agent", "failed")
    _run(_sse(f"data: {notice}\n\n", "data: [DONE]\n\n"), {"route": "agent"})
    assert _chat_count("agent", "failed") == n + 1


def test_empty_answer_is_failed():
    n = _chat_count("general", "failed")
    _run(_sse('data: {"type": "config"}\n\n', "data: [DONE]\n\n"), {"route": "general"})
    assert _chat_count("general", "failed") == n + 1


def test_exception_is_error_and_still_raised():
    async def gen():
        yield "data: partial\n\n"
        raise TimeoutError("downstream")
    n = _chat_count("document", "error")
    with pytest.raises(TimeoutError):
        _run(gen(), {"route": "document"})
    assert _chat_count("document", "error") == n + 1


def test_client_disconnect_is_cancelled_not_failed():
    n_c, n_f = _chat_count("team", "cancelled"), _chat_count("team", "failed")

    async def main():
        stream = pm.timed_chat_stream(_sse("data: a\n\n", "data: b\n\n"), {"route": "team"})
        await stream.__anext__()
        await stream.aclose()  # what Starlette does when the client goes away
    asyncio.run(main())
    assert _chat_count("team", "cancelled") == n_c + 1
    assert _chat_count("team", "failed") == n_f


def test_record_chat_never_raises():
    pm.record_chat("not-a-route", "weird", "not-a-number")  # swallowed
    pm.record_chat("unknown", "ok", 1.0)  # unknown route -> general


def test_failure_markers_cover_the_agent_run_markers():
    import multi_agent
    from evals import checks
    for m in multi_agent._RUN_FAILURE_MARKERS:
        assert m in pm.CHAT_FAILURE_MARKERS, m
    for m in checks.FAILURE_MARKERS:
        assert any(m.lower() in x or x in m.lower() for x in pm.CHAT_FAILURE_MARKERS), m


def test_real_answer_mentioning_errors_is_ok():
    assert pm.chat_outcome("Here is how to handle a timeout error in Python: ...") == "ok"


# ---------------------------------------------------------------- /chat/stream wiring

@pytest.fixture
def stream_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api
    import multi_agent
    from modules.memory import ConversationMemory, FactMemory
    from modules.rag import SimpleRAG

    class FakeAgent:
        def __init__(self, key):
            self.name, self.key = key.title(), key

        def execute_stream(self, task, critique=False, reflect=False):
            if "fail" in task:
                yield {"type": "token",
                       "content": f"⚠️ The {self.name} could not complete this task. Please try again."}
            else:
                yield {"type": "token", "content": f"[{self.key}]"}

        def critique(self, task, text):
            return ""

    class FakeOrchestrator:
        def __init__(self):
            self.agents = {k: FakeAgent(k) for k in api.AGENT_KEYS}

        def collaborate_stream(self, task, critique=None, reflect=None, parallel=None):
            yield {"type": "agent_start", "agent": "Writer", "step": "writer"}
            yield {"type": "token", "content": "<writer>"}
            yield {"type": "agent_done", "agent": "Writer", "step": "writer", "content": "w"}
            yield {"type": "final", "content": "FINAL"}

    api.app.state.rag = SimpleRAG()
    monkeypatch.setattr(api, "UPLOAD_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setattr(multi_agent, "MultiAgentOrchestrator", FakeOrchestrator)
    monkeypatch.setenv("GROQ_API_KEY", os.getenv("GROQ_API_KEY") or "test-key")
    monkeypatch.setattr(api.app.state, "memory", ConversationMemory(), raising=False)
    monkeypatch.setattr(api.app.state, "facts", FactMemory(), raising=False)
    return TestClient(api.app)


@pytest.mark.parametrize("payload,route,outcome", [
    ({"message": "My name is Ravi"}, "command", "ok"),
    ({"message": "hello there friend", "agent": "coder"}, "agent", "ok"),
    ({"message": "please fail now", "agent": "coder"}, "agent", "failed"),
    ({"message": "@team plan a product launch"}, "team", "ok"),
])
def test_chat_stream_records_route_and_outcome(stream_client, payload, route, outcome):
    n = _chat_count(route, outcome)
    http = {"method": "POST", "endpoint": "/chat/stream"}
    n_http = _val("llama_agent_response_time_seconds_count", http)

    r = stream_client.post("/chat/stream", json={**payload, "use_critic": False})
    assert r.status_code == 200 and "[DONE]" in r.text

    assert _chat_count(route, outcome) == n + 1
    assert _val("llama_agent_response_time_seconds_count", http) == n_http + 1


# ---------------------------------------------------------------- rules + dashboard

def _rule_files():
    cm = next(d for d in yaml.safe_load_all(open("k8s/prometheus.yaml"))
              if d and d["kind"] == "ConfigMap" and d["metadata"]["name"] == "prometheus-config")
    return {"k8s": yaml.safe_load(cm["data"]["alerts.yml"]),
            "compose": yaml.safe_load(open("config/prometheus/alerts.yml"))}


def _group(rules, name):
    return next(g for g in rules["groups"] if g["name"] == name)["rules"]


def test_slo_rules_are_identical_in_k8s_and_compose():
    files = _rule_files()
    assert _group(files["k8s"], "llama_agent_slo") == _group(files["compose"], "llama_agent_slo")


def test_slo_recording_rules_and_burn_rate_alerts():
    rules = _group(_rule_files()["k8s"], "llama_agent_slo")
    records = {r["record"]: r["expr"] for r in rules if "record" in r}
    alerts = {r["alert"]: r["expr"] for r in rules if "alert" in r}
    for sli in ("unanswered", "quick_slow", "agent_slow"):
        for w in ("5m", "30m", "1h", "6h"):
            assert f"llama_agent:chat_{sli}:ratio_rate{w}" in records
            assert f"[{w}]" in records[f"llama_agent:chat_{sli}:ratio_rate{w}"]
    assert 'le=~"10(\\\\.0)?"' in records["llama_agent:chat_quick_slow:ratio_rate1h"]
    assert 'le=~"90(\\\\.0)?"' in records["llama_agent:chat_agent_slow:ratio_rate1h"]
    assert 'outcome!="cancelled"' in records["llama_agent:chat_unanswered:ratio_rate1h"]
    assert set(alerts) == {"ChatAvailabilitySLOBurn", "ChatQuickLatencySLOBurn",
                           "ChatAgentLatencySLOBurn"}
    for expr in alerts.values():
        # Fast burn (14.4x, 1h + 5m) or slow burn (6x, 6h + 30m), 95% target.
        for part in ("ratio_rate1h > (14.4 * 0.05)", "ratio_rate5m > (14.4 * 0.05)",
                     "ratio_rate6h > (6 * 0.05)", "ratio_rate30m > (6 * 0.05)"):
            assert part in expr
        # Minimum traffic, so 1 bad chat out of 2 can't fire it.
        assert "[1h])) >= 5" in expr and "[6h])) >= 5" in expr


def test_http_latency_alerts_leave_chat_to_the_slos():
    """Now that streamed answers are timed to the end, a 60 s agent answer
    would trip a 5 s HTTP p95 alert. Chat latency is judged per route by the
    SLO alerts instead."""
    for rules in _rule_files().values():
        app = {r["alert"]: r["expr"] for r in _group(rules, "llama_agent_app")}
        for name in ("HighResponseTime", "VeryHighResponseTime"):
            assert 'endpoint!~"/chat|/chat/stream"' in app[name]


def test_prometheus_keeps_the_30_day_slo_window():
    text = open("k8s/prometheus.yaml").read()
    assert "--storage.tsdb.retention.time=30d" in text


def test_dashboard_has_slo_panels():
    with open("config/grafana/dashboards/llama-agent.json") as f:
        panels = json.load(f)["panels"]
    by_title = {p["title"]: p for p in panels}
    for title in ("SLO: chats answered (30d, target 95%)",
                  "SLO: quick answers within 10 s (30d, target 95%)",
                  "SLO: agent answers within 90 s (30d, target 95%)"):
        p = by_title[title]
        steps = p["fieldConfig"]["defaults"]["thresholds"]["steps"]
        assert steps[-1] == {"color": "green", "value": 0.95}
        assert "[30d]" in p["targets"][0]["expr"]
    assert "Chat answer time p50 / p95 by route (1h)" in by_title
    assert "Time to first answer text p50 / p95 by route (1h)" in by_title
    assert "SLO error-budget burn rate (1h window)" in by_title
    # No two panels overlap on the grid.
    cells = set()
    for p in panels:
        g = p["gridPos"]
        for x in range(g["x"], g["x"] + g["w"]):
            for y in range(g["y"], g["y"] + g["h"]):
                assert (x, y) not in cells, p["title"]
                cells.add((x, y))
