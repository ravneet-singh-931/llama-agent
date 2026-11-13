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
| ~~**5**~~ | ~~**`code-intervals` scores 1.25**~~ | ✅ **TRIAGED, #5 FIXED** — the eval could not run the answer it asked for. See *Finding 5* below. **The 1.25 is not yet re-measured** (needs a CI run), and that is stated, not glossed. |
| 6 | `@team` latency is 90–155 s per case | Genuine work, not a bug: every agent call in the DAG is serial. Needs profiling before a fix is worth guessing at |
| 7 | The model emits JSON with trailing commas (`{"a": 1,}`) | Needs a prompt rule; the check correctly rejects it |
| 8 | `fact-node-latest` cannot establish a current release | Needs a date-grounding prompt rule, not a check change |
| 9 | `llm_guard._tier_of` misclassifies `gpt-4o` as `"other"` | Latent: the only call site always passes a Groq model, and both budgets default to the same value |

---

## Finding 5 — `code-intervals` 1.25: the eval could not run the answer it asked for

**Symptom.** `code-intervals` asks for *"a Python function that merges overlapping
intervals, with type hints and **three doctest examples**."* The coder agent's answer
was graded **1.25** by the judge — run **43279398224**, 2026-11-12T18:23Z, `5cec17e`,
smoke scope. A textbook problem, a specialist *coder* agent, a near-zero score.

**What I could not do, stated first.** The answer text is not in this repository.
CI artifacts are the only place it exists, the recording for that run was never
committed, and artifact download is blocked in this environment. **So every claim
below is about the instrument, not about the model's output.** I did not see the
answer, and I am not going to describe it.

**What the check did.** `checks.py::validate_code` on Python calls
`ast.parse(_doctest_to_code(code))`. It **parses**. It never runs.

`doctest` appears in this repository in exactly one place — `_doctest_to_code`, whose
own docstring explains that the prompt asks for doctest examples, so an answer that
pastes a doctest *session* is compliant and must not be failed for it. The helper
strips the `>>>` prompts and comments out the expected output **so that `ast.parse`
accepts it.** Everything about it is built to make the answer *parse*.

Nothing was ever built to make it *run*.

**The gap, measured.** I wrote two answers to this exact prompt:

| Answer | `check_code` before v3.7.0 |
|---|---|
| Correct `merge()`, three doctests, all passing | `{'pass': True}` |
| `merge()` that **returns its input unchanged** — wrong on all three of its own doctests | **`{'pass': True}`** |

The second one is syntactically perfect and behaviourally wrong, and the check had
no opinion. Verified by direct call, not inferred.

**Why this is finding 5's shape.** The prompt asks for examples precisely so that a
reader can run them. The eval received the examples it asked for, and **graded their
syntax instead of their output** — then handed behavioural judgement to a judge that
had no reference note for this case (23 of 60 cases carry no `reference`; 6 of 12
coding cases do not, `code-intervals` among them). So a wrong answer could be parsed
by the checker and scored by a judge against nothing.

Contrast `code-fib`, which *does* carry a reference. The split is 6/6 inside one
category, which is not a design.

**Fix — run the examples.** `run_doctests()` executes the answer's own doctests and
fails the check when their stated output does not match. Scoped hard, because model
output is being executed:

- **only when the block contains `>>>`** — an answer without doctests is not
  penalised; that is the judge's call, and forcing examples here would be grading
  formatting (the bug fixed three separate times already in this file)
- **subprocess + 5 s timeout** — `import os; os.remove(...)` is a valid doctest
- **`DocTestFinder` over a real module**, not `get_doctest` on raw source, so
  docstring parsing follows Python's actual rules
- **`IMPORTERROR` is not a failure** — an answer pasted as a bare doctest session has
  no definitions to run against, and failing it would punish the format the case
  explicitly allows

**Proof.** Six new tests in `tests/test_evals.py`:

- `test_wrong_doctest_output_fails_the_check` — the bug, as an assertion
- `test_correct_doctests_still_pass`
- `test_answers_without_doctests_are_not_penalised`
- `test_infinite_loop_doctest_times_out_instead_of_hanging`
- `test_fragment_whose_examples_need_missing_names_is_not_failed`
- `test_doctests_run_only_for_python_blocks`

`tests/test_evals.py`: **123 passed**. Whole suite: **818 passed**, 6 skipped, 0 failed.

**One existing test was pinning the bug.** `test_a_doctest_inside_a_function_still_parses`
asserted that `merge(iv): return iv` passes. Its name says "still parses", which was
true and was the point — the check could not tell the difference, and the fixture
encoded that as correct behaviour. It now uses a correct body. Worth saying plainly:
**a test written against a checker's weakness will defend the weakness.**

**What is still open, and why it is stated rather than fixed.**

- **The 1.25 has not been re-measured.** Fixing an instrument does not re-score a
  past run. The next smoke run re-measures `code-intervals`; until then the honest
  claim is *"the instrument that graded it was inadequate"*, not *"the answer was
  fine."*
- **23 of 60 cases still carry no `reference` note**, so the judge scores their
  correctness against its own memory. Writing 23 references is a dataset change that
  needs care — a wrong reference is worse than none — and is not attempted here.
- **Nothing yet checks correctness of code without doctests.** An answer with no
  examples is still only syntactically checked and judge-scored. Stated in
  `docs/EVAL_FINDINGS.md` rather than hidden.

**The lesson, and it is the project's lesson one more time.** The eval asked for
executable examples, then **parsed them instead of running them** — an instrument
one level too shallow, exactly as with the 5-token CRAG verifier, the empty
contextual summaries, and the `hit@6` gate that could not see the reranker.

> **Asking a question is not the same as measuring the answer, and an instrument can
> pass its own test suite while being unable to tell right from wrong.**

**The honest summary:** five real agent findings, all fixed, all with tests. Four open.
`code-intervals` is no longer an open failure of the *agents*; it is a fixed gap in the
*instrument*, with the score itself still to be re-measured on the next run.
