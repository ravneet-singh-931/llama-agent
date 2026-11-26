# The Defence Sheet

Everything in this repo that a senior LLMOps / AI engineer will ask about, what the
honest answer is, and whether it is post-worthy.

**Rule for this document:** every number was measured in the repo, not quoted from
another doc. Where a document and the code disagree, the code wins and the
disagreement is listed at the end.

---

## Part 0 — The 30-second answer

> A multi-agent RAG platform with a **measurement layer that is treated as part of
> the product**: 60-case eval suite with coverage gating, a 17-metric Prometheus
> surface, 27 Grafana panels, 17 alert rules, OTLP tracing into Phoenix, SLOs with
> error-budget burn, and 94 documented findings including the ones that were wrong.

If someone only gives you 30 seconds, that is the sentence. Everything below is the
evidence for each clause.

---

## Part 1 — Measured scale (all verified)

| Thing | Number |
|---|---|
| Python files | **121** |
| Python lines | **44,292** |
| Markdown documents | **53** |
| Test files | **68** |
| Test functions | **1,166** |
| API endpoints | **35** |
| Kubernetes manifests | **30** |
| Kubernetes objects | **87** |
| Prometheus metric objects | **17** (all wired) |
| Prometheus alert rules | **17** in **6** groups |
| Grafana panels | **27** |
| Eval cases | **60** |
| CI jobs | **13** across **964** lines |
| Documented findings | **94** |
| Coverage (CI gate) | **87%** vs gate **74%** |

**Defend this:** "44,292 lines" is `find | xargs cat | wc -l`. Say it is Python only.
If someone asks "how much of it is `rag_optimizations.py`" — 1,704 lines, the largest
module, and it had **zero findings recorded** until the RAG audit.

---

## Part 2 — The observability stack (what you asked about)

### 2.1 Metrics — Prometheus

**Endpoint:** `GET /metrics` on the API, scraped by Prometheus.

**17 metric objects, all with live call sites** (I checked each one for
`.labels()/.inc()/.observe()`):

| Metric | Type | What it answers |
|---|---|---|
| `llama_agent_requests_total` | Counter | volume, by method + endpoint |
| `llama_agent_http_responses_total` | Counter | status-code distribution |
| `llama_agent_response_time_seconds` | Histogram | HTTP latency |
| `llama_agent_active_connections` | Gauge | concurrency |
| `llama_agent_errors_total` | Counter | unhandled exceptions by type |
| `llama_agent_start_time` | Gauge | uptime |
| `llama_agent_llm_tokens_total` | Counter | **input/output tokens by model + component** |
| `llama_agent_llm_cost_usd_total` | Counter | **spend, from a pricing table** |
| `llama_agent_llm_cost_usd_by_user_total` | Counter | **per-user spend attribution** |
| `llama_agent_llm_call_seconds` | Histogram | provider latency |
| `llama_agent_llm_guard_events_total` | Counter | **guard actions: compacted / downgraded / waited / too_large_retry** |
| `llama_agent_llm_tokens_trimmed_total` | Counter | tokens saved by context compaction |
| `llama_agent_grounding_claims_total` | Counter | supported vs unsupported figures |
| `llama_agent_rag_crag_verdicts_total` | Counter | CRAG: correct / incorrect / ambiguous |
| `llama_agent_chat_seconds` | Histogram | **user-visible latency by route + outcome** |
| `llama_agent_chat_first_token_seconds` | Histogram | time to first answer text |
| `llama_agent_agent_run_seconds` | Histogram | agent run duration by agent |

**The four that matter and why:**

1. **`llm_tokens_total{model,kind,component}`** — the `component` label is what lets
   you answer "is the eval harness or the chat path burning the quota?" Almost nobody
   labels by component.
2. **`llm_cost_usd_total`** — real USD, computed from a pricing table, not a guess.
   `cost_by_model`, `cost_by_component`, `cost_by_user` all derive from it.
3. **`llm_guard_events_total{model,action}`** — this is the one to talk about. It
   records **what the guard did**: compacted, downgraded, waited, too_large_retry.
   A guard you cannot see firing is a guard you cannot tune.
4. **`chat_seconds{route,outcome}`** — `outcome` distinguishes `ok` / `failed` /
   `error` / `cancelled`, and `failed` is not an exception — it is a **semantically
   empty answer** ("Stream timed out", "The AI model is unavailable right now"). That
   distinction is the difference between "the server is up" and "the user got an
   answer".

**Defend this:** the `outcome` classifier. Ask-back: *"how do you know a 200 OK was
actually a good answer?"* Answer: `chat_outcome()` inspects the text.

### 2.2 Scrape topology — the detail most people miss

`k8s/prometheus.yaml` does **not** scrape the load-balanced Service. It uses
`dns_sd_configs` against a **headless** service:

```yaml
dns_sd_configs:
  - names: ["llama-agent-api-headless.llama-agent.svc.cluster.local"]
    type: A
    port: 8000
    refresh_interval: 30s
```

Comment in the file: *"One target per API Pod (not the load-balanced Service, which
would hit a random replica per scrape)."*

**Why this is a highlight:** this is the fix for observability finding 1 — *p50 said
0.5 s when the truth was ~3 s, because two pods' series were blended by a
load-balanced scrape.* That is a real, subtle Prometheus bug and almost nobody
catches it. **Post-worthy on its own.**

### 2.3 Alerts — 17 rules in 6 groups

| Group | What it covers |
|---|---|
| availability | `ApiDown` (critical) — `up == 0 or absent(up)` |
| cost | `HighLLMSpend` — 24 h spend > $10 |
| errors | `HighErrorRate`, `ServerErrors` |
| latency | `HighResponseTime` p95 > 5 s, `VeryHighResponseTime` p99 > 10 s |
| SLO burn | **`ChatAvailabilitySLOBurn`, `ChatQuickLatencySLOBurn`, `ChatAgentLatencySLOBurn`** |
| resources | CPU, memory |

**The elite part is the SLO burn alerts.** They are not "error rate > X". They are
multi-window burn-rate:

```
(rate1h > 14.4 * 0.05 AND rate5m > 14.4 * 0.05 AND traffic >= 5)
OR
(rate6h > 6 * 0.05 AND rate30m > 6 * 0.05 AND traffic >= 5)
```

14.4× and 6× are the **standard Google SRE multi-window burn thresholds** for a 30-day
budget. Two windows must both be bad before paging, which suppresses single-spike
flaps. And there is a **traffic floor** (`>= 5`) so a quiet hour cannot burn budget
and page you.

**Defend this:** "why 14.4 and 6?" — they come from a 1-hour window consuming 2% and
a 6-hour window consuming 5% of a 30-day budget. Say that. It is the signal that you
read the SRE workbook and not just a blog post.

**Also:** `ApiDown` uses `absent(up{...})`, not just `up == 0`. A target that
disappears from discovery does not go to `up=0` — it goes to **no series at all**.
Without `absent()`, a vanished target is silent. That is a senior-level detail.

### 2.4 Tracing — Phoenix (Arize) over OTLP

Wired in the FastAPI lifespan (`api.py:651`):

```python
register_phoenix(endpoint="http://phoenix:6006/v1/traces",
                 auto_instrument=True, batch=True)
FastAPIInstrumentor.instrument_app(app)
app.middleware_stack = app.build_middleware_stack()   # ← the elite line
```

**The line to talk about is the last one.** From the code comment:

> *uvicorn invokes the app with a `lifespan` ASGI scope **before** the lifespan body
> runs, which makes Starlette build AND cache the middleware stack (without the OTel
> middleware) ahead of time. `instrument_app` only patches `build_middleware_stack`,
> so the cached stack must be rebuilt explicitly or requests will never be traced.*

That is a genuine, obscure, hard-won debugging detail. Instrumentation "succeeds",
`register()` returns, no error — and **zero spans appear**, because the middleware
stack was already cached. Most people who "set up Phoenix" have this bug and never
notice.

**Post that.** It is specific, correct, non-obvious, and immediately useful to
anyone else instrumenting FastAPI in a lifespan.

`batch=True` uses a BatchSpanProcessor — correct for production; the default
SimpleSpanProcessor blocks on every span.

**Honest boundary:** Phoenix runs in-cluster, and `PhoenixTracer` in
`advanced_rag.py:511` defaults to `phoenix_enabled=False`. So tracing is **deployed
and configured**, and the RAG-layer tracer is **opt-in**. Say that rather than
claiming every span is traced.

### 2.5 Logs — Loki + Promtail

`config/loki/loki-config.yml`, `config/promtail/promtail-config.yml`,
`k8s/loki.yaml`, `k8s/promtail.yaml` (DaemonSet, one pod per node).
54 `logger.*` call sites in app code.

### 2.6 Node + cluster metrics

`node-exporter` (DaemonSet), `metrics-server`, HPA reading `3%/70%` CPU,
`ResourceQuota`, `LimitRange`, **5 PodDisruptionBudgets**.

### 2.7 Dashboards — Grafana, 27 panels

`config/grafana/dashboards/llama-agent.json`, title *"Llama Agent - Operations
Dashboard"*, auto-provisioned via `provisioning/`.

Four of the 27 are the LLMOps ones: **tokens/min vs free-tier ITPM limit**,
**guard actions**, **tokens trimmed by compaction**, **estimated spend (cumulative) +
spend by component**. Plus 3 SLO stat panels and an **error-budget burn rate**
panel.

**Defend this:** "you have 27 panels — which one would you keep if you could only keep
one?" Answer: `llm_tokens_total` vs the ITPM limit. It is the only panel that
predicts an outage instead of reporting one.

### 2.8 SLOs

`docs/SLO.md`. Targets over a 30-day window:

| SLO | Target | Route |
|---|---|---|
| Availability | 95% | all chats except `cancelled` |
| Quick latency | 95% within 10 s | command / document / general |
| Agent latency | 95% within 90 s | single specialist |
| `@team` latency | **none — deliberately** | 84–247 s, quota-bound not app-bound |

**The `@team` row is the mature move:** declaring an SLO you cannot meet is worse than
declaring none and saying why.

### 2.9 The second alerting system (know this exists)

There are **two** alert paths:

1. Prometheus `alerts.yml` — 17 rules, evaluated by Prometheus/Alertmanager.
2. **`modules/advanced_monitoring.py` → `AlertManager`** — an in-process rule engine
   with `check_alerts()`, `_should_alert()` dedup/cooldown, `alert_history`, and
   pluggable `notification_channels`. Called from `api.py:631` and `api.py:1855`.

A senior will ask *"why two?"* The honest answer: the in-process one predates the
Prometheus rules and serves the `/monitoring/dashboard` endpoint; the Prometheus one
is the real paging path. **If you want a good answer, be the one who says this out
loud** rather than letting them find it.

---

## Part 3 — Every layer, and the one thing to say about each

### 3.1 API — 35 endpoints

FastAPI. Auth (`/auth/login|register|refresh|logout|me`), chat
(`/chat`, `/chat/stream` SSE), documents (`upload`, `ask`, `read`, `search`,
`documents`), memory (`remember`, `recall`, `facts`, `memory`), conversations CRUD,
MCP (`/mcp/tools`, `/mcp/status`), metrics (`/metrics`, `/metrics/json`,
`/monitoring/dashboard`, `/agents/stats`), health, tools, WebSocket
(`/ws/ticket`, `/ws/{user_id}`).

**The one thing:** the **WebSocket credential travels in the URL** (`/ws/ticket`
issues a single-use ticket). Browsers' `new WebSocket(url, protocols)` has no header
bag, so the ticket is the workaround — and it is issued, single-use, and expiring.
That is the correct answer to a known-bad constraint, and you should say the
constraint out loud.

### 3.2 Auth & security — 5 modules, 2,265 lines

`auth.py` (780) — `PasswordManager`, `JWTManager`, `TokenGenerationStore`,
`UserManager`. Refresh-token rotation with generation tracking (the store invalidates
replayed generations).
`security_hardening.py` (655) — `BruteForceProtection`, `AuditLogger`, `CSRFProtection`.
`security.py` (443) — rate limiter.
`pii.py` (154) — `StreamRedactor`, redacts **mid-stream**, not after.
`secrets.py` (85) — no hardcoded secrets; `ExternalSecret` + `ClusterSecretStore`.

**The one thing:** `StreamRedactor`. Redacting PII after you have already streamed it
to the client is theatre. Redacting inside the generator is the real thing.

### 3.3 Multi-agent — `multi_agent.py`

5 specialists — `research`, `coder`, `writer`, `analyzer`, `planner` — routed by
embedding similarity against prototype utterances, with a **DAG planner** that runs
dependent steps, a **generator–critic** loop, and an optional **Reflexion** pass.

`mcp_agents_server.py` exposes the same agents over MCP (`research_agent`,
`coder_agent`, `writer_agent`, `analyzer_agent`, `collaborate`).

**The one thing:** the critic is a **separate model tier** from the generator
(`critic_model = resolve_model("fast") if self.model == resolve_model("strong")`).
Self-critique with the same model at the same temperature is close to useless; making
it a cheaper, different tier is a deliberate design choice.

### 3.4 RAG — 3,565 lines across 3 modules

`rag_optimizations.py` (1,704): `HybridSearcher` (BM25 + vector),
`CrossEncoderReranker`, `OnnxCrossEncoder`, `SemanticChunker`, `SemanticCache`,
`CRAGRetriever`, `HyDERetriever`, `QueryDecomposer`, `ContextualRetriever`,
`ConversationalRetriever`, `OptimizedRAG`.
`advanced_rag.py` (854): `AdvancedRAG`, `PhoenixTracer`, `ReRanker`,
`RouterQueryEngine`, `SubQuestionEngine`.
`vector_store.py` (448): Chroma + `KeywordStore`.

**The one thing:** the published retrieval table with **three negative results**. See
Part 5 #6.

### 3.5 Memory — `memory.py` (666)

`ConversationMemory` (Redis or in-process) + `FactMemory` (Postgres). `/remember`,
`/recall`, `/facts`. Ownership enforced at **two layers** — `api.py` passes `user_id`
*and* the store re-checks `conv["user_id"] != user_id`.

**The one thing:** you proved **which layer** catches a bad call by sabotaging each
independently. That is why `COVERAGE_API.md` can say "two layers, both proven."

### 3.6 LLM traffic — `llm_guard.py` (670) + `model_tiers.py` (157)

Worst-case path when about to exceed the TPM limit: **predict** (3.3 chars/token) →
**downgrade** to the fast model → **sleep** up to 20 s → **send anyway**. No refusal
path in the live app.

Exports: `llm_guard_events_total{action}` for each of those four behaviours.

**The one thing:** *"there is no refusal path"* — say it before they find it. The
`CaseBudgetExceeded` refusal exists only in the eval harness.

### 3.7 Context management

`context_budget.py` (126), `prompt_rules.py` (56), `citations.py` (117),
`grounding.py` (256), `doc_scope.py` (219), `doc_identity.py` (76).

**The one thing:** trimming cuts the **middle**, not the end — because an end-cut can
delete the **closing delimiter of a `<<UNTRUSTED_DATA>>` block**, silently promoting
untrusted text into trusted position. That is a security argument for a token
optimisation, and it is the best-argued decision in the codebase.

### 3.8 Evals — 4 modules

`run.py` (1,428) · `checks.py` (648) · `gate.py` (244) · `judge.py` (142) ·
`rag_eval.py` · `recording.py` (476) · `dataset.jsonl` (60 cases).

Coverage **87%** against a **74%** gate. `python -m evals.run --replay <dir>` for
deterministic replay.

### 3.9 Kubernetes — 30 manifests, 87 objects

12 Deployments, 16 Services, 8 Ingresses, 6 NetworkPolicies, 7 PVCs, 5 PDBs,
2 HPAs, 3 ExternalSecrets, 1 CronJob (Postgres backup), 2 DaemonSets (node-exporter,
promtail), TLS via cert-manager (2 ClusterIssuers), SSO via oauth2-proxy, rate
limiting + ModSecurity/OWASP CRS on every ingress.

### 3.10 CI — 13 jobs, 964 lines

`test` → `security` (`pip-audit`, Trivy) → `security-sast` (deviation scan) → `lint`
→ `frontend` → `integration` → `docker` → `deploy-staging` → `deploy-production` →
`notify`.

**The one thing:** `notify` **opens a GitHub issue on every failure.** The failures
become a backlog instead of scrollback. That is a real operational choice.

### 3.11 Frontend — Next.js 16

Pages: chat, agents, documents, dashboard, settings, login. Components include
`AgentCollaborationDashboard`, `ReasoningView` (streams the agent's reasoning),
`DsaControlPanel`, `EnhancedChatInterface`.

### 3.12 Docs — 53 files

Findings: EVAL (1,210) · SECURITY (847) · DARK_MODULES (500) · MY_ERRORS (519) ·
OBSERVABILITY (451) · RAG (372) · KUBERNETES_CHECKS (366) · AGENT (271) ·
CI_FINDINGS (192) · CODE_QUALITY · COVERAGE_API.
Orientation: `AUDIT_OVERVIEW`, `REQUEST_FLOW`, `FILE_MAP`, `GLOSSARY`, `SLO`,
`ROADMAP`, `CASE_STUDIES`, `RUNNING_TESTS`, `MIGRATIONS`, `E2E_JOURNEY`.
**Plus `docs/posts/` with two real charts** and `docs/screenshots/` with four.

---

## Part 4 — The 50 things, ranked by "would a senior remember you for this"

### Tier 1 — the five that carry the interview (say these unprompted)

1. **35% → 72.4%.** The nightly published 35%. 21 of 29 cases that actually ran passed
   = 72.4%. The rule "a case that cannot be graded is not passed" made an outage look
   like a quality regression. `EVAL_FINDINGS.md:143`.
2. **Coverage is a column, not a footnote.** 53/60 graded = 88.7% — *and it is not the
   best run.* A later run graded 57/60 at 76.7%. **"Any headline rate is a rate over
   whatever survived the quota."**
3. **500,000 → 195,500 tokens per run** from one guard at the right scope. A case that
   cannot finish becomes `budget`, reported **not graded** — refused, not silently
   spent.
4. **The deadline bug: 89 minutes of wall clock for 17.3 seconds of work.**
   `f.result(timeout=150)` inside a loop ⇒ the Nth future waits 150 s *plus* everything
   already waited. Three layers: loop timeout, `shutdown(wait=True)`, and a budget
   guard that only runs *between* DAG levels.
5. **Nine for nine: a monitoring control that reported health while the system was
   broken.** 0.002 s vs 2.008 s (clock stopped before the stream) · "no data" vs 0
   failures · error rate 0% vs 100% (divide-by-zero guard on a *badness* metric) ·
   "alerts configured" delivered nowhere.

### Tier 2 — the ten that prove depth

6. **The negative-results retrieval table.** Hybrid adds nothing measurable (worse at
   top-3, 31 vs 32). The reranker does not change *whether* the answer is in the top 6
   (35/38 in all three setups) — only its rank. MRR 0.79 → 0.86, p50 0.29 s → 1.25 s.
   *"Publishing the negative results is what makes the positive one credible."*
7. **Memory: 1,630 MB → 481/627 MB, quality unchanged.** Same hit@1/3/6 and MRR before
   and after a 3× memory cut. The unchanged row is the whole argument.
8. **The 17-rule SLO burn alerts** — 14.4× / 6× multi-window, with a traffic floor.
9. **`absent(up{...})` on `ApiDown`** — a vanished target is silent without it.
10. **Per-pod scraping via `dns_sd_configs` on a headless service** — the fix for
    two pods blending a p50.
11. **The Phoenix `middleware_stack` rebuild** — instrumentation succeeds, returns no
    error, and produces zero spans.
12. **Eight independent retry policies on one request**, none aware of the others; and
    "retry on the fast tier" is a **literal**, so the retry hits the same model again.
13. **Rate limiting counts tokens returned, never tokens in flight** — 6 concurrent
    1,642-token calls all admitted against a 7,000 cap ⇒ true window 9,852.
14. **Fail-open by construction:** an unbound owner is treated as "no scoping", not
    "no access" — and the test suite is *structurally unable* to catch it, because
    every fixture is added with `source=` and no owner.
15. **`ast.parse` proves well-formedness, not correctness.** A `merge()` wrong on 2 of
    its own 3 doctests passed the code check.

### Tier 3 — the fifteen that show you build, not just audit

16. 5 specialists + DAG planner + generator–critic with a **separate critic tier** + Reflexion.
17. MCP server exposing the same agents over the Model Context Protocol.
18. SSE streaming with **semantic outcome classification** (`ok`/`failed`/`error`/`cancelled`).
19. **Streaming PII redaction** inside the generator.
20. WebSocket **single-use ticket** because browser WS has no header bag.
21. **Mid-truncation** to preserve the assistant's conclusion *and* the untrusted-data closing delimiter.
22. Per-case **token budget that refuses** rather than overspending.
23. **CI exit 3** for ungraded runs + `::warning title=No result::`.
24. **Record/replay with a hard miss** — `RecordingMiss` raises; `produce()` is never invoked; first write wins.
25. **Coverage gate at 74%** with actual coverage at 87%.
26. **Sabotage-tested tests** — break the code, confirm the test fails.
27. **17 Prometheus metrics, all verified wired** (no decorative metric objects).
28. **27 Grafana panels**, provisioned as code, not clicked.
29. **Loki + Promtail + node-exporter + metrics-server** as a full stack.
30. **5 PodDisruptionBudgets + ResourceQuota + LimitRange** — capacity is declared.

### Tier 4 — the twenty that show judgement

31. `MY_ERRORS.md` — **25 entries** of the author being wrong, kept deliberately.
32. `CASE_STUDIES.md` through-lines table.
33. **The refusal to publish a rate below 90% coverage.**
34. Declaring **no `@team` SLO** and saying why.
35. **`@team` latency left open** with "needs profiling before a fix is worth guessing at."
36. **`llm_guard._tier_of` left open** — proven latent, both budgets default equal, "not worth churn on working code."
37. The route split (`api.py`) **stopped on purpose** after five steps.
38. Three known problems published before a reader finds them.
39. **"Cloud deploy skipped"** stated in the README instead of hidden.
40. Single-node kind cluster declared as a limit, not dressed up as HA.
41. **SLO targets justified with measured numbers** (general chat 0.7 s avg, RAG p95 3.0 s, research 40 s avg).
42. **Judge never validated against human labels** — named as "the biggest known weakness in the measurement."
43. **Judge is the same model family as the writer** — known, and `EVAL_JUDGE_MODEL` exists to change it.
44. **60-case suite called a smoke test, not a benchmark** — "a category with 4 cases moves 25 points per case."
45. **Sabotage that didn't change the code proved nothing** — a `text.replace(..., 1)` patched the wrong one of five occurrences.
46. **Two ownership layers, each proven independently.**
47. **A correction published for a correction** — the "retry re-sends" claim was wrong for one of the two sites and was corrected in place.
48. **A row that disagreed with itself for two commits** (`48/60 (60.0%)`) — found and published.
49. **`notify` opens an issue per failure** so failures become a backlog.
50. **`tests/test_boot_without_key.py`** — boots the app in a subprocess with the key removed, because "no key" is a configuration nobody tested.

---

## Part 5 — What a senior will attack, and the honest answer

| Attack | Your answer |
|---|---|
| "Single-node kind cluster — not real k8s ops" | Correct. The manifests are production-shaped; the cluster is not. Say it first. |
| "Free-tier Groq — not a real cost model" | The *plumbing* is real: per-case budgets, cost-by-component, refusal path. The prices are small. |
| "The judge is an LLM, unvalidated" | Agreed, and it is written in the README as the biggest weakness. That is why the judge **can only subtract** and everything else is a rule check. |
| "60 cases is tiny" | Yes — it is a smoke test, and per-category percentages are directional. The coverage gate exists because of that. |
| "Phoenix defaults off in the RAG layer" | True. Tracing is configured and deployed; `PhoenixTracer` is opt-in at `advanced_rag.py:511`. |
| "Two alerting systems — which is real?" | Prometheus. The in-process one predates it and feeds `/monitoring/dashboard`. |
| "Who wrote the 94 findings — you or the repo?" | **The docs predate this work.** `git diff beb378f..HEAD -- docs/` is empty. Frame as **re-verified and re-measured**, with ~20 new ones from the sessions. |

---

## Part 6 — The known inconsistencies (fix or disclose)

| Document says | Repo actually has |
|---|---|
| `CASE_STUDIES.md`: "all seven parts are finished" | body prose expands **4** of 7 — RAG, Security, Dark modules are table rows only |
| "The four through-lines" | the table has **6** rows |
| `CASE_STUDIES.md` / `ROADMAP.md`: **39** bugs | README table has **61** rows |
| `CASE_STUDIES.md`: Observability **4** findings | `OBSERVABILITY_FINDINGS.md` has **9** |
| `EVAL_FINDINGS.md`: **19** findings | 15 numbered `FINDING` headings, 20 claimed |
| `STUDY_PLAN.md`: `judge.py` is 244 lines | **142** |

**Say:** "the index docs drift from the findings docs; the findings docs are the source
of truth." Which is itself an instance of the project's own thesis — *a summary is a
claim, and claims drift from the artefact.*

---

## Part 7 — If you post, post these five (in this order)

1. **"A monitoring control that cannot fail is indistinguishable from one that
   passed."** — the 9-for-9 table. Strongest single idea in the repo.
2. **"My eval suite published 35%. The real number was 72.4%."** — with
   `docs/posts/img/number_trail_chart.png`, which already exists as a chart.
3. **"Instrumentation returned no error and produced zero spans."** — the Phoenix
   `middleware_stack` teardown. Immediately useful to other engineers.
4. **"We cut memory 3× and changed nothing else."** — the unchanged hit@1/3/6/MRR row.
5. **"Our reranker improved a metric that doesn't affect the answer."** — the negative
   results table.

Each is one idea, one number, one artefact. None of them claims discovery. All five
are checkable in the repo.

---

## The one figure that has a chart already

`docs/posts/img/number_trail_chart.png` — seven real runs, same harness. Blue = pass
rate over 60 cases; green = rate over cases that actually ran; the shaded gap is what
the daily quota ate before grading. That gap **is** the thesis of the whole project.
