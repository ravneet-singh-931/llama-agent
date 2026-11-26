# SESSION 17 REFERENCE — The judge, and refusing to trust it

`evals/judge.py` (**142**, not the 244 the study plan claims) — `CRITERIA` **18**, `RUBRIC` **20–34**,
`relevant_sources` **50**, `judge_sources` **73**, `build_messages` **81**, `parse_verdict` **93**, `judge` **114** ·
`evals/run.py` — judge call **553–563**, threshold **566** · `docs/EVAL_FINDINGS.md` (1210),
`docs/ROADMAP.md:237`, `docs/SECURITY_FINDINGS.md:187` (S4)

**Baseline:** `pytest tests/test_evals.py -q` → **130 passed** (3 of them about the judge).

---

## §0 The answer

> **Q: how do you know your judge works?**

**You don't. And that is the finding, not the omission.**

The honest answer, from `docs/ROADMAP.md:237`:

> **The judge has never been validated against human labels** and the user has permanently declined
> to do so. This is the biggest known weakness in the measurement and must be stated, not hidden.

Everything that *can* be checked about the judge, has been — its prompt assembly, its JSON parsing, its
failure behaviour, and its **inability to rescue a wrong answer**. What cannot be checked is whether its
scores are *correct*, because there is no ground truth to check them against, and the only candidate
(human labels) was declined.

**So the judge is used as a tripwire, not an oracle.** What it can do, measured:

```
wrong answer   + judge says 5.0/5   -> passed: False   the checks veto it
correct answer + judge says 2.0/5   -> passed: False   the judge can overrule checks
```

It fires in one direction only. The judge **cannot pass** what the deterministic checks failed, and it
**can fail** what they passed. That asymmetry is the whole reason it is safe to ship an unvalidated
judge: its mistakes are one-directional, and the direction is *against* the app.

**The rule that follows** (`EVAL_FINDINGS.md`, meta-lesson 1):

> A disagreement between the check and the judge means one of them is broken — and you have to find out
> which. ... So the rule is not "trust the check" or "trust the judge". It is: **when they disagree,
> stop and audit, because the disagreement is the bug report.**

Six of the eval's own bugs were found that way.

---

## §1 What the judge is given — and what it is deliberately not

`build_messages` (**81**) assembles a 2-message prompt. Measured, for a case with a reference and no
sources:

```
messages: 2  roles: ['system', 'user']
system: 900 chars (the RUBRIC)
user:   269 chars, blocks: TODAY · REQUEST · REFERENCE NOTE · ANSWER
same input twice -> identical prompt? True
```

| block | present when | purpose |
|---|---|---|
| `TODAY` | always | stops the judge calling a current date wrong from stale training data |
| `REQUEST` | always | what was asked |
| `REFERENCE NOTE` | 37 of 60 cases | a prose sketch of a good answer |
| `SOURCES` | only when tools ran | what the agent actually retrieved |
| `ANSWER` | always | clipped to 9 000 chars by `_clip` |

**What it does NOT see: the case's `checks`.** Measured — `'must_include' in prompt` → **False**. The
judge is given the question, the reference and the sources, **never the assertion it is being graded
alongside**. That is a deliberate independence property: if the judge could read
`must_include_any: ["O(1)"]`, it would grade *"did the answer satisfy the checker"* rather than *"is the
answer good"*, and the two graders would stop being independent — destroying the disagreement signal
that found six bugs.

---

## §2 The four properties that *can* be verified

### 2a. Parsing is tolerant but strict about completeness — measured

| judge output | result |
|---|---|
| clean JSON | `overall 4.5` |
| JSON inside ` ```json ` fences | `overall 4.5` |
| prose before/after the JSON | `overall 3.0` |
| float scores (`4.6`, `4.4`) | `overall 4.5` (rounded) |
| **out of range** (`9`, `-3`) | `overall 3.75` — **clamped to 5 and 1** |
| missing one criterion | **`None`** — rejected |
| one criterion non-numeric | **`None`** — rejected |
| no JSON at all | **`None`** — rejected |

Two behaviours worth knowing: **clamping** (`max(1, min(5, …))`, **105**) means a judge returning `9` is
recorded as `5` with no signal — deliberate tolerance, but it hides a misbehaving grader; and
**all-four-required** means a partially-parsed verdict is discarded rather than filled in, which is the
right side to fail on.

### 2b. The prompt is deterministic and inspectable

`temperature=0` (**132**), one retry on unparsable output (**130**), and `judge_sources` (**73**) saves **exactly the
text the judge saw** into the report (`judge_sources`, `run.py:587`). That is the property that makes the
unvalidated judge auditable after the fact: every verdict — including every `fabrication: true` — can be
disagreed with by reading the same input the judge read.

### 2c. It never raises

`judge()` returns `{"error": ...}` on any exception (**141–142**), and a case whose judge failed is
**not graded**, not passed (F/`run.py:563`, Session 15 F102). Finding 12 was the bug: a failed judge used
to decide the case by the rule checks alone and **could pass**. That is *"the failure mode that makes an
eval worthless. A broken judge quietly inflating your score is worse than no judge at all."*

### 2d. Source selection is answer-aware

`relevant_sources` (**50**) ranks retrieved passages by shared salient tokens (names, numbers, 6+ letter
words) with the answer. The docstring records why: *"A blind head+tail clip of several web searches hid
the passages the answer was built from, so the judge flagged sourced facts as fabricated."* That is a
judge-side bug that was found and fixed — **the judge was being fed evidence that did not include the
evidence.**

---

## §3 What cannot be verified — demonstrated

`judge()` accepts an injected `client` (`114–115`), which is how tests stub it. The same injection proves the
limit. Measured with a stub that always answers `5`:

```
=== a judge that always says 5 (an always-pass grader) ===
"TOTAL NONSENSE zzz"        -> {'overall': 5.0, 'fabrication': False}
"TCP is ordered; UDP is not" -> {'overall': 5.0, 'fabrication': False}

=> identical score. By construction, this judge cannot distinguish them.
   Nothing in evals/ can tell you that, because both verdicts are 'valid'.
```

Both verdicts parse. Both are structurally perfect. **No property of the code can separate an
always-pass grader from a good one**, because a broken grader's output is *well-formed*. This is the same
shape as Session 16's `ast.parse`: the check asks whether the verdict is valid, not whether it is right.

**What would be needed:** a set of answers with human-assigned scores, then measure agreement
(correlation, or accuracy at the 3.5 threshold). That requires human labelling. It was declined.

**What exists instead, and is weaker:**

| test | what it proves | line |
|---|---|---|
| `test_parse_verdict` | the JSON contract, clamping included | 173 |
| `test_judge_uses_rubric_reference_and_sources` | the three blocks reach the prompt | 183 |
| `test_judge_never_raises` | a 429 becomes `{'error': …}` | 205 |

All three are about the **transport**, none about the **judgement**. The suite is honest about which is
which.

---

## §4 How the judge is used safely — the veto asymmetry

`run_case` (**run.py:566**): `passed = passed and verdict["overall"] >= judge_threshold`. Combined with
`passed = all(r["pass"] for r in results.values())`, the judge sits on top of the checks and can only
subtract. Measured both directions:

```
wrong answer,   judge 5.0/5  -> checks: includes False  -> passed: False
correct answer, judge 2.0/5  -> checks: all True        -> passed: False
```

**The judge cannot rescue. It can only reject.** This is the design that makes an unvalidated grader
defensible: if the judge is too lenient, the checks still catch wrong answers; if it is too harsh, the
damage is false failures, which *lower* the score and get investigated (§5). A grader whose errors push
the number **down** is safe to ship unvalidated. One whose errors push it **up** is not.

`judge_threshold` defaults to **3.5** of 5 (`run.py:488`).

---

## §5 What the judge actually gets wrong — the documented cases

The judge is not merely untested; it has **documented errors in both directions**, and each was resolved
by auditing rather than by trusting either side.

| case | check said | judge said | who was right |
|---|---|---|---|
| `news-markets` | grounding **failed** (11/17 figures) | **5.0** | **the check.** Five decimals (`81,772.65`, `0.13 %`, `210`) appear in no source. *"A judge reading for plausibility cannot see a fabricated decimal."* |
| `code-debug` | `code` **failed** | **5.0** | **the judge.** The answer was correct; a non-breaking hyphen broke the parser (Finding 4) |
| `team-slack-bot` | `code` **failed** | **4.75** | **the judge.** Correct Python rejected by a formatting/paste artefact (Finding 3) |
| `code-regex-pin` | "no regex block" | **5.0** | **the judge.** Three separate runs, three ways (Finding 5) |
| `safety-exfil-file` | — | **1.75** | **the judge.** The app really did write `requests.post()` |

So the disagreement signal is genuinely two-sided: **five times the check was wrong** (all five the same
mistake — *grading the shape of the answer instead of the answer*), **and once the judge was wrong** on a
fabricated decimal. That is why the rule is "audit the disagreement", not "trust the check".

### 5a. The circularity the code itself names

`judge()` (**123–127**) comments:

> *"On @team runs the agents themselves run fast-tier, so a fast judge is the same model checking its own
> reasoning — it agrees with its own mistakes."*

Default is therefore the **strong** tier, and `EVAL_JUDGE_MODEL` allows a different provider. But note
what that fix does and does not do: it reduces *self-agreement*, it does not create *validation*. A
different model is still an unvalidated judge — just a less correlated one. The comment is honest about
being a mitigation.

### 5b. Judge variance is real and is already in the numbers

Meta-lesson from Finding 19: a red build was triggered by *"three judge-noise flips (`team-library-api`
3.75 → 2.75` and friends)"*. The same answer, judged twice, moved a case by a full point. That variance
is why `regression` requires the pass rate to confirm a category move (`run.py:1042–1055`) — one judge
wobble must not move a build.

---

## §6 Findings

| ID | Location | Finding |
|---|---|---|
| **F119** | `docs/ROADMAP.md:237` | **The judge has never been validated against human labels, and this is stated rather than hidden.** The only candidate validation (human scoring) was declined. Every number that depends on the judge inherits that weakness. |
| **F120** | `judge.py:114–142` | **A well-formed verdict is indistinguishable from a correct one.** Measured with an always-5 stub: "TOTAL NONSENSE" and a correct answer both return `overall 5.0`, and both parse cleanly. No code property can separate a broken grader from a good one — this is Session 16's F110 one level up. |
| **F121** | `run.py:566` + checks | **The judge can only subtract.** Measured both directions: judge 5.0 on a check-failing answer → `passed: False`; judge 2.0 on a check-passing answer → `passed: False`. An unvalidated grader is shippable **only because** its errors are one-directional and point down. |
| **F122** | `judge.py:81–90` | **The judge is never shown the case's `checks`.** Measured: `must_include` absent from the prompt. Deliberate independence — otherwise it would grade "did the answer satisfy the checker" and the two-grader disagreement signal would collapse. |
| **F123** | `judge.py:93–111` | **Out-of-range scores are clamped silently.** `9` becomes `5`, `-3` becomes `1`, with no flag anywhere. A judge whose scale has drifted reports a perfect score that is indistinguishable from a genuine one. |
| **F124** | `judge.py:73–78` + `run.py:587` | **The judge's exact input is saved with every verdict** (`judge_sources`). This is what makes an unvalidated judge auditable: every `fabrication: true` can be disputed by reading what the judge read. |
| **F125** | `judge.py:123–128` | **The self-agreement problem is mitigated, not solved.** Strong tier by default and `EVAL_JUDGE_MODEL` for a different vendor reduces correlation with the agent's own model; it does not produce validation. Stated as a mitigation in the comment, which is the correct claim. |
| **F126** | `EVAL_FINDINGS.md` meta-lesson 1 | **The disagreement is the bug report, and it fired in both directions.** Five times the check was wrong (all the same error: grading shape not substance); once the judge was wrong (`news-markets`, a fabricated decimal). Auditing both directions found six of the eval's own bugs. |

**Positive, measured:** parsing is strict where it matters (all four criteria required, floats rounded,
fences and surrounding prose tolerated); the prompt is deterministic and inspectable; failure returns
`{'error': …}` and the case becomes **not graded** rather than passed; and `relevant_sources` fixed a real
bug where the judge was shown evidence that omitted the passages the answer used.

---

## §7 Interview phrasing

> *"I can't tell you the judge is right, and I'd be lying if I said otherwise — it has never been scored
> against human labels, and the person who owned the call declined to pay for them. What I can tell you is
> what I verified: the prompt it gets is deterministic and saved with every verdict, so any score can be
> audited after the fact; the parser is strict enough to reject a partial verdict; a 429 makes the case
> not-graded instead of passing it; and — the important one — I measured that the judge can only ever
> subtract. A wrong answer with a 5/5 score still fails, because the checks veto it. A correct answer with
> 2/5 fails. So an unvalidated grader whose mistakes point one way, downward, is shippable; one that
> could wave things through is not. And I have documented cases where it was wrong in both directions —
> five times the deterministic check was wrong and the judge right, once the judge gave 5.0 to an answer
> with five fabricated decimals. The rule isn't 'trust the check', it's that a disagreement between the
> two is the bug report, and I audited every one."*

---

## §8 Redraw checklist

1. Say **plainly**: never validated against human labels; the call was made and recorded.
2. Say what *was* verified: deterministic prompt, strict parse, error → not-graded, saved sources.
3. Give the **veto asymmetry** with the measured two-direction example.
4. Say why it is safe: unvalidated errors point **down**, so they cost false failures, not false passes.
5. Say what the judge is **not** shown (the checks) and why (independence).
6. Give one judged-wrong case (`news-markets`, 5.0 with five fabricated figures) **and** one
   check-wrong case (`code-debug`).
7. Say the self-agreement fix is a **mitigation**, not validation.
8. Name the clamping behaviour — a `9` is silently a `5`.

---

## §9 Explain-back questions

**Q1.** "So how do you know the judge works?" Answer in two sentences without claiming validation, and
name the document that records the refusal.

**Q2.** A wrong answer gets 5.0/5 from the judge and the case still fails. Explain the mechanism, name the
line, and then say what the *reverse* case proves about which side the asymmetry protects.

**Q3.** Why is the judge not shown the case's `checks`? What measurement would you expect to break if it
were, and what would collapse as a result?

**Q4.** Prove that a broken judge is undetectable by any test in this repo. Construct the stub, state the
two inputs, and name what you would need to detect it instead.

**Q5.** A judge returns `{"correctness": 9, ...}`. Trace what is recorded and what is lost, and say which
side of the honesty line that sits on.

**Q6.** Give one case where the check was wrong and the judge was right, and one where the judge was wrong
and the check was right. Then state the rule that does not require you to know which is which in advance.
