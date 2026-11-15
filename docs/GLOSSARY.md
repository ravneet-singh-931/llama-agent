# Glossary

Every term below is used in the code and in the docs, and several are used
without ever being defined. This file defines them in the order a newcomer
meets them, and says **where in the repo each one lives** so the definition can
be checked rather than trusted.

If a term here does not match the code, the code is right and this file is
wrong. Every entry names its source.

---

## The two halves

**Backend** — `api.py` and `modules/`. A FastAPI server. Speaks JSON over HTTP
plus one WebSocket. Has no pages of its own.

**Frontend** — `frontend/`. Next.js 16, 34 `.tsx` files, 6 routes
(`/`, `/login`, `/agents`, `/dashboard`, `/documents`, `/settings`). Talks to the
backend over `fetch`. Verified: `find frontend/src/app -name page.tsx` returns
those six.

They are separate programs in separate containers. The frontend holds **no**
secrets and makes **no** model calls; it only calls the backend.

---

## Retrieval (RAG)

**RAG** — Retrieval-Augmented Generation. Instead of asking the model to
remember something, you *look it up first*, paste the result into the prompt, and
ask the model to answer **from that text**. It turns "what does my uploaded PDF
say?" into a lookup plus a reading comprehension question.

**Chunk** — a document is cut into overlapping pieces (a few hundred words)
because an embedding represents a passage, not a whole book.

**Embedding** — a list of numbers representing meaning. Two passages about the
same thing have numerically close embeddings. This is how search finds relevant
text without matching keywords.

**Vector store** — the thing that holds embeddings and answers "which chunks are
closest to this one?" — `modules/vector_store.py` (ChromaDB).

**`SimpleRAG`** — the baseline pipeline in `modules/rag.py`: chunk, embed, store,
retrieve top-k, answer. Small and readable.

**`OptimizedRAG`** — the upgraded pipeline in `modules/rag_optimizations.py`
(1704 lines, the second-largest file after `multi_agent.py`). Adds the layers
below. `SimpleRAG` is the fallback when it fails to construct.

**Grounding** — proving an answer is *supported by the retrieved text* rather
than invented. `modules/grounding.py` checks this. Ungrounded answers are the
"confidently wrong" failure mode.

**Citation** — the chunk a sentence came from, shown to the user so they can
check it. `modules/citations.py`.

**Context budget** — how many tokens of retrieved text fit in a prompt. Over
budget means truncating; `modules/context_budget.py` decides what to drop.

**Document scope / identity** — *which document is the user asking about?*
`modules/doc_scope.py` and `modules/doc_identity.py`. Asking "what does it say
about pricing?" is ambiguous without knowing which "it".

**PII** — personally identifiable information. `modules/pii.py` detects it so it
can be redacted before going to a third-party model.

**LLM guard** — `modules/llm_guard.py`. Screens model output before it reaches
the user (prompt injection leaking out, unsafe content).

---

## Agents

**Agent** — a specialist with its own system prompt and tool set, not a separate
model. Five exist in `multi_agent.py`: `ResearchAgent` (1142), `CoderAgent`
(1189), `WriterAgent` (1231), `AnalyzerAgent` (1267), `PlannerAgent` (1300).

**Orchestrator** — `MultiAgentOrchestrator` (1371). Picks which agent handles a
task (`route_task`) or runs several in parallel (`run_agents_parallel`, `api.py`
1432). Described in its own docstring as "like a TEAM LEADER who assigns tasks to
specialists".

**Tool** — a function the model may call instead of answering directly (search
the web, run code). The loop that drives this is `modules/agent_tools.py`
(`run_tool_rounds` / `run_tool_loop`).

**Tool round** — one cycle of: model asks for a tool → tool runs → result goes
back to the model. Bounded, because an unbounded loop is a runaway bill.

**MCP** — Model Context Protocol. A standard way to expose tools to a model over
a socket instead of importing them. `mcp_agents_server.py` is the **server**;
`modules/mcp_client.py` is the **client** the API uses to reach it.

**Runaway / loop breaker** — the guard that stops tool rounds after N cycles.

---

## Sessions and identity

**Access token** — a short-lived JWT (30 minutes) proving who you are.
`modules/auth.py`.

**Refresh token** — a long-lived JWT (7 days) used only to get a new access
token. **Known limitation:** it is never rotated and cannot be revoked except by
rotating the JWT secret, which signs everyone out. Measured and documented in
`COVERAGE_API.md`; there is an `xfail` test waiting for it to be fixed.

**JWT** — JSON Web Token. A signed blob of claims. Signed, not encrypted —
anyone can *read* it; only the server can *forge* it.

**By httpOnly cookie** — the browser stores the token in a way JavaScript cannot
read, so a cross-site scripting bug cannot steal it.

**CSRF** — Cross-Site Request Forgery. Because cookies are sent automatically,
another site can make your browser act as you. `modules/security_hardening.py`
(CSRF section) defends against this; httpOnly alone does not.

**WebSocket ticket** — a **single-use, 60-second** credential used to open a
WebSocket. A WebSocket handshake cannot carry an `Authorization` header from a
browser, and putting a real token in the URL would leak it into logs and history.
So the client posts to `/ws/ticket`, gets a disposable ticket, and puts *that* in
the URL. `api.py`: `_issue_ws_ticket` (1118), `_consume_ws_ticket` (1141).

**Brute-force protection** — counts failed logins and locks the identifier/IP
out. `modules/security_hardening.py` (~247). The store is **global and
IP-keyed**, which is why tests must snapshot and restore it.

**Rate limiter** — caps requests per window. `modules/security.py`. Tests call
`rate_limiter._requests.clear()` for the same reason.

---

## Storage

**SQLAlchemy / Alembic** — the ORM and its migration tool (`migrations/`,
`alembic.ini`).

**SQLite vs Postgres** — tests use in-memory **SQLite** because it needs no
server. Production uses **Postgres** via `modules/pg_store.py`. Different enough
(SQL dialect, concurrency, `JSONB`) that "tests pass" does not prove the Postgres
path works. This is a real gap, listed in `COVERAGE_API.md`.

**`redis_store`** — `modules/redis_store.py`. Optional shared state so several
API replicas agree. Absent when `REDIS_URL` is unset; everything has an
in-process fallback. `pop_json` is `GETDEL` (atomic read-and-delete), which is
what makes single-use tickets actually single-use.

**Shared state** — `_shared_state()` in `api.py` (134). Wraps "Redis if
configured, else a dict in this process".

**Fact / `remember` / `recall`** — long-term user facts, separate from chat
history. `/remember`, `/recall`, `/facts`. `FactMemory` in `modules/memory.py`
(381).

**Conversation memory** — the message history of one chat.
`ConversationMemory` in `modules/memory.py` (40).

---

## Operations

**`lifespan`** — `api.py` 521. Startup and shutdown code: connect the DB, build
the RAG pipeline, warm models. Bugs here appear as "the app starts but half of it
is missing".

**Circuit breaker** — `api.py` ~165. After repeated failures a dependency is
declared dead and calls fail fast instead of queueing behind a timeout.

**Phoenix / OpenTelemetry** — tracing. Each request produces spans showing where
time went. `evals/`, `modules/advanced_monitoring.py`.

**Prometheus / Grafana / Loki** — metrics, dashboards, logs. Configured in
`config/`.

**SLO / SLA** — service objectives and agreements; `modules/advanced_monitoring.py`.

**`orchestrator` in evals** — unrelated to the agent orchestrator; `evals/run.py`
orchestrates *evaluation runs*.

---

## Testing

**`journey`** — `tests/conftest.py` 45. The fixture that boots the **real app**
on a temp directory with a fake LLM, in-memory SQLite and no network. Nearly
every integration test in this repo uses it. If a probe does not use `journey`
and calls an endpoint needing the DB, it fails with `503 Database unavailable` —
that is the fixture missing, not a product bug.

**`TestClient`** — FastAPI's in-process test client. Re-raises exceptions by
default, so a test of an error path passes `raise_server_exceptions=False`.

**Sabotage** — deliberately breaking the code to check a test notices. A test
that cannot be broken this way is not testing the thing it claims to test.

**`xfail(strict=True)`** — an expected failure that **fails loudly if it starts
passing**. Used for the refresh-token one-time-use gap so that fixing it cannot
go unnoticed.

**Coverage vs comprehension** — coverage says a line *executed*; it says nothing
about whether a reader understands it. The audit measured the first. This file
is about the second.
