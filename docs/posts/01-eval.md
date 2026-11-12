# Post 1 — The 35% That Was Really 72.4%

**Status:** ready to publish.
**Image:** `img/number_trail_chart.png` — attach it to the post.

Your draft was **3,651 characters**. LinkedIn's limit is **3,000**, so it would have been cut
off mid-sentence. Both versions below fit. **No claim was changed and no number was altered** —
these are cuts only.

| | characters | headroom | use when |
|---|---|---|---|
| **Version A** | 2,973 | 27 | you want all the detail kept |
| **Version B** | 2,474 | 526 | you want a faster scroll to the payoff |

**Recommend B.** It reaches the 4.35/5 contradiction before LinkedIn's "see more" fold, keeps
every number, and 526 characters of headroom means no risk of a surprise truncation if you edit
a line before posting.

---

## Version A — full detail, trimmed

> 2,973 characters. Plain text — paste as-is.

My eval suite reported 35%.

The real number was 72.4%.

It had published its own outage as quality.

A nightly run came back at 35%, down from 75% the day before. Every category down. It looked like the agent had collapsed.

But the judge — a second model that scores every answer 1-to-5 — was still averaging 4.35/5. A 35% pass rate next to a 4.35/5 quality score does not add up.

So I read the rest of the report.

The free tier's token allowance had run out at case 29 of 60. The evidence was in the same report:

- not graded: 31
- 30 cases where the agent replied "couldn't complete that task"
- 18 rows literally saying RateLimitError: 429
- cases 29–60 each finished in 0.08 to 0.9 seconds

Almost nothing was answered. The system kept calling the model, kept getting refused, kept marking those cases not-passed.

A case that cannot be graded counts as not passed. That rule is correct — without it a broken judge would silently inflate the score. But those 31 zeros still entered the denominator.

21 passed out of 29 that actually ran = 72.4%. Against 75% the day before. Within noise. Nothing had regressed.

The tell was there all along: p50 latency fell from 3.0s to 0.9s.

A quality drop and a speed-up do not happen together. When your agent gets faster AND worse at once, it is usually not answering. It took hours to notice, because everyone watched the pass rate.

The fix: below 90% coverage, the report refuses to print a pass rate.

INCONCLUSIVE — only 29/60 cases ran (48.3% coverage)
Pass rate among cases that ran: 72.4%

It exits with code 3 — neither a green tick nor a red build. A run that didn't measure the app shouldn't score it, and shouldn't fail the build either.

This was the first of four times the same habit showed up:

- A not-graded case entered the pass-rate denominator → 35%
- A failed judge call counted as a pass → score inflated
- The app's own rate-limit notice was graded as a fabricated answer → fabrication inflated
- An ungraded case scored 0% on tool recall because set(expected) <= set() is False

Four instances of one habit: the system kept turning "we did not observe it" into "it failed."

Absence of evidence is not evidence of failure. Once you internalize that, eval systems stop lying to you.

A measurement tool fails toward false alarms as easily as toward false confidence. The 35% was a false alarm. A red build on noise is a false alarm. A KeyError in the diff step is a false alarm.

Everything around the tests is also code, and it fails loudly and wrongly.

The chart is the full number trail: 87%, 75%, 35% (the outage), 83.3%, 88.7% (first run to reach the end), 60.0% (my own fix broke the request cap), 76.7% (best coverage ever, reported as a regression).

Two of those numbers were lies.

Posts 2 and 3 cover the rest — five grader-side bugs in one night, and a regression detector that ran for weeks without detecting one.

#LLMOps #AIEvaluation #SoftwareEngineering #QualityAssurance #AIAgents

---

## Version B — recommended

> 2,474 characters. Plain text — paste as-is.

My eval suite reported 35%.

The real number was 72.4%.

It had published its own outage as its quality.

A nightly run came back at 35%, down from 75% the day before. Every category down. It looked like the agent had collapsed overnight.

But the judge — a second model that scores every answer 1-to-5 — was still averaging 4.35/5.

A 35% pass rate next to a 4.35/5 quality score does not add up. So I read the rest of the report.

The free tier's token allowance had run out at case 29 of 60:

- not graded: 31
- 30 cases where the agent replied "couldn't complete that task"
- 18 rows literally saying RateLimitError: 429
- cases 29–60 each finished in 0.08 to 0.9 seconds

Almost nothing was answered. The system kept calling the model, kept getting refused, kept marking those cases as not-passed.

A case that cannot be graded counts as not passed — correct, or a broken judge would silently inflate the score. But those 31 zeros still entered the denominator.

21 passed out of 29 that actually ran = 72.4%. Against 75% the day before. Within noise. Nothing had regressed.

The tell was there the whole time: p50 latency fell from 3.0s to 0.9s. A quality drop and a speed-up do not happen together. When your agent gets faster AND worse at once, it is usually not answering.

The fix: below 90% coverage the report refuses to print a pass rate.

INCONCLUSIVE — only 29/60 cases ran (48.3% coverage)
Pass rate among cases that ran: 72.4%

It exits with code 3 — neither a green tick nor a red build.

This was the first of four times the same habit showed up:

- A not-graded case entered the pass-rate denominator → 35%
- A failed judge call counted as a pass → score inflated
- The app's own rate-limit notice was graded as a fabricated answer → fabrication inflated
- An ungraded case scored 0% on tool recall because set(expected) <= set() is False

Four instances of one habit: the system kept turning "we did not observe it" into "it failed."

Absence of evidence is not evidence of failure. Once you internalize that, eval systems stop lying to you.

A measurement tool fails toward false alarms as easily as toward false confidence. The 35% was a false alarm. A red build on noise is a false alarm. A KeyError in the diff step is a false alarm. Everything around the tests is also code, and it fails loudly and wrongly.

The chart is the full number trail. Two of those numbers were lies.

#LLMOps #AIEvaluation #SoftwareEngineering #QualityAssurance #AIAgents

---

## Fact-check against the repo

Every claim in your draft, checked. **All of them hold** — including the one I expected to be
wrong.

| Claim | Verdict | Where it lives |
|---|---|---|
| 35% → 21/29 = 72.4% | ✅ | `EVAL_FINDINGS.md` finding 6 |
| judge averaging 4.35/5 on that run | ✅ | number trail, "after grader fixes" row |
| every category down | ✅ | finding 6 |
| not graded: 31 | ✅ | finding 6 |
| 18 rows of `RateLimitError: 429` | ✅ | finding 6 |
| 0.08–0.9 s per case | ✅ | finding 6 |
| p50 3.0s → 0.9s | ✅ | finding 6 |
| 29/60 = 48.3% coverage | ✅ | arithmetic |
| **exits with code 3** | ✅ | `evals/run.py:1218` — `return 3`. The comment beside it reads: *"3 = ran, but not enough of it to score"* |
| INCONCLUSIVE message text | ✅ | `evals/run.py:797–799` |
| four "absence → failure" instances | ✅ | README rows 12–13, 24, 37, and finding 6 |
| 60.0% = my own fix broke the request cap | ✅ | finding 18 / README row 39 |

**The exit code was the one worth checking**, because that is the kind of detail people invent.
It is real, and the comment next to it says what your post says.

---

## The image

`img/number_trail_chart.png` — light theme, high contrast, no decoration.

- **Blue line** — the pass rate of seven real runs.
- **The grey number under each point** — how many of the 60 cases actually ran.
- **Red point** — the 35% night, with the cause *and* its tell (3.0s → 0.9s) in one callout.
- **Green 76.7%** — the run with the best coverage, sitting *below* the 88.7% that skipped cases.

It makes one argument without words: **a point on this chart means nothing without the number
underneath it.**

Caption to use with it:

> Seven runs of one 60-case eval suite. The number under each point is how many cases actually
> ran. The highest score skipped the seven most expensive ones.

---

## Before you hit post

1. **Use Version B** unless you specifically want the longer one.
2. **Repo link goes in the first comment**, not the post. LinkedIn suppresses reach on posts with
   external links.
3. **Don't add a title line.** The first line is the hook; LinkedIn truncates around 210
   characters and "My eval suite reported 35%" should be the whole visible opening.
4. **Attach the image, not a link to it.**
5. **Don't publish the long-form version the same day.** Post it in 2–3 days with a link back.
