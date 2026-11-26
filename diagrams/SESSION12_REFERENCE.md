# SESSION 12 REFERENCE — Model traffic and cost

`modules/llm_guard.py` (670) · `modules/model_tiers.py` (157) · `modules/provider_client.py` (136)
**+ the two files the tier docstring calls "call time": `llama_agent1.py` (382 / 388) and `multi_agent.py` (265–420).**

**Baseline: `pytest tests/test_llm_guard.py tests/test_guardrails_batch.py tests/test_provider_client.py -q`
→ 72 passed, 1.9 s.** Every number below was measured by importing and calling the real functions.

---

## §0 The two answers

> **Q: what does the app do when it is about to exceed the provider's tokens-per-minute limit?**
> **A: four things, in this order — predict with a 3.3-chars-per-token estimate, downgrade to the cheap
> model, sleep up to 20 s, then send anyway.** There is **no refusal path**. `prepare_call()` returns a
> request, always; the only "no" in the module (`CaseBudgetExceeded`) belongs to the eval harness and is
> invisible to the live app.

> **Q: why is trimming the *middle* better than trimming the end?**
> **A: three independent reasons, and only the first is about tokens:** (1) it keeps the recency half of
> the prompt — the last thing said is usually the thing being answered; (2) an end-cut deletes the
> **closing delimiter** of a `<<UNTRUSTED_DATA>>` block, silently promoting untrusted text to trusted
> position; (3) the head is where the *instruction* lives and the tail is where the *conclusion* or the
> user's actual request lives — an end-cut can only ever keep one of the two.

---

## §1 The four bounds, and which one actually fires

`prepare_call()` (`modules/llm_guard.py:571`) is the whole guard, called from exactly one place:
`InstrumentedGroqClient._Completions.create` (`multi_agent.py:333`).

| # | Bound | Value | Where | Effect when it trips |
|---|---|---|---|---|
| 1 | `input_budget(model)` — per **request** | **6 000** tokens | 83 | `fit_messages` compacts the request |
| 2 | `itpm_limit(model)` — per **minute, per model** | **7 000** strong / **8 000** fast | 100 | downgrade strong→fast, else sleep |
| 3 | `LLM_GUARD_MAX_WAIT_S` | **20 s** | 113 | sleeps, then sends anyway |
| 4 | `AGENT_USER_TOKEN_BUDGET` — per **user per window** | **0 = off** | 241 | `/chat/stream` refuses — *the only refusal* |

Measured configuration:

```
STRONG = qwen/qwen3.8-27b       input_budget 6000   itpm 7000
FAST   = openai/gpt-oss-20b     input_budget 6000   itpm 8000
estimate_tokens -> 1 000 chars = 309 tokens   (CHARS_PER_TOKEN = 3.3, line 50)
user_budget()             -> 0    (AGENT_USER_TOKEN_BUDGET unset, guard 4 disabled)
```

**Note the shape of this stack**: the per-request cap (6 000) is *below* the per-minute cap (7 000), so a
single well-behaved request can never exhaust the minute. That is deliberate and correct: the schedule is
built so the cheap control always has room to act before the expensive one is reached.

---

## §2 What it does when the minute is filling up — measured

`prepare_call` (`571`) in order:

```python
est = estimate_tokens(messages, tools)                     # 580
... case budget (eval only) ...                            # 587–628
if proactive and model == strong:                          # 632
    if used + min(est, input_budget(strong)) > strong_itpm_limit():   # 634
        if _has_room(window, fast, ...):   model = fast                # 635–641
waited = _wait_for_room(window, model, min(est, input_budget(model)))  # 645
fitted, trimmed = fit_messages(messages, input_budget(model), tools)   # 650
return model, fitted, est - trimmed                        # 656
```

### 2a. The downgrade is computed on the *un-trimmed* estimate — measured

```
raw request:                        18 187 tokens
window already at:                   6 000 / 7 000
input_budget(strong):                 6 000
chosen model:                        openai/gpt-oss-20b      <- downgraded
fitted request actually sent:         5 960 tokens           <- would have fit comfortably
```

The decision at 634 uses `min(est, input_budget(strong))` = 6 000, but the *request it is predicting* has
already been compacted to 5 960 by 650. It compares 6 950 + 6 000 = 12 950 against 7 000 and bails to the
cheap model on a request that needed 6 950 + 5 960 = 12 910 — a 40-token difference deciding which model
answers. Trimming happens **after** the routing decision that trimming is supposed to justify.

### 2b. There is no refusal path — measured

```
both tiers saturated (S 6 950/7 000, F 7 950/8 000), max_wait = 2 s
  elapsed: 2.1 s   ->   chosen model: qwen/qwen3.8-27b   ->  SENT ANYWAY
```

`_wait_for_room` (`486`) returns `(waited)`; `prepare_call` uses it only to emit a note (`647`). After the
deadline the loop exits and **execution falls through to the same request**. The docstring at 643 says
*"wait briefly for the window to slide instead of sending a request the provider will reject"* — but
sending it is exactly what happens when the wait expires.

### 2c. The wait cannot succeed for a *burst* — measured

`_wait_for_room` (`497`) re-reads `window.used(model)` each second and the caller **never records anything
while waiting**:

```
crowding spend 45 s old  -> ages out mid-wait, the loop can exit  (...in principle)
crowding spend  5 s old  -> sleeps the full 20 s, then sends the same request
```

So for the common case — several parallel team steps spending at once — the guard converts a fast 429 into
a **20-second-delayed 429**, and adds 20 s to a request that was already latency-bound. A wait is only
rational if the thing crowding you will expire during it, and nothing checks that before sleeping.

### 2d. The window can be exceeded before any of this runs — measured

`record_call` (`659`) is called *after* the request returns (`multi_agent.py:420`). `prepare_call` only
**reads** (`580`). Six concurrent callers through `prepare_call`:

```
6 threads, 1 642 tokens each, ITPM limit 7 000
  all six admitted as qwen/qwen3.8-27b
  window after: 9 852 / 7 000   ->  VIOLATION
```

This is a check-then-act race with no reservation and no lock. Four parallel DAG steps
(`AGENT_PARALLEL=4`) all read the same `used()` and all pass. It is the same defect shape as Session 11's
F51 — *the breaker counts failures observed, never tasks admitted* — here: **the window counts tokens
returned, never tokens in flight.**

---

## §3 `_trim_middle` — why the middle, measured

`_trim_middle` (`122`): keep `60 %` head + `40 %` tail, insert `_TRIM_MARK` (`54`) with the removed count.

```python
head = int(keep * 0.6); tail = max(0, keep - head)         # 126–127
return text[:head] + _TRIM_MARK.format(n=removed) + (text[-tail:] if tail else "")   # 129
```

Measured on a 60 053-char / 18 203-token body with markers at 0 %, 50 %, 100 %, budget 6 000:

| | HEAD (0 %) | MIDDLE (50 %) | TAIL (100 %) |
|---|---|---|---|
| `_trim_middle` | kept | **dropped** | kept |
| naive end-cut | kept | dropped | **dropped** |

### Why it is better — the three reasons, each with a measurement

**1. Recency.** The tail is where the conclusion and the user's actual turn live. `fit_messages` step 3
(`179`) applies `_trim_middle` to the **last** message too, so a STEP prompt and a pasted document both
keep their last 40 %. A clean document whose first line is the question and last line the answer:

```
13 048 chars, keep 2 000 -> 'QUESTION' kept, 'ANSWER' kept
end-cut,     keep 2 000 -> 'ANSWER' LOST
```

**2. It keeps the structure valid.** The hand-off payload is wrapped
`<<UNTRUSTED_DATA>> … <</UNTRUSTED_DATA>>` by `_build_step_prompt` (`multi_agent.py:1611` region). An
end-cut deletes the closing tag:

```
_trim_middle : open=1 close=1   closing delimiter intact
end-cut      : open=1 close=0   closing delimiter GONE
```

Every agent's system prompt carries `PROMPT_INJECTION_GUARD`, which tells the model to ignore instructions
*inside* the delimiters. An unterminated block is not "untrusted text with a missing footer"; it is the
model's only structural signal about where untrusted text ends, removed. `_trim_middle` preserves the
frame because the *tail* is the frame.

**3. Truncation becomes visible.** `_TRIM_MARK` reports `{n} characters trimmed`. The end-cut idiom used
elsewhere in this repo (`llama_agent1.py:63`, `_evidence_block`) appends a bare `[...]`.

---

## §4 The compaction ladder — and the two ways it fails to converge

`fit_messages` (`132`) sacrifices in this order:

1. **tool results, oldest first, two passes** (`153`): keep 1 500 chars, then 500.
2. **the longest non-system, non-last message > 1 200 chars**, repeatedly (`167`).
3. **the latest message's middle**, down to a 1 200-char floor (`179`).

Measured 54 % → 92 % of the budget is *left unused* even after trimming:

| input | had to remove | removed | final | unused |
|---|---|---|---|---|
| 20 000-char tool result | 79 tok | 5 586 tok | 493 / 6 000 | **92 %** |
| 14 000-char tool result | 0 | 0 | 4 261 / 6 000 | 29 % |

Pass 1 keeps a fixed 1 500 chars regardless of how much room exists; it is a **floor, not a target**. For a
step prompt of ~3 800 tokens against a 6 000-token budget (`multi_agent.py` `_build_step_prompt` output —
measured 12 486 chars → 3 789 tokens), nothing trims at all and 37 % of the budget is unusable.

### 4a. Two inputs cannot be trimmed at all — measured

```
system prompt alone, 40 000 chars      12 133 tokens -> fits budget?  NO
tool schemas alone,  10 × 2 000 chars   6 434 tokens -> fits budget?  NO
```

`fit_messages` never touches `role == "system"` (`167`) and never touches `tools` (the argument is only
passed to `estimate_tokens`). The real `AGENT_TOOL_SCHEMAS` is 3 schemas / 1 780 chars / **545 tokens** =
**9 %** of the 6 000 budget before a single user word — fine today, but the guard's own arithmetic says
that six more tool schemas would make the request impossible and the only feedback would be a 413.

### 4b. Many small messages are un-trimmable — measured

```
100 messages × 1 200 chars   36 963 tokens -> 6 000 budget
  after fit_messages:        36 963 tokens   trimmed 0
```

Every message is exactly at `_MIN_CONTEXT_CHARS` (1 200, line 53) so the candidate filter at `167`
(`> _MIN_CONTEXT_CHARS`) rejects all of them, and none is `role == "tool"`. The function returns
**unchanged** and `prepare_call` bills `est - trimmed = est`. `trimmed` is `max(0, …)` (`182`), so the
caller cannot distinguish "nothing needed trimming" from "nothing could be trimmed". That is the same
silence as `_topo_levels`' discarded `ok` (Session 11, F61).

---

## §5 `_trim_middle`'s marker is not part of its own budget

`_TRIM_MARK` is 66 chars for a typical `n`. The function's contract is *"within ~`keep` characters"* — the
tilde is doing real work:

| keep | returned | over |
|---|---|---|
| 200 | 266 | **+33 %** |
| 500 | 567 | +13 % |
| 1 200 | 1 267 | +6 % |
| 1 500 | 1 567 | +4 % |

At the tool-floor (`_MIN_TOOL_CHARS = 500`) every trimmed tool result is 13 % larger than the floor claims.
Harmless at 6 000 tokens; a real ceiling violation if anyone sets `LLM_INPUT_BUDGET_FAST=1000`
(`input_budget` floors at 1 000, line 88).

### 5a. It also destroys JSON — measured

```
JSON tool result, 4 663 chars -> _trim_middle(…, 1500) -> 1 567 chars
  json.loads(...) -> JSONDecodeError
```

Not a live bug — the tool layer already truncates with `_cap` (`modules/agent_tools.py:269`) before the
guard sees it, and tool results are strings the model reads, not payloads the code parses. **But it is the
reason the guard must never be moved below the tool boundary**: an MCP/JSON consumer that expected to
parse would break. Worth knowing before someone "simplifies" `_cap` away.

---

## §6 The counter is keyed on the wrong thing

### 6a. Only two model names are tracked — measured

```
_tier_of("qwen/qwen3.8-27b")  -> strong   itpm 7 000
_tier_of("openai/gpt-oss-20b")-> fast     itpm 8 000
_tier_of("llama-3.3-70b")     -> other    itpm None
record 999 999 tokens on an 'other' model -> used() = 999 999, _has_room() = True
```

`_tier_of` (`74`) compares **names**, so any model that is not exactly the configured strong/fast string is
`other` and `itpm_limit` (`100`) returns `None` → untracked. The comparison is also where a *third*
provider would silently fall out of the guard.

### 6b. Output tokens are never counted

`record_call(model, input_tokens)` (`659`) records the input estimate only; `prepare_call`'s `est` is
`estimate_tokens(messages, tools)` (`580`) — messages and schemas, nothing else. `max_tokens` is never
consulted. The module docstring (4–9) describes Groq's **ITPM** limit as an *input* limit, which is true
for ITPM — but TPM/RPD (the limits `friendly_llm_error` actually reports at 202–219) include completion
tokens. A 4 096-token answer (`AGENT_MAX_TOKENS`, `multi_agent.py` `_max_tokens_default`) is invisible to
the thing predicting the 429.

### 6c. The window is 60 s wide in one implementation and 50–70 s in the other — measured

```
redis-backed TokenWindow: sums 6 × 10 s buckets
  spend at t=1009.9 -> still counted at 1059.9, GONE at 1060.1   (window 50.2 s)
  same spend counted from 1009.9 .. 1060.1                        (up to 69.9 s)

in-process TokenWindow: exact 60 s deque (tests/test_llm_guard.py:118)
```

Tests exercise the deque (`tests/test_llm_guard.py:118–156`); the deployed shared path is the bucket sum.
They agree on the arithmetic and disagree on the boundary by ±10 s — enough to admit or refuse differently
in a multi-replica deployment than in CI.

### 6d. A downgrade leaves the strong window full — measured

```
strong at 6 950/7 000 -> downgraded to fast -> record_call(fast, 1 006)
strong window: still 6 950   fast window: 1 006
```

Correct for provider limits (the call really did go to fast), but it means one burst of downgrades pins the
strong tier for the rest of the minute; the next call sees 6 950 and downgrades again without ever
re-testing whether the minute has room.

---

## §7 The per-user ceiling: recorded everywhere, checked on one route

`AGENT_USER_TOKEN_BUDGET` (241) defaults to **0 = disabled**. `over_budget()` (`331`) is called from
**one place** — `api.py:1904`, inside `/chat/stream`:

```
grep -n over_budget api.py  ->  1904  (only)
  POST /chat       (chat(), 1673)         not checked
  WebSocket        (1220)                 not checked
  /chat/stream     (1904)                 checked
```

The **recording** half is broader than I first assumed, and the correction matters. `record_spend` (`347`)
is called only from `llama_agent1.py:304` (`GroqLLM._record_usage`) — and `InstrumentedGroqClient`
**routes through `GroqLLM`**, so the agent/team path reaches it too. Measured:

```
AGENT_USER_TOKEN_BUDGET=100000, owner='alice'
InstrumentedGroqClient(...).chat.completions.create(...)   # usage: 1 234 in + 56 out
  budget before: 0
  budget after : 1 290        <- agent/team calls DO record spend
```

So the accurate statement is asymmetric in a different direction than the obvious one:

| path | spend recorded? | budget checked? |
|---|---|---|
| `/chat/stream` general chat (`run_agent_stream`, 730) | yes | **yes** (1904) |
| `/chat/stream` @team / agent (`InstrumentedGroqClient`) | **yes** (measured 1 290) | **yes** |
| non-stream `POST /chat` (`run_agent` 632 → ReActAgent) | **yes** | **no** |
| WebSocket (`1220`) | yes | **no** |

**The ceiling is a floor on one door.** Spend accrues from every route, so a user can exhaust a budget on
`POST /chat` (which never checks) and then be locked out of `/chat/stream` (which does) — and a WebSocket
client can keep spending indefinitely while `/chat/stream` refuses it.

### 7a. Estimates go to the window, actual usage goes to the budget — measured

```
after the same agent call:  ITPM window for qwen/qwen3.8-27b = 6 tokens
                            spend budget for alice           = 1 290 tokens
```

`record_call(model, est)` (`multi_agent.py:420`) puts a **3.3-chars-per-token estimate** in the ITPM
window; `record_spend(tin + tout)` (`llama_agent1.py:304`) puts the provider's **reported usage** in the
budget. Two counters, two different sources of truth for the same call, and only one of them is what the
provider bills.

### 7b. Unbound spend is *attributed*, not blocked — measured

```
AGENT_USER_TOKEN_BUDGET=1000
record_spend(5000) with no owner bound  -> lands on subject "anonymous"
over_budget("alice")  -> False          <- alice pays nothing for it
over_budget()         -> True           <- and every following anonymous caller is refused
```

`budget_subject()` (`322`) falls back to the literal `"anonymous"`. A call that runs before `bind_owner`
(`api.py:1921`) charges a shared bucket, so the refusal lands on **the next anonymous visitor**, not on the
caller who spent it. This is F43's shape one layer up: the same ContextVar, the same fallback, and the
fallback is a *value* rather than an error.

---

## §8 The per-case budget is threading.local() — and every team step runs on another thread

`case_budget_start` (`526`) stores `_CASE_BUDGET.stack` on a `threading.local()`. Measured:

```
main thread  _case_limit() = 9000
plain thread          -> (limit=None, spent=0)
submit_with_context   -> (limit=None, spent=0)      <- exactly what multi_agent.py:1707 uses
```

`submit_with_context` (`modules/request_context.py:38`) copies **contextvars**, not thread-locals. Three
`prepare_call`s at ~3 600 tokens each inside a worker: `spent = 0`, no exception. The same three calls on
the main thread: **`CaseBudgetExceeded` after call 2 (4 960/5 000)**.

Measured per runner (case budget 3 000, each step's prompt ~6 060 tokens ⇒ a visible budget must raise):

| runner | what the step thread saw | case spent |
|---|---|---|
| `collaborate()` — pool thread (`1707`) | `None, None` | 0 |
| `collaborate_stream(parallel=False)` — inline | **`3000, 3000`** | **2 960** |
| `collaborate_stream(parallel=True)` — pool (`1880`) | `None, None` | 0 |

So the budget is live on exactly one of the three execution shapes: sequential streaming. This is the
third appearance of one mechanism — *a guard attached one scope too low* — which the module's own comment
(505–520) names. It was fixed for **per call → per case** and never for **per thread → per request**.

---

## §9 The `llm_guard` is not applied to the path that talks to the model most

`InstrumentedGroqClient` (`multi_agent.py:265`) is the sole caller of `prepare_call`/`record_call`/
`shrink_for_retry`. The other client — `GroqLLM` (`llama_agent1.py:224`), used by the module-level `llm`
(`382`) and `ReActAgent` (`621`), i.e. **the general non-streaming chat path** (`api.py:1826`,
`run_agent` 632) — calls `self._client.chat.completions.create` directly at `322`/`344`.

Measured with `prepare_call` monkeypatched to raise:

```
GroqLLM.chat(...)        -> returned normally, prepare_call invocations: 0
GroqLLM.stream_chat(...) -> streamed 2 tokens, prepare_call invocations: 0
```

And `fit_messages` on that path exists in exactly one place — `llama_agent1.py:780`, inside
`run_agent_stream`. So `run_agent` (632) has **no input budget, no ITPM accounting, no compaction**, only
`max_iterations=20` (`626`) on the ReAct loop. The streaming chat path gets `fit_messages` + `record_call`
but **not** `prepare_call` — so it trims to the per-request budget and never downgrades or waits.

---

## §10 The provider layer: the registry is wired, `resolve_target` is not

`llama_agent1.py:247–256` calls `build_client(provider, self.api_key, …)`, so vendor selection *is* live
(this was the "build, test, never wire" fix). What is not live:

| function | non-test callers |
|---|---|
| `build_client` (`provider_client.py:73`) | `llama_agent1.py:255` |
| `provider_for_model` (`103`) | `llama_agent1.py:248` |
| `resolve_target` (`119`) | **0** |
| `resolve_provider_chain` (`model_tiers.py:116`) | **0** |
| `should_fallback_to_next_provider` (`152`) | **0** |

Measured consequence: `resolve_target("strong", provider="openai")` correctly reports
`api_key_env = OPENAI_API_KEY`, and `GroqLLM` **does not use it** —

```
GROQ_API_KEY=gsk_GROQ_ONLY   OPENAI_API_KEY=sk-OPENAI_ONLY
GroqLLM(model="gpt-4o", api_key=GROQ_API_KEY, provider="openai")
  provider handed to build_client : openai
  api_key  handed to the OpenAI SDK: gsk_GROQ_ONLY     <- the Groq key
```

`_PROVIDER_API_KEY_ENV` exists (`model_tiers.py:109`), is tested
(`tests/test_provider_client.py:93–99` asserts the right key per provider), and the only production
constructor passes one key for every vendor. The multi-provider feature is half-wired: **the vendor moves,
the credential does not.**

Also measured: `LLM_PROVIDER_CHAIN=groq,openai,anthropic` resolves to a chain containing `anthropic`,
while `build_client("anthropic", …)` raises `ProviderConfigError` (`80–87`) — the chain can name a
provider the adapter refuses, and nothing resolves that contradiction before a request.

---

## §11 Tiers are frozen at import

`model_tiers.py:34` resolves at call time *and its docstring says so* (10–13: *"resolved at call time so
no restart is needed between environments"*). But the two long-lived clients are module-level constants:
`llama_agent1.py:382` and `388`.

```
import time : llm = qwen/qwen3.8-27b, stream_llm = openai/gpt-oss-20b
change LLM_MODEL_STRONG=openai/gpt-oss-20b
resolve_model("strong") -> openai/gpt-oss-20b      <- the function moves
llm.model               -> qwen/qwen3.8-27b        <- the client does not
```

`resolve_model` is genuinely call-time for anything that asks it per call (`_tier_of`, `prepare_call`,
`InstrumentedGroqClient._llms`); the two objects that make the *most* calls froze theirs at import.

---

## §12 Findings

| ID | Location | Finding |
|---|---|---|
| **F64** | `llm_guard.py:645` + `486` | **No refusal path.** After `_wait_for_room` expires the request is sent unchanged. `prepare_call` always returns a call. The only refusal (`CaseBudgetExceeded`) is eval-only. A saturated minute becomes a 20 s-delayed 429. |
| **F65** | `llm_guard.py:634` vs `650` | **The downgrade decides on a request that has not been compacted yet.** Measured: an 18 187-token request is routed away from strong using a 6 000-token estimate, while the request actually sent is 5 960. Trimming runs after the decision it should inform. |
| **F66** | `llm_guard.py:580` + `659` | **Check-then-act: the window counts tokens *returned*, never tokens in flight.** Measured: 6 concurrent 1 642-token calls all admitted against a 7 000 limit → window 9 852. No reservation, no lock. |
| **F67** | `llm_guard.py:153, 167, 179` | **Compaction does not converge and does not report.** 92 % of the budget left unused on a 20 000-char tool result; **0 tokens trimmed** on 100 messages × 1 200 chars (all at the floor) and on a 12 133-token system prompt. `trimmed = max(0, …)` hides "could not trim". |
| **F68** | `llm_guard.py:129` vs `54` | **`_TRIM_MARK` is inside the budget it reports.** 200-char keep returns 266 chars (+33 %); at the 500-char tool floor, +13 %. |
| **F69** | `api.py:1904` (only check) vs `331`/`347` | **The per-user ceiling is recorded everywhere and checked on one route.** `over_budget` runs only inside `/chat/stream`; `POST /chat` and the WebSocket record spend and never check it, so a user can spend their budget through a door that does not lock. Default is `0` = disabled. |
| **F70** | `llm_guard.py:526` | **The per-case budget is `threading.local()`; every DAG step runs on another thread.** Measured: visible to `collaborate_stream(parallel=False)` (spent 2 960), invisible to `collaborate()` and to `parallel=True` (spent 0, limit `None`). |
| **F71** | `llama_agent1.py:322/344` | **`GroqLLM` bypasses the guard.** `prepare_call` invocations measured 0 for both `chat` and `stream_chat`; `run_agent` (the non-streaming `/chat` path) has no `fit_messages`, no ITPM accounting, no compaction. |
| **F72** | `provider_client.py:119` | **`resolve_target` is exported, tested, and called by nothing.** `GroqLLM` passes one API key to every vendor: an OpenAI model receives the Groq key (measured). The chain resolver and `should_fallback_to_next_provider` are also dead; `anthropic` can appear in the chain while `build_client` refuses it. |
| **F73** | `llama_agent1.py:382/388` | **Tier resolution is frozen at import for the two highest-traffic clients**, contradicting `model_tiers.py:10–13`. |
| **F74** | `llm_guard.py:74` + `100` | **Only the two configured model names are rate-tracked.** Any other model is `_tier_of -> "other"`, `itpm_limit -> None`, so `_has_room` is unconditionally `True` (measured with 999 999 tokens recorded). |
| **F75** | `llm_guard.py:390` vs `399` | **Two TokenWindow implementations, two window widths.** Redis path (390) sums 6 × 10 s buckets → a spend is counted for 50–70 s; in-process path (399) is an exact 60 s deque. Tests only exercise the deque. |

---

## §13 Interview phrasing

> *"The app never refuses a call because of the token-per-minute limit. It predicts the size with a
> chars-per-token estimate, drops to the cheap model if the strong tier is filling up, sleeps up to twenty
> seconds, and then sends anyway — so the worst case is a twenty-second delay followed by the same 429.
> The prediction itself is off, in a specific way: it decides to downgrade using the un-trimmed estimate and
> then trims the request to half that size, so it routes a 5 960-token request away from the strong model
> because it computed 6 000. And the window it reads is a count of tokens that already came back, not
> tokens in flight, so four parallel team steps all read the same number and all pass — the guard is a
> check-then-act with no reservation. When it does trim, it trims the middle, and that's the right call for
> three reasons: the tail is the most recent thing and usually the thing being answered; the head holds the
> instruction and the tail holds the conclusion, so an end-cut can only keep one; and the hand-off payload
> is wrapped in an untrusted-data delimiter pair, which an end-cut deletes — you'd be stripping the only
> structural signal that says where untrusted text ends."*

---

## §14 Redraw checklist

1. Name the **four** bounds and their values (6 000 request / 7 000–8 000 minute / 20 s wait / 0 = off).
2. Say what happens when the wait expires: **the request is sent** (F64).
3. Say what decides the downgrade and why that number is stale (F65).
4. Say why the window can be exceeded: **recorded after return, no in-flight accounting** (F66).
5. Give the three reasons for mid-trim, with the delimiter one measured (open=1/close=0).
6. Name the ladder's two non-convergent cases: **system prompt and tool schemas are untouchable** (F67).
7. Say which route the per-user ceiling actually protects (F69).
8. Say which execution shape sees the case budget (F70).
9. Say what `GroqLLM` does that `InstrumentedGroqClient` doesn't (F71).
10. Say which credential an OpenAI model receives (F72).

---

## §15 Explain-back questions

**Q1.** `prepare_call` computes `est`, then checks the ITPM window, then calls `fit_messages`. Say what is
wrong with that order using the measured numbers for an 18 187-token request, and name the *mechanism*
(not "the estimate is off"). Then: when both tiers are saturated and `LLM_GUARD_MAX_WAIT_S` expires, what
does the caller receive? Quote the line.

**Q2.** `llm_guard` claims the ITPM window stops the provider rejecting a request. Give the measured
sequence for six concurrent 1 642-token calls against a 7 000 limit, name the class of defect, and say
which one-line change would make the control correct (and what it would cost in latency).

**Q3.** `_trim_middle` keeps 60 % head / 40 % tail. Give the three independent arguments for trimming the
middle instead of the end, and for each say what is measured. One of them has nothing to do with tokens —
name it and say what breaks without it.

**Q4.** `fit_messages` is documented as fitting a request to a budget. Give two inputs for which it
returns the messages **unchanged** while still over budget, and say why the caller cannot tell. Then say
what happens to a 4 663-char JSON tool result at `keep=1500` and why that is (currently) not a live bug.

**Q5.** Trace one user token budget: where is spend *recorded*, where is it *checked*, and which chat
routes do each. Then explain why a user can exhaust the budget on `POST /chat` and still be refused on
`/chat/stream` — and why the ITPM window and the spend budget disagree about how many tokens one call cost
(measured: 6 vs 1 290).

**Q6.** `case_budget_start()` is called by the eval harness on the main thread. Name the two multi-agent
runners and say, for each, whether the case budget is visible inside a step and what the measured
`case_budget_spent()` was. Name the Python object responsible for the difference.

**Q7.** A team runs `gpt-4o` through `GroqLLM(provider="openai")` with `GROQ_API_KEY` and
`OPENAI_API_KEY` both set. Which key reaches the OpenAI SDK, which function was written to prevent that,
who calls it, and what does `tests/test_provider_client.py` actually assert?

---

## §16 Hour 2 — the fallback chain: eight layers, none aware of the others

`modules/model_tiers.py` (157) · `modules/provider_client.py` (136) · the call sites in
`multi_agent.py` (556, 575, 980) · `modules/doc_answers.py:348`

### 16.1 What one failed request can trigger — measured

| # | Layer | File:line | What it does on failure |
|---|---|---|---|
| 1 | `llm_guard.prepare_call` | `llm_guard.py:632` | downgrades strong → fast **silently**, before the call |
| 2 | `llm_guard._wait_for_room` | `llm_guard.py:645` | sleeps up to 20 s, then sends anyway |
| 3 | `InstrumentedGroqClient` | `multi_agent.py:414` | on 413: shrink to 60 % of budget, retry |
| 4 | `BaseAgent._chat` / `_run_tools` | `multi_agent.py:567` / `587` | on 429: retry once on the fast tier |
| 5 | `_stream_with_tools` | `multi_agent.py:980` | re-runs the **whole tool round** on fast |
| 6 | `_stream_with_tools` | `multi_agent.py:994` | then streams a plain answer: `for candidate in (self.model, fast)` |
| 7 | `ReActAgent(max_retries=3)` | `llama_agent1.py:621–627` | llama-index retries internally |
| 8 | provider SDK | `llama_agent1.py:69–70` | `timeout=60`, `max_retries=2` |

**Eight independent retry policies stacked on one request**, none of which is aware of the others. Layers
1–6 are this repo's; 7–8 are llama-index's and the vendor SDK's. A single 429 can be answered with several
full re-sends of the same conversation and every layer believes it is the only one retrying.

### 16.2 `should_fallback` decides with a substring match — measured

```python
_FALLBACK_SIGNALS = ("429", "rate limit", "rate_limit", "quota", "does not exist", "not found",
                     "connection", "connecterror", "timeout", "request too large", "413")
return any(signal in text for signal in _FALLBACK_SIGNALS)      # model_tiers.py:75
```

Measured, all `True` — i.e. all of these spend a fast-tier re-send:

| error text | why it matched |
|---|---|
| `Invalid 'timeout': must be > 0` | **a validation error**, not a transport failure |
| `Validation failed: connection_string is required` | config error containing `connection` |
| `quota field missing in billing payload` | schema error containing `quota` |
| `The uploaded doc explains how to rate limit an API` | **content** containing `rate limit` |
| `tool not found: web_search_v2` | a *tool* error containing `not found` |

The shortest signals are `429` (3 chars), `413` (3) and `not found` (9). `not found`, `connection`,
`timeout` and `quota` are ordinary English — and `model_tiers.py:16` explains *why* they are strings at all:
*"String based so it works with any Groq client error without importing groq error classes"*. The trade is
explicit, and this is its cost.

### 16.3 The retry cannot change tier — measured

Every call site hardcodes the literal:

```
modules/doc_answers.py:359    if should_fallback(e, "strong"):
multi_agent.py:567            if should_fallback(e, "strong"):
multi_agent.py:587            if should_fallback(e, "strong"):
multi_agent.py:980            if should_fallback(e, "strong"):
```

So the handler always believes the failed call was on strong. But `llm_guard` (layer 1) may already have
**downgraded it to fast** without telling anyone:

```
window: strong 6 950 / 7 000  ->  prepare_call returns openai/gpt-oss-20b
that call now 429s             ->  should_fallback(e, "strong") == True
retry target                   ->  resolve_model("fast") == openai/gpt-oss-20b
```

**Same model, same request, immediately again.** The "retry on the fast tier" is a literal, not the tier of
the call that actually failed. Two users of `resolve_model` disagree about which model is in play and
neither tells the other.

### 16.4 What the retry re-sends — **the two sites disagree (corrected)**

> **Correction (found while preparing Session 14).** The first version of this section described one
> behaviour and applied it to both retry sites. They are not the same. Measured separately below.

`run_tool_rounds` mutates the caller's list **in place** (measured: 1 message in, 4 messages out, same
object). Whether that saves the retry depends entirely on **which list the caller passes the second time**:

| retry site | second call passes | measured dispatches | outcome |
|---|---|---|---|
| `_run_tools` (`multi_agent.py:588–590`) | **the same `messages` object** | `['q1','q2']` once each | **resumes** — prior tool work carried forward |
| `_stream_with_tools` (`multi_agent.py:984–987`) | **`_initial_messages()`** — a fresh list built at 893 | `['q1','q2','q3']` | **restarts** — the strong attempt's results are discarded |

So the good behaviour I described is real **only on the non-streaming path**. On the streaming path the
retry throws away everything the first attempt learned and starts the tool loop again from the system
prompt. Measured: 3 model calls on strong (`q1`, `q2`, then 429) then 2 on fast (`q3`, answer) — the fast
tier re-planned from scratch and picked a *different* query.

That is worse than an extra re-send: **the work is repeated and the second attempt is a different
investigation**, so the answer the user gets may be grounded in neither of the two searches the first
attempt paid for.

**Expensive on both paths — the whole conversation is paid for again.** Measured on a two-search research
run through `_run_tools` (the resuming path, so this is the *cheaper* case):

```
strong attempt: 3 model calls, conversation now 1 553 tokens
retry on fast : 1 call, re-sending 1 560 input tokens
total input paid for one answer: ~3 113 tokens across two tiers
```

and on the restarting path you additionally pay for the discarded tool calls (`q1`, `q2`) that produced
nothing the final answer used. There is no resume, no memo, no partial credit: `run_tool_loop(client, model, messages, tools, max_rounds,
max_tokens)` has no notion of "continue from where the other tier stopped". Note also that this
in-place mutation **contradicts `fit_messages`' explicit "Never mutates the caller's list"** contract in the
same subsystem — one function documents purity, the other silently depends on mutation for correctness.

### 16.5 A daily cap and a per-minute cap get the same treatment — measured

```
friendly_llm_error per-minute: "⚠️ I've hit the free-tier per-minute AI limit ... try again shortly."
friendly_llm_error per-day    : "⚠️ I've hit today's free-tier AI usage limit ... try again shortly."

should_fallback(per-minute) -> True
should_fallback(per-day)    -> True
```

`friendly_llm_error` (`llm_guard.py:202`) parses the provider's own text and **distinguishes** the two
(`"(tpd)"`/`"(rpd)"`, `"per day"`). `should_fallback` does not. So the code knows a daily cap cannot be
fixed by trying another model and tells the user exactly that — then spends a full re-send trying it.

### 16.6 The chain is a decision procedure with nothing to decide — measured

```
resolve_provider_chain("groq,openai,anthropic") -> ['groq', 'openai', 'anthropic']
build_client('anthropic')                       -> ProviderConfigError
should_fallback_to_next_provider('429', 1, chain) -> True      # i.e. "walk into anthropic"
```

The chain resolver admits a provider the adapter refuses, and the walk decision is the same substring match.
`build_client` (`provider_client.py:73`) has **no loop** over providers — it builds exactly the one it is
given. Live wiring:

| function | non-test callers |
|---|---|
| `build_client` (73) | `llama_agent1.py:255` |
| `provider_for_model` (103) | `llama_agent1.py:248` |
| `resolve_target` (119) | **0** |
| `resolve_provider_chain` (`model_tiers.py:116`) | **0** |
| `should_fallback_to_next_provider` (`model_tiers.py:152`) | **0** |

`grep -rn "provider ="` outside `provider_client.py` returns exactly one live site:
`llama_agent1.py:248`, inside `GroqLLM.__init__` — executed **once per model object**, then cached in
`_llms`. **There is no re-selection on error anywhere.** So `LLM_PROVIDER_CHAIN` is a configuration knob that
changes nothing: the provider is fixed at construction and a failure never revisits it.

### 16.7 Two tier systems, and they disagree — measured

| operator sets | model actually sent | `resolve_target()` says | agree |
|---|---|---|---|
| *(defaults)* | `qwen/qwen3.8-27b` | `qwen/qwen3.8-27b` (groq) | ✅ |
| `LLM_MODEL_STRONG=gpt-4o` | **`gpt-4o`** | **`qwen/qwen3.8-27b`** (groq) | ❌ |
| `LLM_MODEL=gpt-4o` (legacy) | **`gpt-4o`** | **`qwen/qwen3.8-27b`** (groq) | ❌ |
| `GROQ_MODEL_STRONG=gpt-4o` | `qwen/qwen3.8-27b` | **`gpt-4o`** (groq) | ❌ |
| both set | `gpt-4o` | `gpt-4o` (**groq**) | ✅ |

The app resolves through `LLM_MODEL_STRONG`/`LLM_MODEL` (`model_tiers.py:34`); `provider_client` resolves
through `GROQ_MODEL_STRONG`/`GROQ_MODEL_FAST` (`resolve_model_for_provider`, 134). Different env vars,
different results, and the *deployed* one is whichever object was constructed — so `resolve_target()`'s
answer describes a request the app is not making.

### 16.8 The credential: every production path reads one key — measured

```
grep -rn "OPENAI_API_KEY\|ANTHROPIC_API_KEY" --include=*.py .   (non-test)
  modules/model_tiers.py:111-112        <- the registry's names, used only by resolve_target
```

Every live constructor passes `GROQ_API_KEY`:

| call site | key |
|---|---|
| `multi_agent.py:553` (`BaseAgent`) | `os.environ["GROQ_API_KEY"]` (+ Docker secret) |
| `multi_agent.py:1414` (router) | `os.getenv("GROQ_API_KEY")` |
| `llama_agent1.py:382/388` | `GROQ_API_KEY` |
| `modules/doc_answers.py:686` | `GROQ_API_KEY` |
| `modules/rag_optimizations.py` | caller-supplied, always the Groq key |
| `evals/judge.py:122` | `GROQ_API_KEY` |

Measured with both keys set:

```
GROQ_API_KEY=gsk_GROQ_ONLY   OPENAI_API_KEY=sk-OPENAI_ONLY
GroqLLM(model="gpt-4o", api_key="gsk_GROQ_ONLY", provider="openai")
  provider handed to build_client : openai
  api_key  handed to the OpenAI SDK: gsk_GROQ_ONLY
```

`_PROVIDER_API_KEY_ENV` (`model_tiers.py:109`) exists, is exported through
`provider_api_key_env` (147), is asserted by `tests/test_provider_client.py:93–99`, and **no production path
calls it**. The multi-provider feature moves the vendor and not the credential: turning it on produces a 401
from the second vendor, not a fallback.

### 16.9 Findings

| ID | Location | Finding |
|---|---|---|
| **F76** | `model_tiers.py:75` | **The fallback trigger is a substring match on `str(error)`.** Measured `True` for `Invalid 'timeout': must be > 0`, `connection_string is required`, `quota field missing in billing payload`, and for *document content* mentioning "rate limit". Signals as short as `429`/`413`/`not found` are ordinary English. |
| **F77** | `multi_agent.py:567/587/980`, `doc_answers.py:359` | **Every call site hardcodes `should_fallback(e, "strong")`**, so after `llm_guard` has already downgraded a call to fast, a 429 triggers a "fast-tier" retry **on the fast tier again** — measured: same model, same request, immediately. |
| **F78** | `multi_agent.py:590` vs `984–987` + `agent_tools.py:445` | **The two retry sites disagree, and the streaming one restarts.** `_run_tools` re-passes the same mutated list, so prior tool results survive (measured `['q1','q2']`, no repeats). `_stream_with_tools` passes a fresh `_initial_messages()`, so the strong attempt's tool work is **discarded** and the fast tier re-plans from the system prompt (measured `['q1','q2','q3']`). Either way the full input is paid twice: **1 553 + 1 560 = 3 113 tokens**. Purity is documented for `fit_messages` and silently violated here. |
| **F79** | `llm_guard.py:202` vs `model_tiers.py:75` | **Two functions read the same 429 and reach opposite conclusions.** `friendly_llm_error` distinguishes per-minute from per-day and says waiting will not help; `should_fallback` returns `True` for both and spends a full re-send on a cap that cannot clear. |
| **F80** | `provider_client.py:73` + `model_tiers.py:116/152` | **The provider chain has no implementer.** `build_client` has no loop, `resolve_provider_chain` and `should_fallback_to_next_provider` have 0 non-test callers, and `LLM_PROVIDER_CHAIN` changes nothing — the provider is chosen once in `GroqLLM.__init__` and never revisited on error. The chain can also admit `anthropic`, which `build_client` refuses. |
| **F81** | `model_tiers.py:34` vs `provider_client.py:134` | **Two tier-resolution systems disagree.** `LLM_MODEL_STRONG=gpt-4o` makes the app send `gpt-4o` while `resolve_target()` reports `qwen/qwen3.8-27b`; `GROQ_MODEL_STRONG=gpt-4o` makes them disagree the other way. Different env vars, one deployed. |
| **F82** | `llama_agent1.py:247–256` + `multi_agent.py:553` | **The multi-provider keys are dead.** Every production constructor passes `GROQ_API_KEY`; measured, an OpenAI model receives the Groq key. `provider_api_key_env`/`resolve_target` are the only readers of `OPENAI_API_KEY`, and no production path calls them. |
| **F83** | `llama_agent1.py:69–70, 253–256` + `621–627` | **Retry multiplication.** Eight independent retry layers (six in this repo, llama-index's `max_retries=3`, the SDK's `max_retries=2`) act on one request with no shared budget and no knowledge of each other. |

### 16.10 Interview phrasing

> *"The fallback chain is eight layers deep and none of them knows the others exist — six in our code,
> llama-index's own retry, and the vendor SDK's. Each one is individually reasonable and together they
> multiply. The trigger is a substring match on the error text, so `Invalid 'timeout': must be > 0` and a
> document that talks about rate limiting both get a retry. Every call site hardcodes the literal `"strong"`,
> so when the token guard has already silently downgraded a call to the fast model, a 429 retries on the fast
> model — the same request, immediately, measured. And the retry re-sends the whole conversation: one 429
> cost us 3 113 input tokens for an answer that needed about 1 560. The multi-provider story is worse than
> unbuilt: the registry, the chain resolver and the per-provider key lookup all exist and are tested, the
> provider actually moves, and the credential does not — so switching vendors gets you a 401, not a
> fallback."*

### 16.11 Redraw checklist

1. Name the **eight** retry layers and say that none coordinates.
2. Say how `should_fallback` decides, and give one false positive you measured.
3. Say what tier the "retry on fast" actually runs on after a `llm_guard` downgrade — and why.
4. Give the measured token cost of one 429 (1 553 + 1 560).
5. Say why the tools are **not** re-run, and that correctness here rests on undocumented in-place mutation.
6. Say what `friendly_llm_error` knows that `should_fallback` does not.
7. Say what `LLM_PROVIDER_CHAIN` changes (nothing) and why.
8. Say which credential an OpenAI model receives.

### 16.12 Explain-back questions

**Q8.** `should_fallback` returns `True` for `Invalid 'timeout': must be > 0`. Name the mechanism (not
"the check is loose"), say where that trade is justified in the file, and give the cheapest fix that keeps
the offline-testability the comment asks for.

**Q9.** `llm_guard` downgrades a call to the fast model, the call 429s, and `_chat` retries. Say which model
the retry uses, quote the line that decides it, and explain why the two modules cannot detect the mistake.

**Q10.** A research run makes two tool calls on the strong tier, then 429s, then retries on fast. Answer it
twice: once for `_run_tools` and once for `_stream_with_tools`. Say what the retry sends, whether the tools
run twice, and what each costs in input tokens. Name the single line that makes the difference.

**Q11.** One 429 arrives. Name the eight layers that may act on it, and say which two would have to be
deleted for a *single* retry policy to be reproducible.

**Q12.** `LLM_PROVIDER_CHAIN=groq,openai` is set in production and the key is exposed. Explain what happens
to the next request after a Groq 429, with the measured call graph — and name the one line that would have
prevented it.
