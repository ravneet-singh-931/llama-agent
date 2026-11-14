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

## What is still untested, in priority order

1. **`/chat/stream` (133 lines).** The main streaming path: agent-team fan-out,
   token-budget refusal, `STREAM_MAX_DURATION` timeout, streamed error handling.
   No test drives it with a fake agent.
2. **`/chat` command branches (76 lines).** `remember` / `recall` / `show facts`
   parsing. `remember` and `recall` are covered indirectly by the facts tests;
   the failure modes (empty key, oversized value, unknown key) are not.
3. **`lifespan` (37 lines).** Startup wiring under failure: database
   unreachable, re-index of the uploads directory failing.
4. **WebSocket ticket lifecycle (28 lines).** Issue → consume → replay. A ticket
   that can be consumed twice is an auth hole, and nothing checks it.
5. **Error branches (106 lines).** Lowest value of the five. These mostly
   convert an exception into a 500 that is already logged.

## The lesson worth keeping

Line coverage counts executed lines. It does not know which lines decide **who is
allowed to see what**. Ten tests over a `secure` handler are worth more than a
hundred over an `except` block.
