# SESSION 19 REFERENCE — Record and replay: running the suite for zero tokens

`modules/recording.py` (476) — `RecordingMiss` **73**, `_key` **83**, `Recording` **89**,
`begin` **112**, `_load` **145**, `_append` **171**, `_save_manifest` **179**, `finish` **199**,
`llm` **211**, `tool` **252**, `today` **277**, `stamp_date` **287**, `stats` **304**, `mode` **310**,
`llm_call` **333**, `tool_call` **341**, `encode_response` **376**, `decode_response` **457** ·
`evals/run.py` `--record`/`--replay` **1338–1352**, `served_by` **1391** ·
`multi_agent.py` **322–415** · `modules/agent_tools.py` **232** · `evals/recordings/` (README only)

**Baseline:** `pytest tests/test_recording.py -q` → **24 passed**.

---

## §0 The answer

> **Q: how do you run the full eval suite with no API key?**

```bash
python evals/run.py --replay evals/recordings/2026-11-12-full
```

**You don't run the suite. You replay a recording of it.** Every model reply and every tool result from one
real run is saved to disk; a replay serves them by hash lookup, in seconds, for zero tokens, with no
provider reachable at all.

The mechanism is a **keyed cache with a hard miss**, not a mock:

| property | implementation | why it must be this |
|---|---|---|
| key = model + messages + tools + sampling | `multi_agent.py:377–384` | anything omitted lets two different requests share a key |
| **miss raises** `RecordingMiss` | `recording.py:226` | a silent fallthrough spends quota and returns a *different* answer under the same name |
| **replay never runs a tool** | `agent_tools.py:232` | a replayed Tavily call spends quota and returns something else |
| **date pinned in the manifest** | `stamp_date` **287** | `today_str()` is in every system prompt; unpinned, Tuesday's recording misses every key on Wednesday |
| **first write wins** | `llm` **239–248** | so re-recording cannot change what a committed artefact means |
| `off` is a pure no-op | `llm_call` **333** | the nightly path is untouched |

Measured, end to end on a real recording directory:

```
RECORD  reply -> <Resp the model's answer>   tool -> "search results text"
        manifest {'llm_calls': 1, 'tool_calls': 1, 'complete': True}
REPLAY  reply -> same value        produce() called? False      ← no provider call
        tool  -> same value        produce() called? False
MISS    different prompt -> RecordingMiss        produce() called? False
```

`produce()` — the zero-arg callable that performs the real request — is **never invoked** outside `record`
mode. That is the entire guarantee, and it is asserted rather than assumed.

**And it starts without a key**, because `llama_agent1.py:186–193` substitutes a placeholder when
`EVAL_RECORD_MODE == "replay"`:

```python
if (os.getenv("EVAL_RECORD_MODE", "") or "").strip().lower() == "replay":
    GROQ_API_KEY = "replay-no-key-needed"
```

Measured: `import llama_agent1` with `GROQ_API_KEY` unset and replay mode set → **imports OK**.

---

## §1 What replay actually proves — and what it does not

`evals/recordings/README.md` states the boundary precisely, and it is the most important paragraph in this
session:

> **It is** proof that the grading pipeline is deterministic: the same answers in produce the same report
> out. … **It is not** a test that the agent answers well today. The answers are frozen. … **Replay checks
> the *measuring stick*; the nightly checks the *thing being measured*.**

That distinction is what stops replay from becoming self-congratulation. A green replay says *"my grader
still grades these fixed answers the same way."* It says nothing about whether the agent improved, and it
would stay green forever against a frozen recording even if the app were deleted.

The README also names the motivation honestly: **314,330 input tokens** against a **200,000/day** free tier
means a full run **cannot be reproduced from scratch at all** — which is why the numbers lived only in CI
logs before this existed.

---

## §2 The four properties, each measured

### 2a. First write wins — `llm` (239–248)

```
call 1 returned: FIRST answer
call 2 returned: FIRST answer   ← the recorder hands back the FIRST, not what the provider just said
lines on disk: 1 (not 2)
```

Two things in one. **No duplicate append**, and — the subtle one — **record mode returns the recorded value,
not the live one**. Otherwise the run that records and the run that replays can disagree, and the committed
artefact is the one that was never used.

### 2b. The date pin — `stamp_date` (287)

```
manifest run_date: 2026-11-10
replay supplies today()  = 2026-11-10
replaying with TODAY: 2026-11-11 -> RecordingMiss
```

The date is part of **every system prompt**, so it is part of **every cache key**. Without pinning, a
recording made on Tuesday is unusable on Wednesday — not degraded, *unusable*, with a miss on every case.

And it is written **immediately**, not at `finish()`:

> *"A run that dies at case 55 (a quota cap, a killed job) never reaches finish(), and without a manifest
> the recorded date is lost — which makes every reply in it unreplayable… A partial recording with a date
> is worth keeping; a partial recording without one is scrap."*

### 2c. Partial recordings stay usable — measured

```
manifest after a run killed before finish():
  {'run_date': '2026-11-10', 'llm_calls': 1, 'tool_calls': 0, 'complete': False}

replay: case-1 -> serves the answer
        case-2 -> RecordingMiss (never recorded)
```

The `complete` flag is honest, the recorded cases replay, and the missing ones **fail loudly** — they do not
silently become "not graded" or "passed". Tests: `test_a_run_that_dies_keeps_a_usable_recording` (312),
`test_a_partial_recording_does_not_claim_the_missing_cases` (355).

CI uploads with `if: always()` for exactly this reason, and the comment says so: *"Uploading only on success
would throw away everything the night bought."*

### 2d. The rate limiter is deliberately bypassed — `multi_agent.py:322–335`

```python
replaying = recording.replaying()
if replaying:
    messages = list(kwargs.get("messages", []))
    est = 0
else:
    model, messages, est = llm_guard.prepare_call(...)
```

The reason is a genuine trap: `prepare_call` picks the model and **rewrites the messages** from the *live*
rate-limit window. Two replays of the same recording would then hash differently and **miss every key**. The
guard exists to protect quota; a replay spends none. This is the Session 12 machinery (F65/F67) being
correctly switched off on the one path where it is not merely useless but actively destructive.

---

## §3 The gap — the artefact does not exist

Everything above works. **There is nothing to replay.**

```
evals/recordings/  ->  README.md only
git ls-files evals/recordings/  ->  evals/recordings/README.md
COMMITTED RECORDINGS: 0
```

And `docs/ROADMAP.md:232` says the opposite:

> Recordings **commit** to `evals/recordings/`; a partial one still replays (manifest carries
> `"complete": false`).

What actually happens (.github/workflows/nightly-evals.yml:159–167): the nightly runs `scope=record`,
writes to `evals/recordings/$(date -u +%Y-%m-%d)-full`, and **uploads it as a GitHub Actions artifact** with
90-day retention. No `git commit`, no `git push`. So:

| claim | reality |
|---|---|
| README: `--replay evals/recordings/2026-11-12-full` | **fails on a fresh clone** — measured: `No recording at evals/recordings/2026-11-12-full.` |
| ROADMAP: "recordings commit to `evals/recordings/`" | never committed; artifact only |
| README preamble: "so the numbers can be checked by someone else" | only by someone with **repo Actions access**, within **90 days**, who knows to download the artifact |

Measured on the actual repo:

```
$ python -m evals.run --replay evals/recordings/2026-11-12-full
No recording at evals/recordings/2026-11-12-full.
```

The capability is real and tested 24 ways. The **promise** — reproducibility by a third party — is one
`git add` away from being true and is not true today. That is the same class of defect as Session 17's
unvalidated judge: **stated, but the statement and the artefact disagree.**

---

## §4 What a recording would contain

Layout (README, verified against `_append` **171** and `_save_manifest` **179**):

```
evals/recordings/<date>-full/
  manifest.json     run_date, llm_calls, tool_calls, complete
  llm.jsonl         one line per model call:  {"key": "<16 hex>", "value": …}
  tool.jsonl        one line per tool call:   {"key": "<16 hex>", "value": …}
```

16 hex chars = the first 64 bits of a SHA-256 over `json.dumps(payload, sort_keys=True)` (`_key` **83**), so
key order does not matter: *"The same request gets the same key regardless of dict order"*
(`test_the_same_request_gets_the_same_key_regardless_of_dict_order`, 53).

Plain JSONL on purpose: *"so a recording is reviewable in a diff — you can see a prompt change made a key
disappear."* That is the right format for the job and also why a recording would be large — one line per
model call, with full message arrays.

---

## §5 Findings

| ID | Location | Finding |
|---|---|---|
| **F136** | `evals/recordings/` | **No recording is committed, so the suite cannot be replayed by anyone who clones the repo.** `git ls-files evals/recordings/` returns only `README.md`; the README's own example path does not exist and the CLI prints `No recording at evals/recordings/2026-11-12-full.` The mechanism works; the artefact that makes it useful does not exist. |
| **F137** | `docs/ROADMAP.md:232` vs `nightly-evals.yml:158–167` | **The documentation says recordings *commit*; the workflow uploads an artifact.** Artifacts expire in 90 days and require repo access. So "reproducible without an API key" is true only for someone with Actions access, inside a 90-day window. |
| **F138** | `recording.py:226` | **A miss raises rather than falling through — and this is the property that makes replay honest.** Measured: `produce()` is never called on a miss. A replay that quietly hit the provider would spend quota and return a different answer under the same name. |
| **F139** | `recording.py:239–248` | **Record mode returns the *recorded* value, not the live one, and first-write-wins.** Measured: two identical requests return the first answer both times, with one line on disk. Without this, the run that records and the run that replays can disagree and the committed artefact is the one never used. |
| **F140** | `recording.py:287–302` | **The date is written to the manifest immediately, not at `finish()`.** Because `today_str()` is in every prompt, a partial recording without a date is *scrap* — every key misses. Measured: a killed run leaves `complete: false` but keeps a usable `run_date`. |
| **F141** | `multi_agent.py:322–335` | **The rate limiter is deliberately disabled during replay, and re-enabling it would break replay rather than protect it.** `prepare_call` rewrites messages from the live window, so two replays would hash differently and miss every key. |
| **F142** | `run.py:1391` | **Reports record `served_by: provider / recorded / replay`.** A replayed report cannot be mistaken for a live one — which matters because replay proves the grader, not the app (README boundary). |
| **F143** | `evals/recordings/README.md` | **The README states the capability's limit before its benefit is claimed**: *"It is not a test that the agent answers well today. The answers are frozen."* This is the same discipline as Session 17's unvalidated judge, applied to a different artefact. |

**Positive, measured:** 24 tests cover the mechanism thoroughly — keying (`38`, `47`, `53`, `61`),
record/replay round-trip (`69`), miss-raises (`91`, `137`), no-duplicate-write (`104`), date pinning (`149`,
`164`), no-op when off (`180`), streaming chunk order (`236`), **starts without an API key** (`256`),
partial recordings (`312`, `330`, `345`, `355`). All **24 passed** in 3.46 s.

---

## §6 Interview phrasing

> *"The suite can't be run from scratch — a full pass needs about 314,000 input tokens and the free tier
> gives 200,000 a day, so it literally cannot complete in one day. So the nightly records: every model reply
> and every tool result is keyed by a hash of the model, the messages, the tools and the sampling params, and
> written to JSONL. A replay serves those by hash in seconds for zero tokens. The important design choice is
> that a miss **raises** rather than falling through — a replay that quietly hit the provider would spend
> quota and hand back a different answer while still calling itself a replay. Same for tools. And the date
> has to be pinned, because today's date is in every system prompt, so it's in every key; without it a
> recording made on Tuesday misses everything on Wednesday. What replay proves is that the *grading* is
> deterministic — same answers in, same report out. It does not prove the agent is good today, because the
> answers are frozen. And the honest gap: none of it is committed. The README says `--replay
> evals/recordings/2026-11-12-full` and that path doesn't exist in the repo, because CI uploads the recording
> as a 90-day artifact instead of committing it. So the capability is real and tested; the thing that would
> let a stranger check my numbers is one `git add` away and isn't there."*

---

## §7 Redraw checklist

1. Say **"you replay, you don't run"** — and give the command.
2. Name the key: model + messages + tools + sampling.
3. Say a **miss raises**, and why falling through would be worse than failing.
4. Say replay **never runs a tool**.
5. Give the **date pin** and the reason (date is in every prompt ⇒ every key).
6. Say **first write wins**, and that record returns the recorded value.
7. State the boundary: proves the **grader**, not the **app**.
8. Say the rate limiter is **off** during replay, and why turning it on breaks it.
9. **Say no recording is committed** — Git artifact, not repo file; the README's path 404s.

---

## §8 Explain-back questions

**Q1.** A colleague adds a `TODO` to a prompt template and runs the replay suite. Every case fails with
`RecordingMiss`. Is the replay broken, and what exactly did their edit invalidate?

**Q2.** Why does a miss raise instead of falling through to the provider? Give the two distinct costs of the
alternative, and say which one makes the report wrong rather than merely expensive.

**Q3.** A run is killed at case 55 by the daily cap. It finished recording 54 cases. Say what the manifest
contains, what replays, and what happens on case 56 — and name the function whose *timing* makes the whole
partial recording usable.

**Q4.** Replay bypasses `llm_guard.prepare_call`. Explain why leaving it enabled would break replay, which
component of Session 12 it is, and why its stated purpose does not apply here.

**Q5.** What does a green replay run prove, and what does it not? Answer using the README's own distinction
and then give the observation that would make a green replay meaningless.

**Q6.** The README prints a command with a real-looking path. Run it mentally against a fresh clone and say
what happens, then name the two documents that disagree about whether recordings are committed and the
workflow lines that explain the discrepancy.
