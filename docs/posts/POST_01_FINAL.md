# POST 1 — FINAL. Copy from here.

**Image:** `docs/posts/img/post01-grid-dark.png`
**Publish:** paste the block under "THE POST", attach the image, put the comment under it.

Every number in this post was verified by **running** `summarize()` against the real
2026-11-11 night shape, not by reading the code. See `## The rule, as measured`.

Sources: `docs/EVAL_FINDINGS.md` finding 6 · `evals/run.py:681,707,725,730-731` ·
`tests/test_evals.py::test_a_half_run_report_is_inconclusive_not_a_pass_rate`

---

## THE POST

My test suite told me my AI agent had dropped from 75% to 35% overnight. For about ten minutes, I believed it.

I run a 60-question test suite against my agent every night. That night, the free-tier API quota ran out at question 29, so the other 31 never ran.

My rule is that anything that can't be graded counts as not passed—which I still think is right, because otherwise a broken grader quietly inflates your score. But those 31 zeros stayed in the denominator: 21 out of 60 is 35%.

The real number was 21 out of the 29 that ran, which is 72.4%. The night before was 75%, so nothing had actually changed.

The clue was right there in the same report: median response time had dropped from 3.0 seconds to 0.9s. Quality doesn't normally fall while speed improves. It only looked that way because almost nothing was being answered.

Here is how I fixed it:

• **Below 90% coverage:** The report refuses to print a headline pass rate. It says "INCONCLUSIVE, only 29/60 ran"—that's the run in the chart—and shows the rate among the questions that did: 72.4%. (The one exception: if every missing question sits in the category I deliberately run last, it names the gap and still reports.)

• **Below 70% coverage:** It is INCONCLUSIVE no matter what—no exemption can save it—and the run trips a hard completeness floor, so it can never be read as a quality score by accident.

• **Regression test:** I added a test that replays that exact night. It checks that the report is marked INCONCLUSIVE, that the graded rate is 72.4%, and that the raw 35% is still computed—demoted, not deleted.

Every percentage is a rate over whatever survived the run. If you can't see the denominator, you don't have a number.

How do you handle partial runs in your evals? I'd genuinely like to know—this felt obvious in hindsight.

---

## FIRST COMMENT (post this immediately after)

Code, the test, and the full write-up:
https://github.com/ravneet-singh-931/llama-agent

The two files that matter:
• `evals/run.py` → `summarize()` — the rule is `inconclusive = completeness < threshold`, threshold defaults to 90
• `tests/test_evals.py` → `test_a_half_run_report_is_inconclusive_not_a_pass_rate`

That test replays 21 passed / 31 ungraded / 8 failed and asserts status is `inconclusive`, the graded rate is 72.4%, and `pass_rate` is still 35.0 — demoted, not deleted.

---

## The rule, as measured

Run against the real night shape (q0–q28 ran: 21 pass, 8 fail; q29–q59 never ran):

```
cases=60 graded=29 coverage=48.3%
status      = inconclusive
pass_rate   = 35.0      <- still computed, no longer the headline
pass_rate_graded = 72.4 <- the number worth quoting
```

Rendered report head:

```
- **INCONCLUSIVE - only 29/60 cases ran** (48.3% coverage)
- Pass rate among cases that ran: **72.4%** (31 not graded: provider rate limit / outage)
```

Tail-only case (`team` absent, 53/60 = 88.3%):

```
status = ok   tail_only_loss = True
- **No result at all for: team** - those categories did not run, so nothing is claimed about them
- pass rate **x%** over the 53/60 cases that ran
```

That is the exception the post describes: it **names the gap** and **reports anyway**.

Note on the "below 70%" line: `evals/run.py:730` sets `massive = completeness < 70`,
which removes the tail exception. With `team` at 7 of 60 cases, a purely tail-only
loss bottoms out at 53/60 = 88.3%, so that clause cannot fire from tail loss alone
today — it is a backstop for a larger tail category. The sentence is still true:
anything below 70% coverage is INCONCLUSIVE either way.

**DO NOT** describe the rule as "tail loss vs spread loss". There is no spread-loss
branch. The trigger is `completeness < 90`; the only exemption is that every missing
case falls inside `_TAIL_CATEGORIES` (`{"team"}`, `evals/run.py:638`). Missing 5 cases
spread across 5 categories at 91.7% coverage reports `status=ok`.

---

## Before you hit publish — 4 checks

1. **Attach `post01-grid-dark.png`.** Green = 21, red = 8, grey = 31. Counted cell by cell.
2. **Do not write "35% → 72.4%"** anywhere without the denominators. The post is about denominators; the punchline cannot skip them.
3. **Do not claim the test asserts "never 35%".** It asserts `pass_rate == 35.0` — 35% is *still there, demoted*. That is the stronger claim and the true one.
4. **Link goes in the comment**, not the body.

---

## If someone challenges it

| They say | You answer |
|---|---|
| "Why didn't 31 run?" | Free-tier token cap, hit at question 29. The report had 18 rows literally saying `RateLimitError: 429`. |
| "So your suite is too big for your quota." | Correct. Later runs were re-ordered cheapest-first and a per-case token budget was added: 500,000 → 195,500 tokens per run. |
| "Isn't excluding ungraded cases just hiding failures?" | The opposite. `pass_rate` still counts them as failures and is still computed — the test asserts it equals 35.0. It just stops being the headline. |
| "Show me the test." | `test_a_half_run_report_is_inconclusive_not_a_pass_rate` — 21 pass / 31 ungraded / 8 fail → INCONCLUSIVE, 72.4% graded, 35.0 retained. |
| "Your nightlies all sit under 90% coverage." | Right, and that is the named exception: the missing cases are all `team`, which `order_by_cost()` deliberately runs last. The report says "No result at all for: team" and gives the rate over the 53 that ran. It never claims to cover team. |
