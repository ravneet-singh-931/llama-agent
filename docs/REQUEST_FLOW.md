# Request Flow: what happens when a user asks a question

`ARCHITECTURE.md` shows the *system*: containers, data flow, security layers.
This file shows *one request*, line by line, through the actual code. It is the
"where do I start reading" document.

Every entry point below is a real line number in `api.py` at the time of writing
(commit before this file: `a380351`). If a number has drifted, the function name
will still find it.

---

## The three ways in

| entry | route | line | who uses it |
|---|---|---:|---|
| REST chat | `POST /chat` | 1664 | anything that wants one JSON answer |
| Streaming chat | `POST /chat/stream` | 1872 | the frontend, which renders tokens as they arrive |
| WebSocket | `/ws/{user_id}` | 1165 | live chat, pushed from the server |

There is also `POST /documents/ask` (2504) — the same retrieval machinery,
narrowed to one uploaded document.

---

## `POST /chat`, step by step

```python
async def chat(request, http_request, user = Depends(optional_auth)):
```

**1. Who is asking.** `optional_auth` (not `require`) — the endpoint works
logged-out. `_request_uid(user)` turns the user (or the guest cookie) into an
owner id. Every document lookup after this is scoped to that id; that is the
mechanism behind "one user cannot retrieve another user's document".

**2. Rate limit and brute force.** `require_api_key()` and the middleware layer
from `modules/security.py` / `security_hardening.py` run before the body matters.

**3. Input screening — audit, not blocking.** Two checks, and the comments here
matter because the names suggest more than they do:

- `strip_html_vectors()` is **destructive** (it HTML-escapes quotes and braces),
  so it is used only as a *signal*: if it changes the message, an audit entry is
  written, and then the **original** text is used so the agent is not degraded.
  It is explicitly **not** prompt-injection defence.
- Prompt-injection detection is also **audit only, non-blocking**. The real
  structural defence lives in the agent modules (`PROMPT_INJECTION_GUARD` plus
  delimiters around untrusted text).

This is a place where a reader could easily assume "we sanitise input" and be
wrong. The protection is real; it just is not here.

**4. Retrieve.** `rag.get_context(query)` runs in a thread
(`asyncio.to_thread`) under `asyncio.wait_for(..., timeout=DOWNSTREAM_TIMEOUT)`.
Two reasons, both important: retrieval is **blocking** (so a thread, or the event
loop stalls for every other user), and it is **bounded** (so a slow vector store
cannot hold a request open forever).

**5. Which path?** The request then goes one of three ways:

| condition | path |
|---|---|
| the message looks like a **document question** | `modules/doc_answers.py`, with follow-up tracking so "what about the pricing column?" still knows the topic |
| the message names a **specialist** intent | `route_multi_agent()` → one agent, or all matched agents concurrently via `run_agents_parallel` (1432) |
| otherwise | plain RAG + the general agent |

**6. Answer, then post-checks.** Grounding (`modules/grounding.py`) and citations
(`modules/citations.py`) run on the answer. These exist because a model will
happily invent a number; the check is what makes "the figure is supported by the
source" a claim the system can make.

**7. Record.** Memory (`ConversationMemory`) and metrics, then the response.

---

## `POST /chat/stream` — same, but with a reason to be different

Same steps, but the answer is yielded as server-sent events. Two consequences
that matter when reading it:

- **Errors cannot be an HTTP status.** The status line is already sent before the
  first token, so a mid-stream failure has to arrive *as content*. It does — but
  not as a distinct `event: error`. Verified at `api.py` 2136, 2197, 2221: each
  `except` logs the exception and yields a normal `data:` line carrying a
  user-facing apology ("⚠️ The agent team could not complete this task").
  So **a client cannot tell a failure from an answer by inspecting the event
  type** — it must look at the text. Worth knowing before writing a frontend
  error handler that waits for `event: error` and would wait forever.
- **Backpressure is real.** A slow client leaves the generator suspended.

Both routes are covered by the parity tests added in an earlier part, because the
first version of these tests compared the two routes to each other — which cannot
catch a bug they share.

---

## The WebSocket

```python
@app.websocket("/ws/{user_id}")   # 1165
```

The path contains a `user_id`, and **it is ignored**. Identity comes from, in
order: a **ticket** (`?ticket=`, single-use, 60s), an **access token**
(`?token=`), or the access-token **cookie**. If none is valid the socket closes
with code `1008`.

Why a ticket exists at all: a browser cannot set an `Authorization` header on a
WebSocket handshake, and putting a real JWT in a URL writes it into access logs
and browser history. So the client trades a real credential for a disposable one
at `POST /ws/ticket` (1155) and puts *that* in the URL.

---

## Where a beginner should read, in order

1. `modules/rag.py` (353 lines) — the whole RAG idea, small.
2. `api.py` `chat()` at 1664 — read only until step 5 above.
3. `modules/doc_answers.py` — how a document question is actually answered.
4. `multi_agent.py` `MultiAgentOrchestrator.route_task` (1389) — how a specialist
   is chosen, and why the task is delimited as untrusted data inside the prompt.
5. `modules/agent_tools.py` — the tool loop, and why it is bounded.
6. `rag_optimizations.py` **last** — 1704 lines of accumulated improvements; it
   is far easier to read once the baseline makes sense.

---

## Things that surprise people

| surprise | why |
|---|---|
| `/chat` works logged out | `optional_auth`, not `require_auth` |
| `user_id` in the WebSocket URL does nothing | identity comes from the credential |
| a URL can be a credential | that is what `?ticket=` is — hence single-use, 60s |
| tests use SQLite, production uses Postgres | so "tests pass" does not prove the production DB path |
| `strip_html_vectors` does not sanitise the message | it is a signal; the original text is used |
| `optimistic`-looking `except: pass` everywhere | `modules/silent.py` exists to make each one state what it swallows |
