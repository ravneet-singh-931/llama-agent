# SESSION 11 REFERENCE — The Tool Loop, and the Tenant Hole

`modules/agent_tools.py` (652) · `modules/web_tools.py` (337) · `modules/mcp_client.py` (108) ·
`mcp_agents_server.py` (342) · `api.py` `/mcp/status` 2839, `/mcp/tools` 2856

**Baseline: `pytest tests/test_agent_tools.py -q` → 51 passed, 1.75 s.** Every number below was measured.
**Findings: F43–F63.** Hour 3 answers graded in §10 → **4.5 / 4 / 3.5 / 4.5 = 16.5 / 20.**

---

## §0 The two answers

> **Q: what happens when a tool call loops?**
> **A: five independent bounds, executed in a specific order — and the loop always terminates with
> `final_content = None`, at which point a *successful* `WRAP_UP_INSTRUCTION` call forces an answer.**

> **Q (the one I actually asked): what stops a tool call reading another tenant's file?**
> **A: nothing, on the `/chat` route.** The document tools scope by
> `get_owner()`, which is only bound by `/chat/stream` (1921) and the WebSocket handler (1220).
> `chat()` (1673) never calls `bind_owner`, so `scoped()` returns the raw store and
> `read_uploaded_document("alice.pdf")` succeeds. **Measured, end to end.**

---

## §1 The tool loop — five bounds, not one

| # | Bound | Value | Where | What it stops |
|---|---|---|---|---|
| 1 | `max_rounds` (`_MAX_TOOL_ROUNDS`) | **4** | 36 | Total rounds |
| 2 | `_MAX_IDENTICAL_CALLS` | **2** (`AGENT_MAX_IDENTICAL_CALLS`) | 45 | Same `(tool, args)` pair repeated |
| 3 | `_MAX_TOOL_TOKENS` | **48 000** (`AGENT_MAX_TOOL_TOKENS`) | 52 | Cumulative prompt tokens across rounds |
| 4 | `_MAX_READ_CHARS` | **6 000** | 37 | One document read, windowed |
| 5 | `_TOOL_RESULT_CHARS` | **4 000** (`AGENT_TOOL_RESULT_CHARS`) | 39 | Any single tool result fed back |

The comments in the file are unusually honest about *why* each one exists:

> *"_MAX_TOOL_ROUNDS bounds the number of rounds and _MAX_IDENTICAL_CALLS bounds repeats, but neither
> bounds SIZE: each round resends the whole conversation plus the tool results, so four rounds of a long
> web search is 4x the tokens of one and the caps never fire. This is the missing ceiling."*

That is a **cost model**, not a guess: rounds × conversation-size is the actual spend, so a round cap
alone multiplies the worst case by 4.

### Jargon

| Term | Plain meaning |
|---|---|
| **tool call** | The model emits a structured request (`name` + JSON `arguments`) instead of prose; the host executes it and appends the result. |
| **round** | One `create()` call that may return tool calls, plus the execution of those calls. |
| **bound / cap** | A hard limit checked in code, as opposed to an instruction in a prompt. |
| **idempotency key** | A value derived from the request such that repeating it produces the same result. Here: `(name, json.dumps(args, sort_keys=True))`. |
| **token ceiling** | A budget on cumulative prompt size, not on round count. |
| **wrap-up** | A final call made *without* tools, instructing the model to answer from what it already has. |

---

## §2 The order of operations, and what happens at each stop

```
for round in range(4):                                   # 497
    round_tokens = estimate_tokens(messages, tools)      # llm_guard's own counter
    if spent_tokens + round_tokens > 48_000:             # 506
        events.append("🛑 tool budget reached")
        messages.append({"role":"user", "content": _over_budget()})
        return messages, events, None                    # ← EARLY RETURN, no answer
    spent_tokens += round_tokens                         # 510
    response = create(..., tools=tools, tool_choice="auto")
    ...
    for tc in message.tool_calls:                        # 608
        result = _guarded_dispatch(name, args)           # ← bound #2 lives here
        messages.append({"role":"tool", "content": f"<<UNTRUSTED_DATA>>\n{result}\n<</UNTRUSTED_DATA>>"})
                                                  # ↑ every tool result is delimited (S10's delimiter,
                                                  #   here actually load-bearing for the model)
# fell out of the loop → round cap hit
return messages, events, None                            # 616
```

### Measured: a model that loops forever

Fake client that always emits `web_search({"query":"same"})`:

```
LLM calls made   : 4          (= _MAX_TOOL_ROUNDS)
final_content    : None       (round cap, no answer)
events           : ['⚙️ **web_search**'] × 4

what the model saw on each tool result:
  1. <<UNTRUSTED_DATA>> Web search is currently unavailable ...        ← real call
  2. <<UNTRUSTED_DATA>> Web search is currently unavailable ...        ← allowed retry (2nd)
  3. <<UNTRUSTED_DATA>> You have already called web_search with exactly these
                        arguments 2 times and the result will not change. Use a
                        DIFFERENT query ...                             ← refused (3rd)
  4. <<UNTRUSTED_DATA>> You have already called ... 3 times ...        ← refused (4th)
```

**Two things to notice.**

1. `_MAX_IDENTICAL_CALLS = 2` does **not** mean "run it twice". The counter increments *before* the
   comparison, so `seen[key] > 2` refuses on the **third** call → exactly **one permitted retry**, which
   is what the comment says: *"a transient error is worth repeating, an unchanged success is not."*
2. A refused call still **appends a tool message**. The model is never left with a dangling
   `tool_call_id`, which would make the next `create()` a 400. That is the subtle correctness detail
   most implementations get wrong.

### What happens when the bound is hit — the part nobody expects

`run_tool_rounds` returns `(messages, events, None)`. `run_tool_loop` (620) then does:

```python
if final_content is None:
    messages.append({"role": "user", "content": WRAP_UP_INSTRUCTION})
    response = client.chat.completions.create(model=model, messages=messages, max_tokens=max_tokens)
                                              #  ↑ NOTE: no `tools=` argument
```

> **The bound does not produce an error. It produces a sixth, tool-less call that forces an answer.**

And if that call *still* fails because the model tries another tool call with no tools offered
(`is_tool_call_error`, 340), it retries once more with `plain_wrap_up_messages` (359) — which strips the
tool-call turns out of the history entirely.

**So the total worst case is 4 tool rounds + 1 wrap-up + 1 retry = 6 LLM calls**, and the user gets an
answer either way. The bound is a *degradation ladder*, not a wall.

<<<<<<< HEAD
=======
**Correction (Session 11, hour 2).** The wrap-up above belongs to `run_tool_loop` (620). The
**streaming** path does not call it — `BaseAgent._stream_with_tools` calls `run_tool_rounds` **directly**
(`multi_agent.py:972`), which returns `final_content=None` and no wrap-up. The streaming path then runs
its **own** wrap-up at `multi_agent.py:1064` with its own `WRAP_UP_INSTRUCTION`, and independently
degrades to a plain non-tool stream when both tool-loop attempts raise (1005). So there are **two**
wrap-up implementations and the "6 LLM calls" figure applies to the **non-streaming** route only. That
is a duplicated policy, and duplicated policy rots independently — exactly the failure mode the
`run_sources` docstring warns about (*"Two gates for one rule is how they drift apart"*).

>>>>>>> 2da4157 (docs: S11 correction - two wrap-up implementations (run_tool_loop:620 vs _stream_with_tools:1064); 6-call figure is non-streaming only)
---

## §3 The tenant hole — measured

### The mechanism

```python
def _current_store():                                     # 69
    """The document store, scoped to the current request's owner."""
    from modules.doc_scope import scoped
    from modules.request_context import get_owner
    if _document_store is not None:
        return scoped(_document_store, get_owner())        # ← owner comes from a ContextVar
    ...
```

```python
def scoped(store, owner):                                  # modules/doc_scope.py:211
    """Wrap store for owner. Returns store unchanged when either is None."""
    if store is None or not owner:
        return store                                       # ← UNBOUND ⇒ RAW STORE
```

`_document_store` is bound **once, globally, at startup**:

```python
set_document_store(app.state.rag)                          # api.py:564 — "any user can ask the
                                                           #  agents to read their uploaded files"
```

### The measurement

```
owner bound = 'alice'  -> 'Uploaded documents:\n- alice.pdf'
owner bound = 'bob'    -> 'Uploaded documents:\n- bob.pdf'
owner bound = None     -> 'Uploaded documents:\n- alice.pdf\n- bob.pdf'   ← every tenant
```

And with a realistic store:

```
read_uploaded_document('alice_private.pdf') ->
    ALICE: salary band 180k, api key sk-alice-123
```

**Executed by a request that never identified itself as anyone.**

### Why this is live and not latent

| Route | Binds owner? | Line |
|---|---|---|
| `POST /chat/stream` | **yes** | 1921 (inside `event_generator`) |
| `WebSocket /ws` | **yes** | 1220 |
| **`POST /chat`** | **NO** | — grep: `bind_owner` appears **0 times** in 1673–1877 |

`chat()` *does* reach the tool loop — `MultiAgentOrchestrator()` at ~1795, which calls
`agent.execute()` → `_execute_with_tools` → `run_tool_loop` → `dispatch_tool` → `read_uploaded_document`.

`submit_with_context` (`modules/request_context.py:38`) faithfully copies the context into each worker thread —
**but there is nothing in it to copy.** Perfect propagation of `None`.

### Why no test catches it

`tests/test_agent_tools.py` calls `set_document_store(_store_with_docs())` and every document is added
with a **`source=` and no `owner=`**. `grep owner tests/test_agent_tools.py` → **nothing**. The suite is
structurally incapable of seeing this: it never creates a two-tenant store.

### The irony worth stating

`mcp_agents_server.py:84` has this comment:

> *"Fresh orchestrator per call: agent memory / task history must never be shared across MCP sessions
> (multi-tenant isolation). The previous module-level singleton leaked one user's task history..."*

Someone found and fixed a **memory**-level tenant leak — while the **document**-level one stayed open on
the main route.

---

## §4 The good news: `web_tools.py` SSRF defence is genuinely strong

`_assert_http` (95) rejects non-`http(s)` schemes **and** private destinations, with the reasoning in
the docstring:

> *"Scheme validation alone does not stop SSRF: `http://169.254.169.254/latest/meta-data/` is a
> perfectly valid https URL, and it returns the pod's cloud credentials."*

`_is_private_host` (67) covers:

```
0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16,
172.16.0.0/12, 192.168.0.0/16, ::1/128, fc00::/7, fe80::/10
+ hostnames: localhost, localhost.localdomain, metadata, metadata.google.internal
+ `.localhost` suffix
+ DNS resolution followed, so a name pointing at 127.0.0.1 is caught
```

That is a better SSRF guard than most production code has. Two honest caveats:
- Unresolvable hosts return `False` (allow) *by design* — the comment says the real DNS error is more
  useful. Fine, but it is fail-open.
- `_is_private_host` resolves, then `_http_get` resolves **again** — a classic TOCTOU/DNS-rebinding
  window between check and use.

---

## §5 MCP: the server takes no identity

```python
def _execute_agent(key: str, task: str) -> str:            # mcp_agents_server.py:136
    agent = _orchestrator().agents[key]
    return agent.execute(task)
```

Every MCP tool signature is `(task: str) -> str`. **No owner, no token, no tenant id.** The HTTP layer
does gate: `/mcp/tools` requires `get_current_user` (2856) and a feature flag, and documents why
(*"each call runs LLM-backed agents (real cost), so it is not available to anonymous visitors"*). But
the identity stops at the gateway — it is never forwarded into the tool, so the MCP server's document
tools read the same globally-bound, unscoped store.

---

## §6 Findings

| ID | Location | Finding |
|---|---|---|
| **F43** | `api.py:1673` `chat()` | **The non-streaming `/chat` route never calls `bind_owner`** (0 occurrences in 1673–1877) while reaching the agent tool loop. Measured: `get_owner() == None` ⇒ `scoped()` returns the raw store ⇒ `list_uploaded_documents()` returns **every tenant's** files and `read_uploaded_document("alice_private.pdf")` returns Alice's content to an unbound request. `/chat/stream` (1921) and `/ws` (1220) bind correctly, so the fix is one line in the wrong place rather than a design flaw. |
| **F44** | `tests/test_agent_tools.py:25` | The suite is **structurally unable** to catch F43: every fixture document is added with `source=` and **no `owner=`**, and no test binds an owner. `grep owner` → 0 hits. A two-tenant fixture is the missing test. |
| **F45** | `modules/agent_tools.py:69` `_current_store` | Fail-open by construction: an unbound owner is treated as "no scoping" rather than "no access". Correct for a CLI/standalone path, catastrophic for an HTTP path that forgot to bind. Same class as S6's `{"owner": ""}` finding. |
| **F46** | `mcp_agents_server.py:136` | MCP tools take `(task: str)` only — identity is authenticated at `/mcp/tools` (2856) then dropped. Every MCP agent therefore reads the globally-bound store. |
| **F47** | `modules/agent_tools.py:45` | `_MAX_IDENTICAL_CALLS = 2` permits **one** retry, not two (`seen[key] > 2` refuses the 3rd). Correct and intentional per the comment, but the variable name reads as "two calls allowed". |
| **F48** | `modules/web_tools.py:67` | SSRF guard resolves the hostname, then `_http_get` resolves it **again** — a TOCTOU rebinding window. And unresolvable hosts return `False` (allow), so the guard is fail-open by design. |

---

## §6b The MCP surface (hour 2)

### The transport, correctly implemented

`modules/mcp_client.py` is a textbook MCP SSE client: `GET /sse` opens the event stream,
`POST /messages/?session_id=…` carries JSON-RPC (`initialize`, `notifications/initialized`,
`tools/list`, `tools/call`), responses return on the stream. `call_mcp_tool` (82) joins every text block
and reports `isError`. No shortcuts, no hand-rolled framing.

### **The timeout stack is inverted — the outermost deadline is the shortest**

| Layer | Variable | Value | Where |
|---|---|---|---|
| REST → MCP client | `MCP_TIMEOUT` | **25 s** | `api.py:173`, `asyncio.wait_for` |
| MCP client → server | `SSE_READ_TIMEOUT` | **300 s** | `modules/mcp_client.py:35` |
| server, inside the tool | `MCP_TOOL_TIMEOUT` | **120 s** | `mcp_agents_server.py:97`, `fut.result(timeout=…)` |

Measured ordering: **25 < 120 < 300**. Every single call that exceeds 25 s is cancelled by the API
first, so the two inner numbers can never be observed. The 120 s backstop produces a user-facing
*"The request timed out, please try a shorter task"* — **a message that can never reach anyone**, because
the caller gave up 95 seconds earlier and returns `504 MCP tool call timed out` instead.

### And cancellation does not stop the work

The server's own docstring says it plainly:

> *"The submitted task keeps running until the underlying call finishes (the Groq SDK has its own
> timeout), so this is a backstop, not a hard cancel."* — `mcp_agents_server.py:121`

`asyncio.wait_for` cancels the **client** coroutine. The server-side agent has already been handed to
`_tool_executor` (`max_workers=4`, line 98) and runs to completion. So:

**Correction to my own hour-2 statement.** I wrote "3 timeouts × 4 workers = 12 orphaned agents".
That arithmetic is wrong: `_run_with_timeout` is called **once per tool call** (`mcp_agents_server.py:149`,
159), so each cancelled request submits exactly **one** task. `max_workers=4` means at most **4** run
concurrently — a 5th submits to a queue and does not create a thread.

**The true statement is worse than 12, not smaller.** `ThreadPoolExecutor`'s work queue is
**unbounded** (`SimpleQueue`), so every request cancelled at 25 s leaves its task **queued**, and each
queued task still runs to completion when a worker frees up:

> In-flight work is bounded at **4 concurrent**, with an **unbounded queue** behind it. The orphan
> population is therefore `4 + k`, where `k` is however many requests arrived before the breaker
> opened — not a fixed 12.

That is the exact scenario the breaker was written to prevent, and it cannot see the work: it counts
failures *observed*, never tasks *admitted*.

### The breaker itself is correct

```python
MCP_FAILURE_THRESHOLD = 3
MCP_CIRCUIT_COOLDOWN  = 30.0
_mcp_record_failure(): _MCP_FAILURES += 1; if >= 3: open for 30 s; reset counter
_mcp_record_success(): _MCP_FAILURES = 0
```

Standard three-state behaviour, per-process module globals (`api.py:185`), no state sharing between
replicas — acceptable for a per-pod breaker, worth stating as a known limitation.

### **The F46 thread, pulled**

Identity is authenticated at `/mcp/tools` (`get_current_user`, 2857) and then **dropped**: every MCP
tool is `(task: str) -> str`, and `_orchestrator()` (88) builds a fresh `MultiAgentOrchestrator()` per
call. Consequences, in order of severity:

| What | Effect | Where |
|---|---|---|
| **Statelessness is actually the defence** | A fresh orchestrator per call means no task history crosses callers — this is *why* F43 does not reach the MCP server | `mcp_agents_server.py:86` |
| **Documents are empty, not leaked** | `mcp-server` is a separate container; `api.py` never runs there, so `multi_agent.py:89` binds `SimpleRAG()` — an **empty in-process store**. Document tools return "No documents have been uploaded yet." | `multi_agent.py:89` |
| **`team_status` can only ever report 0** | `_orchestrator().get_team_status()` (299) builds a brand-new orchestrator whose `task_history` is `[]` (`multi_agent.py:1379`) and discards it. A tool whose entire purpose is *"how many tasks has each agent handled"*, structurally incapable of counting. | `mcp_agents_server.py:299` |
| **No facts, no rate limiting** | `grep facts` → 0, `grep rate_limit` → 0 in the MCP server | — |

So the F46 answer is a genuine **shape change**: the MCP server is stateless, which makes it immune to
the F43 cross-tenant read *and* makes several of its tools decorative.

### Findings

| ID | Location | Finding |
|---|---|---|
| **F50** | `api.py:173` vs `mcp_agents_server.py:97` vs `modules/mcp_client.py:35` | Timeout stack inverted: **25 < 120 < 300**. The API deadline is the shortest, so both inner timeouts are unobservable and the server's user-facing timeout message is dead code. |
| **F51** | `mcp_agents_server.py:121` + `api.py:2880` | `asyncio.wait_for` cancels the client, not the agent. **4 concurrent** orphaned agents plus an **unbounded queue** of not-yet-started tasks keep running and spending after the breaker opens. The breaker counts failures observed, never tasks admitted. |
| **F52** | `mcp_agents_server.py:299` | `team_status` builds a fresh orchestrator per call, so `task_history` is always `[]`; the tool's advertised purpose (task counts) is unimplementable in this design. |
| **F53** | `mcp_agents_server.py:86` | Worth recording as a **positive**: per-call orchestrator construction is what prevents the F43 cross-tenant document read from reaching the MCP server. Statelessness is the control. |

---

## §6c `collaborate` — two implementations, divergent guarantees (hour 3)

`MultiAgentOrchestrator` has **two** multi-agent runners. They are not a wrapper and its core; they are
parallel implementations with different feature sets.

| Feature | `collaborate()` **(1640)** | `collaborate_stream()` **(1757)** |
|---|---|---|
| Grounding on hand-offs (`_checked_inputs`) | **no** | **yes** (1804, 1823) |
| `grounding` module used at all | **no** | yes |
| Records `run_tool_outputs` (evidence) | **no** | yes |
| Budget guard (`_budget_exceeded`) | **yes** | **no** |
| `critique=` / `reflect=` forwarded | **yes** | **no** |
| Injection scan of hand-offs | no | no (`_scan_handoff` exists at 1611 in `_build_step_prompt`, which **both** use) |

### Who calls which

| Caller | Route | Runner |
|---|---|---|
| `api.py:2119` | `/chat/stream` @team | `collaborate_stream()` |
| `mcp_agents_server.py:159` | MCP `collaborate` tool | **`collaborate()`** |
| `multi_agent.py:1943` (`run_multi_agent`) | CLI | **`collaborate()`** |
| `multi_agent.py:1979` | CLI `/multi` | **`collaborate()`** |

**So: the web route gets figure-verification on hand-offs; the MCP route and the CLI do not.**

### The consequence, stated precisely

On `collaborate()`, an unsourced figure produced by step *s1* is handed **verbatim** to step *s3* with no
`(unverified)` marker and no `Fact check` event. The mechanism that produces the marker
(`_checked_inputs`, 1803) exists only in the other class of runner. Everything the Session 9 analysis
established about grounding — F26 (set membership), F28b (skipped checks vanish) — still applies on
`/chat/stream`, and **not at all** on the MCP path.

### And the hand-off truncation is silent (both runners)

`_build_step_prompt` (1600) does:

```python
limit = int(os.getenv("AGENT_HANDOFF_CHARS", "12000") or 12000)
...
scanned = self.agents[step["agent"]]._scan_handoff(dep_out[:limit])
```

Measured: a **16 051**-character dependency output is cut to **12 000**, and the tail is gone with **no
marker** — no ellipsis, no `(truncated)`, no note. The downstream agent has no way to know text was
removed. Compare `agent_tools._cap`, which at least reports what it did.

**Interview sentence:** *"the hand-off pipe truncates at 12 000 characters and doesn't say so, so a
finding that sits in the last quarter of a research step never reaches the writer, and the writer
confidently summarises without it."*

### The tests do not cover the divergence

```
tests/test_grounding.py:127      exercises collaborate_stream()   ← the grounded path
tests/test_chat_fixes.py:280-326 exercises collaborate_stream()
tests/test_mcp.py:171            "collaborate requires Groq API calls, so we just verify
                                  the method exists"  → asserts hasattr + callable
```

**The ungrounded runner is asserted to exist and nothing more.** Its docstring (1640–1655) advertises
every feature — *"Injection scan (item 6)… Critic/validator (item 5) + reflection (item 8)"* — but never
mentions that the grounded hand-off is absent. That is the same class of docstring/behaviour drift
recorded in Session 6.

### A note on `results` and the closure

`_run_step` (1674) closes over `results` and `_build_step_prompt` reads `results.get(dep)`, but
`results` is only assigned **after** the whole level completes (1733). It works because edges only
point backwards across levels — `_topo_levels` guarantees it — so a step's dependencies are always
already in the dict. **Correct, but by invariant rather than by construction:** any future "let steps
in a level read each other" change breaks it silently.

### Findings

| ID | Location | Finding |
|---|---|---|
| **F56** | `multi_agent.py:1640` vs `1757` | **Two divergent multi-agent runners.** `collaborate()` (used by MCP `collaborate` at `mcp_agents_server.py:159` and the CLI at 1943/1979) has **no** hand-off grounding, no evidence collection — while `collaborate_stream()` (used by `/chat/stream`, 2119) has both. The figure check protects the web route only. |
| **F57** | `multi_agent.py:1605-1611` | **Silent hand-off truncation** at `AGENT_HANDOFF_CHARS` (12 000): measured 16 051 → 12 000 with no truncation marker, so the receiving agent cannot know its input was cut. |
| **F58** | `tests/test_mcp.py:171` | The ungrounded runner is covered by `assert hasattr(...)` / `assert callable(...)` and nothing else. `tests/test_grounding.py:127` exercises only the grounded one. The divergence is untested **by construction**. |

---

## §6d What grading Q10–Q13 turned up — the fix that fixes nothing

Every answer below was re-measured before grading. Four new findings, all in the **same 60 lines** of
`multi_agent.py` the hour was taught from.

### 1. The obvious fix for F56 is a no-op — and that is the real lesson (`F59`)

The natural remedy for *"`collaborate()` has no hand-off grounding"* is to transplant
`_checked_inputs` (1802) into `collaborate()`. **Measured: it changes nothing.**

```
run_sources(task, run_tool_outputs)                     -> []
ground(text, [])   -> checked=False  unsupported=[]  marked == text
_checked_inputs gate: `if report.checked and report.unsupported`  -> False
```

Why: `grounding.run_sources` (grounding.py) **returns `[]` when there is no tool output** — its own
docstring says returning the task text there is how *"a run with zero tool output looks like it had
sources"*. And `collaborate()` has **no tool output to pass**, because the evidence channel it would
need does not exist:

* `ResearchAgent.execute()` (1177) signature is `(task, critique, reflect)` — **no `sources=`**.
* `_execute_with_tools_impl` **does** build `tool_texts` at **704** — and uses them only for its own
  critique/reflect at 706. It has **no return channel**: the function returns `result` (711).
* `_accepts_sources` (1334) is consulted **only** for `execute_stream` (1831). The non-streaming path
  was never wired.

So **the missing feature is an evidence channel, not a grounding call.** Transplanting the guard makes
the code look like `collaborate_stream` while guaranteeing `checked=False` — a guard that can only ever
report "unchecked". That is the *same* failure shape as F32 (floor makes the gate vacuous): **a control
whose input is structurally empty passes everything and looks wired.**

**Fix that would actually work:** give `execute()` a way to return `(answer, tool_texts)` — or reuse
`_run_tools` to reach `final_messages` and extract `role == "tool"` messages the way 704 does — *then*
call `_checked_inputs`. Two changes, and the second is the one everyone forgets.

### 2. The truncation is per-dependency, and the "12 000 cap" is not a cap (`F60`)

`limit` is applied **inside the per-dependency loop** (1611):

```python
for n, dep in enumerate(step["depends_on"], 1):
    dep_out = results.get(dep)
    if dep_out:
        marked = marked or "(unverified)" in dep_out
        scanned = ..._scan_handoff(dep_out[:limit])     # 1611 — per dep, not per prompt
```

Measured: **3 deps × 12 000 → 36 558-char prompt (~9 139 tokens)**; **5 deps → 60 678 chars (~15 169
tokens)**. There is **no total hand-off budget**. `AGENT_HANDOFF_CHARS` bounds *each edge*, not the
step's input.

Two consequences worth naming:

* **The token guard cannot see it.** At 1701 `est_tokens += len(instruction)//4 + 256` — it charges
  **257 tokens** for a step whose prompt measures **15 169**. Under-count **59×**. `_budget_exceeded`
  compares 257 against `AGENT_TOKEN_BUDGET=20000` and will never fire for the one thing that actually
  costs money: the *inherited* context. (`F41` says charge-after-check; this says the *charge formula*
  omits hand-offs entirely.)
* **The `(unverified)` instruction can point at text that was cut.** `marked` is computed on the
  **full** `dep_out` (1610) *before* the slice; the payload is the sliced text (1611). Measured: a marker
  at char 12 000+ is **absent from the prompt** while the prompt still carries
  *"Figures marked (unverified)… leave them out or keep the marker"*. The model is told to mind a marker
  it cannot see. `F62`.

### 3. The cycle fallback is *order-dependent* and its failure flag is discarded (`F61`)

`_topo_levels` returns `(levels, ok)`; **both call sites discard `ok`** — `levels, _ok =` at **1664**
(`collaborate`) and **1789** (`collaborate_stream`). On a cycle it returns
`[[s["id"]] for s in steps], False` — **plain file order**, no logging, no marker in the answer text.

Measured on `s1→s2→s1`:

| plan order | levels | who sees whose output |
|---|---|---|
| `s1, s2` | `[['s1'], ['s2']]` | **s2 sees s1; s1 sees nothing** |
| `s2, s1` | `[['s2'], ['s1']]` | **s2 runs first, prompt length 38 — sees nothing** |

So the answer Q12 described ("a step reads `None` and skips silently") is the *right mechanism*, but the
trigger is **not a future change**: it is reachable **today**, because **`_plan_dag` accepts a cyclic
DAG**. Its only dependency validation is *"is the id known"* (1517–1520):

```python
for s in steps:
    for d in s["depends_on"]:
        if d not in ids:
            return None
```

Measured: a planner emitting `{"id":"s1","depends_on":["s1"]}` is **accepted** (`_plan_dag` returns 2
steps), and a two-node cycle is accepted too. **The parser checks that names resolve, not that the graph
is acyclic.** `_plan_dag`'s prompt even says *"depends_on:[] for steps with no dependency"* — the rule
lives in the prompt, and **a limit that lives in the prompt is not a limit.**

Also: a dependency that is **absent from `results`** (id known to the planner but never executed) is
skipped by `if dep_out:` with no note. Measured: a step with `depends_on:["s1","s9"]` receives **one**
`Source material` block and **zero** mention of `s9` — the agent cannot tell a missing input from a
short one.

### 4. `_cap` reports; `_build_step_prompt` truncates *before* the injection scan (`F63`)

Q11's comparison (`_cap` at 269 reports, 1611 does not) is correct — and the ordering makes it worse than
"no marker". The slice happens **before** `_scan_handoff`:

```
poison at char 0        -> "[INJECTION_SUSPECTED]" prefix present  ✅
poison beyond 12 000    -> NOT in prompt, no flag                  ❌
```

So text past the cut is **never scanned** — and (per F60) it is also reachable when a *different*
dependency's block pushes the prompt past what the model can digest. The scan and the slice are in the
wrong order for any future "scan everything, show a window" change.

### 5. Where the `(unverified)` marker actually lands — the visibility answer

Q10 argued the difference is *invisible to the user*. That is right for the **answer text**, but the
mechanism needs naming, because the marker is not shown anywhere else either:

* `_reasoning_sse` (`modules/api_utils.py:231`) forwards grounding notes as `stage: "grounding"` SSE.
* `ReasoningView.tsx:16` opens with `useState(true)` — the panel is **open by default**, so hand-off
  notes *are* visible on `/chat/stream` while the run is live.
* `api.py:2154` persists `final_text or streamed` — **`grep -c grounding api.py` = 0.** Reasoning events
  are **never persisted**, and `loadConversation` resets `reasoning: []`
  (`chat-store.ts:211`). **Reload the page and every fact-check note is gone.**
* `evals/run.py:302` reads only `collaborate_stream`, so **no eval covers `collaborate()` either.**

**Restated precisely:** on `/chat/stream` the marker appears in the **prompt** to the next agent and in
the **live reasoning panel**; it does not appear in the answer text (`AGENT_FACTCHECK_MARK` default
`"0"`, F31) and it does not survive a reload. On MCP/CLI (`collaborate()`), there is no marker, no
event, and no panel — the note channel does not exist.

### 6. Q12's "invariant" — correct, and narrower than stated

`results[sid] = out` lands at **1733**, *after* the level's `f.result()` loop — so within a level nothing
is written. That is exactly what makes the claim true today. But the invariant is **enforced by
`_topo_levels`, and `_topo_levels` fails open** (F61). The correct statement is not *"the invariant
holds"* but *"the invariant holds only when `ok` is true, and nobody reads `ok`."*

### Findings added this hour

| ID | Location | Finding |
|---|---|---|
| **F59** | `multi_agent.py:704` + `1334`/`1831` + `grounding.run_sources` | **The obvious fix for F56 is a no-op.** `execute()` has no `sources=` channel and `_execute_with_tools_impl` never returns `tool_texts`, so `run_sources(task, [])` → `[]`, `checked=False`, and a transplanted `_checked_inputs` marks nothing. The missing piece is an **evidence channel**, not a grounding call. |
| **F60** | `multi_agent.py:1611` + `1701` | **Per-dependency truncation, no total budget.** 3 deps → 36 558 chars / 9 139 tokens; 5 deps → 60 678 / 15 169. Budget guard charges **257** tokens for that step (`len(instruction)//4 + 256`) — **59× under-count**, so hand-off cost is invisible to `_budget_exceeded`. |
| **F61** | `multi_agent.py:1517–1520` + `1664`/`1789` + `1551` | **`_plan_dag` accepts cyclic and self-referential DAGs** (validates id *resolution* only). `_topo_levels` then returns a **file-order** sequential fallback with `ok=False`, and **both callers discard `ok`**. Measured: cycle order `s2,s1` runs `s2` first with a 38-char prompt and no inputs — **silently**. |
| **F62** | `multi_agent.py:1610–1611` | **`marked` is computed on the full `dep_out`, the payload is the slice.** A `(unverified)` marker beyond `AGENT_HANDOFF_CHARS` is dropped from the prompt while the instruction *"leave them out or keep the marker"* is still appended — the model is told to mind a marker it cannot see. |
| **F63** | `multi_agent.py:1611` (order) + `modules/agent_tools.py:269` (contrast) | **Truncation happens before the injection scan**, so text past the cut is never scanned. `_cap` (269) reports what it dropped; `_build_step_prompt` (1611) does not. Same repo, two standards for the same decision. |

---

## §7 Interview phrasing

> *"The loop has five bounds and they answer different questions: rounds answer 'how many', identical
> calls answer 'the same thing twice', and the token ceiling answers 'how big' — which the other two
> can't, because each round resends the whole conversation. When a bound fires you don't get an error,
> you get a sixth tool-less call that forces an answer, so the worst case is six LLM calls and a
> degraded reply. The tenant story is the opposite: the scoping is real and well-built, but it depends
> on a ContextVar that only the streaming route binds. The non-streaming route reaches the same tools
> with no owner, and the scoping function treats 'no owner' as 'no filter'. One missing line, and the
> tests can't see it because no fixture ever sets an owner."*

---

## §8 Redraw checklist

1. Name the **five** bounds and the value of each.
2. Say **why** rounds alone are insufficient (each round resends the conversation → 4× tokens).
3. State that `_MAX_IDENTICAL_CALLS = 2` permits **one** retry, and where the counter increments.
4. State what a *refused* call still does (appends a tool message — no dangling `tool_call_id`).
5. Say what happens at the round cap: `final_content=None` → `WRAP_UP_INSTRUCTION` → a **tools-less**
   call. Worst case 6 LLM calls.
6. Draw the owner path: `_current_store` → `scoped(store, get_owner())` → unbound ⇒ raw store.
7. Name the three routes and which one forgets to bind.
8. Say why the tests can't catch it.

---

## §9 Explain-back questions

**Q1.** A model calls `web_search({"query": "x"})` on every round. Give the measured sequence: how many
LLM calls, what the model sees on round 3, and why round 2 is allowed to run. Then say why
`_MAX_IDENTICAL_CALLS = 2` permits only **one** retry — name the comparison, not the name.

**Q2.** Rounds are capped at 4. Explain why that alone does not bound cost, using the file's own
reasoning, and say what the third bound adds. Then: when the round cap is hit, what does the user
actually receive — and name the two extra calls that can follow.

**Q3.** Trace `read_uploaded_document("alice_private.pdf")` on the **non-streaming** `/chat` route: what
is `get_owner()`, what does `scoped()` return, and what does the tool read? Then name the two other
routes that get this right, with line numbers, and say why `submit_with_context` does not save you.

**Q4.** Why is this not caught by the test suite? Name the fixture pattern and the grep that proves it.
Then write, in words, the one test that would catch it.

**Q5.** The SSRF guard in `web_tools.py` blocks `169.254.169.254`. Name three things it does right, then
name its two weaknesses — one about *when* it resolves, one about what it does when resolution fails.
Does either make the guard useless? Argue it.

---

## §10 Hour 3 grade — Q10–Q13

| Q | Score | Verdict |
|---|---|---|
| Q10 two runners / four call sites | **4.5 / 5** | All four call sites correct and machine-confirmed; both runner descriptions correct. Off by one call site (`evals/run.py:302` also calls `collaborate_stream`) and by one word: the grounding note *is* surfaced live (`ReasoningView.tsx:16` opens the panel by default) — but never persisted (`api.py:2154`, `grep -c grounding api.py` = 0), so "invisible" is right for a different reason than given. |
| Q11 truncation | **4.0 / 5** | Measurement (16 051 → 12 000) and the `_cap` contrast both correct. **Missed that `limit` is per-dependency**, so `AGENT_HANDOFF_CHARS` bounds no total (F60: 3 deps → 36 558 chars / 9 139 tokens, charged as **257** by the budget guard). The proposed fix also leaves the slice before `_scan_handoff` (F63). |
| Q12 invariant | **3.5 / 5** | The mechanism answer is precisely right (`results[sid]` at 1733, `if dep_out:` skips silently). But it is **not a future risk**: `_plan_dag` accepts a cyclic plan today (1517–1520 only checks id resolution), and `_topo_levels`' `ok=False` is discarded at **1664** and **1789**. Measured: plan order `s2, s1` runs `s2` first with a 38-char prompt. |
| Q13 coverage | **4.5 / 5** | Correct verdict, correct justification, correct test shape. I ran the proposed test — **it fails, as it should**, which makes it a real regression test. The one thing missing: it would *still* fail after the obvious fix (F59). |

**16.5 / 20.**

### What this hour was actually about

Q12 is the through-line again, in a new costume. The answer said *"it works because of the topological
level invariant."* The invariant is real. But **the function that establishes it returns a boolean and
both callers throw it away** — `levels, _ok =` — and the function's failure mode is to return a
*plausible-looking* sequential order. So the controlling question is not *"does the invariant hold?"* but
**"what does this control read, and who wrote that value?"** Here: `_topo_levels` *computes* `ok`, and
nobody *reads* it. A guard that fails silently is indistinguishable from a guard that passed.

And Q13's fix has the same shape. `_checked_inputs` is the visible control. Its **input**
(`run_tool_outputs`) is what makes it work — and on `collaborate()` that input can only ever be `[]`,
because `execute()` returns a `str` and the tool texts built at **704** are thrown away at **711**.
Transplant the guard and you get `checked=False`, `unsupported=[]`, `marked == text` — a control that
runs, does nothing, and logs nothing. **Before proposing a fix, grep for the thing you're about to build
the fix on.**

### §6d findings, in one line each

* **F59** — transplanting `_checked_inputs` into `collaborate()` is a **no-op**; the missing piece is an evidence channel (`execute()` has no `sources=`, `_execute_with_tools_impl` discards `tool_texts`).
* **F60** — truncation is **per-dependency**; no total hand-off budget; budget guard charges 257 tokens for a 15 169-token prompt (**59×** under-count).
* **F61** — `_plan_dag` accepts **cycles and self-dependencies**; `_topo_levels` falls back to **file order** with `ok=False`; both callers discard `ok`.
* **F62** — `marked` reads the **full** dep output, the payload is the **slice**: the *"keep the marker"* instruction can point at a marker the model cannot see.
* **F63** — slice happens **before** the injection scan, so text past the cut is never scanned; `_cap` reports, `_build_step_prompt` does not.
