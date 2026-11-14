# `api.py` coverage: what is actually missing

Measured, not estimated. At the start of this work `api.py` was 51.1%
(614/1202 lines) with 588 lines unexecuted.

## The claim I had been repeating was wrong

I had been describing the remaining `api.py` gap as **"mostly error branches."**
Counting the missing lines that actually fall inside an `except` handler:

```
missing lines inside `except` blocks : 106 / 588  (18%)
everything else                      : 482
```

**Eighteen percent.** The other 482 lines were untested *features*, and the
largest single group was conversation CRUD — `create_conversation`,
`get_conversation`, `delete_conversation`, `list_conversations`, `clear_memory`,
`get_memory`, `get_facts`, `delete_fact` — none of which had a single test.

That reframing matters, because the two categories have different risk. An
uncovered error branch makes a failure ugly. An uncovered *ownership check* makes
a failure **silent**: user A reads user B's data and nothing errors.

## What the 588 lines were

| missing | area |
|--------:|------|
| 106 | error handling (`except` bodies) |
| ~45 | conversation CRUD (entirely untested) |
| ~37 | `lifespan` startup wiring |
| ~133 | `/chat/stream` — agent-team streaming, budget refusal, timeout |
| ~76 | `/chat` — `remember`/`recall`/`show facts` commands, multi-agent routing |
| ~20 | MCP tool call |
| ~18 | WebSocket endpoint + ticket issue/consume |
| ~35 | auth: `login` edge cases, `refresh_token` |

## What now has tests

`tests/test_conversation_isolation.py` — 10 tests, all against the **real
application** (the shared `journey` fixture in `tests/conftest.py`, which boots
the actual lifespan, middleware and auth against in-memory SQLite):

* a user cannot **read** another user's conversation (404, not 200)
* a user cannot **delete** another user's conversation, and it still exists after
* one user's conversations do not appear in another's list
* `/memory` returns only the caller's conversations
* `DELETE /memory` clears only the caller's data
* `/facts` is scoped to the caller
* deleting a fact cannot remove another user's fact
* unknown conversation id is a clean **404, not 500** (the `except HTTPException: raise`)
* paging: `limit`/`offset` are clamped rather than trusted

`api.py` is now **55.5%** (667/1202).

## The sabotage results — this is the part that matters

Each test was verified by breaking the code and checking it fails:

| sabotage | result |
|---|---|
| `get_conversation` drops the owner | **4 tests fail** |
| `delete_conversation` inner ownership check removed | **1 test fails** |
| `except HTTPException: raise` removed | **2 tests fail** |
| `/facts` stops passing `user_id` | **1 test fails** |

### Two sabotages that first appeared to pass, and what they taught

**1. `delete_conversation(None, id)` passed all 6 tests.**

Not because the test was weak — because ownership is checked **twice**:
`api.py` passes `user_id` to `memory.delete_conversation`, and that method
re-checks `conv["user_id"] != user_id` itself. Passing `None` is caught by the
inner guard. Removing the *inner* guard does fail the test. Two layers, both
proven. Worth knowing which one you are relying on.

**2. `/facts` sabotage appeared to pass.**

This was my mistake, not the code's. The line
`all_facts = facts.get_all_facts(user_id=user_id)` appears **five times** in
`api.py`. `text.replace(old, new, 1)` patched the **first** occurrence — a
different handler — so `/facts` was never sabotaged and the test correctly
passed an unchanged program.

> A sabotage that does not change the code proves nothing. Assert the pattern was
> found, *and* that you patched the occurrence you meant.

Patching line 2215 by line number made the test fail immediately.

## `/chat/stream` — the biggest gap, now covered

`tests/test_chat_stream_branches.py`, 14 tests, driving the **real app** with only
`run_agent_stream` faked. `api.py` **55.5% → 58.7%**; unexecuted lines inside
`chat_stream` went from 133 to 98.

Covered for the first time: the SSE contract (`[DONE]` exactly once, the
`text/event-stream` and `X-Accel-Buffering` headers), `remember`/`recall`/`show
facts` and their usage messages, that a local command never reaches the model,
the budget refusal, `STREAM_MAX_DURATION`, the empty-answer guard, and the PII
redactor surviving a token boundary inside a card number.

Sabotages, each reverted:

| sabotage | tests that failed |
|---|---|
| general route's `[DONE]` removed | 2 |
| general empty-answer guard removed | 1 |
| budget refusal bypassed (`if False`) | 1 |
| general duration cap disabled | 1 |
| `_redactor.feed(token)` replaced with the raw token | 1 |

### Finding: the general route had no empty-answer guard

The team route and the agent route both guard against an empty model answer
(`if not final_text`, `if not full.strip()`) — measured at two sites. The general
route, the one a plain "hello" takes, had none.

Consequence: a model that streams nothing produces a stream with **no answer text
at all** and a `[DONE]`. The browser renders an empty bubble with no explanation.
It also mirrors an empty assistant message into the conversation history.

Fixed by applying the same guard and message the other two routes use, so
`chat_outcome()` classifies it identically. The test fails on the old code and
passes on the new.

### Finding, recorded not fixed: no error handling around the model call

When `run_agent_stream` raises, the client receives the two preamble frames
(`config`, `reasoning`) and then **nothing** — no error event, no `[DONE]`. The
agent route has an `except` that emits a readable message and still terminates.

`test_an_exception_mid_stream_is_an_open_finding_NOT_a_guarantee` records this.
Its assertions are written to **fail when the gap is fixed**, so the fix cannot be
made without updating the record.

### A measurement mistake worth recording

While measuring this I reported that lines 2144–2145 never execute, because the
coverage JSON listed them as missing while line 2143 was covered. That was wrong,
and the way it was caught is the point: **replacing line 2145 with a `raise`
broke the test**, which is only possible if the line runs.

The lesson is the one this project keeps relearning: **coverage output is not
evidence that a line did not run.** A test that fails when the line is touched is.
Where the two disagree, the failing test wins.

## WebSocket tickets — a URL-borne credential, now tested

`tests/test_ws_ticket.py`, 9 tests. A browser cannot send an `Authorization`
header on a WebSocket handshake, so the token has to travel in the query string —
where it lands in proxy logs, browser history and `Referer` headers. The design
avoids that with a short-lived, single-use ticket.

Two properties make it safe, and **neither was tested**:

| property | why it matters | test |
|---|---|---|
| a ticket works **once** | otherwise it is a password with a 60 s life sitting in a log | `test_a_ticket_can_be_consumed_exactly_once` |
| identity comes from the **ticket**, not the URL path | `/ws/{user_id}` is deliberately ignored; trusting it lets anyone connect as anyone | `test_the_websocket_takes_its_identity_from_the_ticket_not_the_path` |

The identity test does not settle for "the connection opened". It wraps
`handler.manager.connect` and asserts **which user id was bound** — the path
segment is a random UUID, so if the path ever wins, the assertion names it.

Sabotages, each reverted:

| sabotage | result |
|---|---|
| `user_id` taken from the path instead of the ticket | 1 fails |
| ticket made replayable (`get` instead of `pop`, `used` ignored) | 1 fails |
| expiry check removed | 1 fails |
| `/ws/ticket` opened to unauthenticated callers | 1 fails |

Also verified: an invalid or expired ticket closes with **1008** (fail closed,
never a silent anonymous fallback), the store purges expired entries, and tickets
are unique per call and bound to the caller.

The Redis path is atomic by construction — `pop_json` uses `GETDEL` with a
transactional fallback — but it is **not exercised here**: Redis is not configured
in tests, so the in-memory path is the one under test.

## Startup degradation — the promise that the app still boots

`tests/test_startup_degradation.py`, 5 tests. The design promise is that a dead
database produces **memory-only mode, not a boot loop**, so an operator can still
reach `/health` and read what is wrong. `test_boot_without_key.py` covered only
the missing-LLM-key case.

Verified:

* startup survives `init_db()` raising, and `get_db_manager()` itself raising
* the failure does **not** print `✅ Database initialized` — an operator trusts
  that line, so printing it after a failed init is the same defect as the CORS
  test that printed a checkmark
* the app is *usable* in the degraded state, not merely running: the endpoints the
  frontend calls answer instead of crashing
* `USE_OPTIMIZED_RAG=true` with a broken vector store falls back to `SimpleRAG`

Sabotages: uncaught `init_db` failure → all 5 fail; printing the success line
after a failure → 1 fails; removing the `SimpleRAG` fallback → 1 fails.

**A test-isolation bug found this way.** `test_a_fresh_boot_serves_the_endpoints_
the_frontend_calls` passed alone and failed in the full suite with **429**. The
rate limiter is a process-wide singleton keyed on client IP, and every request in
the suite shares the TestClient's address. It now resets the counters — the same
fix `tests/test_conversation_isolation.py` needed. A test that passes alone and
fails together is not flaky; it is sharing state it did not declare.

## What is still untested, in priority order

1. **The agent and team routes (98 lines).** The budget refusal, command parsing
   and the general path are now covered; the `@team` and explicit-agent streaming
   bodies are covered only for their metrics, not their behaviour.
2. **`/chat` (the non-streaming route) command branches (76 lines).** `remember` / `recall` / `show facts`
   parsing. `remember` and `recall` are covered indirectly by the facts tests;
   the failure modes (empty key, oversized value, unknown key) are not.
3. **`lifespan` (29 lines).** The database-down and OptimizedRAG-fallback paths
   are now covered; what remains is the uploads re-index loop (lines 565–584) and
   the baseline alert sweep.
4. **WebSocket ticket lifecycle (28 lines).** Issue → consume → replay. A ticket
   that can be consumed twice is an auth hole, and nothing checks it.
5. **Error branches (106 lines).** Lowest value of the five. These mostly
   convert an exception into a 500 that is already logged.

## The lesson worth keeping

Line coverage counts executed lines. It does not know which lines decide **who is
allowed to see what**. Ten tests over a `secure` handler are worth more than a
hundred over an `except` block.
