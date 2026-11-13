# Observability findings

The app calls itself "observable by design". This file is the evidence, in the same
shape as [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md): what was wrong, what it cost, what
fixed it, and the number that proves it.

Three surfaces are instrumented — Prometheus metrics, Phoenix traces, Loki logs —
and three of them were measuring the wrong thing in ways that made the dashboard
*look healthy*.

**The through-line:** a metric that reports the wrong number is worse than no
metric. No metric leaves a gap someone notices. A wrong one is read, trusted, and
acted on.

---

## 1. The dashboard said p50 0.5 s. The pods said 3 s.

**Symptom.** Grafana's per-agent latency panel showed a p50 of **0.5 s**. The raw
metrics scraped from each pod said **~3 s**. Both were reading the same counter.

**Cause.** Prometheus scraped the load-balanced `Service`. With `replicas: 2`, each
scrape landed on a different pod, so the histogram series from two pods were
interleaved under one label set. Half the observations came from a pod that had
just started and the other half from one that had been running — the histogram
was a blend of two different populations, and the blend's median landed at 0.5 s.

The metrics were correct. The **scrape target** was wrong, and nothing in the
pipeline could see that: Prometheus has no concept of "this series mixes two
instances".

**Fix.** Scrape each pod through a **headless Service** (`clusterIP: None`) so DNS
returns pod IPs, and sum across pods in the dashboard query. v3.5.1.

**Proof.** `test_agent_metrics.py::test_prometheus_scrapes_every_api_pod_via_headless_service`
asserts the scrape config targets pods, not the load balancer.

**The lesson, and it is not "configure Prometheus right".** A load-balanced scrape
is a *correct* scrape of a *wrong* thing. Every individual data point was true. The
aggregate was fiction. This is the observability equivalent of the eval suite
averaging ungraded cases into the denominator.

---

## 2. "No data" was read as "no problems"

**Symptom.** The "Agent failure rate" panel showed **No data** instead of 0%.

**Cause.** A Prometheus counter series does not exist until its first increment. With
no failures yet, `llama_agent_agent_runs_total{outcome="failed"}` had never been
created, so `rate()` over it returned nothing, and Grafana rendered "No data".

**Why this is worse than a missing panel.** An operator glancing at a dashboard sees
"No data" and reads *nothing to report*. The truth was *zero failures so far* —
which is a fact worth knowing, and a different one. The failure signal and the
absence of signal rendered identically.

**Fix.** Every agent × outcome series is initialised at **0** on startup. v3.5.1.

**Proof.** `test_agent_metrics.py::test_agent_run_series_exist_at_zero_from_startup`.

**The lesson.** Absence of evidence rendered as absence of problems. The eval suite
had exactly the same bug in a different place (finding 37: `tool recall 0.0%` for a
case that never ran). Two subsystems, one mistake: a missing measurement printed as
a bad one.

---

## 3. A 2.0-second stream measured as 2 milliseconds

**Symptom.** "Avg Response Time" and every latency alert never fired for a slow chat.
A request that took the user two seconds recorded **0.002 s**.

**Cause.** The timing middleware stopped its clock when the response **headers** were
ready. For a streaming response, the headers are sent before the first token — so
the metric measured "how long it took to start answering", not "how long the user
waited". The gap between those two things is the entire latency of the answer.

**Fix.** Measure to the **last byte of the body**, and give chat its own SLO per
route. v3.5.3. See [`SLO.md`](SLO.md).

**Proof.**
`test_chat_slo_metrics.py::test_streamed_response_time_is_measured_to_the_end_of_the_body`:
a 2.0 s stream recorded as **0.002 s**, now **2.008 s** — a **1,000× understatement**,
silently, for as long as the endpoint had existed.

**The lesson.** This is the same defect as the eval suite's 89 minutes of wall clock
for 17.3 seconds of work (finding 11c): **the timer was attached one level too
high**. There it was a timeout that could not fire; here it was a clock that stopped
before the work started. Both looked fine from the code and both were invisible from
the dashboard.

---

## 4. "Traced cheaply" was an assumption, and the cheap version is the lucky one

**Symptom.** None. This is the one finding in this document that was not found by
something going wrong — it was found by noticing that a claim in this project had
never been tested, and that the untested claim was load-bearing.

**Story.** `docs/OBSERVABILITY_FINDINGS.md` ended with an honest paragraph: *"The
claim 'traced' is verified; the claim 'traced cheaply' is not."* That sentence is
the whole finding.

Every request gets a `GroqLLM.chat` span per LLM call, exported to Phoenix with
`batch=True` (`api.py:607`), plus an agent span from `llama_agent1._span_cm`
carrying three attributes. The obvious reading is "spans are cheap, this is fine."
That reading is almost certainly true — but *almost certainly true* is exactly the
standard this project has spent five parts dismantling. Measuring it takes one
afternoon and it either removes a doubt or finds a problem.

**Measured: ~5 µs per span, and it scales linearly.**

CPython 3.11.2, `otel-sdk 1.45.1`, 300 runs × 9 samples, median of medians, three
attributes per span (the exact shape of `_span_cm`):

| Spans per run | No tracing | Traced | Delta per run | Per span |
|---|---|---|---|---|
| 2 | 45 µs | 57 µs | +11 µs | 5.6 µs |
| 6 | 140 µs | 179 µs | +39 µs | 6.6 µs |
| **13** (a real run) | **300 µs** | **363 µs** | **+64 µs** | **4.9 µs** |
| 26 | 560 µs | 687 µs | +127 µs | 4.9 µs |
| 51 | 1,105 µs | 1,506 µs | +401 µs | 7.9 µs |

The scaling is the point. A constant overhead per *request* would mean something
different from a constant overhead per *span*: the first could be amortised, the
second grows with how much work the agent does — and `@team` runs produce an order
of magnitude more spans than a plain chat. It is linear, so the cost is bounded by
"how many spans did this run emit", which is a number the team can see.

**Against a real answer:**

- a 13-span run costs **~0.065 ms** more
- a plain answer is **~2 s** (pods measured ~3 s; the dashboard's 0.5 s was
  finding 1)
- **0.003%** of the thing being measured

Three orders of magnitude below the noise floor of anything a user could feel.

**The part that could have gone the other way.** The obvious worry with a
background exporter is *what happens when Phoenix is not there*. If a full export
queue blocked the caller, a monitoring outage would become a latency incident —
the classic own-goal of adding observability. Measured directly, with a queue of 8
and a dead collector on `127.0.0.1:9`:

```
Queue full, dropping Span.
200 spans into a FULL queue with a dead collector:
  median span: 0.028 ms   p95: 0.056 ms   worst: 0.788 ms
```

**OTel drops; it does not block.** The cost of a Phoenix outage is *lost traces*,
not slow requests. That is the right behaviour and it was not documented anywhere
— it was a property of a library this project depends on and had never looked at.

**A number I could not pin, and am not claiming.** An early run showed a dead
collector costing *more* (+200–235 µs/run) than a healthy one (+117 µs). Three
repeat runs reproduced the direction, which was tempting. But when I varied the
exporter timeout to test the hypothesis, a **50 ms** timeout cost *more* than a
**1 s** one — which cannot be a real effect, so the measurement was dominated by
thread and GC noise, not by the exporter. The per-span figures above survived
because they are a *slope* across five span counts, and a slope is far more robust
to constant noise than a difference of two medians.

**So: no claim is made about the dead-collector cost beyond "it does not block".**
That is the honest boundary, and it is written here rather than rounded into the
table.

**Proof.** `tests/test_tracing_overhead.py`, three tests:

- `test_a_span_costs_single_digit_microseconds` — ceiling of 50 µs, ~10× the
  measurement, because a tight timing bound on shared CI hardware is flaky and a
  flaky timing test gets deleted
- `test_the_whole_traced_run_is_negligible_against_a_real_answer` — asserts the
  overhead stays under 0.25% of a 2 s answer
- `test_a_dead_collector_does_not_block_span_creation` — **pins the drop-not-block
  behaviour**, because the alternative would be a production hazard and the only
  way to know which one you have is to run it

**The lesson.** This is the only finding in this document that found nothing
broken, and it is the one that most needed writing down. The other three were
discovered by a wrong number reaching a user. This one was discovered by asking
*"which of our claims has no measurement behind it?"* — and the answer was the
sentence in this very file that said so.

> **An assumption that happens to be true is still an assumption, and the ones
> that happen to be true are the ones that never get checked.**

## The pattern, four for four

| # | What the metric said | What was true | Why |
|---|---|---|---|
| 1 | p50 0.5 s | ~3 s | two pods' series blended by a load-balanced scrape |
| 2 | "No data" | 0 failures | a counter that does not exist until it fires |
| 3 | 0.002 s | 2.008 s | the clock stopped before the streamed answer began |
| 4 | "traced cheaply" | ~5 µs/span — **true**, but unmeasured | the claim had no number behind it |

Findings 1–3 were **read and trusted**. None was caught by looking at the code —
each was caught by comparing the dashboard against a second source: the pods' raw
metrics, the absence of failures in the logs, and a stopwatch.

**Finding 4 is the odd one out and completes the pattern.** It found nothing
broken. It was caught by asking which claims had no second source at all — and then
becoming one.

**A dashboard is a measuring instrument, and it needs the same suspicion as a test
suite.** The eval part of this project has 19 findings about the measuring stick.
This part has three about the dashboard. They are the same kind of bug in different
clothes.

---

## What is *not* measured

Stated so it is not discovered by a reader first:

- ~~**No tracing overhead measurement.**~~ **Measured 2026-11-13** — finding 4.
  ~5 µs per span, ~0.065 ms for a 13-span run, 0.003% of a 2 s answer. A dead
  collector costs nothing on the request path: OTel drops spans rather than
  blocking.
- **No alert delivery.** Prometheus alert rules exist; no Alertmanager is deployed.
  The rules evaluate and nothing is sent anywhere. See [`SLO.md`](SLO.md) for the
  targets and README "Known limits" for the gap.
- **`/chat/stream` skips the in-app business metrics** that `/chat` records, though
  request metrics and Phoenix traces do cover it.
- **The judge is not traced as its own span tree** — eval judge calls appear as
  `GroqLLM.chat` with `component="judge"`, but not as a separate eval trace.
