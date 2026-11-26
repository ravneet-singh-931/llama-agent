# SESSION 16 REFERENCE — The checks: parsing is not running

`evals/checks.py` (648) — `validate_code` **324**, `_doctest_to_code` **351**, `_DOCTEST_RUNNER` **371–396**,
`run_doctests` **399**, `check_code` **452**, `evaluate` **601** ·
`evals/gate.py` (244) — `gate_routing` **81**, `_CHECK_FIXTURES` **120**, `gate_checks` **162**

**Baseline:** `pytest tests/test_evals.py -q` → **130 passed** ·
`python3 -m evals.gate` → **53 routes unchanged, 18 check fixtures correct** · 0.15 s, no API key.

---

## §0 The answer

> **Q: `validate_code` parses Python with `ast.parse`. What did it fail to catch, and why was parsing
> instead of running the answer's doctests a bug in the *instrument* rather than the answer?**

**What it failed to catch: everything that is syntactically legal and behaviourally wrong.**

A parser answers exactly one question — *is this a well-formed program?* It cannot answer *does this
program do what it claims?* And the cases in this dataset ask for the second thing: they require "three
doctest examples", so the answer **states its own expected output**. That statement is an oracle sitting
in the answer text, and `ast.parse` reads it as a string literal and never evaluates it.

Measured — a `merge()` that returns its input unchanged, wrong on 2 of its own 3 stated examples:

```
1) validate_code  -> None                                          ← "valid"
2) run_doctests   -> '2 of 3 doctest example(s) do not produce
                      their own stated output'
3) check_code     -> {'pass': False, 'detail': '2 of 3 ...'}        ← correct, after the fix
```

**Why it is a bug in the instrument:** the answer was unambiguously wrong *by its own stated criterion*.
Nothing about the answer changed between `ast.parse` returning `None` and `run_doctests` returning an
error. What changed was the **question the checker asked**. A judge that reports "valid" for code that
contradicts itself is not measuring the answer — it is measuring a property (well-formedness) that the
answer happened to satisfy. The defect was in the ruler, and it produced a false pass, which is the
failure mode that matters: **an instrument that cannot fail is not an instrument** (the same shape as
Session 12's F69, a ceiling recorded everywhere and checked once).

---

## §1 What parsing structurally cannot see — measured

Five classes of wrongness, all returning `None` from `validate_code`:

| what is wrong | `validate_code` | `run_doctests` |
|---|---|---|
| wrong **result** (claims `[[]]`, returns `[]`) | `None` | `1 of 1 … do not produce their own stated output` |
| wrong **logic** (claims merged, returns input) | `None` | `1 of 1 …` |
| **raises** at runtime (`1/0`) | `None` | `1 of 1 …` |
| **infinite loop** | `None` | `doctests did not finish in 5s` |
| calls a function that **does not exist** | `None` | `1 of 1 …` |

The parser's blindness is not a gap in its implementation — it is what a parser *is*. `ast.parse` turns
the docstring into a `Constant` node. Measured:

```
ast.parse -> OK. The tree: FunctionDef
The docstring is just a STRING NODE to the parser
=> a parser sees the promise; it never sees whether it is kept.
```

**The two questions, stated side by side:**

```
validate_code asks: does the answer agree with PYTHON?
the case asks     : does the answer agree with ITSELF?
```

Only the second is what the product promised the user.

---

## §2 The fix, and its four deliberate scopes

`run_doctests` (`410`) executes the answer's own examples in a **subprocess** and compares actual output
against stated output. The scoping decisions are the interesting part — each one is a bug avoided:

| decision | line | why |
|---|---|---|
| runs **only when the block contains `>>>`** | `417` | no doctests → no penalty; the judge scores it. Forcing doctests would grade *formatting*, "the bug fixed three separate times already in this file" |
| **subprocess with a timeout** | `426–430` | the code is model output; `import os; os.remove(...)` is a perfectly valid doctest |
| the **answer's own output is the oracle** | docstring | nothing is invented — the check cannot disagree with the answer about what the answer should do |
| uses **raw** source, not `_doctest_to_code` | `419–424` | that helper comments out every non-`>>>` line to make a pasted session parseable — which **deletes the function body** doctest needs |
| `IMPORTERROR` → `None` | `432–434` | examples referencing names defined elsewhere is a fragment, not a wrong answer |
| no `FAILURES=` line → `None` | `441–443` | "no verdict: do not fail on our own bug" |

That last pair is the discipline this repo is about: **the instrument refuses to fail the answer when the
instrument itself could not run.** It is the opposite of the `ast.parse` bug, where a check that could not
see behaviour reported success anyway.

Measured behaviours, all five cases above plus:

```
infinite loop            -> 'doctests did not finish in 5s'   (bounded, not hung)
fragment needing imports -> None                               (not penalised)
no doctests at all       -> None                               (not penalised)
```

---

## §3 What `validate_code` does and does not cover

Three real branches, everything else falls through to `return None`:

| language | mechanism | caught? |
|---|---|---|
| `python`/`py`/`python3` | `ast.parse` (after `_doctest_to_code`) | yes — syntax only |
| `json` | `json.loads` | yes |
| `html`/`htm` | `_TagBalance` (an `HTMLParser` subclass) | yes — unclosed tags only |
| **everything else** | **no branch — `return None`** | **no** |

Measured — identical junk, labelled seven ways:

```
python      -> pass=False  python syntax error line 1: unmatched ')'
json        -> pass=False  invalid json
html        -> pass=True                              ← tag balance only
javascript  -> pass=True
sql         -> pass=True
go          -> pass=True
bash        -> pass=True
```

And the dataset depends on this: **15 cases declare a code language**, of which `python` 7, `html` 3,
`javascript` 1, `sql` 1, `bash` 1, `go` 1, `regex` 1. **Six of the fifteen use a language with no
validator**, so for those the `code` check can only assert that a fenced block exists.

Note `_LANG_ALIASES["javascript"]` includes `"html"`, but `validate_code` tests `_LANG_ALIASES["python"]`
then `["json"]` then `["html"]` — so a block tagged `javascript` matches none of the three and is
unchecked. The alias table and the dispatch disagree about what `javascript` means.

---

## §4 The gate — the cheap tier, and what it leaves unguarded

`evals/gate.py` runs on **every PR**, free, in **0.15 s**, with no API key. Two gates:

1. **routing** — every case declaring a `route` must still route there (**53** checked). Catches a
   reordered keyword group that reroutes "write me a blog post" from writer to general.
2. **check logic** — **18 synthetic fixtures**, each a `(case, answer, tool_calls, expected_pass)` tuple,
   asserting the checker is neither too strict nor too lax.

Measured coverage of those 18 fixtures:

```
(global only)      4        max_words        2
must_include_any   3        needs_sources    2
code               3        min_words        2
must_not_include   2
```

**Seven of the twelve `check_*` functions are never exercised by the gate by name** — and crucially:

```
'doctest'      in gate.py: 0
'run_doctests' in gate.py: 0
gate fixtures containing '>>>': 0 of 18
```

**The gate that protects every PR does not test the check that was just fixed for producing false
passes.** The doctest runner is covered only by `tests/test_evals.py` (`test_wrong_doctest_output_fails_the_check`
at 1565, `test_infinite_loop_doctest_times_out_instead_of_hanging` at 1587). That is real coverage — but it
runs in the pytest suite, not the eval gate, and `test_infinite_loop...` costs 5 s of wall clock, which is
why it is not in a gate that advertises "well under a second".

---

## §5 The safety posture — running model output as code

`run_doctests` executes **model-generated code** in a subprocess. That is the correct choice and the
comment says so (`426–430`: "importing model output into the eval process is strictly worse"). The
bounding is:

| control | present? |
|---|---|
| separate process | yes (`subprocess.run([sys.executable, "-c", script])`) |
| no shell | yes — argv list, no `shell=True` |
| wall-clock timeout | yes — 5 s default |
| **filesystem sandbox** | **no** |
| **network sandbox** | **no** |
| **memory/CPU cap** | **no** |
| **privilege drop** | **no** |

So model output runs as the eval process's user, with its filesystem and network reachable. The timeout
bounds *duration*, not *damage*: `os.remove` of a workspace file completes in milliseconds. The docstring
calls the subprocess "the sandbox"; measured against what a sandbox normally means, it is a **timeout**.

This is acceptable **because** the only thing executed is an answer to a *coding* case that the operator
chose to run, against a model they chose — not untrusted user input. But the boundary should be stated
rather than implied, because "subprocess" and "sandbox" are not synonyms.

---

## §6 Findings

| ID | Location | Finding |
|---|---|---|
| **F110** | `checks.py:324–343` | **`validate_code` proves well-formedness, not correctness.** Measured: a `merge()` returning its input passes `ast.parse` and fails 2 of its own 3 stated doctests. The instrument reported "valid" for code that contradicts the answer it came from — a **false pass**, the failure mode a checker exists to prevent. |
| **F111** | `checks.py:452–465` | **The fix is `run_doctests`, and its oracle is the answer itself.** No external truth is needed: the prompts require doctests, so the answer states its expected output. This is why the check can be deterministic without knowing the right answer — and why it narrowly applies (only blocks containing `>>>`). |
| **F112** | `checks.py:324–343` | **Six of fifteen code cases use a language with no validator.** `validate_code` branches on python/json/html only; `javascript`, `sql`, `go`, `bash` (and the `regex` case) fall through to `return None`. Measured: identical junk labelled `go`/`bash`/`sql`/`javascript` → `pass=True`. |
| **F113** | `checks.py:80–86` vs `329–343` | **The alias table and the dispatch disagree.** `_LANG_ALIASES["javascript"]` contains `"html"`, but `validate_code` dispatches by *dict key*, so a block tagged `javascript` matches neither the python nor the html branch and is silently valid. |
| **F114** | `gate.py:120–162` | **The PR gate never exercises the doctest runner.** `'doctest'` appears **0 times** in `gate.py`; none of the 18 fixtures contains `>>>`. The check that was just fixed for false passes is unguarded by the guard that runs on every PR. |
| **F115** | `gate.py:162–190` | **The gate tests 7 of 12 check types, and 4 of its 18 fixtures test only the global checks.** `check_grounding`, `check_english`, `check_route`, `check_fences` and others have no gate fixture; they are covered only by pytest, which the gate exists to front-run. |
| **F116** | `checks.py:399–450` | **"Sandbox" means "timeout".** The subprocess has no filesystem, network, memory or privilege isolation; a 5 s cap bounds duration, not blast radius. Correct for trusted-operator model output, worth stating explicitly. |
| **F117** | `checks.py:351` vs `419–424` | **Two doctest helpers, opposite requirements.** `_doctest_to_code` exists so `ast.parse` can read a pasted *session*; `run_doctests` must **not** use it because it deletes the function body. Two functions named around the same concept with incompatible input expectations — a trap documented only in a comment at the call site. |
| **F118** | `checks.py:417`, `432–434`, `441–443` | **Three ways `run_doctests` returns `None`, only one of which means "passed".** No `>>>` (not applicable), `IMPORTERROR` (not runnable here), and no `FAILURES=` line (instrument failure). All three are indistinguishable to the caller, so "no error" conflates *passed* with *did not run*. |

**Positive, measured:** the fix works and does not regress the good path — `test_correct_doctests_still_pass`
(1572), `test_answers_without_doctests_are_not_penalised` (1577), and the 5 s timeout test (1587) all pass
inside **130 passed**. The infinite loop is **bounded**, the fragment is **not penalised**, and the
instrument declines to fail the answer when it cannot produce a verdict.

---

## §7 Interview phrasing

> *"The check parsed Python with `ast.parse` and called that 'valid code'. That is a true statement about
> well-formedness and a useless one about the product — I measured a merge function that returned its
> input unchanged, wrong on two of its own three doctests, and the check passed it. The bug is in the
> instrument, not the answer: nothing about the answer changed when I fixed it, only the question being
> asked. And the answer had already told me what it was supposed to do — the prompts ask for doctests, so
> the expected output is sitting in the answer text. So the fix runs those examples in a subprocess,
> five-second cap, and compares actual against stated. The scoping matters more than the code: it only
> runs when there is a `>>>`, it never penalises a fragment that needs names from elsewhere, and if it
> can't produce a verdict at all it refuses to fail the answer. The honest gap is that the PR gate never
> exercises it — zero fixtures contain a doctest — so the fix is protected by pytest, not by the thing
> that runs on every pull request."*

---

## §8 Redraw checklist

1. Say the parser answers **well-formedness**, the case asks **correctness**, and name the gap.
2. Give the measured `merge()` example with both verdicts (`None` vs *2 of 3*).
3. Say why it is the **instrument's** bug: the question changed, not the answer.
4. Name the oracle: **the answer's own stated output**, which the prompt required.
5. State the three scopes: `>>>` only, subprocess+timeout, refuse to fail when no verdict.
6. Say `validate_code` covers **3 of 7** languages in the dataset.
7. Say the subprocess is a **timeout, not a sandbox**.
8. Say the gate has **0 doctest fixtures**.

---

## §9 Explain-back questions

**Q1.** `ast.parse` returns no error for code that is wrong on every example it states. Explain why that
is not a limitation of `ast` but a mismatch between the check and the case — and name the property each
one is actually testing.

**Q2.** The fix needs no reference solution. Say what the oracle is, why the prompt guarantees it exists,
and what the check becomes unable to judge when an answer omits doctests.

**Q3.** `run_doctests` returns `None` in three different situations. Name them and say what a caller loses
by treating them identically.

**Q4.** A case asks for Go. What can the `code` check assert, and what percentage of this dataset's code
cases are in that position?

**Q5.** The eval gate runs on every PR in 0.15 s. Say what it guarantees, name two things it does not
exercise, and explain the trade-off that made the doctest runner pytest-only.

**Q6.** Someone calls the `subprocess.run` in `run_doctests` a sandbox. Correct them precisely: name what
is bounded, what is not, and why the code being executed makes that acceptable here but not in general.
