# Changelog

All notable changes to Llama Agent will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
