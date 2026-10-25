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

"component" is the part of the app that made the LLM call: an agent
(research, coder, writer, analyzer, planner), chat, rag, router or judge.
"""

import time
import prometheus_client
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
    "Response time in seconds",
    ["method", "endpoint"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
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


RAG_CRAG_VERDICTS = Counter(
    "llama_agent_rag_crag_verdicts_total",
    "Corrective-RAG checks of document answers against their excerpts",
    ["verdict"],  # supported | unsupported | unsure
)


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
        from modules.advanced_monitoring import cost_tracker
        try:
            from modules.request_context import get_owner
            user = get_owner() or "anonymous"
        except Exception:
            user = "anonymous"
        cost = cost_tracker.record_usage(model or "unknown", input_tokens, output_tokens,
                                         user_id=user)
        comp = _component(component)
        LLM_TOKENS.labels(model=model or "unknown", kind="input", component=comp).inc(input_tokens)
        LLM_TOKENS.labels(model=model or "unknown", kind="output", component=comp).inc(output_tokens)
        LLM_COST.labels(model=model or "unknown", component=comp).inc(cost)
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


async def prometheus_middleware(request: Request, call_next):
    """Middleware to track request metrics automatically."""
    # Skip the /metrics endpoint itself
    if request.url.path == "/metrics":
        return await call_next(request)

    method = request.method

    ACTIVE_CONNECTIONS.inc()
    start = time.time()

    try:
        response = await call_next(request)
        elapsed = time.time() - start
        endpoint = _route_label(request)

        # Track request count
        REQUEST_COUNT.labels(method=method, endpoint=endpoint).inc()

        # Track response time
        RESPONSE_TIME.labels(method=method, endpoint=endpoint).observe(elapsed)

        # Track HTTP status codes
        HTTP_RESPONSES.labels(
            status_code=str(response.status_code),
            method=method,
            endpoint=endpoint,
        ).inc()

        return response

    except Exception as e:
        ERROR_COUNT.labels(error_type=type(e).__name__).inc()
        raise

    finally:
        ACTIVE_CONNECTIONS.dec()
