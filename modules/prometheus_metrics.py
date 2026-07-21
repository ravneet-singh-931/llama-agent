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
    endpoint = request.url.path

    ACTIVE_CONNECTIONS.inc()
    start = time.time()

    try:
        response = await call_next(request)
        elapsed = time.time() - start

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
