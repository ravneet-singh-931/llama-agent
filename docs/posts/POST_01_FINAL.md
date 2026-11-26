# POST 1 — FINAL. Copy from here.

**Image:** `docs/posts/img/post01-grid-dark.png`
**Publish:** paste the block under "THE POST", attach the image, put the comment under it.

Every number verified: 21 passed, 8 failed, 31 never ran, 60 total.
21/60 = 35.0% · 21/29 = 72.4% · median response time 3.0 s → 0.9 s.
Source: `docs/EVAL_FINDINGS.md` finding 6 · `evals/run.py:725-731` ·
`tests/test_evals.py::test_a_half_run_report_is_inconclusive_not_a_pass_rate`

---

## THE POST

My test suite told me my AI agent had dropped from 75% to 35% overnight. For about ten minutes, I believed it.

I run a 60-question test suite against my agent every night. That night the free-tier API quota ran out at question 29, so the other 31 never ran. My rule is that anything that can't be graded counts as not passed — which I still think is right, because otherwise a broken grader quietly inflates your score. But those 31 zeros stayed in the denominator: 21 out of 60 is 35%.

The real number was 21 out of the 29 that ran, which is 72.4%. The night before was 75%, so nothing had actually changed.

The clue was right there in the same report: median response time (p50) had dropped from 3.0 seconds to 0.9 s. Quality doesn't normally fall while speed improves. It only looked that way because almost nothing was being answered.

Here is how I fixed it:

The report now looks at *which* cases are missing, not just how many.

• **Tail loss** — if the only thing missing is the category I deliberately run last (it's the expensive one), the report still gives a rate, but it names the gap: "no result for @team". You get a number back, and you're not allowed to quote it as if it covered the thing that didn't run.

• **Spread loss** — if misses are spread across categories, or more than 30% of the suite never ran, the report is INCONCLUSIVE. No headline pass rate at all. It prints "only 29/60 ran", gives the rate among the cases that did, and exits with code 3 — so it's neither a green tick nor a red build.

• **Regression test** — I added a test that replays that exact night. It checks the report is marked INCONCLUSIVE, that the headline number is 72.4%, and that 35% is still there — demoted, not deleted.

Every percentage is a rate over whatever survived the run. If you can't see the denominator, you don't have a number.

How do you handle partial runs in your evals? I'd genuinely like to know — this felt obvious in hindsight.

*(Full write-up and the test: first comment.)*

---

## FIRST COMMENT (post this immediately after)

Code, the test, and the full write-up:
https://github.com/ravneet-singh-931/llama-agent

The two files that matter:
• `evals/run.py` → `summarize()`, where the report decides whether it is allowed to print a rate
• `tests/test_evals.py` → `test_a_half_run_report_is_inconclusive_not_a_pass_rate`

That test replays 21 passed / 31 ungraded / 8 failed and asserts the report is INCONCLUSIVE, that the graded rate is 72.4%, and that 35% is still present but no longer the headline.

---

## Before you hit publish — 4 checks

1. **Attach `post01-grid-dark.png`.** Green = 21, red = 8, grey = 31. Counted cell by cell.
2. **Do not write "35% → 72.4%"** anywhere without the denominators. The post is about denominators; the punchline cannot skip them.
3. **Do not claim the test asserts "never 35%".** It asserts 35% is *still there, demoted*. That is the stronger claim and the true one.
4. **Link goes in the comment**, not the body.

---

## If someone challenges it

| They say | You answer |
|---|---|
| "Why didn't 31 run?" | Free-tier token cap, hit at question 29. The report had 18 rows literally saying `RateLimitError: 429`. |
| "So your suite is too big for your quota." | Correct. Later runs were re-ordered cheapest-first and a per-case token budget was added: 500,000 → 195,500 tokens per run. |
| "Isn't excluding ungraded cases just hiding failures?" | The opposite. The pass rate still counts them as failures and is still computed. It just stops being the headline. |
| "Show me the test." | `test_a_half_run_report_is_inconclusive_not_a_pass_rate` — 21 pass / 31 ungraded / 8 fail → INCONCLUSIVE, 72.4% graded, 35% retained. |
| "Your nightlies are all under 90% coverage — doesn't that make this moot?" | That is tail loss, not spread loss: the missing category is the expensive one deliberately ordered last, and the report names it. Below 70% it refuses outright. |
