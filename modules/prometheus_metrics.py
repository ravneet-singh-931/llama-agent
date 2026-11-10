"""
Llama Agent - Prometheus Metrics Middleware

Exposes /metrics endpoint in Prometheus format for scraping.
Metrics exposed:
- llama_agent_requests_total (counter)
- llama_agent_errors_total (counter)
- llama_agent_response_time_seconds (histogram)
- llama_agent_active_connections (gauge)
- llama_agent_http_responses_total (counter by status code)
- llama_agent_start_time (gauge)
- llama_agent_llm_tokens_total / llama_agent_llm_cost_usd_total (by model, component)
- llama_agent_agent_run_seconds (histogram by agent, outcome)
- llama_agent_chat_seconds (histogram by route, outcome): what the user waits for a
  /chat/stream answer, start to end of the stream - the SLO metric
- llama_agent_chat_first_token_seconds (histogram by route): time to the first answer text

"component" is the part of the app that made the LLM call: an agent
(research, coder, writer, analyzer, planner), chat, rag, router or judge.
"""

import asyncio
import json
import os
import time
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from starlette.requests import Request
from starlette.responses import Response

# ============ METRICS DEFINITION ============

REQUEST_COUNT = Counter(
    "llama_agent_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint"],
)

ERROR_COUNT = Counter(
    "llama_agent_errors_total",
    "Total number of errors",
    ["error_type"],
)

RESPONSE_TIME = Histogram(
    "llama_agent_response_time_seconds",
    "Response time in seconds, until the last byte of the body is sent "
    "(for streamed responses: the end of the stream)",
    ["method", "endpoint"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
)

ACTIVE_CONNECTIONS = Gauge(
    "llama_agent_active_connections",
    "Number of active connections",
)

HTTP_RESPONSES = Counter(
    "llama_agent_http_responses_total",
    "Total HTTP responses by status code",
    ["status_code", "method", "endpoint"],
)

START_TIME = Gauge(
    "llama_agent_start_time",
    "Server start time in Unix timestamp",
)


LLM_TOKENS = Counter(
    "llama_agent_llm_tokens_total",
    "LLM tokens consumed (Groq)",
    ["model", "kind", "component"],  # kind = input | output
)

LLM_COST = Counter(
    "llama_agent_llm_cost_usd_total",
    "Estimated LLM spend in USD (Groq list prices)",
    ["model", "component"],
)

# Per-user spend. This existed only as an in-memory dict in CostTracker, so it
# was per-process: two replicas reported two different totals and a restart
# erased both. A counter is shared through Prometheus with everything else, so
# there is exactly one number.
#
# Cardinality: bounded by LLM_USERS_BUCKETS. A real user id is kept; anything
# else is bucketed, because an unbounded user label is how a metrics endpoint
# OOMs a Prometheus server.
LLM_COST_BY_USER = Counter(
    "llama_agent_llm_cost_usd_by_user_total",
    "Estimated LLM spend in USD, by user (bucketed beyond LLM_USERS_BUCKETS)",
    ["user"],
)
LLM_USERS_BUCKETS = 500


def _user_label(user: str) -> str:
    """Bound the user label so cardinality cannot grow without limit."""
    user = (user or "").strip()
    if not user:
        return "anonymous"
    if user == "anonymous" or len(LLM_COST_BY_USER._metrics) < LLM_USERS_BUCKETS:
        return user
    if user in LLM_COST_BY_USER._metrics:
        return user
    return "other"

# Components and run outcomes are fixed sets (bounded label cardinality).
LLM_COMPONENTS = ("research", "coder", "writer", "analyzer", "planner",
                  "chat", "doc_answer", "rag", "router", "judge", "other")
AGENT_RUN_OUTCOMES = ("ok", "failed", "cached", "error", "cancelled")

AGENT_RUN_SECONDS = Histogram(
    "llama_agent_agent_run_seconds",
    "Wall time of one agent run (one specialist agent task), by outcome: "
    "ok | failed (empty or failure message) | cached | error (exception) | cancelled",
    ["agent", "outcome"],
    buckets=[1, 2.5, 5, 10, 20, 30, 60, 120, 300],
)


def _component(name: str) -> str:
    return name if name in LLM_COMPONENTS else "other"


# Create every agent x outcome series at 0 on startup. Prometheus can't see the
# first increment of a series that appears already at 1 (rate/increase need two
# samples), so the first run per pod was missing, and "failure rate" showed no
# data instead of 0% while nothing had failed.
AGENT_NAMES = ("research", "coder", "writer", "analyzer", "planner")
for _agent in AGENT_NAMES:
    for _outcome in AGENT_RUN_OUTCOMES:
        AGENT_RUN_SECONDS.labels(agent=_agent, outcome=_outcome)


def record_agent_run(agent: str, seconds: float, outcome: str) -> None:
    """Record one agent run. Never raises - metrics must not break chat."""
    try:
        AGENT_RUN_SECONDS.labels(
            agent=_component(agent),
            outcome=outcome if outcome in AGENT_RUN_OUTCOMES else "error",
        ).observe(max(0.0, float(seconds)))
    except Exception:
        pass


# ============ CHAT ANSWER TIME (SLO metrics) ============
# The HTTP histogram above has one label for all of /chat/stream, where a
# general-chat reply takes ~1 s and an @team answer several minutes. The SLOs
# need the route, and whether the user got an answer or a failure notice.
CHAT_ROUTES = ("command", "document", "general", "agent", "team")
CHAT_OUTCOMES = ("ok", "failed", "error", "cancelled")
# Must contain the SLO thresholds (10 s quick answers, 90 s agent answers).
CHAT_BUCKETS = [0.5, 1, 2.5, 5, 10, 20, 30, 60, 90, 120, 180, 300, 600]

CHAT_SECONDS = Histogram(
    "llama_agent_chat_seconds",
    "Time a user waits for a /chat/stream answer (message received -> end of the "
    "stream), by route and outcome: ok | failed (failure notice instead of an "
    "answer) | error (exception) | cancelled (client went away)",
    ["route", "outcome"],
    buckets=CHAT_BUCKETS,
)

CHAT_FIRST_TOKEN_SECONDS = Histogram(
    "llama_agent_chat_first_token_seconds",
    "Time from receiving a /chat/stream message to its first answer text "
    "(reasoning, tool and config events don't count)",
    ["route"],
    buckets=CHAT_BUCKETS,
)

# Same reason as the agent series: create every series at 0 on startup, so
# rates and SLO ratios exist before the first chat on a pod.
for _route in CHAT_ROUTES:
    CHAT_FIRST_TOKEN_SECONDS.labels(route=_route)
    for _outcome in CHAT_OUTCOMES:
        CHAT_SECONDS.labels(route=_route, outcome=_outcome)

# Notices the app sends INSTEAD of an answer (agents, team, general chat,
# rate-limit and outage notices). Superset of multi_agent._RUN_FAILURE_MARKERS
# (a test keeps them in sync) and of the eval's failure markers.
CHAT_FAILURE_MARKERS = (
    "couldn't complete that task", "could not complete this step",
    "could not complete this task", "stream timed out", "returned an empty answer",
    "i couldn't produce an answer", "so i can't answer right now",
    "error while generating response", "the ai model is unavailable right now",
)


def chat_outcome(answer: str) -> str:
    """ok, or failed when the answer is empty or one of the app's failure notices."""
    low = (answer or "").strip().lower()
    if not low or any(m in low for m in CHAT_FAILURE_MARKERS):
        return "failed"
    return "ok"


def record_chat(route: str, outcome: str, seconds: float,
                first_token_seconds=None) -> None:
    """Record one /chat/stream answer. Never raises - metrics must not break chat."""
    try:
        route = route if route in CHAT_ROUTES else "general"
        CHAT_SECONDS.labels(
            route=route, outcome=outcome if outcome in CHAT_OUTCOMES else "error",
        ).observe(max(0.0, float(seconds)))
        if first_token_seconds is not None:
            CHAT_FIRST_TOKEN_SECONDS.labels(route=route).observe(
                max(0.0, float(first_token_seconds)))
    except Exception:
        pass


def is_answer_chunk(chunk) -> bool:
    """True for an SSE chunk that carries answer text: `data: <text>`, not a
    JSON event with a "type" (config, reasoning, tool, agent_start/done) and
    not the closing [DONE]."""
    if not isinstance(chunk, str) or not chunk.startswith("data: "):
        return False
    payload = chunk[6:].strip()
    if not payload or payload == "[DONE]":
        return False
    if payload.startswith("{"):
        try:
            obj = json.loads(payload)
        except ValueError:
            return True
        return not (isinstance(obj, dict) and "type" in obj)
    return True


async def timed_chat_stream(events, meta: dict):
    """Pass a /chat/stream SSE generator through unchanged and record, when it
    ends, how long the user waited (`meta["start"]`, monotonic), the route the
    handler set in `meta["route"]`, the outcome and the time to first answer text."""
    start = meta.get("start") or time.monotonic()
    first = None
    answer = []
    outcome = "ok"
    try:
        async for chunk in events:
            if is_answer_chunk(chunk):
                if first is None:
                    first = time.monotonic() - start
                answer.append(chunk[6:])
            yield chunk
    except (asyncio.CancelledError, GeneratorExit):
        outcome = "cancelled"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        if outcome == "ok":
            outcome = chat_outcome("".join(answer))
        record_chat(meta.get("route", "general"), outcome,
                    time.monotonic() - start, first)


LLM_GUARD_EVENTS = Counter(
    "llama_agent_llm_guard_events_total",
    "LLMOps guard actions (compacted | downgraded | waited | too_large_retry)",
    ["model", "action"],
)

LLM_TOKENS_TRIMMED = Counter(
    "llama_agent_llm_tokens_trimmed_total",
    "Input tokens removed by context compaction to fit the token budget",
    ["model"],
)


GROUNDING_CLAIMS = Counter(
    "llama_agent_grounding_claims_total",
    "Figures in agent answers checked against tool results (modules/grounding.py)",
    ["result"],  # supported | unsupported
)


# Per-tool execution time. Everything else here is labelled by agent or
# component; this is the only place a single TOOL can be blamed for a slow
# run. `web_search` fans out to three providers (200 ms - 8 s) and
# read_uploaded_document scales with PDF size, so a mixed p95 hides the one
# tool worth optimising.
_TOOL_SECONDS = Histogram(
    "llama_agent_tool_seconds",
    "Tool execution time by tool name",
    ["tool"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30],
)

_TOOL_NAMES = (
    "web_search",
    "list_uploaded_documents",
    "read_uploaded_document",
    "other",
)


def record_tool_seconds(tool: str, seconds: float) -> None:
    """Record one tool execution. Never raises - metrics must not break chat."""
    try:
        _TOOL_SECONDS.labels(
            tool=tool if tool in _TOOL_NAMES else "other"
        ).observe(max(0.0, float(seconds)))
    except Exception:  # nosec B110 - telemetry must never fail a tool call
        pass


def tool_span(name: str, seconds: float, ok: bool = True, detail: str = ""):
    """Context manager emitting an OpenTelemetry span for one tool call.

    The tool-call half of a run had only a duration histogram: in a Phoenix
    trace a slow web_search was a gap between the LLM spans, not an event you
    could open. This makes it a span, so a trace shows what the agent actually
    did and which call was slow.

    Yields the span (or None when tracing is not configured / not installed),
    so a caller can attach further attributes. Never raises.
    """
    class _Null:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    try:
        from llama_agent1 import _AGENT_TRACER
    except Exception:
        return _Null()
    if _AGENT_TRACER is None:
        return _Null()

    class _Span:
        def __enter__(self):
            try:
                self._cm = _AGENT_TRACER.start_as_current_span(f"tool.{name}")
                span = self._cm.__enter__()
                try:
                    span.set_attribute("tool.name", name)
                    span.set_attribute("tool.seconds", round(max(0.0, seconds), 3))
                    span.set_attribute("tool.ok", bool(ok))
                    if detail:
                        span.set_attribute("tool.detail", detail[:200])
                except Exception:
                    pass
                return span
            except Exception:
                self._cm = None
                return None

        def __exit__(self, *exc):
            try:
                if getattr(self, "_cm", None) is not None:
                    return self._cm.__exit__(*exc)
            except Exception:
                pass
            return False

    return _Span()


# Citation checks on document answers. A non-zero invented_file rate means the
# model is naming documents it was never shown -- the answer looks rigorously
# sourced, which is exactly why it needs counting.
_CITATION_FAILURES = Counter(
    "llama_agent_citation_failures_total",
    "Document answers whose citations did not match the retrieved excerpts",
    ["kind"],  # invented_file | wrong_chunk | unsourced
)
_CITATION_KINDS = ("invented_file", "wrong_chunk", "unsourced")


def record_citation_failure(kind: str) -> None:
    """Record one failed citation check. Never raises."""
    try:
        _CITATION_FAILURES.labels(
            kind=kind if kind in _CITATION_KINDS else "unsourced").inc()
    except Exception:  # nosec B110 - telemetry must never fail an answer
        pass


RAG_CRAG_VERDICTS = Counter(
    "llama_agent_rag_crag_verdicts_total",
    "Corrective-RAG checks of document answers against their excerpts",
    ["verdict"],  # supported | unsupported | unsure
)


# Groq list prices, USD per 1M tokens (console.groq.com/docs/models, checked
# 2026-10). This table lived in CostTracker.DEFAULT_PRICING, which meant the
# numbers on Grafana and the numbers on /monitoring/dashboard could disagree --
# only one of them was computing a cost at all. There is one table now.
# Override/extend with GROQ_PRICING='{"model": {"input": 0.1, "output": 0.2}}';
# unknown models use GROQ_DEFAULT_PRICE_IN / GROQ_DEFAULT_PRICE_OUT.
PRICING = {
    "qwen/qwen3.8-27b": {"input": 0.80, "output": 4.00},      # strong tier
    "openai/gpt-oss-20b": {"input": 0.075, "output": 0.30},   # fast tier
    "openai/gpt-oss-120b": {"input": 0.15, "output": 0.60},
    "qwen/qwen3-32b": {"input": 0.29, "output": 0.59},
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
}


# Latency of ONE model call. Agent/chat/tool latency already existed; the
# model call itself did not, so "is the provider slower today?" had no answer
# -- the agent histogram mixes model time with tool time and context trimming.
LLM_CALL_SECONDS = Histogram(
    "llama_agent_llm_call_seconds",
    "Wall time of a single LLM completion call, by model, component and stream",
    ["model", "component", "stream"],
    # A chat call is sub-second to ~10 s; a @team step can take a minute. The
    # buckets straddle both so p50 and p95 are usable without interpolation.
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300),
)


def record_llm_call_seconds(model: str, seconds: float, component: str = "other",
                            stream: bool = False) -> None:
    """Time one completion call. Never raises."""
    try:
        LLM_CALL_SECONDS.labels(model=model or "unknown",
                                component=_component(component),
                                stream="true" if stream else "false").observe(max(0.0, seconds))
    except Exception:
        pass


def _pricing_table() -> dict:
    """PRICING plus any GROQ_PRICING override, computed once."""
    table = dict(PRICING)
    try:
        for model, price in (json.loads(os.getenv("GROQ_PRICING", "") or "{}")).items():
            table[model] = {"input": float(price["input"]), "output": float(price["output"])}
    except Exception:
        pass
    return table


def _price(model: str, input_tokens: int, output_tokens: int) -> float:
    """USD for one call."""
    try:
        table = _pricing_table()
    except Exception:  # never let pricing break a chat
        return 0.0
    price = table.get(model) or {
        "input": float(os.getenv("GROQ_DEFAULT_PRICE_IN", "0.15")),
        "output": float(os.getenv("GROQ_DEFAULT_PRICE_OUT", "0.60")),
    }
    return ((input_tokens / 1_000_000) * price["input"]
            + (output_tokens / 1_000_000) * price["output"])


def _cumulative(metric, **labels) -> float:
    """Current value of a Prometheus counter child (0.0 when it does not exist).

    Counters cannot be read with .get(), so the dashboard renderer below reads
    the child's internal value instead of keeping a second ledger.
    """
    try:
        child = metric.labels(**labels)
        return float(getattr(child, "_value").get())
    except Exception:
        return 0.0


def cost_by_model() -> dict:
    """{model: usd} from the LLM_COST counter - the same series Grafana reads."""
    out = {}
    for model, comp in list(LLM_COST._metrics):
        value = _cumulative(LLM_COST, model=model, component=comp)
        if value:
            out[model] = out.get(model, 0.0) + value
    return out


def cost_by_component() -> dict:
    """{component: usd}; component is the per-step attribution dimension."""
    out = {}
    for model, comp in list(LLM_COST._metrics):
        value = _cumulative(LLM_COST, model=model, component=comp)
        if value:
            out[comp] = out.get(comp, 0.0) + value
    return out


def cost_by_user() -> dict:
    out = {}
    for (user,) in list(LLM_COST_BY_USER._metrics):
        value = _cumulative(LLM_COST_BY_USER, user=user)
        if value:
            out[user] = value
    return out


def cost_summary() -> dict:
    """The cost block of /monitoring/dashboard, computed from Prometheus.

    Replaces CostTracker.get_cost_summary(). Same keys (so the dashboard and
    its tests keep working), one store, correct across replicas and restarts.
    """
    by_model = cost_by_model()
    by_component = cost_by_component()
    by_user = cost_by_user()
    total_input = total_output = 0.0
    # LLM_TOKENS labels are ordered ["model", "kind", "component"] -- unpack in
    # that order. Getting it wrong silently reads every token as "output".
    for model, kind, comp in list(LLM_TOKENS._metrics):
        total = _cumulative(LLM_TOKENS, model=model, kind=kind, component=comp)
        if kind == "input":
            total_input += total
        else:
            total_output += total
    total_cost = sum(by_model.values())
    return {
        "total_cost": round(total_cost, 4),
        "total_input_tokens": int(total_input),
        "total_output_tokens": int(total_output),
        "total_tokens": int(total_input + total_output),
        "cost_by_model": {k: round(v, 4) for k, v in by_model.items()},
        "cost_by_query_type": {k: round(v, 4) for k, v in by_component.items()},
        "top_users_by_cost": dict(sorted(by_user.items(), key=lambda x: x[1],
                                         reverse=True)[:10]),
    }


def record_llm_usage(model: str, input_tokens: int, output_tokens: int,
                     component: str = "other") -> float:
    """Record one LLM call's token usage in Prometheus + the CostTracker
    (/monitoring/dashboard), labelled with the component that made the call.
    Never raises - metrics must not break chat."""
    try:
        input_tokens = int(input_tokens or 0)
        output_tokens = int(output_tokens or 0)
        if not (input_tokens or output_tokens):
            return 0.0
        comp = _component(component)
        cost = _price(model, input_tokens, output_tokens)
        LLM_TOKENS.labels(model=model or "unknown", kind="input", component=comp).inc(input_tokens)
        LLM_TOKENS.labels(model=model or "unknown", kind="output", component=comp).inc(output_tokens)
        LLM_COST.labels(model=model or "unknown", component=comp).inc(cost)
        try:
            from modules.request_context import get_owner
            user = get_owner() or "anonymous"
        except Exception:
            user = "anonymous"
        if cost:
            LLM_COST_BY_USER.labels(user=_user_label(user)).inc(cost)
        return cost
    except Exception:
        return 0.0


def _route_label(request: Request) -> str:
    """Use the matched route TEMPLATE (/conversations/{conversation_id}), not
    the raw path, so label cardinality stays bounded (raw ids / scanner
    paths would create unbounded time series)."""
    route = request.scope.get("route")
    path = getattr(route, "path", None) or getattr(route, "path_format", None)
    return path or "unmatched"


def init_metrics():
    """Initialize metrics on app startup."""
    START_TIME.set(time.time())


def metrics_endpoint(request: Request) -> Response:
    """Return all metrics in Prometheus format."""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )


class _TimedBody:
    """Wraps a response body iterator and calls `done()` once, when the body is
    fully sent, fails, is cancelled, or the iterator is dropped unfinished."""

    def __init__(self, body, done):
        self._body = body.__aiter__()
        self._done = done
        self._finished = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self._body.__anext__()
        except BaseException:  # StopAsyncIteration, cancellation, errors
            self._finish()
            raise

    def _finish(self):
        if not self._finished:
            self._finished = True
            try:
                self._done()
            except Exception:
                pass

    def __del__(self):
        self._finish()


async def prometheus_middleware(request: Request, call_next):
    """Middleware to track request metrics automatically.

    Response time and active connections are measured until the body has been
    sent. `call_next` returns as soon as the headers are ready, so before
    v3.5.3 a 60 s streamed chat answer was recorded as ~0.1 s.
    """
    # Skip the /metrics endpoint itself
    if request.url.path == "/metrics":
        return await call_next(request)

    method = request.method

    ACTIVE_CONNECTIONS.inc()
    start = time.time()

    try:
        response = await call_next(request)
    except Exception as e:
        ERROR_COUNT.labels(error_type=type(e).__name__).inc()
        ACTIVE_CONNECTIONS.dec()
        raise

    endpoint = _route_label(request)
    REQUEST_COUNT.labels(method=method, endpoint=endpoint).inc()
    HTTP_RESPONSES.labels(
        status_code=str(response.status_code),
        method=method,
        endpoint=endpoint,
    ).inc()

    def _done():
        RESPONSE_TIME.labels(method=method, endpoint=endpoint).observe(time.time() - start)
        ACTIVE_CONNECTIONS.dec()

    body = getattr(response, "body_iterator", None)
    if body is None:
        _done()
    else:
        response.body_iterator = _TimedBody(body, _done)
    return response
