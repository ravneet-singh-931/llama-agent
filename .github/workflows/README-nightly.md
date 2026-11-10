# Nightly Agent Evals

`nightly-evals.yml` runs the 60 agent goldens once a day and **diffs the result
against the previous run**.

## Why it is not a PR check

Every golden is a real model call plus a judge call. The suite costs minutes and
money, so it cannot gate a pull request. The cheap half of the same evaluation —
routing (53 cases) and answer checks (18 fixtures) — is pure functions with no
model in them, and that *does* gate every PR via `python -m evals.gate` in the
`test` job.

## What the diff is for

One run tells you "87% passed". That number drifts and is not actionable. Two
runs tell you:

```
| Metric     | Old   | New  | Delta |
| pass_rate  | 100.0 | 75.0 | -25.0 |

Regressed: ['write-essay-topic']
Verdict: REGRESSION
```

A single case flipping on a Tuesday is invisible in an absolute number and
obvious in a diff.

## Setup

The only requirement is the repository secret **`GROQ_API_KEY`** (already used
by the deploy jobs).

Without it the job still runs and stays **green**: it emits a warning
annotation ("Nightly evals skipped - add the repository secret to enable them")
and skips the eval steps. A job that goes red because a secret was never set
teaches people to ignore red jobs.

## Behaviour matrix

| Situation | Result |
|---|---|
| No `GROQ_API_KEY` | warning annotation, job green, nothing else runs |
| Key set, no previous run | runs the suite, prints absolute numbers, green |
| Key set, previous artifact expired | same as above (notice emitted) |
| Key set, no regression | comparison table, green |
| Key set, a case regressed | comparison table, **exit 1** |
| Key set, `compare()` crashes | **exit with the real code** (a workflow bug, not a regression) |

## Verified

Triggers, the no-key path and the comparison path were all exercised against
the live repository:

- `workflow_dispatch` on `main` (run `42823339464`): checkout → install → key
  check → warning annotation → skipped eval steps → **job success**.
- The comparison command was run directly against synthetic reports with a
  flipped case: **`Regressed: ['b']`, exit 1**.

## Manual run

```bash
gh workflow run nightly-evals.yml --ref main
# opt out of failing on a regression (investigating a known one):
gh workflow run nightly-evals.yml --ref main -f fail_on_regression=false
```
