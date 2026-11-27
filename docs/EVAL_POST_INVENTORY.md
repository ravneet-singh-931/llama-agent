# POSTABLE EVAL MATERIAL — the verified inventory

**Scope:** the eval area only. Nothing here depends on tonight's run.
**Rule:** every number below was re-derived in-session, not copied from memory.
**Status:** 13 August-style errors were found while building this file and are
recorded in `## Corrections` at the bottom. Read that section before quoting.

Last verified: against `origin/main` @ `317209f`.

---

## How to read this file

Each entry has:

- **The fact** — what you can say in a post
- **The proof** — the file, line or test that makes it true
- **Status** — `READY` (quote it), `VERIFY-FIRST` (needs a fresh check), or
  `DO-NOT-QUOTE` (it is wrong or unstable)

---

# A. Fixed numbers — no new data needed

These will not change while the nightlies run.

### A1 — 21/60 = 35%, but 21/29 = 72.4%
- **Status:** `READY` — already published as post 1.
- **Proof:** `docs/EVAL_FINDINGS.md:143` (finding 6); `tests/test_evals.py:501`
- **Note:** the 72.4% is 21 ÷ 29. It is **not** 36 ÷ 48 (that is 75.0%, a
  different run, and it is the bug fixed in PR #62).

### A2 — A full run cannot be faster than 45 minutes
- **The fact:** 5,239 input tokens per case × 60 cases = **314,340 tokens**.
  The free tier allows **7,000 input tokens/minute**. 314,340 ÷ 7,000 = 44.9 →
  **45 minutes is the floor**, whatever else you do.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:376-379` (finding 14)
- **Re-derived:** 5,239 × 60 = 314,340. 314,340 ÷ 45 = 6,985/min. ✓

### A3 — The daily cap is 200,000 tokens, and the provider printed it
- **The fact:** not measured by subtraction — the error message named it:
  `Limit 200000, Used 198265, Requested 4506`.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:413-416` (finding 15)

### A4 — Budget: 500,000 → 195,500 tokens per run
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:811-818`
- **Re-derived and consistent:**

  | | team | other | total |
  |---|---|---|---|
  | before | 7 × 52,500 = 367,500 | 53 × 2,500 = 132,500 | **500,000** |
  | after | 7 × 9,000 = 63,000 | 53 × 2,500 = 132,500 | **195,500** |

  304,500 tokens saved per run. `(200,000 − 132,500) ÷ 7 = 9,642.86`, so a
  9,000/case budget leaves real headroom.

### A5 — 2.2% headroom
- **The fact:** 195,500 ÷ 200,000 = 97.8%, leaving **2.2%**.
- **Status:** `READY`
- **Proof:** the preflight line printed by the 2026-11-27 run:
  `fits (headroom 2.2%)`
- **Why it matters:** one retry or one long answer loses the tail, and the tail
  is `@team`. Say it that way — it is a budget fact, not a bug.

### A6 — 108% of the meter
- **The fact:** 52 cases × 5,239 = 272,428 tokens over 36 minutes =
  **7,567/min against a 7,000 cap = 108.1%**.
- **Status:** `READY` — but see **Correction 1**. The doc's prose pairs this
  number with the wrong duration.
- **Proof:** `docs/EVAL_FINDINGS.md:374-380` (finding 14)

### A7 — 100% on `team` = 1 of 7 cases ran
- **The fact:** 6 of 7 `team` cases hit the rate limit; the one that ran passed,
  so the table printed 100%.
- **Status:** `READY` — written as post 2 (`docs/posts/POST_02.md`).
- **Proof:** the 2026-11-27 report, category row `team | 7 | 100.0 (of 1 that ran)`

---

# B. Grader bugs — each with a named test

The strongest material in the repo: every one is "the check was wrong, here is
the proof", and every fix has a test that fails if the bug comes back.

### B1 — Correct code failed because it was not in a box
- **The number:** `code-debug` scored **5.0/5** from the judge; the rule check
  failed it with *"no python code block"*.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:70` (finding 3);
  `test_an_inline_snippet_counts_as_code_when_the_case_asks_for_python` ✓ exists
- **The line:** *it was grading formatting, not correctness.*

### B2 — A correct answer failed on a hyphen you cannot see
- **The number:** `team-slack-bot` scored **4.75/5**; the error was
  `invalid character '‑' (U+2011)`.
- **Status:** `READY` — **highest reach in the whole inventory.**
- **Proof:** `docs/EVAL_FINDINGS.md:88` (finding 4);
  `test_every_dash_in_the_family_is_folded` ✓ exists
- **The guard that makes it senior:** a test asserts the fold does **not** change
  real subtraction.

### B3 — A regex answered in a Python block, failed three runs three ways
- **The number:** `code-regex-pin`, judge **5.0/5**, check *"no regex code block"*.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:108` (finding 5);
  `test_regex_inside_a_python_block_passes` ✓ and
  `test_any_code_block_is_not_automatically_a_regex` ✓ both exist
- **Best line in the repo:** *"I kept adding a special case instead of
  enumerating the shapes a real answer can take."*

### B4 — Grounding graded cases that never asked for sources
- **The number:** a migration plan failed with *"1 of 42 figures found in sources"*.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:53` (finding 2);
  `test_grounding_is_not_applied_to_cases_that_do_not_ask_for_sources` ✓ exists

### B5 — A failed judge counted as a pass
- **The fact:** when the judge call itself failed (429, unparseable reply), the
  case was decided by rule checks alone and could pass.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:343` (finding 12);
  `test_a_failed_judge_means_not_graded_not_passed` ✓ exists
- **The line:** *a broken judge quietly inflating your score is worse than no
  judge at all.*

### B6 — The eval graded the app's own error message
- **The number:** the app's rate-limit notice was sent to the judge, which scored
  it **1.0** and flagged it as fabrication.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:357` (finding 13);
  `test_eval_does_not_grade_the_apps_rate_limit_notice` ✓ exists
- **The line:** *that number describes the provider, not the app.*

### B7 — One weak word cost a whole case
- **The number:** `no_meta` fired **once in 60** cases — on `team-cafe`, matching
  *"source material 2."*
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:260` (finding 10);
  `test_a_single_weak_meta_phrase_does_not_fail_the_case` ✓ and
  `test_real_pipeline_narration_still_fails_the_case` ✓ both exist
- **Why two tests:** one loosens it, the other proves it did not loosen too far.

### B8 — The comparison crashed because a check improved
- **The number:** a real **83.3%** run died at the diff step with
  `KeyError: 'no_meta'`. `no_meta` failed once in the baseline and zero times
  that day, so the key existed only on the old side.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:202` (finding 8);
  `test_a_check_that_goes_to_zero_does_not_crash_the_compare` ✓ exists
- **The line:** *the compare crashed precisely because the check got better.*

---

# C. Design findings — the senior-engineer material

### C1 — The comparison gate has no idea what "acceptable" means
- **The fact:** the whole test is one line —
  `_pass_rate_down = _pr.get("delta") is not None and _pr["delta"] < 0`
- **Status:** `READY`
- **Proof:** `evals/run.py:992` — see **Correction 2** (the line moved from 927)
- **The line:** *a gate that only compares itself to yesterday can tell you that
  you slipped today. It cannot tell you that you have been slipping for two weeks.*

### C2 — Thirteen green nights, and the thing got worse
- **The numbers:** 13 scheduled runs, 2026-11-14 → 2026-11-26, all 60 cases.

  | | first 4 runs | last 4 runs |
  |---|---|---|
  | pass rate | 65.0 | 64.2 |
  | **fabrication flagged** | **6.88%** | **12.93%** |
  | grounding rate | 0.52 | 0.31 |

  Counted rather than averaged: runs with fabrication ≥ 10% went from **1 of 6**
  to **3 of 6**.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:1232-1260` (finding 20); `docs/DRIFT_FINDING.md`
- **Every one of those thirteen runs was green.** That is the post.

### C3 — A metric whose history expires in 30 days is a notification
- **The fact:** nightly reports upload as build artifacts with
  `retention-days: 30`. The comparison that ever happens is today vs yesterday.
- **Status:** `READY`
- **Proof:** `.github/workflows/nightly-evals.yml:179` and `:272`
- **Careful:** the same workflow also has a `retention-days: 90` at `:170` — check
  which artifact you are naming before you say "30 days".
- **The line:** *a metric whose history expires before you can look at it is a
  notification, not a metric.*

### C4 — The change-detector hashed the wrong thing
- **The fact:** `prompt_fingerprint` hashes prompt **text**. The largest change to
  the grading logic in the project's history left it identical. Fixed by
  `evidence_fingerprint()` — a hash of `checks.py`, `judge.py`,
  `dataset.jsonl`, the judge model and the threshold.
- **Status:** `READY`
- **Proof:** `evals/run.py:427` (`evidence_fingerprint`), `:453`
  (`prompt_fingerprint`), `:1038-1040` (where the two are compared)

### C5 — Small categories were structurally guaranteed to trip the gate
- **The fact:** `MIN_CASES_FOR_SIGNAL = 8` (`evals/run.py:990`). Before it, any
  negative category counted as a regression — and `analysis` has 4 cases, so one
  flipped case moved it 25 points.
- **Status:** `READY` — see **Correction 3** for the exact wording.
- **Proof:** `evals/run.py:990, 997`

### C6 — The report refuses a rate below 90% coverage
- **The fact:** `threshold = float(os.getenv("EVAL_MIN_GRADED_PCT", "90") or 90)`,
  then `inconclusive = n > 0 and completeness < threshold`, with one exemption
  (`_TAIL_CATEGORIES = {"team"}`) and one hard floor (`completeness < 70`).
- **Status:** `READY` — measured, not read. See `## Measured behaviour` below.
- **Proof:** `evals/run.py:681, 707, 725, 730-731`;
  `test_a_half_run_report_is_inconclusive_not_a_pass_rate`

---

# D. Cost and engineering findings

### D1 — The budget broke the request cap
- **The fact:** a per-case token budget fixed the daily spend and **broke the
  per-request cap**. 36/60 = 60.0% raw, **36/48 = 75.0%** graded.
- **Status:** `VERIFY-FIRST` — this row was mis-stated twice in the repo's own
  history (see the note at `docs/EVAL_FINDINGS.md:457`).
- **Never quote:** `36/48 = 72.4%`. That is wrong. It is 75.0%.

### D2 — `order_by_cost()`: expensive cases starve cheap ones
- **The numbers:** the five `@team` cases sat at positions **24–28**;
  `team-cafe` took **139.8 s**, `team-todo-landing` **74 s**. 31 cheap cases died
  behind them. After the fix, team occupied positions **54–60**.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:183-196` (finding 7);
  `test_team_cases_run_last_so_quota_starvation_hits_them_not_the_cheap_ones` ✓

### D3 — The timeout applied N times
- **The numbers:** `team-library-api` took **309 s** against a 300 s case timeout
  — "exactly two `AGENT_STEP_TIMEOUT`s". `f.result(timeout=150)` was called in a
  loop, so the Nth future got 150 s *on top of* the first N−1; and
  `shutdown(wait=True)` then blocked on the hung worker anyway.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:278-292` (finding 11a);
  `tests/test_team_step_deadline.py` ✓ exists

### D4 — Three hanging steps now return in 0.5 s, not 1.5 s
- **The fact:** **measured, standalone** (the real test imports `multi_agent`,
  which needs `llama_index`, so it cannot run in this sandbox). The deadline
  logic was reproduced exactly:

  ```
  FIXED : 0.50s   (one shared deadline, shutdown(wait=False))
  BUGGY : 1.50s   (per-future timeout in a loop, shutdown(wait=True))
  ```

- **Status:** `READY` — but quote **0.5 s vs 1.5 s at a 0.5 s step timeout**, not
  the doc's "1.00 s vs 3 s" (that is the test's assertion bound `< 1.5`, not a
  measurement). See **Correction 4**.
- **Proof:** `tests/test_team_step_deadline.py:24-49`, reproduced above.

### D5 — 89 minutes of eval step, 11 cases, 17.3 s of work
- **The numbers:** run `43029519652`. **89 minutes** producing **17.3 seconds** of
  actual work across 11 cases; case 12 (`safety-english`) never returned and the
  90-minute job timeout killed the run.
- **The cause:** the deadline was checked *between stream events*. A stream that
  goes **silent** has no events to check between. And `_bounded()` guards the gap
  between events — but the stream object is created on the calling thread first.
  If *that* blocks, nobody is holding a clock.
- **Status:** `READY`
- **Proof:** `docs/EVAL_FINDINGS.md:293-310` (finding 11b and 11c)
- **The line:** *a stream that goes silent is never checked, because there is no
  event to check between.*

---

# Measured behaviour — the coverage rule, executed not read

`summarize()` was run against synthetic night shapes built from
`evals/dataset.jsonl` (60 cases, `team` = 7):

| graded | coverage | status | headline rate? | floor |
|---|---|---|---|---|
| 60 | 100.0% | ok | yes | ok |
| 54 | 90.0% | ok | yes | ok |
| 53 | 88.3% | ok | yes | ok ← **tail only**, `team` absent |
| 53 | 88.3% | inconclusive | NO | ok ← **same coverage, spread loss** |
| 50 | 83.3% | inconclusive | NO | ok |
| 42 | 70.0% | inconclusive | NO | ok |
| 41 | 68.3% | inconclusive | NO | **BREACH** |
| 29 | 48.3% | inconclusive | NO | **BREACH** |

**What this proves:**

1. The trigger is **coverage < 90%**, not "spread loss". There is no spread-loss
   branch: 53/60 with the losses spread is inconclusive only because it is under
   90, and 55/60 spread is `ok`.
2. The single exemption is "**every** missing case is in `_TAIL_CATEGORIES`".
3. The 70 floor fires **independently** of status, via `check_floors()`.
4. The rendered report **does not print the raw pass rate** when inconclusive —
   only the graded rate. The raw 35.0 survives in the JSON, which is what the
   regression test asserts on.

---

# Corrections — found while building this file

These are errors in the sources above, or errors I made describing them. Fix them
before quoting the entry.

### Correction 1 — finding 14 contradicts itself
`docs/EVAL_FINDINGS.md:380-381` says:

> *"At a 10-second pause the run finished in ~35 minutes, which is 7,567
> tokens/min against a 7,000 cap — 108%."*

That pairing is wrong. Re-derived:

| scenario | tokens | minutes | rate | % of 7,000 |
|---|---|---|---|---|
| **52 cases (what actually happened)** | 272,428 | 36 | **7,567** | **108.1%** ✓ |
| full 60 cases | 314,340 | 35 | 8,981 | 128.3% |
| full 60 cases | 314,340 | 45 | 6,985 | 99.8% |

So **108% is correct** — it is 52 cases in 36 minutes. But the prose attaches
7,567 to "~35 minutes" for a full run, and the code block on the next lines says
`pause 10s → 35 min → 8,981/min`. Those are two different scenarios in the same
paragraph, and 8,981 is 128%, not 108%.

**Fix:** state it as **272,428 tokens ÷ 36 min = 7,567/min = 108%**, and label
the 8,981 row as a full-60-case projection.

### Correction 2 — C1's line number moved
The `_pass_rate_down` line is **`evals/run.py:992`**, not 927. Line numbers in
this file drift; the string is stable. Quote the string.

### Correction 3 — C5's wording was backwards
I wrote that `analysis`'s −25 point flip "is structurally guaranteed to trip the
gate". That was true **before** `MIN_CASES_FOR_SIGNAL = 8` landed. Now
`analysis` (4 cases) is **excluded** from significance because
`min(n_new, n_old) >= 8` is false. The small category is ignored, not handled.

**Correct framing:** *an `analysis` case flipping is −25 points, so the gate used
to fire on it every time. It is now excluded for being too small — which stops
the false alarm but also means a real 25-point drop in that category is invisible.*

### Correction 4 — D4's numbers were the test's bound, not a measurement
The doc says *"3 hanging steps now return in 1.00 s, not 3 s"*. The test asserts
`elapsed < 1.5` at a 0.5 s step timeout. Reproduced standalone:
**0.50 s fixed vs 1.50 s buggy**. Quote those two.

### Correction 5 — the artifact retention is 30 days *and* 90 days
`nightly-evals.yml` has `retention-days: 90` at `:170` and `retention-days: 30`
at `:179` and `:272`. C3 is about the **nightly report** artifact. Name the path
before naming the number.

---

# What is NOT postable yet

| Held | Why |
|---|---|
| pass_rate 56.7 → 76.7 | different coverage; the tool printed `NOT A QUALITY COMPARISON` |
| fabrication 14.9 → 7.7 | z = −0.28 against a 13-run mean of 8.79, sd 3.87. Inside the noise. |
| grounding 0.225 → 0.333 | measured on **9 cases**, 2 skipped |
| latency p50 1.7 → 2.1 | it got worse, and coverage differed |
| any "range of 70 points" | the worst 8.3 is a 12-case smoke run, not a real run |

**Gate for the improvement post:** five or six consecutive nights at ≥ 90%
coverage, then compare the mean of those against the mean of the thirteen.
Until then, the honest post is *"my eval printed a 12-point win and I did not
believe it"* — which needs no new data and is the better story.

---

# Post queue

| # | Post | Entry | Data ready? |
|---|---|---|---|
| 1 | 35% vs 72.4% | A1 | ✅ published |
| 2 | 100% on one case | A7 | ✅ written |
| 3 | The invisible hyphen | B2 | ✅ no new data |
| 4 | Correct code, 5.0/5, "no code block" | B1 | ✅ |
| 5 | The eval graded the app's 429 message | B6 | ✅ |
| 6 | 13 green nights, the thing got worse | C2 + C3 | ✅ |
| 7 | The gate has no idea what "acceptable" means | C1 | ✅ |
| 8 | 89 minutes, 17.3 seconds of work | D5 | ✅ |
| 9 | A regex failed three runs three ways | B3 | ✅ |
| — | The improvement | held | ❌ needs 5–6 clean nights |
