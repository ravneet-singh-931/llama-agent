# The drift

**A nightly that was green thirteen nights in a row while the thing it measures
got worse.**

Every number here comes from the `eval-today.json` artifacts of 13 scheduled
nightly runs, 2026-11-14 to 2026-11-26, all 60 cases, collected with
`scripts/collect_nightly_reports.sh` and tabulated with `scripts/nightly_trend.py`.
Nothing in this document is inferred from a chart or quoted from another file.

---

## The finding in one line

> **Pass rate flat at ~65. Fabrication flagged 6.9% → 12.9%. Every run green.**

The gate cannot see it, because the gate compares each run to **the one before it**
and nothing else. That is a design that detects a cliff and is blind to a slope.

---

## The series

13 runs. `fab%` is the share of judged cases the judge flagged as fabricated.

| date | pass | graded | cov | judge | ground | fab% | p95 |
|---|---|---|---|---|---|---|---|
| 11-14 | 71.7 | 89.6 | 80.0 | 4.62 | 0.43 | 6.2 | 121 |
| 11-15 | 45.0 | 75.0 | 60.0 | 4.30 | 0.30 | 11.1 | 138 |
| 11-16 | 75.0 | 93.8 | 80.0 | 4.76 | 0.78 | 4.2 | 146 |
| 11-17 | 68.3 | 82.0 | 83.3 | 4.58 | 0.56 | 6.0 | 92 |
| 11-18 | 70.0 | 84.0 | 83.3 | 4.66 | 0.29 | 8.0 | 120 |
| 11-19 | 63.3 | 88.4 | 71.7 | 4.71 | 0.81 | 4.8 | 162 |
| 11-20 | 68.3 | 82.0 | 83.3 | 4.58 | 0.30 | 4.0 | 139 |
| 11-21 | 70.0 | 80.8 | 86.7 | 4.46 | 0.22 | 9.6 | 139 |
| 11-22 | 65.0 | 83.0 | 78.3 | 4.62 | 0.59 | 8.7 | 122 |
| 11-23 | 71.7 | 91.5 | 78.3 | 4.65 | 0.74 | **14.9** | 168 |
| 11-24 | 61.7 | 72.5 | 85.0 | 4.34 | 0.17 | **13.7** | 105 |
| 11-25 | 66.7 | 81.6 | 81.7 | 4.55 | 0.10 | 8.2 | 91 |
| 11-26 | 56.7 | 72.3 | 78.3 | 4.39 | 0.23 | **14.9** | 158 |

**First four vs last four**, which is the robust comparison — it does not depend on
which single night you pick as the start or the end:

| | first 4 (11-14 → 11-17) | last 4 (11-23 → 11-26) |
|---|---|---|
| pass rate | 65.0 | 64.2 |
| fabrication | **6.88%** | **12.93%** |
| grounding | 0.52 | 0.31 |
| judge | 4.56 | 4.48 |

Fabrication **roughly doubled** while the pass rate did not move.

### The count that matters for a small sample

A 13-point mean on 13 runs could be two unlucky nights. So count runs, not means:

| | first 6 runs | last 6 runs |
|---|---|---|
| runs with fabrication ≥ 10% | **1 of 6** | **3 of 6** |
| runs with pass rate ≥ 70 | 3 of 6 | 2 of 6 |

The fabrication threshold is crossed three times as often in the second half. The
pass rate does not move. This is the shape of a drift, not of a wobble.

---

## Why the gate stayed green

`evals/run.py` compares **old vs new** and nothing else. `compare()` has no
absolute floor: a run is "worse" only relative to the run before it
(`evals/run.py:927`, `_pass_rate_down = _pr.get("delta") < 0`).

So the only sequence that can fail the build is **two consecutive bad nights**.
Thirteen nights drifting in one direction never produce that pair, because each
individual step is small:

```
6.2 → 11.1 → 4.2 → 6.0 → 8.0 → 4.8 → 4.0 → 9.6 → 8.7 → 14.9 → 13.7 → 8.2 → 14.9
```

The largest single-step move is +6.6 points. Nothing in that list is a regression
under the current rule. Three of them are, in aggregate, a doubling.

**This is the same defect as `EVAL_FINDINGS` finding 19** — *"the best run ever
reported as a regression"* — wearing the opposite mask. Finding 19 was a **false
positive**: the gate fired on a change that was not a regression. This is a **false
negative**: a change that is a regression, and the gate never fires, because
"regression" is defined one step at a time.

---

## The honest caveats

These are load-bearing. Without them the finding is a story, not a measurement.

1. **The judge is unvalidated against human labels.** `fabrication_rate` is
   *the judge's* flag rate, not a verified fabrication rate. A judge that drifts
   would produce this exact series with no change in the app. This is the single
   biggest weakness in the finding and it cannot be resolved from the data.
2. **`GROUNDING_FINDINGS` says set-membership is not entailment.** The judge decides
   whether a figure appears in a cited source; it does not decide whether the source
   supports the claim. A rising flag rate may be a rising *number of figures*, not a
   rising number of *false* figures.
3. **n = 13.** A sign test on 3-of-6 vs 1-of-6 is not significant on its own.
   It is a reason to look, not a conclusion.
4. **No run reached 90% coverage.** All 13 are between 60% and 86.7%. Under
   `evals/run.py:707-731` they are quotable only because the losses are
   tail-only, and the tail is the `@team` category — which is also where the
   expensive failures live. **A category that is routinely the tail is a category
   whose trend nobody is measuring.**
5. **The 11-23 row is the strongest counter-evidence, and it is included on
   purpose.** Grounding 0.74 — the best of the 13 — with fabrication 14.9% — the
   worst. If grounding and fabrication were two views of one thing, that row could
   not exist.

---

## What this is not

- Not a claim that the app regressed on 11-23. Between 11-15 and 11-22 the only
  merge to `main` in this repo was **PR #53, a docs-only change** ("correct 22
  findings from the Session 1 audit (no code changed)"). So the drift cannot be
  attributed to a code change — which is exactly why it points at the instrument.
- Not a claim that the judge is wrong. It is a claim that **nobody knows**, and that
  the series is consistent with the judge drifting.
- Not a new finding about the code. It is a finding about **the measuring
  instrument**, which is the same class as the nine in `OBSERVABILITY_FINDINGS.md`:
  a control that reported health while the system was not healthy.

---

## What to do about it

In order, and the first one is cheap:

1. **Store the series.** `eval-today.json` has **30-day artifact retention**. The
   report for 11-14 is deleted on 2026-12-14. A metric whose history expires before
   it can be looked at is not a metric — it is a notification. Commit a
   `evals/history/<date>.json` summary line per night, or append to a file. This is
   the only fix that makes every other fix possible.
2. **Add an absolute floor, not just a delta.** `pass_rate < 55` or
   `fabrication_rate > 12` should fail the build regardless of what yesterday did.
   A delta gate answers "did it move?"; a floor answers "is it acceptable?" — and
   those are different questions that the current workflow conflates.
3. **Validate the judge, or stop quoting it alone.** Until `fabrication_rate` is
   checked against human labels on a sample, it measures the judge as much as the
   app. Pair it with the rule-based `grounding_rate`, which cannot drift.
4. **Fix the denominator.** 0 of 13 runs reached 90% coverage. Either the suite is
   priced above the free tier, or the tail category should not be in the suite. A
   suite that normally runs 78% of itself is reporting on a different suite each
   night.

---

## Reproducing it

```bash
bash scripts/collect_nightly_reports.sh 40 reports   # 30-day window
python scripts/nightly_trend.py reports/
```

The window closes 2026-12-14 for the oldest row in this table.
