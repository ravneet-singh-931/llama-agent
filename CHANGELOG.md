# Changelog

All notable changes to Llama Agent will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **The scheduled nightly records, and asks whether it can finish first.** The
  cron runs `scope=record` (all 60 cases, plus a replayable artefact) instead
  of `full`. Both run the whole suite; only one leaves something a stranger can
  re-run. The guard that asserts the nightly is not silently shrunk to `smoke`
  now reads the scope default and what it selects, rather than grepping for the
  literal `SCOPE="full"` — it fired on this change, which was correct
  behaviour on the wrong axis.

- **`run.py --preflight`, before a token is spent.** A full run needs ~195,500
  input tokens after the per-case budget and the cap is 200,000 **per model,
  per day** — 2.2% headroom. That number was being discovered at case 55.
  The estimate is category-aware (7 @team × 9,000 + 53 × 2,500), because a
  flat average reproduces the pre-budget figure and predicts a failure that no
  longer happens. A run that cannot fit now exits 4 with the arithmetic and
  three options; `--force` overrides. Under 10% headroom it prints a warning
  rather than letting a green "fits" imply the run is comfortable.

- **A partial recording is usable.** If the run dies at case 55 the previous
  behaviour left `llm.jsonl` with content and **no manifest** — and without the
  recorded date every reply in it is unreplayable, because the date is part of
  every system prompt. The manifest is now written as soon as the date is
  stamped, carries `"complete": false`, and the upload step runs on `always()`
  so a capped night still produces an artefact.

- **`docs/RAG_FINDINGS.md` — the largest module finally has findings.** `rag_optimizations.py`
  is 1,703 lines, the biggest file in the project, and had zero recorded findings while
  holding the best before/after numbers in the repo. Five findings, each traced to code:

  * the "cross-encoder reranker" was keyword overlap — a three-deep backend fallback where
    the last entry always succeeds, so PyTorch never being installed was invisible;
  * Corrective RAG never rejected anything: a 5-token verifier budget spent on hidden
    reasoning returned an empty string, which parsed as "supported". The same 100-token
    bug emptied the contextual chunk summaries for 6 of 7 documents;
  * the semantic cache served answers to different questions at cosine distance 0.3 —
    "Team plan cost" got the Team plan rate limit. Now 0.05 plus matching numbers;
  * a 65 KB upload returned 502 because a *question asked earlier* made the reranker
    reserve ~940 MB that onnxruntime never gave back.

  It also publishes the retrieval table with its three negative results: hybrid search
  adds nothing measurable over vector search on this corpus, and the reranker does not
  change whether the right passage is in the top 6 — only its position.

  Four of the five findings are features that reported success while doing nothing.
  That is the eval part's lesson moved down a layer: the system was confident and wrong,
  not broken and loud.

- **v3.6.0 — the RAG gate could actually be blind, and now it says so.** `main.yml`
  gated retrieval on `hit@6` alone, and `hit@6` is 35/38 in all three setups in the
  README table — so the gate passed identically with the reranker installed and with
  it silently fallen back to keyword overlap, reproducing R1 one level up in CI.
  Three floors now: `hit@6 >= 0.89` (reachability), `hit@1 >= 0.75` and
  `MRR >= 0.82` (ranking, the numbers the reranker moves), plus `--require-reranker`
  failing the run unless the live backend is ONNX. `gate_sensitivity()` re-runs the
  gate for each setup as if it were deployed and the job summary prints which setups
  would pass — a gate that is blind now reports its own blindness in every run.
  `evals/rag_eval.py`, `.github/workflows/main.yml`, `tests/test_rag_eval.py`
  (**29 passed**, six new, one of which reproduces the old gate's blindness).

- **`docs/CASE_STUDIES.md` — the index the project was missing.** Four parts, four
  findings documents, one headline number each, and the through-line that connects
  them. The README now links it first, because until now a visitor had to find the
  interesting material by scrolling past 400 lines of bug table.

- **`docs/AGENT_FINDINGS.md`** — the agents part, four findings fixed and five stated
  as open. Every one is "the agent was answering without the thing it needed": a
  critic without the sources, a synthesis without a no-repeat instruction, a
  tool-using step whose rescue path wrapped the call and not the stream, and a DAG
  level with a per-future timeout instead of a per-level deadline. `code-intervals`
  at judge 1.25 (run 43279398224) is recorded as the first open item rather than
  quietly omitted.

- **`docs/OBSERVABILITY_FINDINGS.md`** — three findings where a metric reported a true
  number about the wrong thing: p50 0.5 s on the dashboard against ~3 s in the pods
  (a load-balanced scrape interleaved two pods' series), "No data" rendered
  identically to "zero failures", and a 2.0 s stream recorded as 0.002 s because the
  timer stopped when the headers were ready. Includes a "what is not measured"
  section: tracing overhead has never been measured, and there is no Alertmanager.

- **`docs/KUBERNETES_CHECKS.md`** gains a front matter tying it to the other two
  documents, and names the through-line for that part: the cluster worked exactly as
  configured, and the configuration was wrong.

- **Eval record/replay (`modules/recording.py`).** A full 60-case run needs
  ~314,330 input tokens and the free tier allows 200,000 a day, so the suite
  could not be reproduced by anyone who did not have the CI logs. Every model
  reply and every tool result is now recorded once and replayed for zero
  tokens:

      python evals/run.py --record evals/recordings/<run>   # once
      python evals/run.py --replay evals/recordings/<run>   # free, no API key

  Three things had to be captured, not one. The model replies (both the
  streaming and non-streaming paths), the tool results - a replay that called
  Tavily would spend quota and return a different answer - and **the date**.
  `today_str()` is injected into every system prompt, so a recording made on
  Tuesday replayed on Wednesday would hash differently and miss every key;
  the date is stored in `manifest.json` and pinned during replay.

  Two guards on the guard, because a replay that silently falls back to the
  provider is worse than no replay at all: a missing key raises
  `RecordingMiss` rather than hitting the network, and a replay runs with no
  API key at all - refusing to import without one would defeat the purpose of
  ever having a recording. `EVAL_RECORD_MODE=off` is the default and a pure
  no-op, so the nightly is unchanged.

  The LLM guard is skipped during replay on purpose: `prepare_call()` picks a
  model and rewrites the messages from the **live** rate-limit window, so two
  replays of one recording would hash differently. The guard protects quota;
  a replay spends none.

### Added
- **A ceiling on what one user can spend.** `llm_guard` has always bounded
  tokens per *minute*, globally — nothing bounded a single tenant across runs,
  so one client looping a `@team` request could consume the whole organisation
  quota while the guard kept obliging, minute after minute. `AGENT_USER_TOKEN_BUDGET`
  (per `AGENT_USER_BUDGET_WINDOW`, default 24 h) is checked at `/chat/stream`
  and refuses with a normal SSE message instead of calling the model. Opt-in:
  0, the default, disables it.
- **PII redaction on the way *out*.** `modules.pii.redact` ran in two logging
  paths and nowhere else, so a model echoing an SSN or card number out of an
  uploaded document sent it straight to the browser. The three `/chat/stream`
  paths now pass tokens through `StreamRedactor`, which holds back enough text
  that a number split across two chunks is still redacted whole — the cut is
  made at a digit-run boundary, never a fixed offset.
- **A token ceiling inside the tool loop.** `_MAX_TOOL_ROUNDS` bounds rounds and
  `_MAX_IDENTICAL_CALLS` bounds repeats, but neither bounds *size*: every round
  resends the conversation plus the tool results. `AGENT_MAX_TOOL_TOKENS`
  (default 48 000) stops before paying for a round that would exceed it and
  tells the model to answer from what it has.
- **Spans for tool calls and for eval cases.** `_TOOL_SECONDS` was a histogram,
  so a slow `web_search` was a gap in a Phoenix trace rather than an event you
  could open. Tool calls are now spans, and each nightly eval case is a span
  carrying its id, category and route — a failing case has a trace, not just an
  answer string.
- **`PROMPT_VERSION` / `prompt_fingerprint` in eval reports.** `git_sha` says
  which commit produced a report, not which *prompt*. Reports now carry a hash
  of the prompts actually in force, and `compare()` reports `prompt_changed` so
  a diff across a prompt edit is labelled as such.
- **SSRF guard in `web_tools`.** `_assert_http` validated the scheme only.
  `http://169.254.169.254/latest/meta-data/` is a valid https URL and returns
  the pod's cloud credentials. Private, loopback and link-local destinations are
  now refused. Not currently reachable (URLs are fixed endpoints and input is
  query-encoded) — this closes the hole before a refactor opens it.
- **Tests for the citation wiring**, which shipped with unit tests for
  `modules/citations.py` and none for `_check_doc_citations` in `api.py`.

### Changed
- **The cost ledger is now the Prometheus counter, and `CostTracker` is gone.**
  Costs were recorded twice: an in-memory `CostTracker` dict behind
  `/monitoring/dashboard`, and the `llama_agent_llm_cost_usd_total` counter
  Grafana reads. The dict was per-process, so replicas disagreed and a restart
  erased it. The pricing table moved to `modules.prometheus_metrics.PRICING`,
  the dashboard renders from the counter, and per-user spend is a real counter
  (`llama_agent_llm_cost_usd_by_user_total`, bucketed past 500 users) instead
  of a dict. **124 lines deleted.**
- **A single TracerProvider.** `modules/advanced_rag.py` built its own provider
  and called `trace.set_tracer_provider()`, which takes effect exactly once per
  process: whichever module initialised first won and the other's spans were
  dropped silently. It now reuses the installed tracer.

### Fixed
- **One `@team` step timeout is now one timeout, not one per future.**
  `f.result(timeout=150)` was called in a loop, so the Nth future got 150 s *on
  top of* the first N-1: a two-step level could block for 300 s, past
  `AGENT_LATENCY_BUDGET` (180 s) and past the eval's 300 s case timeout. The
  measured 309 s `team-library-api` run is exactly two step-timeouts. The
  deadline is now shared across the level, and the pool is shut down with
  `wait=False` so a fired timeout actually shortens the request.
- **`_price()` referenced `os` without importing it.** The `NameError` was
  swallowed by `except Exception: return 0.0`, so every call would have been
  priced at zero — spend would read 0 forever and nothing would fail.
- **Token totals in the cost summary unpacked the label tuple in the wrong
  order**, reporting every token as output.

### Earlier

### Added
- **`modules/citations.py` — the document-answer prompt's citation rule is
  now checked.** The prompt demands "cite the source for EVERY claim ... never
  cite a file that is not in the excerpts" and nothing verified either half.
  A model that names a document it was never shown produces an answer that
  *looks* rigorously sourced — correct format, plausible file — which is
  exactly why it needs catching. The answer's `(file, chunk N)` citations are
  matched against the `--- Source: ... ---` labels it actually received; an
  invented file, a chunk that was not shown, or claims with no citation at all
  are appended as a visible note and counted in
  `llama_agent_citation_failures_total{kind}`. Detector, not blocker: silently
  rewriting a citation would hide the mistake instead of surfacing it.
- **`AuditLogger.log_tool_call`.** Every other security-relevant event was
  audited (login, upload, rate limit, suspicious input) but tool calls were
  not — and they are the only thing this app does that leaves its own process.
  "Who searched for what, and when" now has a trail, with the same SSN/card
  redaction as the rest of the audit log.
- **FactMemory history.** `remember()` silently replaced the previous value, so
  once the agent had the wrong fact there was no way to see what it used to
  say or which turn overwrote it. Updates and deletions are now appended to a
  bounded history (500 entries, Redis or in-memory), tenant-scoped and
  queryable via `history(key=..., user_id=...)`.

### Changed
- **`record_llm_usage` now passes `query_type` to the CostTracker.** The
  tracker has bucketed by query type since it was written, but nothing ever
  supplied one, so `cost_by_query_type` was **always empty in production** —
  you could see total spend and spend per user, but not which part of a `@team`
  run cost the money. Now populated from the existing per-agent `component`
  label.

- **The critic now reviews on a different model tier than the one that wrote
  the answer.**- **`modules/provider_client.py` — the provider registry is now actually
  called.** `GroqLLM.__init__` hardcoded `Groq(api_key, base_url)`, so every
  model in `PROVIDER_MODELS` other than Groq's own was POSTed to
  api.groq.com and 404'd. The registry, `resolve_provider_chain`,
  `should_fallback_to_next_provider` and their tests had existed for weeks and
  production called none of it. One factory now picks the SDK that matches the
  model. The default deployment still resolves both tiers to Groq, so nothing
  changes unless a model name or `provider=` says otherwise.
- **Anthropic is refused at construction, not at call time.** Its SDK has a
  different shape (`messages.create`, `system` lifted out of the message list,
  a different response object), so pretending it matches would surface as an
  AttributeError in the middle of a tool loop. `build_client("anthropic")`
  raises `ProviderConfigError` naming the reason.
- **`evals/gate.py` — the eval gate CI can actually afford.** Every prior PR ran
  only `evals.rag_eval retrieval`, so prompt and routing regressions shipped
  green: reorder a keyword group in `modules/routing.py` and "write me a blog
  post" routes to `general` with all ten jobs passing. The agent goldens need
  `GROQ_API_KEY` and a judge call per case, so they cannot gate a PR — but the
  two layers beneath them are pure functions. The gate replays all 28 routed
  dataset cases and 16 hand-built check fixtures with no key and no network, in
  well under a second, and is wired into the `test` job. Verified both ways: a
  sabotaged `route_multi_agent` fails 20 cases with exit 1, and a checker that
  stops failing bad answers is caught too.
- **`modules/pii.py` — SSN and card-number redaction on log writes.**
  `monitoring.log_interaction` stored the user's message and the agent's reply
  verbatim, and `AuditLogger._write_log` persisted user-supplied text to a file
  and mirrored it to Redis. Anything a user pasted therefore outlived the
  request in a log anyone with access could read. US SSNs and Luhn-valid card
  numbers are now replaced with `[REDACTED]` on the way out; the agent still
  reasons over the original text. The Luhn check is what keeps a 16-digit order
  number intact — deliberately not a general PII classifier, because names and
  emails cannot be told from ordinary words without destroying the log.
- **Goldens doubled: 30 -> 60 cases, with `difficulty` and `tool_recall`.** The
  original 30 never pinned the router's hard boundaries — the regex that decides
  "write an essay on functional programming" is prose but "write a Python class"
  is code had no golden either way. The 30 new cases target those seams (six
  coder verb/noun shapes, three writer topic-word cases, five adversarial
  attacks incl. an injected document) plus a `difficulty` field on every case
  (easy 17 / medium 19 / hard 16 / adversarial 8) and `expected_tools` on cases
  that must search. Every asserted route was checked against the live router
  before being written down.
- **Nightly eval workflow with a baseline diff** (`.github/workflows/nightly-evals.yml`).
  The goldens need `GROQ_API_KEY` and a judge call per case, so they still
  cannot gate a PR — but they ran *nowhere at all* before, which meant the
  compare machinery in `evals/run.py` (built weeks earlier) had never produced
  a diff. Runs at 03:17 UTC: executes the suite, downloads the previous run's
  artifact, and calls `--compare --fail-on-regression`. Absolute pass rate is
  not the signal — a one-case regression on Tuesday is noise in one report and
  obvious in a diff. Missing baseline or missing key degrades to a notice, not
  a red job.
- **`min_words` check.** "Write an essay" graded on keywords alone passes a
  twelve-word answer that name-drops the right vocabulary. `checks.py` only had
  a ceiling. The offline gate now covers it in both directions.
- **Trajectory metrics in the eval report.** `avg_steps`, `total_redundant_steps`
  (repeat `(tool, arguments)` calls) and `tool_recall` (cases whose declared
  `expected_tools` were actually called). A run that succeeds in 3 tool calls
  beats one that succeeds in 15, and nothing measured the difference before.

### Changed
- **The "a provider swap breaks Phoenix spans" risk did not exist.** This work
  was deferred for a half-day because of it. `InstrumentedGroqClient` routes
  through llama-index's `GroqLLM` precisely so the OpenInference instrumentor
  emits a `GroqLLM.*` span, and swapping the vendor SDK underneath was assumed
  to lose that. Measured with an in-memory exporter: the span comes from
  llama-index's `@dispatcher.span` on `LLM.chat`, which wraps a subclass's
  override at class-creation time — the vendor SDK is invisible to it. Both
  providers produce the identical span. Pinned by a test that fails if a future
  change routes around llama-index.
- **Conversation history is rendered from one shared budget, not three copies
  of `content[:200]`.** The same line was pasted in `api._with_context`,
  `memory.get_context_string` and `llama_agent1._budget_context_string`, each
  with different surrounding code. Measured on six ordinary turns: 281 tokens
  admitted by `_window_messages`, 181 sent — 36% lost, and a steady 54% on
  longer conversations because assistant answers run ~500 characters. The cap
  also cut mid-word ("...the sorti" / "ng is O(n log n)"), so a reply that
  said "O(n) if already sorted" looked like it had not. The third copy was
  worse: it cut to 200 chars *before* estimating token cost, so it budgeted
  against text a fifth the size of what it was deciding about. All three now
  call `modules/context_budget.render_history`, which spends one budget
  newest-first and keeps short conversations verbatim.
- **The critic now reviews on a different model tier than the one that wrote
  the answer.** A model checking its own output with the same weights agrees
  with itself — it confidently passes exactly the errors it made. Falls back to
  the agent's own tier if the alternate is rate-limited.
- **The eval judge defaults to the strong tier.** On `@team` runs the agents
  themselves run fast-tier, so a fast judge was the same model grading its own
  reasoning. `EVAL_JUDGE_MODEL` still overrides, which is the better answer (a
  different provider has no shared blind spots at all).
- **`evals/run.py` imports routing from `modules.routing`, not `api`.** Same
  functions, but `api` pulls in FastAPI and the whole app; the eval runner does
  not need the web stack, and the offline gate must not depend on it.

### Fixed
- **The eval suite was failing correct answers, not the app.** The first real
  nightly run scored **75%**, yet the judge averaged **4.43/5** — two graders
  disagreeing about the same answers, which means one of them was wrong. It
  was the deterministic tier, three times over:

  1. **Grounding ran on cases that never asked for sources.** `evaluate()`
     applied the numeric-grounding check to *every* case that happened to have
     tool output, so a migration plan was graded against a web search it never
     relied on. A plan's figures come from the request and its own arithmetic —
     "0/13 figures found in sources" is not a defect, it is the nature of a
     budget. It failed 7 cases the judge scored 4-5 and drove the reported
     grounding rate (0.28) to meaninglessness. Grounding now applies only to
     cases declaring `needs_sources`, which is what the check means.
  2. **A correct inline snippet was a failure.** `code-debug` and
     `code-regex-pin` both scored **5.0** from the judge and failed on
     `no python code block` because the snippet was written inline
     ("use ``len(items) == 0``") rather than fenced. That graded formatting,
     not correctness. An inline span is now accepted when it parses as Python.
  3. **`no_meta` flagged ordinary words.** The bare nouns `hand-?offs?` and
     `team members?` failed legitimate content ("the handoff between phases
     takes one week"). What must never reach a user is an agent narrating the
     pipeline it belongs to, so the patterns now carry that context and fire on
     "I'll pass this to the other agents" while staying quiet on "the handoff
     between phases".

  Each fix is covered by a regression test that fails with the bug restored.

### Added
- **`evals.checks.inline_code_spans()`** and **`_parses_as_python()`** — the
  inline-snippet half of the code check.

### Earlier

- **Corrective RAG was inverting its own check.** `CRAGRetriever._is_supported`
  required every answer sentence to appear as a **substring** of the retrieved
  context. That asks the opposite of the question it claims to ask: a correct
  paraphrase necessarily fails ("Postgres offers strong transactional
  guarantees" is not in a chunk saying "PostgreSQL ... ACID guarantees") while
  a verbatim copy trivially passes. Measured: paraphrase → `False`, copy →
  `True`.

  The consequences compounded. A `False` on a good answer sent the request down
  a retry path costing two more LLM calls, and that path then returned
  `{"supported": True}` **hardcoded** — so the caller's unsupported branch
  could never fire on it. The caller, `_retrieve_context`, preferred the CRAG
  answer only when `supported` was true and otherwise fell back to
  `results[0]["content"]`: the first **raw retrieved chunk**, unprocessed and
  never shown to any model. Net effect: a well-phrased answer could be thrown
  away in favour of a document fragment, after paying for three extra calls.

  Fixed by (a) delegating to the same LLM verdict judge `verify_answer`
  already used, so the file has one definition of "supported"; (b) reporting
  the measured verdict instead of assuming the retry worked; (c) keeping the
  CRAG answer rather than substituting a raw chunk; and (d) giving the answer
  step the **question** — the old prompt was the context plus `"Your answer: "`
  with nothing to answer.
- **`CRAGRetriever` no longer crashes on a single-prompt LLM client.** Both the
  answer and rewrite steps passed `system_prompt=`/`user_prompt=`/`max_tokens=`
  unconditionally, so a client accepting one argument raised `TypeError`
  instead of degrading.
- **`summarize()` tolerates report records without trajectory fields.** A
  baseline JSON written before `steps`/`redundant_steps` existed raised
  `KeyError`.
- **`web_search` can no longer be used to exfiltrate.** It is the app's only
  outbound channel, which makes it the one realistic path for an indirect
  prompt injection to ship data out. Queries over `WEB_MAX_QUERY_CHARS`
  (default 400) are refused — a real query is well under 100 characters — and
  content flagged `[INJECTION_SUSPECTED]` on a hand-off is refused rather than
  forwarded verbatim.

- **One hung agent can no longer hang an entire `@team` request.** `collaborate()`
  called `f.result()` with no timeout, so a wedged step blocked forever — and the
  budget guard only runs *between* DAG levels, so it could never catch it. Each
  future now has `AGENT_STEP_TIMEOUT` (default 150 s, below the 180 s latency
  budget) and degrades to the same placeholder a failed step produces.
- **`read_uploaded_document` can now read a whole document.** It returned the
  first 6,000 characters and appended "[...truncated, document is large...]", so a
  40-page PDF silently became its first three pages and the agent answered
  confidently from the head. It now reports the total length and takes a
  `start_char` offset, so the model can page through. The 6,000 cap was also dead
  code: `dispatch_tool` re-capped every result at 4,000 chars afterwards.
- **Tool error strings now say what to do next.** `Tool 'x' failed: 429` was the
  model's only feedback; it now suggests retrying with a different query, and an
  unknown tool lists the valid names. The document tool already did this
  ("Available documents: ...") — the dispatch layer did not.
- **Dead `temperature` config removed.** `LLMConfig.temperature` read
  `LLM_TEMPERATURE` and no code ever consumed it, so setting the variable did
  nothing. Sampling is set per call site instead (the eval judge pins 0 for
  reproducible grading), which a single global value could not serve.
- **`sanitize_input` renamed to `strip_html_vectors`.** The old name promised
  prompt-injection protection; the function strips `<script>`/`on*` handlers and
  HTML-escapes markup, which an injection attempt contains none of. Its docstring
  also claimed SQL-injection protection, which only parameterized queries give.
  `sanitize_input` remains as a deprecated alias.

### Added
- **Per-tool latency histogram** `llama_agent_tool_seconds{tool}`, recorded in
  `dispatch_tool`. Every other metric is labelled by agent or component; this is
  the first one that can blame a single tool.
- **Repeat-call guard in the tool loop.** The same `(tool, arguments)` pair is
  executed at most `AGENT_MAX_IDENTICAL_CALLS` (default 2) times per loop; after
  that the model is told the result will not change. `_MAX_TOOL_ROUNDS` bounded
  the total damage, not the waste.
- **Span identity attributes.** `_span_cm` now sets `user.id`,
  `conversation.id` and `config.commit` (`GIT_SHA`), so stream spans — the path
  `/chat/stream` actually uses — are no longer anonymous.
- **`ARCHITECTURE.md`: why the context limits are what they are**, and a
  "deliberate limits" table recording five things this system chooses not to do
  and why (no mid-run replanning, no graph checkpointing, SSE over
  streamable-http, no async task queue, no tenant budget enforcement).

### CI
- **The CD jobs no longer hang forever.** `deploy-staging` and `deploy-production` were pinned
  to `runs-on: self-hosted`, but no self-hosted runner is registered for this repo, so the jobs
  queued indefinitely — the workflow has no default job timeout. The previous run showed them
  as `skipped` only because the `docker` job upstream was `skipped`, which hid it.

  Both now use `ubuntu-latest` plus `timeout-minutes: 15`. Their existing
  `✅ Check deploy is configured` gate already skips every kubectl step when
  `KUBE_CONFIG`/`DOCKER_USERNAME` are absent, so the jobs report "cloud deploy skipped" and
  pass in seconds instead of waiting on a runner that will never appear. Verified the gate
  logic against all three cases (neither secret, one secret, both secrets).

### Changed
- **Extracted `modules/routing.py` from `api.py`.** The intent-routing rules (agent/team
  prefixes, the Aho-Corasick keyword matcher, `route_multi_agent`, the name-memory helpers)
  need no FastAPI app, database or vector store, so they now live in one 310-line module that
  can be read and tested on its own. `api.py` re-exports every public name, so
  `from api import route_multi_agent` and the eval imports are unchanged.

  Verified by a differential test: 29 message cases through `route_multi_agent`,
  `detect_agent_prefix`, `detect_team_prefix`, `extract_name_from_message` and
  `is_name_question`, run on the old and new code, produced **byte-identical output**.

### Removed
- **`modules/cache.py` (504 lines).** Nothing imported it: no module, app or test referenced
  `InMemoryCache`, `RedisCache`, `ResponseCache`, `QueryCache`, `EmbeddingCache` or the
  `cached` decorator (0 hits outside the file itself). Caching in the live path comes from
  `modules/rag.py`'s answer cache, `modules/rag_optimizations.SemanticCache` and
  `api.py`'s `BoundedLRUCache`. The `integration` job's import smoke test now checks
  `SemanticCache` instead.
- **`TokenResponse`** (`api.py`) — a leftover from header-based auth; no route returned it and
  cookie auth (`AuthSuccess`) replaced it.
- **`.github/environments.yml`** — GitHub does not read environments from a committed file:
  they live in Settings → Environments. The file protected nothing, and it named an approver
  (`ravneet-png`) that does not own this repo.

### Documentation
- **README CI/CD status:** the old text said "GitHub Actions is currently not running for this
  account" while pointing at a workflow that *was* running. It now states the real situation —
  the CD jobs are parked on hosted runners and report "cloud deploy skipped" until a
  self-hosted runner can reach the kind cluster.
- **CONTRIBUTING:** dropped the `modules/cache.py` import example and the dead entry in the
  project-structure tree.

### Documentation
- **README: agent eval results** (26/30 passed, judge average 4.88 / 5), per category and agent
  with time and cost, and how the runs were done: three parts on v3.4.8, v3.5.1 and v3.5.2
  because of the free tier's daily token cap; "not graded" cases re-run, real failures not.
- **"What measuring found":** all 4 failures are figures that are in no source, which the LLM
  judge scored 4.25 - 5.0; a wrong date copied from a source passed the figure check. Both
  checks are needed.
- New sections: at a glance, bugs found and fixed (14, with proof), design decisions, CI/CD
  status (GitHub Actions currently not running for this account; recent versions deployed by
  hand), commands to check the numbers, and a Proof column plus known gaps in Security.
- Known limits: mixed-version agent eval, agents still invent figures, empty `git_sha` in
  eval reports.
- **README screenshots** (`docs/screenshots/`): the chat UI (Analyzer agent), the Grafana
  LLMOps and per-agent panels, and a Phoenix trace with an LLM span, from the
  live cluster.
- **Screenshots at the top of the README:** the Phoenix trace right under the project
  description, with the chat and Grafana screenshots as clickable thumbnails; shorter
  scope note.

## [3.5.6] - 2026-10-26

Found by a hand-run audit of the whole repo (Bandit, flake8, pip-audit, the MCP server
tests and read-only checks of the live cluster).

### Security
- Removed `fastmcp` from requirements.txt. Nothing imported it (the MCP server uses
  `mcp.server.fastmcp` from the official `mcp` SDK), but it was installed with a known
  advisory (GHSA-7xgj-8392-5q4r).
- The image upgrades pip to 26.2.2 before it switches to the non-root user; pip 24.0 from the
  base image had 6 advisories. pip is only used while the image is built.

### Fixed
- `/health`, the OpenAPI docs and the config reported version 3.0.0. They now report 3.5.6,
  and a test fails when they don't match the newest version in this file.

### Tests
- New `tests/test_version_and_deps.py` (3 tests): the version matches this file, fastmcp is
  not in requirements.txt, and pip is upgraded (>= 26.2) before `USER`. Full suite 574 passed,
  4 skipped; MCP server tests 17/17 without fastmcp; pip-audit 27 -> 14 findings; retrieval
  eval unchanged (hit@6 0.921, 0 cross-user leaks).

### Docs
- README Known limits: WebSocket not used by the UI, Compose not re-run, per-namespace network
  policy, the 14 open advisories and why they don't apply. Fixed a stale fastmcp comment in
  the Dockerfile.

## [3.5.5] - 2026-10-26

### Security
- **App containers no longer run as root.** The Python image runs as `app` (uid 1000, the
  usual host uid, so docker-compose bind mounts stay writable). The code in `/app` stays
  root-owned, so the app can't change it. Only the folders it writes to (found with a live
  check of the pods and a grep of the code) belong to it: data, memory, backups, migrations,
  logs, uploads and `config/*.json`. The embedding model cache moved from `/root/.cache` to
  `/home/app/.cache` (still baked in; works with no network).
- **Kubernetes enforces it:** the api, mcp-server, web and frontend Deployments set
  `runAsNonRoot`, `allowPrivilegeEscalation: false`, drop ALL capabilities and use the
  `RuntimeDefault` seccomp profile. All 4 pass the `restricted` Pod Security level (server
  dry run). The frontend keeps its image user (nextjs, uid 1001).

### Fixed
- **Uploads returned HTTP 502: the API pod was OOMKilled.** One document question made the
  ONNX reranker score 16 long passages in one batch (~940 MB). onnxruntime keeps that memory,
  so the pod stayed at ~1.2 GB and the next upload's embeddings (~420 MB) pushed it over the
  1.5 GiB limit. Found while testing the non-root change; Prometheus shows the old root pods
  reached 1288 MB too, so the bug is older. Reranker batch 16 -> 4: the reranker alone peaks
  at 354 MB instead of 1019 MB over 8 questions in a row, at the same speed, with the same RAG
  eval numbers. Live pods after the fix: 481 / 627 MB max, 0 restarts.

### Tests
- `tests/test_nonroot.py` (6) and `tests/test_reranker_memory.py` (2): 571 passed, 4 skipped.

## [3.5.4] - 2026-10-26

Found by checking the live cluster ([docs/KUBERNETES_CHECKS.md](docs/KUBERNETES_CHECKS.md)).
Kubernetes manifests only; the app images are unchanged.

### Fixed
- **The nightly Postgres backup failed 3 days in a row.** The PC is off at 02:00 UTC, so
  Kubernetes ran the missed job at boot, 13 s after Postgres started and before it accepted
  connections; the job used up its 2 retries within seconds. It now waits for `pg_isready`
  (every 5 s, up to 5 min) before `pg_dump`. Proven by stopping Postgres: the job waited and
  finished when Postgres came back.
- **The autoscalers could never scale.** The cluster had no metrics-server, so both HPAs read
  `cpu: <unknown>`. metrics-server v0.9.0 is vendored in `k8s/metrics-server.yaml`
  (`--kubelet-insecure-tls` for local kind only); the HPAs now read `3%/70%`.

### Changed
- Chroma and Phoenix pinned by image digest instead of `:latest` (k8s and Docker Compose), so
  a restart can't silently pull a new version.

### Verified (no change needed)
- **Restore drill:** the newest dump restored into a throwaway Postgres has the same row
  counts as the live database in all 6 tables.
- **NetworkPolicy is enforced:** a pod in another namespace can't reach the API; a pod in
  `llama-agent` can.

## [3.5.3] - 2026-10-25

### Fixed
- **Streamed chat answers were timed as ~0.1 s.** The HTTP middleware stopped its timer when
  `call_next` returned, i.e. when the response headers were ready. For `/chat/stream` that is
  before any answer text, so a 60 s agent answer was recorded as fast: "Avg Response Time", the
  `llama_agent_response_time_seconds` histogram and the `HighResponseTime` /
  `VeryHighResponseTime` alerts never reflected how long a chat took. The timer (and the active
  connections gauge) now stops when the last byte of the body is sent. On the old code a 2.0 s
  stream was recorded as 0.002 s; now 2.008 s. Buckets extended to 60, 120 and 300 s.

### Added
- **Chat SLOs** (`docs/SLO.md`). New `llama_agent_chat_seconds{route,outcome}` (message received
  to end of stream; route = command / document / general / agent / team; outcome = ok / failed /
  error / cancelled, where failed = the app's failure notice instead of an answer) and
  `llama_agent_chat_first_token_seconds{route}`. Targets over 30 days, set from the measured eval
  numbers: 95% of chats answered; 95% of quick answers (command, document, general) within
  10 s; 95% of single-agent answers within 90 s; no latency SLO for `@team` (free-tier bound).
- **Multi-window burn-rate alerts** (14.4x over 1 h and 5 m, or 6x over 6 h and 30 m, at least 5
  chats in the window) on recording rules summed across pods, in both the k8s and the Compose
  rule files.
- **Grafana SLO row:** 30-day compliance per SLO (green at 95%), chat answer time p50/p95 and time
  to first answer text per route, and the error-budget burn rate.

### Changed
- `HighResponseTime` / `VeryHighResponseTime` skip `/chat` and `/chat/stream`; chat latency is
  judged per route by the SLO alerts (a 60 s agent answer is normal, a 60 s login is not).

## [3.5.2] - 2026-10-25

Found by the agent eval (team cases, `team-todo-landing`).

### Fixed
- **An agent step crashed when the model tried a tool call after its last tool round.**
  When an agent uses all its tool rounds, it is asked for the final answer with no tools
  offered. Sometimes the model tries another tool call anyway, and Groq fails the stream
  midway: `APIError: Tool choice is none, but model called a tool`. The error was not caught
  while streaming, so the whole step failed ("could not complete this step") and the existing
  empty-answer rescue never ran. Now: if part of the answer already streamed, it is kept;
  otherwise one more call is made on the fast model with the task and the tool results as
  plain text and **no tool-call turns in the history**, so the model has no tool call to
  repeat. The non-streaming path (`run_tool_loop`) retries the same way on this error (other
  errors, e.g. rate limits, still reach the strong-to-fast fallback). Applies to all 5 agents.
- **The eval graded the app's own rate-limit notice.** An answer that is the app's "so I can't
  answer right now" (rate limit) or "the AI model is unavailable" notice is now **not graded**
  (and not passed) instead of being judged: one such case had been scored 1.0 and counted as
  "fabrication flagged". The live progress line shows `NOT GRADED` instead of `FAIL` for
  these.

## [3.5.1] - 2026-10-25

Fixes found by checking the v3.5.0 Grafana panels against the raw `/metrics` of each API pod
(2 coder runs, about 3.0 s each on average; Grafana showed a p50 of 0.5 s, broken lines and
"No data" for failure rate).

### Fixed
- **Prometheus scraped the load-balanced API Service**, so each scrape reached a random one of
  the 2 API pods. Series from the two pods interleaved: gaps (dotted lines) whenever a scrape
  hit the pod without that series, and false counter resets when both had it. This affected
  every API panel and alert, not only the new ones. Prometheus now scrapes **each API pod**
  through a new headless Service, `llama-agent-api-headless` (DNS service discovery; no RBAC,
  allowed by the existing NetworkPolicies).
- **The first agent run on each pod was invisible**, and "Agent failure rate" showed "No
  data" instead of 0%. A series that first appears with value 1 has no increase Prometheus
  can see, and with nothing failed yet the failed/error series did not exist. All agent x
  outcome series (5 x 5) are now created at 0 when the app starts.

### Changed
- Dashboard panels and alert rules over the app's metrics add the pods up
  (`sum without (instance) (...)`, keeping their per-endpoint / per-status breakdowns) instead
  of showing one line per pod. `ApiDown` now fires when **any** API pod can't be scraped for 2
  minutes. A test checks that every app query and alert aggregates across pods.

## [3.5.0] - 2026-10-25

Per-agent metrics: see which part of the app spends the LLM budget, and how fast and how
reliable each agent is, in Prometheus/Grafana.

### Added
- **`component` label** on `llama_agent_llm_tokens_total` and `llama_agent_llm_cost_usd_total`:
  the part of the app that made the call - `research`, `coder`, `writer`, `analyzer`,
  `planner` (each agent, including tool rounds, critic and reflection; @team planning runs on
  the planner agent), `chat` (general chat), `doc_answer` (answers from uploaded documents),
  `rag` (RAG helpers: contextual chunk summaries, HyDE, query decomposition,
  conversational query rewrite, CRAG check), `router` (agent routing) and
  `judge` (eval judge). Values are a fixed list; anything else is recorded as `other`.
  `record_llm_usage()` takes an optional `component`; `GroqLLM` and `InstrumentedGroqClient`
  take one and pass it on.
- **`llama_agent_agent_run_seconds{agent,outcome}`** histogram: wall time of each specialist
  agent run (streaming and non-streaming). Outcome: `ok`, `failed` (empty answer or one of the
  failure messages the eval's `completed` check uses), `cached` (semantic-cache hit), `error`
  (exception) or `cancelled` (the client disconnected mid-stream).
- **3 Grafana panels**: estimated spend by component, agent run time p50/p95, agent failure
  rate (21 panels in total).

### Changed
- Existing dashboard panels and the $10/24h cost alert aggregate with `sum` / `sum by (model)`,
  so they work unchanged. Queries that matched these two series by exact label set must add
  `component`.

## [3.4.9] - 2026-10-25

Fixes found by the v3.4.8 agent eval, which hit the Groq free tier's daily token limit
(200,000 tokens/day on the fast model) halfway through.

### Fixed
- **A failed judge counted as a pass.** When the judge call failed (16 cases returned a 429),
  a case passed on the rule-based checks alone; 6 did. Such cases are now **not graded**: they
  don't count as passes, the summary lists them (`not_graded`), the table marks them
  "⚠️ not graded", and `--compare` ignores them.
- **General chat on a rate limit (429) showed two contradictory warnings**: "Error while
  generating response" and "The model returned an empty answer ... or rephrase the question",
  after a retry that could not succeed, and the warning was stored in the model's memory as
  its answer. It now shows the same notice as document answers, e.g. "I've hit today's
  free-tier AI usage limit, so I can't answer right now. Please try again in about 16
  minutes." (the wait is Groq's own "try again in" time), skips the retry and keeps the
  notice out of the model's memory. Other errors keep the old behaviour.
  `friendly_llm_error()` moved from `api.py` to `modules/llm_guard.py` (still importable from
  `api`), next to the new `llm_guard.is_rate_limited()`.
- **Eval failure markers** now include that notice and "error while generating response", so
  an error message can't pass the `completed` check.

### Added
- **The report keeps the source text the judge saw** (`judge_sources`, the same passages, up to
  `EVAL_JUDGE_SOURCE_CHARS`), so every verdict, including fabrication flags, can be checked by
  hand. Before, only the length of each tool result was saved.

## [3.4.8] - 2026-10-25

Two bugs found by the first real run of the RAG answer eval (44 questions, Groq): 39/44
passed, and 2 of the 5 failures came from the semantic cache.

### Fixed
- **Semantic cache served answers to different questions.** Both cache hits in the eval were
  wrong: "How much does the Team plan cost?" got the cached answer about the Team plan's rate
  limit, and "What was Kestrel's gross margin in Q2 2025?" (not in the documents) got the
  cached Q3 2026 revenue answer instead of "not found". A hit now needs cosine distance
  <= 0.05 instead of 0.3 (`RAG_CACHE_MAX_DISTANCE`) and the same numbers, years, quarters and
  period words in both questions. This is live in production (v3.4.5) too.
- **Contextual chunk summaries were mostly empty.** The summary call had `max_tokens=100`,
  which the reasoning fast model spent on hidden thinking: 1 of 7 eval documents got a
  summary. The budget is now 1024 (`RAG_SUMMARY_MAX_TOKENS`) and an empty summary is no
  longer cached.

### Changed
- **README "Results" section** with the measured retrieval and answer numbers (v3.4.7 vs
  v3.4.8: 39/44 -> 41/44 passed, unanswerable questions 5/6 -> 6/6, wrong cache answers 2 -> 0).
- **CI retrieval gate:** `--min-hit6 0.89` (was 0, report-only). Baseline hit@6 is 35/38 = 0.921.
- **Contextual summaries are opt-in** (`RAG_CONTEXTUAL=1`, default off). In practice production
  ran on plain chunks, and that is what the retrieval eval measures; summaries stay off until
  they are measured to help. The startup log says whether they are on.

## [3.4.7] - 2026-10-24

Claims cleanup, and a RAG eval so the RAG claims can be measured. No change to how the app
answers.

### Added
- **RAG eval** (`evals/rag_eval.py`, corpus and 44 fixed questions in `evals/rag/`), indexed
  into a throw-away in-memory ChromaDB:
  - `retrieval` (no LLM): hit@1/3/6 and MRR of the 6 excerpts the answer model sees, for
    vector only vs hybrid vs hybrid + reranker (deployed), by keyword/paraphrase question,
    with latency. A second user's document is indexed too; any of it in the first user's
    results fails the run.
  - `answers` (Groq, on demand): expected fact, cited file, "not found" on unanswerable
    questions, false refusals, figures found in the documents, CRAG "unverified" rate,
    semantic-cache hits, latency and cost.
  - CI runs `retrieval` after the tests with the real reranker, writes the numbers to the job
    summary and uploads the report. The hit@6 floor is 0 until a baseline is measured.
  - `tests/test_rag_eval.py` (23 tests) checks the harness offline: unique labels, scoring,
    leak detection, refusal detection, and that the eval ranks exactly the excerpts
    `api._retrieve_context` sends to the model.

### Changed (wording only)
- **Removed unmeasured numbers:** "saves 80% cost and 20ms" (semantic cache), "beats 95% of
  RAG repos" (CRAG), "improves recall by ~10%" (contextual retrieval, not Anthropic's figure).
- **Fixed wrong facts:** models (README said Llama 3.3-70b / 3.1-8b; the app uses
  `qwen/qwen3.8-27b` and `openai/gpt-oss-20b`), "pgvector" (not installed), "CDN headers"
  (nginx sets security headers only), upload "threat/content scanning" (extension and size
  checks only), "8 alert rules" (9), "13 panels / 13 containers / 8 jobs" on the landing
  page (18 / 14 / 9), test counts (now 495 passed, 4 skipped, 17 deselected, 23 modules, including the RAG eval tests),
  "validated manifests" (CI doesn't validate them), "encrypted at rest" Docker secrets,
  the deploy description (`apply-app.sh` renders `:<sha>`, not `kubectl set image`).
- **OptimizedRAG described as it runs:** query decomposition is off by default, and the LLM
  follow-up rewrite isn't used by the chat path. The startup log no longer lists them as
  active.
- **Monitoring dashboard and input sanitizer described honestly:** both cover only the
  non-streaming `/chat`, not `/chat/stream`.
- Dropped "production-grade" / "enterprise-grade" wording; added a "Known limits" section
  (single-node cluster, free-tier rate limits, small on-demand eval, no Alertmanager,
  streaming-endpoint gaps). Replaced the README's example "fun fact" (an unchecked figure).

## [3.4.6] - 2026-10-24

Makes the RAG steps the code already advertised actually run, without PyTorch.

### Fixed
- **The "cross-encoder reranker" was a keyword ranker.** It needed `sentence-transformers`
  (PyTorch), which was never installed, so every search quietly used keyword overlap. That
  blend also ranked chunks found only by keyword search near the bottom. The reranker
  now runs a real cross-encoder (`ms-marco-MiniLM-L-6-v2`, int8 ONNX, ~23 MB) on
  `onnxruntime` + `tokenizers`, which chromadb already installs, so no new Python packages.
  - The image build downloads the model at a pinned revision with hash checks
    (`scripts/download_reranker.py`), and the build fails if the model doesn't rank a
    relevant passage first.
  - CI downloads the same file and tests the real model's ranking on four
    question/passage sets.
  - The startup log names the reranker in use.
- **Corrective RAG never worked.** The verifier gave the fast reasoning model 5 tokens, so
  its reply was always empty and every answer "passed". It also saw only half of the
  excerpts the answer came from. Now:
  - it has a 1024-token budget and sees the same excerpts (up to 12,000 characters);
  - its verdict is parsed robustly ("NOT SUPPORTED" counts as unsupported);
  - it is on by default (`RAG_CRAG=1`), and verdicts are counted in
    `llama_agent_rag_crag_verdicts_total`.
  - Thin answers skip the check, since they're retried anyway.
  - An unsupported answer triggers the second retrieval, and the retry's answer is checked
    too. One that still can't be verified is shown with a note and not cached.
- **The retry answered the wrong question.** The second pass answered the AI-reformulated
  search query instead of the user's question.
- **Answers cached by older versions were still served** (for example a v3.4.4 answer cut off at
  "Document description CRM-3 is"). Cached answers now carry an answer-format version
  (`SemanticCache.ANSWER_VERSION`), and only current-version answers are served.
- **Questions naming a file skipped the document pipeline.** Wordings like "What does the
  crm file say about…?" or "According to the CRM-3 document, …" went to the general chat
  agent, which has no cache, a different prompt and a generic error. A question now counts
  as a document question when it names an uploaded file: its full name, or a distinctive
  word of the name within 3 words of "file"/"document"/"sheet". Creation requests like
  "write a python file…" are excluded.
- The README and module docs no longer claim a bge reranker or RAGAS evaluation.

### CI
- **"Run workflow" button.** GitHub created no pipeline run for the v3.4.6 merge or for a
  follow-up push. The pipeline can now also be started by hand (`workflow_dispatch`); on
  `main` it builds and deploys that commit, the same as a push.

## [3.4.5] - 2026-10-23

From the CRM-3.docx document (RAG) test.

### Fixed
- **A raw provider error was shown in the chat.** When the free-tier daily token limit was hit,
  the answer was the full Groq 429 text (organisation ID, quota numbers, upgrade link).
  Document answers and comparisons now say which limit was hit (daily or per-minute) and
  roughly when to try again. The full error goes to the logs only.
- **Errors and cut-off answers were cached.** An error became the cached answer (the semantic
  cache lives in ChromaDB and survives deploys), so asking again repeated it. Empty, cut-off
  and error answers are no longer cached, and errors cached by older versions are ignored and
  answered fresh.
- **Answers ended mid-sentence.** The fast model's hidden reasoning counts against the token
  limit, and 1024 tokens could run out after a few words ("CRM-3 is"). The budget is now 2048
  (`DOC_ANSWER_MAX_TOKENS`). An answer cut off almost at once is retried through the existing
  second pass. A long answer that is cut off keeps its text plus a visible note.
- **Every answer began with a "Document description" line.** That line is now only for
  questions about the whole document (summarise, overview, "what is this file?"). Specific
  questions get a direct answer.

## [3.4.4] - 2026-10-23

From a real "Whole Team" chat: a paragraph on pollution plus five years of data.

### Fixed
- **Team answers repeated themselves.** The plan was Writer (paragraph), then Research (data),
  then Writer (combine). The chat shows every step, and the final step rewrote the paragraph and
  the table above it, so the user read both twice. A final step that builds on earlier steps
  is now told they're already shown, word for word, directly above it, so it writes only what
  its task adds (linking, summary, conclusion). Earlier steps still show in full, so code or
  data from them is never lost.
- **Figures mixed different things, and the arithmetic didn't add up.** One "global PM2.5"
  series mixed a world average with single-country values, and a stated difference
  ("2.3 ppm higher") contradicted the table (6.2). The grounding check only verifies that a
  figure appears in some source. Now:
  - every agent follows a rule that values in a table or series must measure the same thing
    (metric, unit, place, scope), with an empty cell rather than a borrowed figure;
  - stated differences, totals and percentages must match the numbers given;
  - the critic also checks the answer's own arithmetic and mixed-up series.

## [3.4.3] - 2026-10-23

Found by the research_news eval run (2/4 passing, while the grounding rate was 0.99).

### Fixed
- **The eval judge flagged sourced answers as fabricated (eval tool only).** It saw a blind
  5,000-char head+tail clip of 11,000+ chars of search results, so the passages an answer came
  from were often cut out. It also judged recent news by its memory: it marked a correct
  "Verstappen won the 11 Oct 2026 Singapore GP" as wrong because, by its memory, the US GP came
  first. That holds for older calendars; the 2026 US GP is on 25 October. Now the judge gets the
  source passages that share the answer's names, numbers and dates (`EVAL_JUDGE_SOURCE_CHARS`,
  default 8000), and the rubric tells it to judge against the sources, not its memory.
- **Answers from the agent's wrap-up path skipped the fact check.** When the tool rounds ended
  without an answer, the forced wrap-up answer and the fast-tier rescue streamed without the
  grounding report that every other path has. Example: a markets answer full of Sensex/Nifty
  figures was never checked. Both are now fact-checked.
- **Citation markers leaked into answers.** The fast reasoning model sometimes leaves internal
  markers such as `【4†L1-L3】`. They are removed from streamed and pre-generated answers,
  including a marker split across stream chunks.

## [3.4.2] - 2026-10-23

### Fixed
- **The critic/reflection step could turn a sourced answer into a refusal.** The second eval
  run caught it: the Research agent searched the web and answered with this week's AI news,
  then the critic, which saw neither the search results nor today's date, judged the
  post-training-cutoff news as "hallucinated". The revision step replaced the answer with
  "I cannot provide the news, I don't have real-time access". Now:
  - the critic is told today's date, gets the tool results, and is told not to call a fact
    invented just because it doesn't recognise it; it flags only claims the sources contradict
    or don't support;
  - the reviser gets the same sources and must keep supported facts, never swapping the answer
    for a refusal;
  - this applies to the streaming and non-streaming agent paths and to the @team final critic.

  Source excerpts are clipped to `AGENT_REVIEW_SOURCE_CHARS` (default 4000) to stay inside the
  free-tier token budget.

## [3.4.1] - 2026-10-23

Fixes found by the first eval baseline (8/9 smoke cases passing) and by the v3.4.0 deploy,
which got stuck and needed manual pod deletes.

### Fixed
- **One rollout per deploy.** The workflow applied the app manifests with the bootstrap `:v7`
  tag, then ran `kubectl set image …:<sha>`. That meant two rollouts per deploy, twice the
  surge pods on a CPU-tight node, and a brief run of whatever stale `:v7` image the node had
  cached. The new `k8s/apply-app.sh <tag>` applies api, web, mcp-server and frontend with the
  commit image already set, in a single apply. Both deploy jobs and `k8s/deploy.sh` (via
  `IMAGE_TAG`) use it; the `set image` step is gone.
- **Rollout strategy sized for the single 4-CPU node.** api and frontend (2 replicas):
  `maxSurge: 0, maxUnavailable: 1`, so a deploy needs no spare CPU. web and mcp-server
  (1 replica): `maxSurge: 1, maxUnavailable: 0` (zero downtime). All four get
  `progressDeadlineSeconds: 1200`, and `wait-rollout.sh` waits 1200s for the API.
- **Coding requests reached the general agent.** 3 of the 5 coding prompts in the eval set
  ("Write a **Python** function…", "Write a JavaScript debounce function", "Write SQL to…")
  matched no phrase in the keyword list. Coding intent is now a pattern: a build verb, up to
  three filler words, then a code noun or language. Topic words (about/on/for/of) and
  explicit writer/planner phrases still win, so "Write documentation for a CLI tool" stays
  with the Writer. All other dataset prompts route as before.
- **Failed agent-team steps are logged.** The orchestrator swallowed the exception behind
  "[… could not complete this step.]". The traceback is now logged, so a repeat shows the
  cause.
- **Eval judge "returned no valid JSON".** The reasoning judge model ran out of its 700-token
  budget on long answers. The budget is now 2000 (`EVAL_JUDGE_MAX_TOKENS`).

### Added
- **Route check in the eval suite.** 28 of 30 cases declare the expected route
  (`agent:coder`, `general`, `team`, …), so a mis-routed answer can no longer pass.

## [3.4.0] - 2026-10-23

### Added (LLMOps: evaluation-driven development)
- **Evaluation suite (`evals/`).** 30 varied prompts in 9 categories (research, coding,
  writing, planning, analysis, general, 5 agent-team domains, safety). Each prompt is routed
  exactly like `/chat/stream`. The suite records:
  - rule-based checks: completed, English, no meta-talk, valid code, used search, case
    expectations;
  - the **grounding rate**;
  - an LLM judge rubric run on the fast model;
  - latency, tokens, cost and guard actions per case.

  It produces a JSON report plus a markdown summary, and `--compare` shows deltas and
  regressed cases. It runs on demand inside the API pod (`python -m evals.run --tags smoke`).
  See `evals/README.md`.
- **Grounding guardrail (`modules/grounding.py`).** A rule-based check of whether the figures
  in an answer (prices, %, amounts, decimals) appear in the tool results the agents retrieved.
  - **Team hand-offs:** figures that aren't found are marked "(unverified)" before the next
    agent reads them (`AGENT_HANDOFF_FACTCHECK`, default on), and the final answer gets a
    fact-check report.
  - **Single agents:** the report appears in the Reasoning view (stage `grounding`). Set
    `AGENT_FACTCHECK_MARK=1` to also mark figures inline.
  - **Metrics:** new Prometheus counter `llama_agent_grounding_claims_total{result}` and a
    Grafana "Grounding rate" panel.
- **Model-chosen search filters.** The `web_search` tool now has optional `topic`
  (general / news / finance) and `time_range` (day / week / month / year) parameters, which
  are passed to Tavily. Results show their published date.

### Changed
- **Context isolation for the agent team.** Intermediate steps see only their own
  instruction and their dependencies' outputs, not the user's whole request. The Coder had
  been inventing market research because it could see "research the market". Only final
  steps see the request. The planner must now write self-contained instructions.
- **Removed patch-style prompt rules.** The "Do ONLY your step / never mention the team"
  sentences are gone, since isolation removes the cause. Dependency outputs are labelled
  neutrally ("Source material 1").
- The planner prompt no longer contains a task-specific example.

### Fixed
- **Default team plan:** the summary step now waits for *every* step. It used to skip the
  last coder/writer step and run in parallel with it, never seeing its output.

## [3.3.6] - 2026-10-23

### Added (LLMOps: run on the Groq free tier)
- **LLM traffic guard (`modules/llm_guard.py`).** Every agent completion now goes through one
  choke point (`InstrumentedGroqClient`), and so does the general chat stream. There the guard:
  - **Budgets tokens.** It estimates each request and compacts it to fit `LLM_INPUT_BUDGET`
    (default 6,000). Old tool results are trimmed first, then the longest context message,
    then the middle of the latest message. The system prompt and the tool-call structure are
    always kept.
  - **Routes around rate limits.** A 60 s sliding window of input tokens is kept per model
    and shared across API replicas through Redis (`llmops:itpm:*`). When the strong model
    would exceed its free-tier limit (`LLM_ITPM_LIMIT_STRONG`, default 7,000 tokens/min), the
    call goes to the fast model *before* Groq rejects it. If every model is saturated, the
    guard waits up to `LLM_GUARD_MAX_WAIT_S` seconds for capacity instead of failing.
  - **Shrinks and retries on HTTP 413.** "Request too large" leads to one retry at 60% of the
    budget, and 413 now also triggers the strong→fast fallback.
  - **Is observable.** New Prometheus counters `llama_agent_llm_guard_events_total{action}`
    (compacted / downgraded / waited / too_large_retry) and
    `llama_agent_llm_tokens_trimmed_total`, plus four new Grafana panels: tokens/min per model
    vs. the limit, guard actions, tokens trimmed, and spend. Each action is also shown in the
    chat's Reasoning view (stage `llmops`).
- **Size caps at the source:** each tool result is capped by `AGENT_TOOL_RESULT_CHARS` (4,000)
  and each search snippet by `WEB_SNIPPET_CHARS` (600).

### Fixed
- **Research no longer crashes with "Request too large" (413).** On Groq's free tier
  (7,000 input tokens/min for qwen3.8-27b), rich Tavily results resent across tool rounds
  went over the limit. The strong→fast fallback also never ran: the streaming wrapper started
  the HTTP request lazily, on first iteration, outside the agents' try/except. Streams now
  start inside `create()`, so errors reach the fallback.
- **No team meta-talk in @team answers.** The step prompt no longer says "you are one member
  of an agent team". It forbids mentioning steps, the team or other agents, so answers stop
  saying "my step is only X; other agents will…".
- **Agents get the full hand-off.** Each dependency's output is passed up to
  `AGENT_HANDOFF_CHARS` (12,000) instead of being cut at 2,000 characters. The token guard
  compacts it further when needed.
- **Research is grounded.** Every price, number, date and product name must come from the
  search results, with the source site cited inline. Anything else is left out or marked
  "(unverified)".

## [3.3.5] - 2026-10-23

### Fixed (agent answer quality)
- **Tavily search works with current keys.** The key is now sent as `Authorization: Bearer`
  (Tavily's documented method). It used to go in the request body as `api_key`, which is
  deprecated and rejected by some keys, so search silently fell back to DuckDuckGo. That
  fallback gets blocked after a few searches ("Web search is temporarily unavailable").
  Tavily failures are now logged (`kubectl logs`), and `TAVILY_SEARCH_DEPTH=basic|advanced`
  picks the search depth.
- **Long answers aren't cut off.** Agent answers are limited to 4,096 tokens instead of a
  hard-coded 2,048, and the Coder gets 6,144. Configure with `AGENT_MAX_TOKENS`,
  `AGENT_CODER_MAX_TOKENS` and `CHAT_MAX_TOKENS` (general chat). The Coder is also told to
  write compact, complete code and never stop mid-file. Before, team landing pages were cut
  off mid-HTML.
- **Agents know today's date.** Every agent, plus the general chat, gets today's date in
  `APP_TIMEZONE` (default Asia/Kolkata). They no longer answer "I can't guarantee the current
  date".
- **English only.** Every agent prompt, the critic included, now requires English output. The
  Qwen tier was slipping into other scripts mid-answer (e.g. "$5/月").
- **The Writer sticks to what it was given.** When summarizing research or code, it describes
  only what is actually there, and says so when something is missing or cut off.

## [3.3.4] - 2026-10-23

### Fixed (@team mode)
- **The planner now builds parallel plans.** It is told to add a dependency only when a step
  needs another step's *output*. Independent work, like market research and writing code from
  the stated requirements, now runs at the same time. Before, plans were usually a straight
  chain (research → coder → writer), so Parallel had nothing to run concurrently. The fallback
  plan also runs planning and research together.
- **Each agent does only its own step.** Step prompts now lead with "YOUR STEP" and tell the
  agent not to do the other agents' work. Before, the first agent got the whole task first and
  tried to do all of it: the Research agent wrote its own landing page and summary,
  duplicating the Coder and Writer and running out of tokens.
- **A cut-off code block no longer swallows the next agent.** When a step stops inside a code
  block (token limit), the block is closed with a note. Before, the next agent's heading and
  code fence showed up as literal text inside the unclosed block.

## [3.3.3] - 2026-10-23

### Fixed (Next.js chat)
- **The DSA toggles show the real state.** A new `GET /chat/options` endpoint returns the server
  defaults (`AGENT_CRITIQUE`, `AGENT_REFLECT`, `AGENT_PARALLEL_STEPS`). Before, an untouched
  toggle looked "off" even though the feature was running. Each toggle's tooltip says which
  routes it affects.
- **The Reasoning badges are honest.** Each route sends a `config` event describing what
  **actually** ran:
  - direct replies (facts, commands, documents, general chat): no badges;
  - a specialist agent: Critic/Reflect, never Parallel;
  - `@team`: all three.
- **Reflect works.** Specialist agents run the critic **and the revision before the answer
  streams**. You get the improved answer instead of the draft plus an after-the-fact note.
  Critic and reflection steps appear in the Reasoning view.
- **Parallel works.** In streaming `@team` mode, the steps of one dependency level now really
  run concurrently, and each result streams as it finishes. With Parallel off they run one
  by one. Before, streaming team mode was always sequential, whatever the toggle said.
- **No more blank answers.** If a model streams nothing (reasoning models sometimes spend the
  whole budget thinking), the agent retries once on the fast tier. If that is also empty, the
  user sees "The model returned an empty answer…" and no blank message is saved to history.
  This applies to specialist agents, `@team` and general chat.
- **Tool markers stay out of the answer.** `⚙️ web_search`-style activity goes to the Reasoning
  view instead of being glued into the answer text. Also fixed "Routing to Research Agent
  agent".
- **Code survives streaming.** SSE escaping now escapes backslashes too, and the client reverses
  it in one pass. A literal `\n` inside code (e.g. `print("a\nb")`) used to turn into a real
  line break. A stray `\r` no longer shows up as text.
- **Guest chats survive pod switches and deploys.** Conversation memory is stored in Redis
  (`llama:conv:<id>` with a 7-day TTL, setting `CONV_MEMORY_TTL`), so both API replicas see the
  same guest history. It falls back to the local JSON file when Redis isn't configured.
- **Deploys don't cut answers mid-stream.** The API Pod waits 10 s in `preStop` (so no new
  stream lands on it), uvicorn drains in-flight streams for up to 120 s
  (`--timeout-graceful-shutdown`), and `terminationGracePeriodSeconds` is 150.

## [3.3.2] - 2026-10-23

### Fixed
- **Sign-up/login returned "Internal server error" in Kubernetes.** The `DATABASE_URL` secret
  used the docker-compose host (`postgres`), which doesn't resolve in the cluster
  (`Temporary failure in name resolution`). Registration and login failed, and chat history
  silently stopped being saved. Added a `postgres` Service alias next to `postgres-service`,
  so both hostnames work.
- **Docker-link env vars no longer clobber config.** App Pods (api, web, mcp-server, frontend)
  set `enableServiceLinks: false`. Services named `postgres`, `redis` and `phoenix` would
  otherwise inject values like `POSTGRES_PORT=tcp://10.x.x.x:5432`, which crashed
  `app.py`'s `int(POSTGRES_PORT)`. `web.yaml` also sets `POSTGRES_PORT=5432` explicitly.
- **Auth errors are visible now.** When the database is unreachable, register, login and
  token refresh return **503 "Database unavailable"** instead of a generic 500, and log the
  cause to stderr (`kubectl logs`). Before, it only went to `logs/audit.log` inside the Pod.
- **The deploy now checks the database.** `k8s/wait-rollout.sh` connects to Postgres from the
  API Pod after the rollout and **fails the job** if it can't, instead of reporting a
  "healthy" deploy where nobody can log in.

## [3.3.1] - 2026-10-23

### Fixed
- **Redis in Kubernetes:** added a `redis` Service alias next to `redis-service`. The
  `REDIS_URL` secret used the docker-compose host (`redis://redis:6379/0`), which did not
  resolve in the cluster. Every Redis call failed silently and each API Pod fell back to
  its own memory, so WebSocket logins could fail with 2 replicas, and rate limits, lockouts
  and facts weren't shared. Both hostnames now work.
- **CD rollout timeout:** the deploy jobs waited 300s, but the API's 2 replicas start one
  after another with a 10-minute startup budget each, so a healthy rollout was reported as
  failed. Waiting now lives in `k8s/wait-rollout.sh`: 900s for the API, 600s for the others.
  On failure it prints Pod status, events and logs (also as GitHub annotations), and it
  warns when the API can't reach Redis.
- **Cost tracking:** `qwen/qwen3.8-27b` (the STRONG tier default) now has its Groq price
  ($0.80 in / $4.00 out per 1M tokens) instead of the generic $0.15 / $0.60 fallback, which
  under-reported its cost by about 6x.

## [3.3.0] - 2026-10-22

### Security & multi-user isolation
- JWTs can no longer be forged: no hard-coded fallback secret in production, refresh
  tokens are rejected as access tokens, tokens for deleted users return 401.
- Every user (and every anonymous browser, via an HttpOnly `guest_id` cookie) now has
  private conversation history, facts, uploaded documents, RAG/semantic caches and
  multi-agent caches. Previously all guests shared one identity and facts/caches leaked
  across users.
- `read_document` and `/documents/read` are confined to the caller's own uploads.
- WebSocket identity comes from a short-lived single-use ticket (`POST /ws/ticket`) or
  the auth cookie, never from the URL path. Presence broadcasting was removed, and
  `/ws/status` returns counts only.
- `/mcp/tools` requires login; `/metrics/json` and `/monitoring/dashboard` require
  admin. Prometheus `/metrics` returns 404 through any proxy (`METRICS_ALLOW_PROXIED=1`
  turns this off). The MCP server is no longer exposed by Nginx or the Ingress.
- New accounts need passwords of at least 8 characters.
- The frontend retries once via `/auth/refresh` on a 401; the API also renews expired
  access cookies transparently.

### Chat & agents
- An agent picked in the UI is always honoured (`ChatRequest.agent`, previously ignored
  unless keyword routing agreed). `agent: "team"` or an `@team ...` message runs the
  full agent team, streaming each agent's progress as `agent_start` / `agent_done`
  events.
- Agent and LLM token streams run on worker threads, so one user's stream no longer
  blocks the event loop for everyone else.
- Next.js chat: the DSA control panel (critic / reflection / parallel / agent) and the
  live reasoning view are wired into the main chat. The sidebar refreshes after every
  reply. Fact keys and conversation ids are URL-encoded. Dead chat components removed.

### RAG
- Hybrid search adds BM25 keyword-only hits (same user only) instead of only re-scoring
  vector hits. The BM25 index is rebuilt after uploads and deletes and every
  `RAG_BM25_TTL` seconds. `rank_bm25` is now actually installed.
- Fixed `TypeError` on tied retrieval scores; added the missing `get_cached_answer` /
  `get_context_for_document`; Corrective-RAG verification (`RAG_CRAG=1`) wired in.

### Monitoring
- LLM token usage and estimated cost are recorded for every Groq call
  (`llama_agent_llm_tokens_total`, `llama_agent_llm_cost_usd_total`, and the
  `/monitoring/dashboard` cost section). This uses current Groq list prices and can be
  overridden with `GROQ_PRICING`.
- Request metrics use route templates (bounded label cardinality).
- `ApiDown` now fires when the API is down (`up == 0`), not only when the target
  disappears. New `HighLLMSpend` alert.
- Compose binds Grafana/Prometheus/Phoenix to `127.0.0.1` (reachable locally, not publicly).

### Deployment
- Fixed the Postgres password mismatch: `DATABASE_URL` carries no password; it is
  injected from `POSTGRES_PASSWORD(_FILE)` in Compose, k8s and the Streamlit UI.
- Ingress: a separate non-rewriting Ingress for `/ws` (WebSocket timeouts) and
  `/streamlit` (served under `--server.baseUrlPath=streamlit`).
- Nginx: generates a self-signed cert when none is provided (`SSL_CN`); proxies
  `/openapi.json`.
- `.dockerignore` fixed (inline comments made it ineffective, so secrets/uploads could
  enter the image).
- Postgres backup CronJob fixed (it exited 127); dumps are written atomically and
  rotated (7 kept).
- CD: every deploy pins the immutable `:<git sha>` images with `kubectl set image`, so
  rollouts always happen. Secrets are passed via env, a frontend typecheck/lint/build
  job was added, tests install `requirements.txt` and report coverage.
- `k8s/deploy.sh`: namespace first, waits for cert-manager/ESO CRDs, builds Secrets
  from `secrets/*.txt` and fails on missing or placeholder values.
- k8s API memory raised (512Mi → 1.5Gi limit); uploads use an emptyDir (documents live
  in ChromaDB). Streamlit runs 1 replica (in-process sessions).

### Dependencies & runtime
- **Python 3.11 is now required** (Docker image and CI). Python 3.10 reaches end-of-life in
  October 2026, and the security fixes below (Pillow 13, Streamlit 1.66) need 3.11 or newer.
- `python-jose` → `PyJWT` 2.15.1. python-jose is unmaintained and has an unfixed HMAC/RSA
  key-confusion bug (PYSEC-2026-4298). Dropping it also removes `ecdsa` (PYSEC-2026-1325).
  Existing tokens stay valid (same HS256 format).
- `arize-phoenix` → `arize-phoenix-otel`. The API only uses the tracing client; the Phoenix
  UI runs as its own container. This removes the strawberry-graphql and pydantic-ai-slim
  CVEs. It also fixes tracing on Python 3.11, where the full package's `phoenix/__init__`
  crashed at import, so tracing was silently switched off.
- Pillow 13.0.0 (35 CVEs), Streamlit 1.66.1, mcp 1.30.0 (CVE-2026-59951),
  opentelemetry-instrumentation-fastapi 0.66b1 → opentelemetry-api 1.45.1 (CVE-2026-114603).
- CI: `pip-audit` ignores transformers PYSEC-2026-4174 (no upstream fix; it only affects
  `load_custom_generate`, which this project never calls). `.gitleaksignore` records one
  reviewed false positive (a `kubectl wait --for=condition=…` flag in `k8s/deploy.sh`).

## [3.2.0] - 2026-08-13

### Added
- **OptimizedRAG** — production RAG pipeline replacing the basic retriever, with 7 optimizations: hybrid search (BM25 + dense vectors), cross-encoder reranker, contextual retrieval with semantic chunking, query decomposition, corrective RAG (CRAG), conversational retrieval, and a semantic cache. Activated via `USE_OPTIMIZED_RAG=true` (set in `k8s/api.yaml` and `docker-compose.yml`).

### Changed
- CI builds and pushes `ravneet231/llama-agent` on every push to `main`. *(Correction: the
  manifests actually pinned `:v7` with `IfNotPresent`, so new builds were not rolled out;
  fixed in 3.3.0 with `:<git sha>` deploys.)*
- ChromaDB ONNX embedding model is pre-downloaded at image build time (CI has fast network) so runtime pods no longer re-download it on cold start.

### Fixed
- Semantic Cache now actually hits on repeat questions (it previously embedded the answer instead of the query, so the cache never matched).
- GitHub Actions Docker build no longer `COPY`s a gitignored local model dir, so the build succeeds in CI.

## [3.1.0] - 2026-07-23

### Added

#### Testing
- End-to-end tests (`tests/test_e2e.py`)
  - Authentication flow tests
  - Chat flow tests
  - RAG pipeline tests
  - Security flow tests
  - Memory flow tests
  - Monitoring flow tests
- Security tests (`tests/test_security.py`)
  - SQL injection tests
  - XSS protection tests
  - Input validation tests
  - Authentication security tests
  - Rate limiting tests
  - File upload security tests
  - CORS tests

#### Security
- Security hardening module (`modules/security_hardening.py`)
  - Input sanitization (SQL injection, XSS, command injection)
  - Filename sanitization (path traversal, special characters)
  - Brute force protection
  - Security headers (HSTS, CSP, X-Frame-Options)
  - Audit logging
  - CSRF protection

#### Performance
- Caching module (`modules/cache.py`)
  - In-memory cache with TTL
  - Redis cache support
  - Response caching
  - Query result caching
  - Embedding caching
  - Cache statistics

#### Monitoring
- Advanced monitoring module (`modules/advanced_monitoring.py`)
  - Custom alerts (email, Slack, webhook)
  - Business metrics tracking
  - Cost tracking (Groq API spending)
  - SLA monitoring
  - Alert deduplication
  - Monitoring dashboard

#### Database
- Database improvements module (`modules/database_improvements.py`)
  - Migration manager (Alembic-like)
  - Backup strategy
  - Connection pooling
  - Query optimizer
  - Database health checks
  - Initial migrations (users, chat_history, documents, api_keys)

#### CI/CD
- CI/CD improvements module (`modules/cicd_improvements.py`)
  - Feature flags manager
  - A/B testing support
  - Deployment manager
  - Rollback capability
  - Deployment history

#### Documentation
- CHANGELOG.md (this file)
- CONTRIBUTING.md
- Updated README.md with new features

### Changed
- Enhanced GitHub Actions workflow with better error handling
- Improved test coverage

## [3.0.0] - 2026-07-23

### Added
- Phoenix (Arize AI) observability
- OpenTelemetry integration
- ChromaDB vector store
- Advanced RAG features (re-ranking, sub-question decomposition, router engine)
- Security hardening (file validation, rate limiting)
- Monitoring dashboards (Prometheus, Grafana)
- Loki + Promtail log aggregation
- 11 Docker containers
- CI/CD pipeline (GitHub Actions)
- REST API (FastAPI)
- WebSocket support
- JWT authentication
- API documentation

### Changed
- Replaced Langfuse with Phoenix for observability
- Updated Docker networking for Phoenix connectivity

### Fixed
- ChromaDB ONNX embedding model download in Docker
- Phoenix traces not showing (network isolation issue)
- Web container network configuration

## [2.0.0] - 2026-07-22

### Added
- ChromaDB vector store for semantic search
- Advanced RAG features
- Re-ranking with CrossEncoder
- Sub-question decomposition
- Router query engine

### Changed
- Improved document chunking
- Enhanced search accuracy

## [1.0.0] - 2026-07-21

### Added
- Initial release
- Streamlit chat interface
- Groq API integration
- File upload support
- Basic RAG pipeline
- PostgreSQL database
- Redis caching
- Docker setup

---

## Version History

| Version | Date | Description |
|---------|------|-------------|
| 3.1.0 | 2026-07-23 | Production hardening (tests, security, monitoring) |
| 3.0.0 | 2026-07-23 | Phoenix observability + advanced RAG |
| 2.0.0 | 2026-07-22 | ChromaDB + advanced features |
| 1.0.0 | 2026-07-21 | Initial release |
