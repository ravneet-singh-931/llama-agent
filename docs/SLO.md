# Chat SLOs

Service level objectives for `/chat/stream`, the endpoint the Next.js chat uses. Added in
v3.5.3. Rules: `k8s/prometheus.yaml` (ConfigMap `alerts.yml`, group `llama_agent_slo`) and the
same group in `config/prometheus/alerts.yml` (Docker Compose). Panels: the "SLO" row at the
bottom of the Grafana dashboard `llama-agent-ops`.

## What is measured

`llama_agent_chat_seconds{route,outcome}`: one observation per chat message, from the moment
the API receives it to the end of the answer stream. That is the time the user waits.

| Label | Values |
|---|---|
| `route` | `command` (remember / recall / name / stored fact), `document` (questions about uploaded files and their follow-ups), `general` (general chat agent), `agent` (one specialist agent: research, coder, writer, planner, analyzer), `team` (`@team`) |
| `outcome` | `ok`; `failed` (the stream was empty or was one of the app's failure notices, e.g. "could not complete this task", "Stream timed out", "The AI model is unavailable right now"); `error` (exception); `cancelled` (the client went away) |

`llama_agent_chat_first_token_seconds{route}`: time to the first answer text (reasoning, tool and
config events don't count; for `@team`, the first agent's section heading). Shown on a panel,
no SLO.

## Targets (30-day window)

| SLO | Target | SLI | Why this number |
|---|---|---|---|
| Availability | 95% | `outcome="ok"` / all chats except `cancelled` | In the 30-case agent eval every case ended with an answer (the 4 failures were unsupported figures, not failure notices); runs hit by free-tier rate-limit notices were re-run. In the 60-case nightly, 1 case was not graded because the *judge* call hit `429 Request too large` — an instrument failure, which is exactly the provider flakiness this budget exists for. 95% leaves room for it |
| Quick latency | 95% within 10 s | `command`, `document`, `general` with `outcome="ok"` | Measured: general chat 0.7 s average, RAG answers p95 3.0 s |
| Agent latency | 95% within 90 s | `agent` with `outcome="ok"` | Measured: research average 40 s, slowest case 72 s; coding 17 s |
| `@team` latency | none | | 84 – 247 s on the free tier, limited by the 7,000 input tokens/min cap, not by the app. `@team` still counts in availability |

Error budget = 5% of chats in 30 days. Prometheus keeps 30 days of data
(`--storage.tsdb.retention.time=30d`), so the 30-day panels are exact.

## Alerts (multi-window burn rate)

Burn rate = observed bad share / 5%. A burn rate of 1 uses the whole budget in exactly 30 days.
Each SLO has one alert (`ChatAvailabilitySLOBurn`, `ChatQuickLatencySLOBurn`,
`ChatAgentLatencySLOBurn`), firing when either holds:

- **fast burn:** burn rate > 14.4 over 1 h **and** over 5 m (2% of the monthly budget in an
  hour), or
- **slow burn:** burn rate > 6 over 6 h **and** over 30 m (5% of the budget in 6 hours),

and at least 5 such chats happened in the long window. The short window makes the alert stop
soon after the problem stops; the traffic minimum stops 1 bad chat out of 2 from firing it in a
low-traffic lab. The ratios are recording rules `llama_agent:chat_<sli>:ratio_rate<window>`
(sli = `unanswered`, `quick_slow`, `agent_slow`; window = 5m, 30m, 1h, 6h), summed across pods.

The older HTTP alerts `HighResponseTime` (p95 > 5 s) and `VeryHighResponseTime` (p99 > 10 s) now
skip `/chat` and `/chat/stream`: a 60 s agent answer is normal there and is judged by the SLOs
above.

## The bug this fixed

Before v3.5.3 the HTTP middleware stopped its timer when the response **headers** were ready.
For a streamed answer that is about 0.1 s, so a 60 s agent answer was recorded as fast:
"Avg Response Time", the response-time histogram and the latency alerts never saw how long a
chat took. Now the timer stops when the last byte of the body is sent
(`tests/test_chat_slo_metrics.py::test_streamed_response_time_is_measured_to_the_end_of_the_body`;
on the old code a 2.0 s stream was recorded as 0.002 s, now 2.008 s).

## Known limits

- `failed` is detected from the answer text. A real answer that quotes one of the failure notices
  word for word would count as failed (unlikely, but possible).
- Only `/chat/stream` is covered. The non-streaming `/chat` endpoint (not used by the UI) has only
  the HTTP metrics.
- Eval runs (`python -m evals.run` inside a pod) run in their own process and don't reach these
  metrics; the SLO panels show real chat traffic only.
- No Alertmanager: alerts show in Prometheus (`/alerts`) and Grafana, nobody is paged.
