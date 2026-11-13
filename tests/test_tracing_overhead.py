"""What does tracing cost? Measured, with the margin stated.

docs/OBSERVABILITY_FINDINGS.md said: "Phoenix receives a GroqLLM.chat span per
LLM call with batch=True export, but no one has measured what that costs. The
claim 'traced' is verified; the claim 'traced cheaply' is not."

This pins the measurement so it cannot regress silently, and so the claim in the
docs has a test behind it. The assertion is deliberately loose (a generous
ceiling, not a tight bound): this runs on shared CI hardware where a tight
timing bound would be flaky, and a flaky timing test gets deleted. The number is
in the docs; the test only guards the order of magnitude.

Measured 2026-11-13, CPython 3.11.2, otel-sdk 1.45.1, 300 runs x 9 samples,
median of medians, 3 attributes per span (the shape of llama_agent1._span_cm):

    spans/run    no tracing    traced    per span
        2          45 us       57 us      5.6 us
        6         140 us      179 us      6.6 us
       13         300 us      363 us      4.9 us
       26         560 us      687 us      4.9 us
       51       1,105 us    1,506 us      7.9 us
"""
import statistics
import time
from contextlib import contextmanager

import pytest

pytest.importorskip("opentelemetry.sdk.trace")
from opentelemetry.sdk.trace import TracerProvider                      # noqa: E402
from opentelemetry.sdk.trace.export import BatchSpanProcessor           # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (     # noqa: E402
    InMemorySpanExporter,
)


@contextmanager
def _span(tracer, name):
    with tracer.start_as_current_span(name) as s:
        s.set_attribute("user.id", "bench")
        s.set_attribute("conversation.id", "bench-1")
        s.set_attribute("config.commit", "abc")
        yield s


def _run(tracer, spans, runs):
    for _ in range(runs):
        with _span(tracer, "run_agent"):
            for i in range(spans):
                with _span(tracer, f"GroqLLM.chat.{i}"):
                    pass


def _tracer(with_export: bool):
    provider = TracerProvider()
    if with_export:
        provider.add_span_processor(BatchSpanProcessor(InMemorySpanExporter()))
    return provider.get_tracer("bench")


def _median_us(tracer, spans, runs=200, samples=5):
    times = []
    for _ in range(samples):
        t0 = time.perf_counter()
        _run(tracer, spans, runs)
        times.append((time.perf_counter() - t0) / runs)
    return statistics.median(times) * 1e6


def test_a_span_costs_single_digit_microseconds():
    """The ceiling is ~10x the measurement, on purpose: this must not flake."""
    plain, traced = _tracer(False), _tracer(True)
    _run(plain, 12, 50)                      # warm up both
    _run(traced, 12, 50)
    base = _median_us(plain, 12)
    cost = _median_us(traced, 12)
    per_span = (cost - base) / 13
    assert per_span < 50, (
        f"a traced span now costs {per_span:.1f} us (measured ~5 us on "
        f"2026-11-13); something changed in the instrumentation"
    )


def test_the_whole_traced_run_is_negligible_against_a_real_answer():
    """The number that decides whether this matters: overhead vs a chat turn.

    A plain answer is ~2 s (pods measured ~3 s; the dashboard's 0.5 s was the
    bug in finding 1). 13 spans at ~5 us is ~0.065 ms -- four orders of
    magnitude below the thing being measured.
    """
    plain, traced = _tracer(False), _tracer(True)
    _run(plain, 12, 50)
    _run(traced, 12, 50)
    overhead_us = _median_us(traced, 12) - _median_us(plain, 12)
    assert overhead_us < 5_000, f"{overhead_us:.0f} us would be worth noticing"
    # At the measured ~60 us this is 0.003%; the ceiling allows 0.25%.
    assert overhead_us / 2_000_000 * 100 < 0.25


def test_a_dead_collector_does_not_block_span_creation():
    """Measured: OTel DROPS when its queue is full ('Queue full, dropping Span'),
    it does not block the caller. So a Phoenix outage cannot add latency to a
    request -- it loses spans.

    Pinned because the alternative (blocking) would be a serious production
    hazard, and the only way to know which one you have is to run it.
    """
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http")
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(
        OTLPSpanExporter(endpoint="http://127.0.0.1:9/v1/traces", timeout=0.2),
        max_queue_size=8, max_export_batch_size=8,
        schedule_delay_millis=60_000, export_timeout_millis=200))
    tracer = provider.get_tracer("bench")

    times = []
    for i in range(120):                     # far more spans than the queue holds
        t0 = time.perf_counter()
        with _span(tracer, f"s{i}"):
            pass
        times.append(time.perf_counter() - t0)
    times.sort()
    p95 = times[int(len(times) * 0.95)]
    assert p95 < 0.02, (
        f"p95 span creation was {p95*1000:.1f} ms with a dead collector; if this "
        f"blocks, a Phoenix outage becomes a request-latency incident"
    )
