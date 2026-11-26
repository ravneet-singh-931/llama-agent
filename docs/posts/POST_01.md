# Post 1 — the text, ready to publish

**Area:** eval. **Series position:** 1 of 7.
**Asset (use this one):** `docs/posts/img/post01-grid-dark.png` — the 60-square grid, dark.
Green = passed (21), red = failed (8), grey = never ran (31). The reader can COUNT the
denominator instead of being told it, which is the whole argument. Regenerate with
`python scripts/make_post_grid.py`.

Also available: `post01-two-denominators-dark.png` (two-card version, `scripts/make_post_chart.py`).

`docs/posts/img/number_trail_chart.png` also exists and is correct, but it is a seven-run
trend chart — it answers "how did the suite move over time", which is not this post's story.
Use it for a later post, not this one.

Every number below was re-derived from `docs/EVAL_FINDINGS.md` and the test that
pins it, in the same session as writing this. The arithmetic is written out so it
can be checked in ten seconds.

---

## The post

> My eval suite published **35%**.
>
> The real number was **72.4%**.
>
> Same run. Same report. Nothing had regressed.
>
> Here is what happened, and why it took me weeks to see it.
>
> The suite runs 60 cases. That night the free tier's token allowance ran out at
> case 29. Thirty-one cases never ran.
>
> My rule was: **a case that cannot be graded counts as not passed.** That rule is
> correct — without it, a broken judge silently inflates your score. But those 31
> zeros still went into the *denominator*.
>
> So: 21 passed ÷ 60 cases = 35%.
>
> The number that describes the app is 21 passed ÷ **29 that ran** = **72.4%**. The
> day before was 75%. That is noise, not a collapse.
>
> **The tell was in the same report, and I missed it for weeks:**
>
> p50 latency fell from **3.0 s to 0.9 s**.
>
> Almost nothing was being answered. A quality drop and a speed-up do not happen
> together. When they do, you are not measuring quality — you are measuring how
> much of the run survived.
>
> What I changed:
>
> **The report now looks at *which* cases are missing, not just how many.**
>
> • **Tail loss** — if the only thing missing is the category I deliberately run
>   last (it is the expensive one), the report still gives a rate, but it *names
>   the gap*: `no result for @team`. You get a number back, and you are not allowed
>   to quote it as if it covered the thing that did not run.
>
> • **Spread loss** — if misses are spread across categories, or more than 30% of
>   the suite never ran, the report is `INCONCLUSIVE`. No headline pass rate at
>   all. It prints `only 29/60 ran`, gives the rate among cases that did, and
>   exits with code 3 — so it is neither a green tick nor a red build.
>
> There is a test that replays this exact run — 21 passed, 31 ungraded, 8 failed —
> it checks the report is marked INCONCLUSIVE, that the headline number is 72.4%,
> and that **35% is still there, demoted rather than deleted.**
>
> **The rule I took from it: any rate is a rate over whatever survived. If you
> cannot see the denominator, you do not have a number. You have a headline.**

---

## The first comment

> Code, test and full write-up: `evals/run.py` (`summarize()`), the test is
> `test_a_half_run_report_is_inconclusive_not_a_pass_rate`, and the findings doc is
> `docs/EVAL_FINDINGS.md`, finding 6.
>
> The chart is seven real runs of the same 60-case suite. Every point is labelled
> with how many cases actually ran — that label is the entire post.

---

## Before you post — six checks

1. **The image is correct.** I read the PNG this session. Every point matches
   `EVAL_FINDINGS.md:444`–`466`. Do not regenerate it.

2. **The caption was wrong and is now fixed** (`a198096`). It used to say
   *"36 of 60 passed, only 48 graded, so 72.4%"* — but 36/48 is **75.0%**. The
   72.4% belongs to 21/29. If you have an old copy of that caption anywhere,
   delete it. Never quote both numbers in one sentence.

3. **Do not say "35% → 72.4%" without the denominators.** The whole post is about
   denominators. Dropping them in your own summary would undercut it in the first
   line.

4. **Say "free tier" somewhere.** Someone will ask why 31 cases didn't run. The
   answer is quota, not corruption, and it takes three words to say.

5. **The exit code 3 detail is the senior signal.** Keep it. Most people who write
   about evals do not have a third exit state. "Neither a green tick nor a red
   build" is the sentence a staff engineer will stop on.

6. **Link in the first comment, not the body.**

---

## Why this one is first, when I earlier said post 4

Because you asked for **world-class senior engineering**, not reach.

Post 4 (the invisible hyphen) gets more views. But it is a *bug story* — it shows
you found a defect. This post shows something rarer:

| A bug story shows | This post shows |
|---|---|
| you can find a defect | you can tell when **your own instrument** is lying |
| you fixed a check | you made the check **refuse to answer** when it cannot |
| you know what to measure | you know what a **measurement is worth** |

Anyone can post a bug. The sentence *"below 90% coverage the report refuses to
print a pass rate"* is a **design decision about honesty**, and it is the kind of
thing that separates a mid-level from a senior engineer in the first paragraph.

It also has the strongest possible ending, because the fix is not a fix — it is a
**refusal**. Engineers who have been burned by confident dashboards will feel that
sentence immediately.

---

## If it lands, the next three

| Next | Post | Why |
|---|---|---|
| 2 | Post 4 — the invisible hyphen | Widest reach in the series; recruits readers |
| 3 | Post 1 — 75% vs 4.43/5 | Opens the argument properly |
| 4 | Post 7 — the unvalidated judge | Now it reads as integrity, not confession |

Post 7 only works **after** people have seen you be rigorous. Posting it first reads
as an admission; posting it seventh reads as a standard.

---

## If someone challenges it

| They say | You answer |
|---|---|
| "Why didn't 31 cases run?" | Free-tier token cap, hit at case 29. The report had 18 rows saying `RateLimitError: 429`. |
| "Then your suite is too big for your quota." | Correct. That's why later runs were re-ordered cheapest-first and a per-case token budget was added — 500,000 → 195,500 tokens per run. |
| "Isn't excluding ungraded cases just hiding failures?" | The opposite. The pass rate is still computed and still counts them as failures. It just stops being the *headline*. |
| "Show me the test." | `test_a_half_run_report_is_inconclusive_not_a_pass_rate` — 21 pass / 31 ungraded / 8 fail → asserts 72.4%, never 35%. |
| "Could the 72.4% be wrong too?" | It is over 29 cases, so it is noisy. Which is the point of the coverage column — you can see the sample it rests on. |

**Never argue a number you cannot open.** If you cannot open it, say "I'd have to
check." That costs nothing; guessing costs the series.
