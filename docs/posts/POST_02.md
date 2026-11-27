# POST 2 — "The best score in the table was the only one I couldn't trust."

**Image:** `docs/posts/img/post02-sample-size-dark.png` (to build)
**Publish:** after post 1. Same voice, same spine, opposite direction.
**Source:** the 2026-11-27 run the author pasted; arithmetic re-derived in-session.
**Series position:** follows the 35% post. Post 1 = "my instrument lied to me."
Post 2 = "now it tells me when it's lying."

---

## THE POST

My test suite scored 100% on team work last night.

That number is worthless.

Here is the category breakdown from the same run:

• general — 100%
• planning — 100%
• writing — 100%
• coding — 100% (of 11 that ran)
• analysis — 100% (of 3 that ran)
• team — 100% (of 1 that ran)
• safety — 87.5%
• research_facts — 71.4%
• research_news — 25%

The top of that list is the one you want to believe. It is also the only one built on a single case.

Team has 7 questions. Six of them hit the API rate limit and never ran. One ran, it passed, and the table printed 100%.

Meanwhile research_news scored 25% — four questions, all four ran, one passed. That is a real failure rate on a complete sample.

The worst number in the table is the one I can actually stand behind.

My eval knows this. That is why the report opens with this instead of a score:

"INCONCLUSIVE — only 52/60 cases ran (86.7% coverage)"
"Pass rate among cases that ran: 88.5%"
"Partly run: analysis, coding, team — rates there are over the cases that ran"

Three lines, and the third one is the one that matters. It does not just tell you coverage was 86.7%. It tells you *which categories* are carrying holes, so you know exactly which rows of that table you are allowed to quote.

The pass rate itself is 46/52. Not 46/60. The report shows both — 88.5% among cases that ran, and it refuses to lead with the raw 76.7% because that number counts eight cases that never happened as failures.

So after a night where my suite scored 100% on team, the honest summary is:

The agent did well on everything that ran. And I do not know how it does on team work, because six of seven team questions never ran and the one that did is not a sample.

The number at the top of the table and the number at the bottom of the table are both answering a question about my provider, not my agent.

How do you report a category when half of it did not run? I default to showing the score *and* the sample size, and I have started to think most dashboards should.

---

## FIRST COMMENT (post immediately after)

The report that refuses to lead with its own best number:
https://github.com/ravneet-singh-931/llama-agent

• evals/run.py → summarize() — prints coverage, the graded rate, and the list of
  partly-run categories before any headline
• evals/run.py → render_markdown() — "Partly run: ... rates there are over the
  cases that ran"

Arithmetic, if you want to check it:
46/52 = 88.5% graded · 46/60 = 76.7% raw · 52/60 = 86.7% coverage
team: 1 of 7 ran. research_news: 4 of 4 ran.

---

## Before you hit publish — 4 checks

1. **Do not say "fabrication dropped from 14 to 7".** The comparison tool printed
   `NOT A QUALITY COMPARISON: coverage` on that delta. Different denominators.
2. **Do not quote 88.5% as the agent's score.** Say "88.5% among the 52 cases that
   ran" — the post's whole point is that the denominator travels with the number.
3. **Do not say team "passed".** One case ran. Say "one case ran and it passed".
4. **No markdown asterisks** — LinkedIn prints them literally.

---

## If someone challenges it

| They say | You answer |
|---|---|
| "100% on team sounds great, why are you complaining?" | Because it's 1 of 7. Six hit the rate limit. A category score over one case is not a category score. |
| "So your agent is bad at research?" | 25% on `research_news` is 1 of 4 — small, but the sample is complete, which is more than team can say. It's the one weak spot I can defend. |
| "Why not just re-run until you get full coverage?" | Free tier is 200k tokens/day, and this run used 195,500. One run per day, and the tail dies first. That's a budget fact, not a bug. |
| "Isn't 86.7% coverage good enough?" | It's above the 90% line? No — it's below. So the report says INCONCLUSIVE and does not print a headline rate. |
| "What's your actual score then?" | 88.5% over 52 cases, and no claim at all about team. That's the honest answer. |

---

## Notes for the author (not for posting)

- `team` ran **1 of 7**. This is the tail loss the series is about — the same
  category, the same cause, three weeks later.
- `research_news` at 25% is the **only full-sample weakness** in the run and is a
  legitimate post-3 candidate ("my agent is weakest at current-events research").
- The 14.9 → 7.7 fabrication delta is **not usable**: the tool itself refused it.
- `46/52 = 88.5%` — check: 88.46, rounds to 88.5. Correct.
