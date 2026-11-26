# SESSION 18 REFERENCE — Regression detection: when to page someone

`evals/run.py` (1428) — `evidence_fingerprint` **427**, `prompt_fingerprint` **453**, `compare` **872**,
coverage guard **1010–1024**, `regression` **1046–1056**, exit codes **1277–1285**, `render_compare` **1059** ·
`.github/workflows/nightly-evals.yml` **220–229** · `docs/EVAL_FINDINGS.md` Findings 9, 19, 20

**Baseline:** `pytest tests/test_evals.py -q` → **130 passed**.

---

## §0 The answer

> **Q: a nightly run reports 55% pass rate, down from 76%. Do you page someone? What do you check first?**

**No. Not yet — and the first thing you check is not the pass rate.**

You check **`summary.coverage`**, specifically `graded` and `status`. Because a 21-point drop has (at
least) three completely different causes, and the report already knows which one it is:

| what happened | graded | `status` | `regression` | page? |
|---|---|---|---|---|
| real quality loss, all cases ran | 60/60 | `ok` | **True** | **yes** |
| quota died, cases never ran | 41/60 | `inconclusive` | **False** | **no** — it's the provider |
| same pass rate, judge harsher | 60/60 | `ok` | **True** | yes — but check judge variance first |

Measured on the real `compare()`:

```
A) REAL loss, all 20 ran        75.0% -> 55.0%  graded 20/20  regression: True
B) QUOTA died, 9 never ran      75.0% -> 55.0%  graded 11/20  regression: False
   coverage.inconclusive: True   newly_not_graded: 9 cases
C) SAME pass rate, judge harsher  pass delta 0.0  judge -0.8  regression: True
```

**Identical headline number, opposite verdicts.** The differentiator is not the pass rate — it is *how many
cases were graded*, which `summarize()` already computed and `compare()` now reads.

The one sentence:

> **A pass rate whose denominator contains cases that never ran is not a quality score, so it cannot
> supply the evidence for a regression.**

---

## §1 The bug this fixed, and why it fired twice

The chain, exactly as it happened:

```
quota runs out
  -> ungraded cases count as not-passed        (correct, deliberate: Session 15 §4)
  -> pass_rate falls
  -> pass_rate is in `worse`  (a headline metric moving the wrong way)
  -> regression = True
  -> red build on a night with no code change
```

`summarize()` had already decided the run was unquotable and written `status: "inconclusive"`.
`compare()` — *"the function whose entire job is to draw a conclusion"* — **never read that field**.

```
Run 43362383328   35/60 graded   FAILED at "Compare against yesterday"
Run 43151721574   41/60 graded   FAILED at "Compare against yesterday"
```

Both while manual runs passed. And the diagnosis was already there: `newly_not_graded` was **computed and
printed by this same function and never consulted**, one field away from the decision.

The guard, `run.py:1022–1024`:

```python
old_status = so.get("status") or "ok"
new_status = sn.get("status") or "ok"
thin_coverage = "inconclusive" in (old_status, new_status)
```

and it enters the decision at `1046`: `not thin_coverage and (…)`.

**The detail that makes this a good story:** the fix is four lines, and the information needed was already
on the page. The bug was not a missing measurement — it was a **measurement nobody read**.

---

## §2 The exit-code ladder — measured, four rungs

Exit codes are separate from the `regression` flag, and the order matters. Measured by running the real
CLI (`python -m evals.run --compare A B --fail-on-regression`):

| condition | exit | line | meaning |
|---|---|---|---|
| `incomparable` (suite/grader changed) | **0** | 1277 | "we changed the measuring stick" — reported, not failed |
| `coverage.inconclusive` (thin run) | **0** | 1283 | "the provider rate-limited us" — reported, not failed |
| `regression` and `--fail-on-regression` | **1** | 1285 | a real regression |
| otherwise | **0** | 1285 | clean |

Measured end to end:

```
quota-thin              exit=0
real-loss               exit=1   ← RED BUILD
no-evidence-baseline    exit=0
```

And a fifth rung that is *not* in `compare()` — the run itself (`run.py:1399–1416`): a run whose own summary is
`inconclusive` exits **3**, deliberately distinct from 1:

> *"Do not exit 0: a run this incomplete must be visible in the job list, or it becomes a green tick that
> measured half a suite. It is also not a regression (exit 1) — nothing was compared."*

**So the taxonomy is: 0 = fine, 1 = the app got worse, 3 = we could not measure.** Three states, because
collapsing "broken" and "unmeasured" into one colour is how a real signal gets ignored.

The workflow handles it (`nightly-evals.yml:225`):

```bash
if [ "$STATUS" -ne 0 ] && [ "$STATUS" -ne 1 ]; then
  echo "::error title=compare() crashed::exit $STATUS"
fi
```

— a 3 from `compare` would be a **workflow bug**, not a real state. Worth knowing the distinction exists.

---

## §3 The three independent guards, and which one fires

`compare()` refuses to call something a regression for **three separate reasons**, checked in order:

| guard | condition | rationale in the file |
|---|---|---|
| **incomparable** | case count changed · `evidence` differs · missing `evidence` · prompt changed | *"a diff taken while the INSTRUMENT changed compares two measurements, not two versions of the app"* |
| **thin_coverage** | either side `status == "inconclusive"` | §1 |
| **agreement** | no headline metric worse **and** (no significant category confirming **or** pass rate not down) | *"a single category falling while the overall rate RISES is a reallocation or judge variance"* |

### 3a. `evidence_fingerprint` — the guard that hashed the wrong thing first

`prompt_fingerprint` (`453`) hashes prompt **text**. The largest change to grading logic in the project's
history left it **identical** — so the one mechanism built to label a diff across a change stayed silent
for exactly that diff (Finding 9c).

`evidence_fingerprint` (`427`) now hashes `checks.py`, `judge.py`, `dataset.jsonl`, the judge model and the
grounding threshold. And the follow-up bug: **the guard did not fire on the case that motivated it**,
because the baseline report predated the `evidence` field and **empty compared equal to present**. Fixed at
`994–996`: a missing field is `incompatible = "missing_evidence"`, not "same".

> **Absent means UNKNOWN, not equal.** This is the same discipline as Session 13's F85 (a read that empties
> a mirror is not a read) — silence is not agreement.

### 3b. The agreement axis — why not "category size"

First attempt was a minimum case count plus a noise band. It failed: `safety` has 8 cases and fell exactly
12.5 points, so it still counted. **Sample size was the wrong axis.** The right one is *direction
agreement* — a real regression moves the whole run down.

```python
regression = bool(worse) or (_cats_confirm and _pass_rate_down)
```

`MIN_CASES_FOR_SIGNAL = 8` (`925`) still exists but only gates *which* categories may confirm — it cannot fire
alone.

### 3c. The over-correction that was caught by its own test

The docstring records it: *"When I first wrote it as 'all headline metrics agree',
`test_compare_treats_slower_and_fabricated_more_as_worse` failed on the first run — the guard I had just
written had disarmed the detector it was supposed to protect."*

Measured, scenario C above: same pass rate, judge 4.0 → 3.2, `regression: True`. **A headline metric
moving alone still fails the build**, because a rising fabrication rate is a fact about the answers and
needs no category to corroborate it.

> **A fix must be tested against both failures it names: the one it stops, and the one it must still
> catch.**

---

## §4 The four scenarios the fix was verified against

| scenario | verdict | old rule |
|---|---|---|
| 10:45 → 13:15, the best run ever | **False** | True — wrong |
| everything down, 76.7 → 55.0 | **True** | True |
| flat run, `safety` −12.5 only | **False** | True — wrong |
| pass rate flat, fabrication 10.5 → 25.0 | **True** | True |

Two are the real numbers from that night; two are the nights that were feared. **Each discounted category
is named in the report, under a line saying it was not counted** — because *"a filter that suppresses a red
build without saying so is the same failure as a guard that never fires."*

---

## §5 The full answer to the question, as a procedure

Given "55%, down from 76%":

1. **Is it a real measurement?** `summary.graded` / `status`. If `inconclusive` → not the app. Look at
   `not_graded` and `provider_failures`. Exit 3, not 1.
2. **Did the suite change?** `diff.incomparable` — case count, `evidence` (checks/judge/dataset/model),
   prompt fingerprint. If changed → the diff is a comparison of two instruments.
3. **Did the *app* change?** `check_failure_delta` — *which check* started failing. This is the question a
   red nightly asks first, and it was computed-but-never-compared before Finding 9b.
4. **Is it the judge?** `judge_overall` delta. Judge variance moves cases by a full point
   (`team-library-api 3.75 → 2.75`); if the pass rate held and only the judge moved, read the
   `judge_sources` saved with each verdict (Session 17 F124).
5. **Which cases?** `regressed_cases` — named, not counted. A category delta alone is meaningless when a
   category has 4 cases (one flip = −25 points).
6. **Did answers stop arriving?** Latency is a diagnostic: p50 falling 3.0 → 0.9 s while the pass rate fell
   40 points means nothing was answered. *"Nobody looked at it for hours."*

**The trap in step 1:** `status` comes from the report, and an **old report has no `status` field**.
`so.get("status") or "ok"` (`1018`) defaults it to `ok` — so a pre-guard baseline is treated as a real
measurement. Measured: `no-evidence-baseline → exit=0` only because `evidence` was also missing. A baseline
that has `evidence` but no `status` would be treated as `ok` and **could go red**. The default is the
permissive one.

---

## §6 Findings

| ID | Location | Finding |
|---|---|---|
| **F127** | `run.py:1010–1024` | **The coverage guard is the fix for two production red builds on unchanged code.** Quota exhaustion → ungraded counted not-passed → pass rate falls → `worse` → red. Measured: identical 75→55 drop returns `regression: False` when 9 cases never ran, `True` when all 20 did. Ran `43362383328` (35/60), `43151721574` (41/60). |
| **F128** | `run.py:1019–1021` | **The diagnosis was already computed and printed, one field from the decision.** `newly_not_graded` existed in this function and was never consulted — the bug was not a missing measurement but an unread one. |
| **F129** | `run.py:1277–1285` | **Four exit codes encode three distinct states.** 0 = unmeasured-or-fine, 1 = regressed, 3 = could not measure. Collapsing "provider broke" and "app broke" into one colour is how a gate gets ignored. Measured: quota-thin → 0, real-loss → 1, no-evidence → 0. |
| **F130** | `run.py:427–451` | **`prompt_fingerprint` hashed the wrong thing for a year of diffs.** It hashes prompt text; the largest grading-logic change left it identical, so the change-detector stayed silent for exactly the diff it existed for. Fixed by `evidence_fingerprint` (checks/judge/dataset/model/threshold). |
| **F131** | `run.py:994–996` | **A missing `evidence` field compared equal to a present one**, so the new guard failed on the very diff that motivated it. Absent = `missing_evidence`, not "same". |
| **F132** | `run.py:1022` | **The same mistake survives in `status`.** An old report has no `status`, and `so.get("status") or "ok"` defaults it to **ok** — the permissive default. A baseline with `evidence` but no `status` would be treated as a real measurement. The `evidence` path learned this lesson; the `status` path did not. |
| **F133** | `run.py:1046–1056` | **The over-correction was caught by its own test.** "All headline metrics agree" disarmed the detector; `test_compare_treats_slower_and_fabricated_more_as_worse` failed on the first run. A fix must be tested against the failures it must still catch. |
| **F134** | `run.py:905–925` | **The first fix (minimum case count + noise band) was structurally wrong.** `safety` has 8 cases and fell exactly 12.5, so it passed the size gate. Sample size is not the axis; direction agreement is. |
| **F135** | `run.py:1059–1075` | **`render_compare` withholds case-level lists for an incomparable diff.** Run `43009590617` printed eight "Fixed" and five "Regressed" cases directly above the line saying the runs do not measure the same suite — *"a reader takes the list, not the caveat."* |

**Positive, measured:** the three guards compose correctly and each is individually tested —
`test_a_quota_starved_run_is_not_a_regression` (1635), `test_a_real_regression_is_still_a_regression_at_full_coverage`
(1645), `test_judge_falling_at_full_coverage_is_still_a_regression` (1653),
`test_inconclusive_on_whichever_side_protects` (1659),
`test_an_old_report_without_a_status_field_behaves_as_before` (1666). The last one tests the *default*
documented in F132 — it is a deliberate, tested choice, not an oversight.

---

## §7 Interview phrasing

> *"No, I don't page on the number — I page on whether the number is a measurement. First thing I check is
> how many cases were graded, because a 76-to-55 drop has three causes and the report already knows which:
> if only 41 of 60 ran, that's the provider, not the app, and the build should be neutral-coloured with a
> warning, not red. That was a real bug — it went red twice on nights with no code change, because the
> pass rate counted ungraded cases as failures — which is correct — and then the regression detector read
> that number as a quality score, which it wasn't. The run's own summary said 'this pass rate is NOT a
> quality score' and the function whose entire job is to conclude never read the field. The diagnosis was
> already computed and printed, one field away from the decision. The fix is four lines, and the harder
> part was the other two guards: if the suite changed, the diff compares two instruments, so I fingerprint
> the checks and the judge and the dataset — and I got that wrong first, because my first fingerprint
> hashed the prompts, which hadn't changed. And a single category can't fail a build on its own, because
> one case in a four-case category is twenty-five points. So: unmeasured, incomparable, or actually worse
> — three states, three exit codes."*

---

## §8 Redraw checklist

1. **Do not** answer "page someone" — answer *"check whether it is a measurement first."*
2. Name `graded` / `status` as the first field, not the pass rate.
3. Give the two identical-55% scenarios with opposite verdicts.
4. Explain the production chain: quota → ungraded → rate falls → `worse` → red, twice.
5. Say the diagnosis (`newly_not_graded`) existed and was unread.
6. Name the three guards: incomparable, thin_coverage, agreement.
7. Say why category size was the wrong axis, with the `safety` 12.5 figure.
8. Say **absent ≠ equal** — the missing-`evidence` bug.
9. Give the three exit states: 0 fine, 1 worse, 3 unmeasured.

---

## §9 Explain-back questions

**Q1.** 55% down from 76%. Do you page? Give the field you read first, the two scenarios where the same
number means opposite things, and the mechanism that separates them.

**Q2.** Trace the production chain that produced a red build on unchanged code, naming the line where the
correct behaviour (counting ungraded as not-passed) and the incorrect behaviour (calling the result a
quality score) diverge. Then say what information was already on the page.

**Q3.** `compare()` has three independent reasons to refuse a regression. Name them, and for each say what
kind of mistake it is protecting against — the app's, the provider's, or the instrument's.

**Q4.** Your change-detector hashes prompt text. A colleague rewrites `checks.py` and the CI diff goes red.
Explain why the detector did not fire, what the correct hash covers, and the follow-up bug where the fix
still failed on the case that motivated it.

**Q5.** A category falls 12.5 points. Under what conditions does that fail the build, and why is
`MIN_CASES_FOR_SIGNAL = 8` not sufficient on its own? Give the dataset fact that refutes the size-based
approach.

**Q6.** Why does an incomplete run exit 3 rather than 0 or 1? Answer in terms of what a reader of the job
list sees, and what would happen if "provider rate-limited us" and "the app regressed" shared a colour.
