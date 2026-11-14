# Observability findings

The app calls itself "observable by design". This file is the evidence, in the same
shape as [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md): what was wrong, what it cost, what
fixed it, and the number that proves it.

Three findings (7, 8, 9) came out of consolidating the three monitoring modules for
problem #14 of the maintenance audit: the log module was keeping a second set of
metrics, a firing alert printed three times and went nowhere, and the SLA monitor
scored a failing service as compliant.

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

## 5. Two sets of books for the same request, and a health check that improved as it failed

**Symptom.** Two numbers called "requests" on two dashboards, permanently different.
Neither was a bug in a counter. Both counters worked.

**Cause.** The same event was counted twice, at different layers:

| | counts | where |
|---|---|---|
| Prometheus `llama_agent_requests_total` / `_errors_total` / `_response_time_seconds` | every HTTP request, in middleware | `prometheus_middleware` (`api.py`) |
| `PerformanceMonitor.request_count` / `error_count` | only chat + MCP-tool handlers, by hand | `track_request()` had 2 call sites, `track_error()` 4 |

So the two were not two views of one thing. They were two things: the middleware
counts every route, every 404, every 429; `PerformanceMonitor` counted only the two
handlers someone had remembered to instrument. Every validation error, every rate
limit and every unhandled 500 anywhere else incremented Prometheus and left
`PerformanceMonitor` untouched, so its error rate was **structurally understated**.

**Then it got worse, and this is the part that matters.** `/health` returns
`PerformanceMonitor.get_health()`, whose `status` was
`"healthy" if error_count / max(request_count, 1) < 0.1 else "degraded"`. And
`/health` is the k8s **liveness, readiness and startup probe**
(`k8s/api.yaml`, three probes). Probe traffic was therefore *most* of
`request_count` on a quiet system — which inflated the denominator and made the
error rate smaller the more often the probe ran. **The health endpoint's verdict
improved the more it was polled, including while the service was failing.**

And the counters lived in process memory while the API runs 2–10 replicas
(`k8s/api.yaml`, `k8s/api-hpa.yaml`): each pod reported its own fraction as if it
were the total. The repository had already made this exact call once — per-user
spend was moved off an in-memory dict onto a Prometheus counter, with the reason
written in `prometheus_metrics.py`: *"two replicas reported two different totals
and a restart erased both."*

**Fix.** One event, one owner. Request and error counting belongs to Prometheus and
is scraped from `/metrics`. `PerformanceMonitor` keeps the one number it owns
honestly — *this process's* uptime — and `get_health()` now returns
`status`/`uptime_seconds`/`uptime_human` and nothing else. When the counters went,
the probe-dilution bug went with them: there is no calculation left to dilute.

The obvious-looking alternative was rejected: reading
`REQUEST_COUNT._value.get()` into `/health` would have replaced a per-pod number
with *a differently per-pod number*, since a Prometheus registry is per process and
there is one process per pod. That is the option that looks conservative and
changes what the number means without changing its name.

**The dashboard.** `AgentCollaborationDashboard` rendered those two fields as live
cards. They were replaced with numbers that are actually true across replicas:
`tasks_handled_total` (from shared storage, not process memory) and a count of
agents used at least once. Two TypeScript interfaces (`MetricsData`, `HealthStatus`)
also described fields the API never returned — `requests_last_hour` exists in no
backend file — and had zero importers. Deleted.

> **A number that is wrong in the same direction as the thing you are checking is
> the most expensive kind of wrong.** The probe did not just dilute the error rate;
> it diluted it *because* the system was being watched.

## 6. Four of five alert rules could not fire, and the alert list stayed green

**Symptom.** None. The alert list was empty, which is what a healthy service looks
like.

**Cause.** `AlertManager.rules` is one flat namespace of metric keys, but the dict
handed to `check_alerts()` came from `business_metrics.get_metrics()`, and the rule
keys were not in it:

| rule | read | the feed publishes | result |
|---|---|---|---|
| `high_error_rate` | `error_rate` | `failure_rate` | never fires |
| `high_cost` | `daily_cost` | *(nothing — spend is in `cost_summary()`)* | never fires |
| `low_uptime` | `uptime_percent` | `uptime_hours` | never fires |
| `memory_high` | `memory_usage_percent` | *(nothing — `psutil` is not even a dependency)* | never fires |
| `slow_response` | `avg_response_time` | `avg_response_time` | **live** |

`.get(key, 0)` turned each absence into a benign default, `check_alerts()` caught
nothing, and returned `[]`. **A dead rule and a healthy system produced identical
output** — the same failure as finding 2, in a different module.

The one rule that did work was pinned by a test that *hand-fed* it the key the real
feed never sends (`check_alerts({"error_rate": 99, ...})`). The test passed, the
production path never did.

**Fix.** `high_error_rate` now reads `failure_rate` — the field that exists, in the
same units (percent), compared against 5 as intended. The spend and memory rules
are **deleted rather than re-pointed**: spend is already covered by
`llama_agent_llm_cost_usd_total` in `_cost_summary()`, and memory needs a dependency
the project does not have. Re-pointing `high_cost` at `total_cost` would have
alerted on *cumulative lifetime* spend against a threshold written for a daily
figure — a rule that fires forever once crossed, which is its own kind of dead.

The two call sites now build their input through one `_alert_metrics()` helper that
merges the business feed with the Prometheus cost summary, so a rule and its input
are defined in the same place.

**The test that prevents a repeat** reads the published keys out of the **real feed
builder** (`inspect.getsource(business_metrics.get_metrics)`) rather than restating
them, then asserts every rule's key is in that set. Adding a rule for a field nobody
publishes fails in CI instead of silently never firing. Verified by re-adding
`memory_high` and watching it fail with the key named:

```
alert rule 'memory_high' reads ['memory_usage_percent'], which the metrics feed
never publishes (it has ['avg_response_time', ..., 'failure_rate', ...,
'uptime_hours']), so the rule can never fire
```

> **A control that reports "nothing to report" is indistinguishable from a control
> that is broken, unless you test the control.**

---

## 7. The log module was also a second metrics system, rewriting two files per request

**Symptom.** None visible. `AgentLogger` worked.

**Cause.** It kept a running `metrics.json` — `total_queries`, `total_tool_calls`,
`avg_response_time`, `popular_tools`, `last_seen` — and **rewrote it on every
request**, after first re-reading the entire `interactions.json` to append one
line. So every chat did: read the whole interactions file, rewrite it, rewrite
metrics. Under the lock, inside the request handler.

Two of its numbers were never true even in principle:

| field | reality |
|---|---|
| `total_tool_calls` | incremented by looping over `tools_used` — and **every caller passes `[]`** |
| `popular_tools` | same, therefore always `{}` |

And the rest were a **per-process second set of books** for events Prometheus
already counted: `total_queries` vs `record_chat`, `avg_response_time` vs
`RESPONSE_TIME`, `total_errors` vs `ERROR_COUNT`. Same defect as finding 5, one
layer down — two replicas, two totals, a restart erasing both.

**Fix.** The log files stay, because Prometheus counts events and cannot tell you
*what a user said* in the request that broke. The metrics half is deleted:
`metrics.json`, `_load_metrics`, `_save_metrics`, `get_metrics`,
`get_popular_tools`, and the two full-file metrics rewrites. `log_interaction`
now appends and rewrites only the interaction history; `metrics.json` is never
created.

The one number worth keeping — how many exceptions this process logged — is now
**derived from the error log itself** (`get_error_count()`), so the count and the
list can never disagree. `/metrics/json` returns process health plus that count;
`get_agent_health()` prints uptime and the error count instead of query totals
that only one replica knew.

> **A log file is a record. A counter is a claim about a population.** Keeping
> both in one class is how the record and the claim drifted apart.

## 8. A firing alert printed three times and was delivered nowhere

**Symptom.** None. The dashboard said alerts were configured.

**Cause.** Three functions — `slack_notification`, `email_notification`,
`webhook_notification` — each of which only `print`ed, and **all three were
registered on `alert_manager` at import time**. When a rule fired,
`check_alerts` looped the channel list and printed the same message three times
with a different prefix.

This is worse than having no channel. A missing channel is visible. A channel
that exists and prints looks like delivery in every config review, while the
only actual destination — stdout — is already where `check_alerts` prints the
alert on its own.

**Fix.** Deleted, not implemented. Real delivery needs a webhook URL, retries, a
failure mode of its own, and somewhere for *its* failures to go. That is a
feature with an env var and a test. The channel list and
`add_notification_channel()` stay as the hook. The limitation is already stated
in "What is *not* measured": no Alertmanager, nobody is paged.

> **A placeholder registered as a real thing is indistinguishable from a real
> thing, until the day it matters.**

## 9. The SLA monitor reported "met" for a service that had just errored

**Symptom.** A service whose first request failed showed `error rate 0%,
met: true`.

**Cause.** The error rate divided by `len(response_times)` — **successful
requests only** — and returned `0` when that list was empty:

```python
error_rate = (error_count / total_requests * 100) if total_requests > 0 else 0
```

So zero successes rendered as zero percent, which is the *best possible* score,
and the compliance check read it as met. Measured before the fix: one error and
no successes gave `current=0.0, met=True`.

**Fix.** The denominator is attempts — recorded response times **plus** recorded
errors. One error with no successes is now **100%**, correctly not met; one error
in 100 successes is 0.99%, met. No traffic still reads 0% met, which is the one
remaining ambiguity, and it is honest: there is nothing to be non-compliant
about yet.

Found while auditing this module for finding 7, not by any test — the SLA
monitor had a test asserting `overall_status in ("COMPLIANT", "NON-COMPLIANT")`,
which is true of every possible input.

> **A divide-by-zero guard that returns zero chooses "perfect" as its fallback.**
> For a *badness* metric, the neutral element is the worst value, not the best.

---

## The pattern, nine for nine

| # | What the metric said | What was true | Why |
|---|---|---|---|
| 1 | p50 0.5 s | ~3 s | two pods' series blended by a load-balanced scrape |
| 2 | "No data" | 0 failures | a counter that does not exist until it fires |
| 3 | 0.002 s | 2.008 s | the clock stopped before the streamed answer began |
| 4 | "traced cheaply" | ~5 µs/span — **true**, but unmeasured | the claim had no number behind it |
| 5 | "healthy", ~2% errors | two uncoupled counters; the probe inflated the denominator | the same event counted twice, at two layers |
| 6 | no alerts | four of five rules read keys the feed never sends | `.get(key, 0)` made absence look like health |
| 7 | `total_queries: 41` | unknown; each replica had its own | a second counter for an event Prometheus counts, rewritten to disk per request |
| 8 | "alerts configured" | printed three times, delivered nowhere | placeholder channels registered as real |
| 9 | error rate 0%, met | 100% error rate | divide-by-zero guard returns 0 for a *badness* metric |

Findings 1–3 and 5–9 were **read and trusted**. None was caught by looking at the
code — each was caught by comparing the dashboard against a second source: the pods'
raw metrics, the absence of failures in the logs, a stopwatch, a `grep` for call
sites, the list of keys a function actually returns, and — for 7 and 9 — actually
running the code on the input that breaks it.

**Finding 4 is the odd one out and completes the pattern.** It found nothing
broken. It was caught by asking which claims had no second source at all — and then
becoming one.

**A dashboard is a measuring instrument, and it needs the same suspicion as a test
suite.** The eval part of this project has 19 findings about the measuring stick.
This part has nine about the dashboard. They are the same kind of bug in different
clothes.

Findings 5 and 6 are the sharpest form of it: in both, the broken instrument and the
healthy system produce **the same output**. A missing metric reads as "no data"
(finding 2). A rule with no input reads as "no alerts" (finding 6). A diluted error
rate reads as "healthy" (finding 5). **The failure mode of a monitoring system is
silence, and silence is what success looks like.**

---

## What is *not* measured

Stated so it is not discovered by a reader first:

- ~~**No tracing overhead measurement.**~~ **Measured 2026-11-13** — finding 4.
  ~5 µs per span, ~0.065 ms for a 13-span run, 0.003% of a 2 s answer. A dead
  collector costs nothing on the request path: OTel drops spans rather than
  blocking.
- **No alert delivery.** Prometheus alert rules exist; no Alertmanager is deployed.
  The rules evaluate and nothing is sent anywhere. See [`SLO.md`](SLO.md) for the
  targets and README "Known limits" for the gap. Note that this is a *second*,
  independent reason an alert would not reach anyone: finding 6 removed the rules
  that could not fire, but the two that can still only print a line at startup.
- **`/chat/stream` skips the in-app business metrics** that `/chat` records, though
  request metrics and Phoenix traces do cover it.
- **The judge is not traced as its own span tree** — eval judge calls appear as
  `GroqLLM.chat` with `component="judge"`, but not as a separate eval trace.
