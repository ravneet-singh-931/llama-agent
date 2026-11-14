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

## The `agent` and `@team` streaming bodies

`tests/test_agent_stream_branch.py` (9 tests) and `tests/test_team_stream_branch.py`
(7 tests). `api.py` **61.9% → 64.4%**.

`test_chat_slo_metrics.py` already posted to both routes — but only to assert that
the **counters** moved. That is a test of the metric, not of the branch: the whole
body ran unexecuted. What is now asserted:

| behaviour | why it matters if it breaks |
|---|---|
| answer tokens stream, `[DONE]` exactly once | the client hangs or ends twice |
| tool/reasoning events go to the **reasoning view**, never into the answer text | a user reads tool output as if it were the reply |
| the per-agent `### Name` header is emitted for each team step | a team answer loses all structure |
| the `final` text is what gets persisted | the sidebar shows a different answer from the one on screen |
| only agents that emitted `agent_start` are recorded as used | usage metrics inflated for agents that never ran |
| the `@team` / `@Coder Agent` prefix is stripped from the task the model sees | the model is asked to act on the routing directive |
| the empty-answer guard fires on **both** routes | an empty bubble with no explanation |
| a failing agent/team emits a readable message and still terminates | a hung stream |
| the critic's note is reasoning, not answer text, and is **skipped** for failure notices | a critique of an error message shown as the reply |
| a failing critic does not lose the answer | an add-on takes down the main path |

Sabotages, each reverted and each caught:

| sabotage | tests that failed |
|---|---|
| team: `### header` not sent | 1 |
| team: final answer persisted as `""` | 1 |
| team: every configured agent recorded as used | 1 |
| team: empty-answer guard removed | 1 |
| agent: tool events glued into the answer | 1 |
| agent: failure `except` removed | 1 |
| agent: empty-answer guard removed | 1 |
| agent: critic reviews failure notices | 1 |
| agent: critic note appended to the answer | 1 |

### Finding: dead code, measured rather than assumed

The agent branch calls `_redactor.flush()` **twice** (lines 2065 and 2087), and
the second one does `streamed += _tail` — but `streamed` is only ever assigned
inside the **team** branch (line 1971), which never runs for the same request.

Read on its own, that looks like a live `UnboundLocalError` waiting to happen.
It is not: `StreamRedactor.flush()` swaps the buffer out and returns it
(`modules/pii.py:128`), so the second call is **always** empty. Verified by
feeding a card number and flushing twice:

```
flush() after a normal reply   -> '4111 1111'
flush() immediately again      -> ''            (always)
```

So the branch is unreachable, not a crash. Recorded by
`test_the_repeated_flush_branch_is_unreachable_dead_code` rather than deleted —
deleting it is a code change, and the finding has to be visible first.

**This is the second time in this audit that a line looked like a bug and was
not.** The first was lines 2144–2145, which coverage reported as missing while
they demonstrably ran. Both were settled by making the line *do something
observable* and checking, not by reading it harder.

## Uploads re-index at startup

`tests/test_uploads_reindex.py`, 6 tests. The loop at `api.py:559-584` had never
executed. It runs on **every boot**, and it decides whether a restart keeps or
loses a user's documents.

| behaviour | why it matters |
|---|---|
| an owner's files are re-added when missing from the vector store | a restart silently empties a user's document knowledge |
| files already indexed are **skipped** | every restart re-embeds and re-summarises the whole library, at cost |
| an unowned legacy file is reported and **never indexed** | it would land in a namespace nobody owns |
| one unreadable file does not stop the walk | one corrupt document would lose everyone's library, every boot |
| a missing `uploads/` directory is not an error | a fresh clone has none |
| a subdirectory is not passed to `add_document()` | a directory is not a document |

Sabotages: skipping `has_document` → 1 fails; indexing the orphan → 1; swallowing
per-file errors → 1; treating a directory as a file → 1.

**A trap worth recording.** The lifespan does `from modules.rag import SimpleRAG`
*inside the function*, so `monkeypatch.setattr(api, "SimpleRAG", ...)` does
nothing at all — it patches a name that does not exist on `api`. My first version
of all six tests did exactly that and silently tested the real `SimpleRAG`
(nothing was re-indexed, so every test failed for the wrong reason). The patch has
to target `modules.rag.SimpleRAG`. **A patch that matches nothing is not a patch**;
assert the target exists before relying on it.

## What is still untested, in priority order

1. **`/chat` — the non-streaming route (67 missing lines).** The streaming twin is
   now well covered; this one is not, and it shares the same command parsing.
2. **`/chat` (the non-streaming route) command branches (76 lines).** `remember` / `recall` / `show facts`
   parsing. `remember` and `recall` are covered indirectly by the facts tests;
   the failure modes (empty key, oversized value, unknown key) are not.
3. **`lifespan` (13 missing lines).** Database-down, OptimizedRAG fallback and the
   uploads re-index are covered; what remains is the baseline alert sweep and the
   success-path prints.
4. **WebSocket ticket lifecycle (28 lines).** Issue → consume → replay. A ticket
   that can be consumed twice is an auth hole, and nothing checks it.
5. **Error branches (106 lines).** Lowest value of the five. These mostly
   convert an exception into a 500 that is already logged.

## The lesson worth keeping

Line coverage counts executed lines. It does not know which lines decide **who is
allowed to see what**. Ten tests over a `secure` handler are worth more than a
hundred over an `except` block.
