# Agent findings

The eval part of this project produced **18 findings about the measuring stick**.
This part is about the thing being measured: five agents, a critic, a team
orchestrator, and the prompt rules they share.

Same shape as [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md): symptom, cause, fix, and the
number or test that proves it. Every row here was found by running the system.

**The through-line is different from the eval part.** The eval findings were all
"the guard was attached one level too low". These are all **"the agent was answering
without the thing it needed to answer"** — no sources, no date, no consistency rule,
no deadline that applied to the step that hung.

---

## 1. The critic overruled a correct, sourced answer

**Symptom.** A research answer with real, dated, cited sources came back as
*"I don't have real-time access to current information."* The answer was right and
the review step destroyed it.

**Cause.** `critique()` was passed the task and the draft answer, and nothing else.
So the critic:
- never saw the search results the answer was built from, and
- never saw the date, because the date lives in the *agent's* system prompt, not
  the critic's.

Given an answer asserting current events and no evidence, "you cannot know this"
is the *correct* verdict. The critic was not wrong. It was under-informed and
confident.

**Fix.** Critic and reviser receive the sources and today's date. v3.4.2.

**Proof.** `tests/test_review_sources.py` — the same answer, with and without sources
passed to the critic, produces opposite verdicts.

**The lesson.** A reviewer starved of context is not a safety net; it is a second
source of error. Every agent that judges another agent needs the evidence the first
one had.

---

## 2. The team repeated itself, and its figures described different things

**Symptom.** `@team` answers restated the same paragraphs twice, and tables mixed
figures that measured different things — a per-month price beside an annual total.

**Cause.** The final synthesis step re-pasted **every** upstream agent's output into
its prompt. With three agents that is three copies of overlapping content and an
instruction that said nothing about avoiding repetition.

**Fix.** The final step is told not to repeat its inputs, and the critic is given an
arithmetic check: figures in a table or series must measure the same thing, and any
total stated must match the numbers given. v3.4.4.

**Proof.** `tests/test_team_output_and_figures.py`.

**The lesson.** In a fan-in, the last step inherits the shape of everything before
it. Concatenation is a decision — and the default one is usually wrong.

---

## 3. `Tool choice is none, but model called a tool`

**Symptom.** Team steps failed outright with a provider error about tool choice,
after the model had already produced usable partial work.

**Cause.** The error is raised **mid-stream**, after content has been emitted. The
existing rescue path wrapped the *call*, not the *stream*, so the exception arrived
outside the `try`. Everything the step had produced was discarded.

**Fix.** Keep the partial answer; or retry on the fast model with tool results passed
as plain text instead of as tool messages. v3.5.2.

**Proof.** `tests/test_tool_call_wrap_up.py` — **6 of its 8 tests fail against the old
code**. Live effect on the suite: two team cases moved **3.5 → 5.0** and **1.25 → 5.0**.

**The lesson.** Same family as the eval part's timeout findings: the guard was attached
to the call, and the failure happened in the stream. A `try` around the wrong object
is indistinguishable from no `try` at all.

---

## 4. One hung agent hung the whole request — three times, three ways

**Symptom.** `team-library-api` took **309 s** against a 300 s case timeout.

**Cause, first layer.** `f.result(timeout=150)` was called **in a loop** over the
level's futures. The timeout applies *per future*, from the moment you call it — so
the Nth future got 150 s on top of the N−1 already waited. A two-step level could
block for 300 s with a 150 s timeout configured. The limit existed, was documented,
and was applied to the wrong object.

**Cause, second layer.** `shutdown(wait=True)` then blocked on the hung worker anyway,
so even a fired timeout did not return.

**Cause, third layer.** The budget guard runs *between* DAG levels, so it can never
catch a hang *inside* one.

**Fix.** One deadline shared across the level; pool shut down with `wait=False`;
each future gets `AGENT_STEP_TIMEOUT` (default 150 s, below the 180 s latency budget)
and degrades to the same placeholder a failed step produces. v3.5.1.

**Proof.** `test_team_step_deadline.py` — three hanging steps now return in **1.0 s**,
not 3 s.

**The lesson.** This is the same bug as eval finding 11c (89 minutes of wall clock for
17.3 seconds of work), in a different file. Both times a per-item timeout was used
where a per-level deadline was needed. **When you find this once, look for it in every
file that runs a DAG** — it will be there again.

---

## The pattern, four for four

| # | The agent was answering | Without |
|---|---|---|
| 1 | a critique | the sources and the date |
| 2 | a synthesis | an instruction not to repeat |
| 3 | a tool-using step | a rescue path that covered the stream |
| 4 | a DAG level | a deadline that applied to the level |

None of these is a model quality problem. None is fixed by a better model. Every one
is a **missing input or a misplaced boundary**, and all four were found by running the
suite, not by reading the code.

---

## Open, not yet fixed

Listed so they are not discovered by a reader first. These are the next findings,
not claims that the system is finished.

| # | What | Why it is open |
|---|---|---|
| **5** | **`code-intervals` scores 1.25** on *"write a Python function that merges overlapping intervals, with type hints and three doctest examples"* — run **43279398224** (2026-11-12T18:23Z, `5cec17e`, smoke scope). A textbook problem, a coder agent, a near-zero judge score | Untriaged. Not known whether it is the prompt, the model tier, the tool loop, or the judge. **This is the first entry of the next session, and it is the reason this row exists rather than a claim that the agents are finished.** |
| 6 | `@team` latency is 90–155 s per case | Genuine work, not a bug: every agent call in the DAG is serial. Needs profiling before a fix is worth guessing at |
| 7 | The model emits JSON with trailing commas (`{"a": 1,}`) | Needs a prompt rule; the check correctly rejects it |
| 8 | `fact-node-latest` cannot establish a current release | Needs a date-grounding prompt rule, not a check change |
| 9 | `llm_guard._tier_of` misclassifies `gpt-4o` as `"other"` | Latent: the only call site always passes a Groq model, and both budgets default to the same value |

**The honest summary:** four real agent findings, all fixed, all with tests. Five open,
one of which (`code-intervals`) is a live quality failure on a problem this system
should not fail.
