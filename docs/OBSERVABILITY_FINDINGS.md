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

## The pattern, three for three

| # | What the metric said | What was true | Why |
|---|---|---|---|
| 1 | p50 0.5 s | ~3 s | two pods' series blended by a load-balanced scrape |
| 2 | "No data" | 0 failures | a counter that does not exist until it fires |
| 3 | 0.002 s | 2.008 s | the clock stopped before the streamed answer began |

All three were **read and trusted**. None was caught by looking at the code — each
was caught by comparing the dashboard against a second source: the pods' raw
metrics, the absence of failures in the logs, and a stopwatch.

**A dashboard is a measuring instrument, and it needs the same suspicion as a test
suite.** The eval part of this project has 18 findings about the measuring stick.
This part has three about the dashboard. They are the same kind of bug in different
clothes.

---

## What is *not* measured

Stated so it is not discovered by a reader first:

- **No tracing overhead measurement.** Phoenix receives a `GroqLLM.chat` span per
  LLM call with `batch=True` export, but no one has measured what that costs. The
  claim "traced" is verified; the claim "traced cheaply" is not.
- **No alert delivery.** Prometheus alert rules exist; no Alertmanager is deployed.
  The rules evaluate and nothing is sent anywhere. See [`SLO.md`](SLO.md) for the
  targets and README "Known limits" for the gap.
- **`/chat/stream` skips the in-app business metrics** that `/chat` records, though
  request metrics and Phoenix traces do cover it.
- **The judge is not traced as its own span tree** — eval judge calls appear as
  `GroqLLM.chat` with `component="judge"`, but not as a separate eval trace.
