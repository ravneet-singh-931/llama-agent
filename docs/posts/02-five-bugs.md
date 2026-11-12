# Post 2 — Five Bugs In One Night, None In The Agent

**Status:** ready to publish. **2,996 characters** (LinkedIn limit 3,000).
**Image:** `img/02-five-bugs.png` — five rows, "what the check measured" vs "what it should have measured".
**Plain text below** — paste as-is, no markdown (LinkedIn renders `**` and backticks literally).

---

I'm building a multi-agent AI assistant on Groq's free tier. Part of it is an eval suite: 60 cases, nightly in CI, graded by rule checks plus a second model scoring answers 1-5.

Post 1: the night it reported 35% when the real number was 72.4%. The night after: 75% passed, judge 4.43/5.

A 4.4/5 answer should not fail a quarter of the time. When they disagree that often, one is broken. So I audited the disagreements.

Five bugs in one night. Not one was in the agent.

1. Grounding a case that never needed a source

A migration plan failed with "1 of 42 figures found in sources".

"Does every number appear in a source?" ran on every case that produced a web search. But a plan's budgets come from the request, not a website. Now it runs only on cases declaring needs_sources.

2. Correct code failed for not being in a box

code-debug scored 5.0/5. Rule check: "no python code block".

The fix was inline — use len(items) == 0 — not in a fence. It graded format, not correctness. An inline snippet now counts if it parses as Python; prose in backticks still fails.

3. A correct answer failed on an invisible hyphen

team-slack-bot: 4.75/5. Error: invalid character '‑' (U+2011).

A non-breaking hyphen where a minus sign belongs — visually identical. Python rejects it. The dash family is now folded to ASCII, with a test that subtraction still works.

4. A regex in a Python block. Three runs, three different failures.

code-regex-pin: "a regex that validates Indian PIN codes." Judge 5.0/5, check: "no regex code block."

Attempt 1: I accepted inline spans. Wrong.

Run 2 failed again: an agent answers in a Python block, not a regex-tagged one. That tag barely exists.

Run 3 failed differently, also mine: I tested for pattern syntax on the whole answer. A code fence contains braces, satisfying it, so print('hello') passed.

Because I kept adding special cases instead of listing the shapes an answer takes.

5. One weak word cost a case

no_meta stops an agent narrating its pipeline ("I'll pass this to the other agents"). It fired once in 60 cases, on team-cafe, matching "source material 2".

An ordinary sentence. Weak phrases are now advisory; real narration still fails.

The one mistake

All five graded the shape of the answer instead of the answer.

Grounding asked if a source existed, not if figures were right. The code check asked for a fence, not for parsing. The hyphen check asked for ASCII, not correct math. The regex check asked for a tag, not a working pattern. no_meta asked for a match, not intent.

The rule is not trust the check or trust the judge. It is: when they disagree, audit. The disagreement is the bug report.

Later the same test fired in reverse: news-markets scored 5.0 and failed grounding — five decimal figures in no source. The judge was wrong that time. A model reading for plausibility cannot see a fabricated 81,772.65.

Eighteen findings later, sixteen were the yardstick.

#LLMOps #AIEvaluation #SoftwareEngineering #Testing #AIAgents

---

## Fact-check against the repo

| Claim | Verdict | Where |
|---|---|---|
| 75% / judge 4.43 | correct | `EVAL_FINDINGS.md` finding 1, number trail |
| 1 of 42 figures | correct | finding 2 |
| code-debug 5.0/5, inline | correct | finding 3 |
| team-slack-bot 4.75/5, U+2011 | correct | finding 4 |
| code-regex-pin, 3 runs | correct | finding 5 |
| no_meta fired once, team-cafe | correct | finding 10 |
| news-markets 5.0, 81,772.65 | correct | number-trail section |

## Corrections I made to the draft

- **Dash codepoints were mislabelled.** The draft said "U+2012 (en dash), U+2013". U+2012 is
  **FIGURE DASH**; U+2013 is EN DASH; U+2014 is EM DASH.
- **`news-markets` was in the wrong run.** The draft said "in a later run". It was the same
  30-case run, the same audit — which is a stronger point.
- **"Five bugs in one night" is not dated anywhere in the repo.** The findings doc endorses the
  hook; nothing records the date. Soften it if you cannot date it.
- **Draft was 5,900 characters**, nearly double the limit.
