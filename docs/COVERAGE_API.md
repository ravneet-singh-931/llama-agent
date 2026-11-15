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

`api.py` is **no longer the interesting gap**. It sits at 74%, and what remains
is the print-and-log layer of startup plus `except` bodies. The security
primitives were at 67% and are now **88%** (`SECURITY_FINDINGS.md`, part B) —
that was a better use of the time than the last 26% of `api.py`.

What is left, by measured coverage:

| module | coverage | why it is or is not worth it |
|---|---:|---|
| `modules/advanced_rag.py` | 28% | Retrieval quality — wrong answers, not crashes |
| `modules/mcp_client.py` | 36% | Live MCP calls never exercised end to end |
| `mcp_agents_server.py` | 52% | Server side of the same path |
| `modules/cicd_improvements.py` | 56% | Feature flags |
| `modules/websocket.py` | 62% | Real-time chat |
| `modules/pg_store.py` | 66% | **Real Postgres path** — tests use SQLite |
| `modules/rag.py` | 67% | The baseline RAG |
| Frontend | **0 of 34 `.tsx`** | Deferred by the owner, deliberately |

`api.py`'s remaining ~328 lines: ~90 `except` bodies, ~13 `lifespan` prints, ~225
scattered helpers and rare branches. Roughly **13 lines are worth covering.**

## Open product decisions (not defects to fix unilaterally)

- **Refresh-token rotation.** The refresh token is never rotated and has no
  revocation list, so it can be replayed for its full 7-day life and logout does
  not revoke it — see "The auth routes" above. Recorded with an
  `xfail(strict=True)` so fixing it fails loudly.

## The lesson worth keeping

Line coverage counts executed lines. It does not know which lines decide **who is
allowed to see what**. Ten tests over a `secure` handler are worth more than a
hundred over an `except` block.

## `/chat` — the non-streaming route, and the four divergences it was hiding

`/chat` was the last large untested block: 67 lines, no behaviour tests at all.
It is also a second implementation of an interface `/chat/stream` already had,
and the frontend calls **both** (`frontend/src/lib/api.ts`: `chat()` posts
`/chat`, `chatStream()` posts `/chat/stream`). The chat box uses the streaming
one, so a defect on `/chat` was invisible to real users — and a defect on
`/chat/stream` was invisible to the tests, because nothing compared the two.

`tests/test_chat_route_parity.py` (31 tests) posts the **same message to both
routes** and requires the same answer.

### What was actually broken

Measured on the same user, same message, before any change:

| message | `/chat` | `/chat/stream` |
|---|---|---|
| `remember colour = blue` | Remembered | Remembered |
| `remember food is pizza` | Remembered | **"Usage: remember key = value"** |
| `remember my name is Ravi` | Remembered | **"Usage: remember key = value"** |
| `recall my name` | reads `user's name` | **reads literal `"my name"` → nothing** |
| `recall nothing-here` | `"✅ nothing-here: I don't remember..."` | `"I don't remember..."` |
| `hi` | canned greeting | **no greeting branch at all — full model call** |

So the route the chat box actually uses **refused two of the three `remember`
forms its own usage text advertises**, could not recall a name it had just
stored, and paid a model round-trip for "hi".

### The fix that holds

Four behaviours, one implementation each:

| helper | replaces |
|---|---|
| `_parse_remember` | the `=`-only parser on `/chat/stream` vs three forms on `/chat` |
| `_parse_recall_key` | `"my name"` → `user's name`, which only `/chat` did |
| `_recall_reply` | the reply text, including the miss |
| `_is_greeting` | a raw substring test, now used by both routes |

### `recall` of a miss wears a success tick

`FactMemory.recall()` does **not** return `None` on a miss. It returns the
sentence `"I don't remember anything about '<key>'"` — a *truthy string*. So
`if value:` in `/chat` always took the success path:

```
✅ nothing-here: I don't remember anything about 'nothing-here'
```

A checkmark on "I don't remember". The first version of `_recall_reply` that I
wrote had **the same bug**, because I wrote `if value:` again. It was caught by
sabotage, not by review (see S2 below).

> A lookup need not return a falsy value on a miss. Read the store; don't assume.

### `"you"` contains `"yo"`: the greeting hijack

The greeting check was:

```python
elif any(g in request.message.lower() for g in greetings):
    response = "Hello! How can I help you today?"
```

A **raw substring test**. The greeting list contains `hi`, `yo` and `sup`, so any
message containing those two letters — not those words — was answered with the
canned greeting *instead of being answered at all*:

| message | answered with |
|---|---|
| `can you help me write a function` | "Hello! How can I help you today?" |
| `do you know python` | "Hello! How can I help you today?" |
| `explain this code to me` | "Hello! How can I help you today?" |
| `summarise this week` | "Hello! How can I help you today?" |

`this`, `think`, `while`, `machine`, `your`, `support` all trigger it. The model
was never called. `_is_greeting` matches whole words **and** the whole message:
a greeting may be followed only by filler (`hi there`, `hello again`), so
`hi, write me a function` is still a request.

### A regression I introduced, and what caught it

The new `/chat/stream` greeting branch was placed **before** the UI override was
applied. Picking "Coder Agent" and typing `hello` then returned the canned
greeting and the agent never ran. `test_multitenancy::test_stream_config_reports_what_actually_ran`
failed, which is why the branch now yields to an explicit `agent`/`team` choice:

```python
_explicit_agent = (agent_override or "").lower() == "team" or agent_override in AGENT_KEYS
if not _explicit_agent and _is_greeting(request.message):
```

This is the one place where adding a branch to a shared codepath broke an
existing test, and the test was right.

### The fixture bug that hid the whole general stream path

`tests/conftest.py` faked the streaming model with `async def`:

```python
async def _fake_stream(message, **kw):   # yields an ASYNC generator
    yield "echo: "
```

but `run_agent_stream` is a plain `def` (a **sync** generator) and the route
consumes it through `aiter_in_thread`, which does `for item in make_iter()` on a
worker thread. Iterating an async generator that way is a `TypeError`. Every
journey test that reached the general streaming path failed — and the fake could
never have worked, so that path was silently untested. The fix is one word.

### Sabotage results

Each was applied by **line number** with a content assertion first, then
`compile()`d, because (as recorded earlier in this file) a sabotage that does not
compile proves nothing and reads as a pass.

| # | sabotage | caught by |
|---|---|---|
| S1 | stream route back to `=`-only `remember` | 3 tests, incl. 2 `remember` forms |
| S2 | `/chat` puts a tick on a failed recall | `test_an_unknown_recall_reads_the_same_on_both_routes` |
| S3 | `/chat` stops mapping `my name` → `user's name` | `test_recall_my_name_works_on_both_routes` |
| S4 | `/chat` greeting back to a substring test | 9 tests |
| S5 | `/chat/stream` loses its greeting branch | 8 tests |
| S6 | `/chat` recall key not normalised | `test_recall_my_name_works_on_both_routes` |
| S7 | conftest fake is `async def` again | 5 tests |
| S8 | greeting ignores an explicit agent choice | `test_multitenancy::test_stream_config_reports_what_actually_ran` |

**8 of 8 caught.** Two survived on the first attempt and both were my error:

1. **S2 survived** because my test only asserted *that the two routes agree*.
   With `/chat` sabotaged and `/chat/stream` using the shared helper, **both**
   returned the ticked string — they agreed, and both were wrong. A parity
   assertion is not a correctness assertion. The test now asserts the answer
   itself (`"✅" not in text`, `starts with "I don't remember"`) *and* the parity.

2. **S8 exposed the regression above** — it was a real bug, not a test gap.

### One claim I am not making

A single full-suite run with `--cov` reported `1 failed, 1100 passed`; two
subsequent full runs (with and without `--cov`) reported `1101 passed, 0 failed`,
and the failing test's name was lost to output that did not reach the log. I am
recording it rather than pretending it did not happen, and rather than naming a
test I did not see fail.

### Measured after this part

```
tests/test_chat_route_parity.py   -> 31 passed
full suite (CI flags)             -> 1101 passed, 6 skipped
api.py coverage                   -> 67%   (was 64.4%)
TOTAL (--cov=., the CI gate)      -> 87%   (gate: 74%)
flake8 on the new test file       -> 0
```

## The MCP tool-call path — correct code, one real defect behind it

`/mcp/tools` is the only route where a **client-supplied string becomes a tool
name** sent to another server. Before this part it had two tests, and both
asserted the same thing: an anonymous caller gets `401`. The successful call, the
timeout, the error mapping and the circuit breaker had never executed in a test.

`tests/test_mcp_tool_call.py` — 13 tests, no network and no LLM (`call_mcp_tool`
is faked at the api boundary).

### What was already right

The route needed **no changes**. It maps failures correctly and the breaker
behaves exactly as designed. Measured, now pinned:

| behaviour | result |
|---|---|
| breaker after 3 consecutive failures | opens; next call is `503` **without** calling the client |
| a success | resets the failure count to 0 |
| timeout | `504`, not `502` |
| `HTTPException` from the client | re-raised unchanged (a `404` stays a `404`) |
| any other exception | `502`, internal message not leaked |
| feature flag off | `404` (not `403`, not `500`) |

### Ordering that is not obvious from the code

**Authentication runs before the feature flag.** `/mcp/tools` is
`Depends(get_current_user)`, so a stranger hitting a *disabled* route gets `401`,
not `404`. `/mcp/status` is optional-auth, so it returns `404`. Both are now
pinned, because reading the handler top-to-bottom suggests the flag comes first.

### There is no allowlist

`_MCP_TOOL_TO_AGENT` looks like one but is only used to decide **which dashboard
counter to bump**. `nonexistent_tool` is forwarded to the MCP server verbatim and
returns whatever the server decides. Pinned as the current contract, not
endorsed — if it should be an allowlist, that test is the one to change.

### The real defect: the dashboard counter resets to 1 — and the fallback that was never there

Found by chasing a coverage line, not by looking for a bug.

`/agents/stats` ("Agent Team" dashboard) reports `tasks_handled` per agent. After
three successful `coder_agent` calls, measured:

```
app.state.agent_stats   -> {"handled": {"coder": 1}}
/agents/stats           -> tasks_handled: 0
```

**The dashboard shows zero tasks while the calls are running.**

Cause, traced through the **old** `_record_agent_usage` (removed by the fix
below; kept here because the failure shape is the lesson):

1. `_load_shared_dict(key, default)` returns `default` when Redis is absent —
   and the caller passes a **newly built** `{"handled": {}, "last_used": {}}` on
   every call, so the "shared" dict is empty each time.
2. `handled = shared.setdefault("handled", {})` therefore starts empty.
3. `handled[key] = handled.get(key, 0) + 1` writes `1`.
4. `stats["handled"] = handled` **replaces** `app.state.agent_stats["handled"]`
   with that one-entry dict instead of merging into it.

The in-process mirror is written *from* the empty shared dict, not *into* it.
That is the bug in one sentence.

It was first pinned by `test_the_counter_does_not_accumulate_without_redis`,
which asserted the **current, wrong** behaviour and said in its docstring that
fixing the merge must invert it. That pin worked exactly as intended: the moment
the code was fixed, that test failed and forced the expectation to be rewritten
as `test_the_counter_accumulates_without_redis`.

> A defect that is recorded fails loudly when it is fixed; a defect that is
> merely described rots.

### The fix, and how deep the defect went

The cause was not the counter arithmetic — it was `_load_shared_dict`, which
gated **both** backends on one call:

```python
async def _load_shared_dict(key, default):
    try:
        val = await redis_store.get_json(key)   # None when Redis is absent
        if isinstance(val, dict):
            return val
    except Exception:
        ...
    return default        # <- the caller's FRESH, EMPTY dict
```

Every caller passed a newly built `{"handled": {}, "last_used": {}}`. So with
Redis down there was **no fallback at all**, despite the comment at `api.py:102`
claiming *"falls back to the in-process copy if Redis is unavailable, so
behaviour is unchanged when Redis is down."* The comment described an intention
the code never implemented.

The fix is one helper, used by both the writer and the reader:

```python
async def _shared_state(name, key) -> dict:
    """Redis' dict when it answers, otherwise this process's own copy."""
    ...
    local = getattr(app.state, name, None)
    if not isinstance(local, dict):
        local = {}
        setattr(app.state, name, local)
    return local
```

Three properties, each measured:

| property | measured |
|---|---|
| no Redis: counter accumulates | 1 → 2 → 3 across three dispatches |
| no Redis: dashboard agrees | `/agents/stats` reports `tasks_handled: 3` |
| Redis present: still used | value written to the store; local copy untouched |

That last row has a subtlety worth keeping: on a **cold** Redis the first call
seeds the store from the local copy (`99 + 2 = 101`), which is what lets a
replica that just started join the running total instead of restarting at zero.
My first version of that test asserted `2` and failed — the test was wrong about
the code, not the other way round. Corrected, and the comment in the test says so.

### Sabotage results for the fix

| # | sabotage | caught by |
|---|---|---|
| D1 | dashboard reader takes a fresh default again | `test_the_dashboard_reports_the_real_count` |
| D2 | writer starts from an empty dict again | 4 tests |
| D3 | `_shared_state` never returns the stored local copy | 4 tests |
| D4 | counter set to 1 instead of incremented | 2 tests |
| D5 | `last_used` not recorded | `test_the_dashboard_reports_the_real_count` |
| D6 | unknown agent key no longer skipped | `test_an_unknown_agent_key_is_skipped_by_the_recorder` |

**6 of 6 caught.**

### A coverage artifact, settled by substitution

The report listed `api.py:2846` — the `raise HTTPException(503)` inside the open
breaker — as **unexecuted**, even though a test asserts its exact body. Replacing
the line with a different status made that test fail, proving the line runs.

This is the second time in this audit that a `raise` line was reported as
unexecuted while demonstrably executing. Do not report dead code from a coverage
report alone.

### Sabotage results

| # | sabotage | caught by |
|---|---|---|
| M1 | `except asyncio.TimeoutError` removed | `test_a_timeout_becomes_a_504` |
| M2 | `except HTTPException: raise` removed | `test_an_http_exception_is_not_rewritten` |
| M3 | success no longer resets the counter | `test_a_success_resets_the_failure_count` |
| M4 | breaker threshold never reached | `test_the_breaker_opens_...` |
| M5 | breaker check ignored | `test_the_breaker_opens_...` |
| M6 | usage never recorded | `test_a_known_tool_records_agent_usage` |
| M7 | flag ignored on `/mcp/tools` | `test_the_flag_refuses_an_authenticated_caller_with_404` |
| M8 | flag ignored on `/mcp/status` | `test_the_feature_flag_gates_both_routes` |

**8 of 8 caught.**

### Measured after this part

```
tests/test_mcp_tool_call.py   -> 16 passed (13 for the route, 3 for the fix)
full suite (CI flags)         -> 1116 passed, 6 skipped
api.py coverage               -> 71%   (was 67%)
TOTAL (--cov=., CI gate 74)   -> 87%
flake8 on the new file        -> 0
```

## The auth routes — and a refresh token that never dies

`tests/test_auth.py` (53 tests) covers `modules/auth.py` thoroughly: password
hashing at the bcrypt byte limit, JWT signing and expiry, the user manager,
`get_current_user`, and that a refresh token cannot authenticate a request.

It never calls the **routes**. Measured before this part:

```
missing lines in api.py's auth block (990-1100) : 26
/auth/refresh                                    : had never executed in any test
register/login failure branches                  : uncovered (audits, lockout, counter)
```

After: **0 missing lines** in that block. `tests/test_auth_routes.py` — 14
tests + 1 `xfail`.

### FINDING 1 — the refresh token is never rotated and cannot be revoked

Measured behaviour of `POST /auth/refresh`:

| action | result |
|---|---|
| refresh with the cookie | `200`, new access token |
| refresh with no token | `401 Missing refresh token` |
| refresh with an *access* token | `401 Invalid refresh token` |
| refresh with the **same** refresh token ×3 | `200, 200, 200` |
| logout, then replay the token taken before logout | **`200`** |

`api.py:1078` says so in a comment: *"Keep the same refresh token (it is still
valid); only rotate access."* Nothing records issued refresh tokens, so there is
no server-side revocation list.

**What that means in practice:**

- A refresh token is valid for **7 days** (`JWT_REFRESH_EXPIRE_DAYS`, default 7).
  Replaying it refreshes the session again — indefinitely, from any number of
  clients, for that whole window.
- `POST /auth/logout` clears cookies on **that browser only**. A copy taken
  earlier keeps working. That is a normal stateless-JWT trade-off, but it is
  usually paired with rotation, and here it is not.
- If a refresh token leaks, there is no way to revoke it short of rotating the
  JWT secret, which signs every user out.

**Not changed in this part**, deliberately — it is a product decision, not a bug
fix. Rotation logs out every second client sharing a token (two tabs, phone plus
laptop), and needs a revocation store the app does not have. Recorded instead:

- `test_a_refresh_token_can_only_be_used_once` — what we *want*, marked
  `xfail(strict=True)`. When rotation is implemented this starts passing, pytest
  fails the suite, and someone must remove the marker and keep the assertion.
- `test_replaying_a_refresh_token_is_currently_accepted` — the measured reality,
  asserted so a change to it cannot slip by unnoticed.
- `test_logout_clears_the_cookies_but_does_not_revoke_the_token` — states the
  logout limitation as behaviour rather than as prose.

> An `xfail(strict=True)` is the only way to record "this is broken" that gets
> louder when it is fixed instead of quieter.

### FINDING 2 — two refreshes in one second return the same token

JWT `iat`/`exp` are **second** granularity, so a refresh in the same second as
the login produces a byte-identical access token:

```
access token 1: iat=1794732145 exp=1794733945
access token 2: iat=1794732145 exp=1794733945    (after POST /auth/refresh)
string differs : False
```

Harmless on its own — the token is still valid for its full 30 minutes — but any
test asserting "the token changed" is flaky by construction. Pinned in
`test_two_refreshes_in_the_same_second_return_the_same_token`.

### Two test bugs of mine, both found by sabotage

**1. "works" is not "was renewed."** My first refresh test called `/auth/me`
after refreshing and passed — even when `_set_auth_cookies(...)` was replaced
with `pass`, because **the old access token was still in the cookie jar and still
valid**. The test now asserts on the *response* (`set-cookie: access_token=...`),
which is the only way to tell a new cookie from a leftover one.

**2. The brute-force store is global and IP-keyed.** Every test in the file
shares one client and therefore one client IP. After the lockout test tripped,
five later tests failed with `429 Try again in 899 seconds`. The fixture now
snapshots and restores `brute_force.attempts`. Keys are `login:<identifier>`, not
the raw username — my first version looked under the wrong key and reported "not
recorded" for a route that records correctly.

### Sabotage results

| # | sabotage | caught by |
|---|---|---|
| A1 | failed login no longer recorded | `test_a_locked_out_login_is_429_and_audited` |
| A2 | lockout check bypassed | `test_a_locked_out_login_is_429_and_audited` |
| A3 | refresh accepts any token type | `test_refresh_rejects_an_access_token_in_the_body` |
| A4 | missing refresh token no longer refused | `test_refresh_with_no_token_is_401` |
| A5 | refresh stops setting the new cookie | `test_refresh_issues_a_usable_access_token` |
| A6 | logout stops clearing cookies | `test_logout_clears_the_cookies_...` |
| A7 | login failure leaks the internal message | 2 tests |
| A8 | refresh failure leaks the internal message | `test_a_database_failure_on_refresh_is_503_not_500` |

**8 of 8 caught. A5 survived the first version** — that is test bug 1 above.

### Measured after this part

```
tests/test_auth_routes.py   -> 14 passed, 1 xfailed
auth route block coverage   -> 0 missing lines (was 26)
full suite (CI flags)       -> 1131 passed, 6 skipped, 1 xfailed
api.py coverage             -> 73%   (was 71%)
TOTAL (--cov=., CI gate 74) -> 87%
flake8 on the new file      -> 0
```

## The WebSocket ticket: the Redis branch and the token fallback

A WebSocket ticket is a credential that travels in a **URL** (`?ticket=`), which
is why it is single-use and lasts 60 seconds. `tests/test_ws_ticket.py` (9 tests,
written in an earlier part) already proved the rule that matters most: **identity
comes from the ticket, never from the path.** What it could not reach:

```
missing lines in the ticket block (1110-1215) : 10
  -> the Redis branch of issue and consume
  -> the `?token=` fallback of the WebSocket handshake
```

Both are covered now by `tests/test_ws_ticket_redis_and_token.py` (6 tests,
against a fake `redis_store`). The block is at **0 missing lines**.

### What was verified, not assumed

| property | how it was checked |
|---|---|
| with Redis configured, the ticket is written **there**, not kept in memory | asserted the fake store has it *and* `_ws_tickets` does not |
| a Redis ticket can be redeemed **exactly once** | three redemptions: record, `None`, `None` |
| an unknown ticket is refused with WS `1008` | socket closed, code asserted |
| `?token=` authenticates, and the **path is still ignored** | connected as `/ws/somebody-elses-id` with your own token; identity is yours |
| an invalid token closes with `1008` | code and reason asserted |
| the access-token **cookie** authenticates the socket | browsers cannot set headers on a handshake |

`redis_store.pop_json` is `GETDEL` (atomic read-and-delete), so the replay
protection is real rather than incidental. That is now asserted instead of
inferred from a docstring.

### A test that hung instead of failing — and two wrong fixes

Sabotaging the `1008` close did not produce a red test. It produced a **run that
sat until the command timed out at 300 seconds**. `ws.receive_text()` waits for a
server that will never speak, and there is no failure to report because the test
never reaches its assertion.

A test that hangs is worse than no test: it blocks CI and says nothing. Two
repairs failed before the third worked:

1. `asyncio.wait_for(...)` around the receive — **does not work**. Starlette's
   receive is a blocking call; the timeout never fires.
2. A worker thread with a 5s ceiling — the assertion now fired, but the run
   **still hung on teardown**, because the never-accepted socket kept the
   `TestClient` portal alive.
3. The same thread, plus closing the connection from the main thread in a
   `finally` — assertion fails in ~2s and the process exits.

The remaining caveat is stated rather than hidden: **in the sabotaged state the
teardown still stalls**, because the socket was never handed a close frame. It is
recorded here so the next person does not read that stall as a new bug.

### Sabotage results

| # | sabotage | caught by |
|---|---|---|
| W1 | tickets never written to Redis | `test_a_ticket_is_written_to_redis_when_it_is_configured` |
| W2 | Redis redemption skipped | `test_a_redis_ticket_can_be_consumed_exactly_once` |
| W3 | `?token=` no longer authenticates | `test_the_websocket_accepts_a_valid_access_token` |
| W4 | invalid token no longer closes the socket | `test_an_invalid_token_closes_the_connection_with_1008` |

**4 of 4 caught.** W4 is the one that first revealed the hang.

### A process error worth recording

Four times in this part a long-running command was interrupted and left
`api.py` in a **sabotaged** state — once with a mutation still applied, three
times with a hung test process. The fix was not more care in general: it was
smaller commands, a `diff` against a backup after every sabotage, and one
sabotage per invocation instead of a loop. Reaching for `pkill` and a restore
after each step costs seconds; discovering later that a commit contains a
sabotage costs a debugging session.

### Measured after this part

```
tests/test_ws_ticket_redis_and_token.py -> 6 passed
ticket block coverage                   -> 0 missing lines (was 10)
full suite (CI flags)                   -> 1137 passed, 6 skipped, 1 xfailed
api.py coverage                         -> 74%   (was 73%)
TOTAL (--cov=., CI gate 74)             -> 87%
flake8 on the new file                  -> 0
```
