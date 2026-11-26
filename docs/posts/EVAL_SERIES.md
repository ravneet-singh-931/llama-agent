# The eval series

**One area. Seven posts. One argument.**

Not seven tips about evaluation. One claim, defended from seven angles:

> **Most of a bad eval score is the eval.**

That is not a slogan. In this repo it is a measured fact: of the failures in the
first full run, **the majority were the grader, not the app**. The agent was
scoring badly on answers that were right.

---

## Why the eval area, and not the whole project

`EVAL_FINDINGS.md` is the only part of the repo where the **instrument** and the
**thing measured** were wrong at the same time and can be separated. That makes it
the best subject for a series: each post can take one mechanism, show the number
before and after, and hand the reader a rule.

The other areas are one post each:
`OBSERVABILITY_FINDINGS` → the 9-for-9 dashboard post,
`RAG_FINDINGS` → the negative-results post,
`KUBERNETES_CHECKS` → the memory-with-unchanged-quality post,
`MY_ERRORS` → a post of its own, later.

---

## The seven, in order

Each has: the hook, the number, the mechanism, the rule, and the honest boundary.
Order is deliberate — post 1 teaches the reader to distrust a score, and every
later post spends that trust.

---

### 1. "The suite said 75%. My own judge said 4.43 out of 5."

**The number.** 45 of 60 passed = 75%. Average judge score **4.43/5**. Both from
the same run, in the same file.

**The mechanism.** The pass rate counted *check* failures. Many of those checks
were failing correct answers (that is posts 3 and 4). A human-quality grader
reading the same answers said they were nearly all good.

**The rule.** When two instruments on the same run disagree, that disagreement is
the finding. Do not pick the one you prefer.

**Honest boundary.** One run, one suite, 60 cases. The judge is an AI and it is
unvalidated — which is exactly the point of post 7.

**Source.** `EVAL_FINDINGS.md:16` (finding 1)

**Why first.** It is the thesis in miniature: the score was wrong before any
argument about the app could start.

---

### 2. "A case that cannot be graded is not a failure. We counted it as one."

**The number.** The nightly published **35%**. Real figure: **21 of 29 that
actually ran = 72.4%**, against 75% the day before. Nothing had regressed.

**The mechanism.** 31 cases never ran — the free tier's quota ran out at case 29.
The rule "ungraded counts as not passed" is *correct* (without it a broken judge
inflates the score), but those 31 zeros still entered the denominator.

**The tell, and the best part of the post.** p50 latency fell **3.0 s → 0.9 s**.
Almost nothing was answered. **A quality drop and a speed-up do not happen
together.** The tell was in the same report the whole time.

**The rule.** Any rate is a rate over whatever survived. Print the denominator
beside it or it is not a number.

**Honest boundary.** The error pointed *down* — the app looked worse than it was.
That is why it survived review for weeks. Say it.

**Source.** `EVAL_FINDINGS.md:143–177, 450`

**Asset.** `docs/posts/img/number_trail_chart.png` — already exists.

---

### 3. "Correct code failed because it wasn't inside a box."

**The number.** `code-debug` scored **5.0/5** from the judge. It failed the rule
check with *"no python code block."*

**The mechanism.** The check asked *"is there a fenced block with a language tag?"*
It never asked *"is there code that runs?"* The answer was correct Python, in
prose, and the check graded the markup.

**The rule.** A check on a *format* is not a check on the *content*, and the
distance between them is where every false failure lives.

**Honest boundary.** This one is easy to over-generalise. The check was added to
enforce a house style; the bug is that a style rule was allowed to fail a case.

**Source.** `EVAL_FINDINGS.md:70` (finding 3)

---

### 4. "A correct answer failed on a hyphen you cannot see."

**The number.** `team-slack-bot` — judge **4.75/5**. Failed on
`invalid character '‑' (U+2011)`.

**The mechanism.** U+2011 is a *non-breaking* hyphen. It renders identically to
`-`. The answer was correct, the arithmetic was correct, and Python refused it.

**The rule.** If a check can fail on a character a human cannot see, the check is
grading the keyboard, not the answer.

**Honest boundary.** Normalising is a real fix but not a free one — it can also
make invalid input look valid. Say which way you chose.

**Source.** `EVAL_FINDINGS.md:88` (finding 4)

**Why this one matters.** It is the single most shareable fact in the whole doc.
Non-breaking hyphens appear in every copy-paste, every PDF, every model output.

---

### 5. "The same answer failed three different ways in three runs."

**The number.** `code-regex-pin`, "give me a regex that validates Indian PIN
codes." Judge **5.0/5**. Check: *"no regex code block."*

**The mechanism.** The answer *contained* a regex, inside a python block. The
check wanted a regex-tagged block. Nothing was predictable: the same correct
answer failed differently depending on which code fence the model chose.

**The rule.** A nondeterministic grader is worse than no grader — it makes
regression detection impossible, because you cannot tell a change in the app from
a change in the format.

**Honest boundary.** Some of this disappears with a stricter prompt. The point is
that the *check* should not depend on the model's formatting mood.

**Source.** `EVAL_FINDINGS.md:108` (finding 5)

---

### 6. "The build failed because a check improved."

**The number.** A run produced a real number (83.3%) and the diff step died.

**The mechanism.** `compare()` computed `old - new` on a field that improved to
zero, and the subtraction path assumed a truthy value. A better result crashed
the comparison.

**The rule.** Your comparison tool must be tested on improvement and on absence,
not only on regression. Everyone tests the case they are afraid of.

**Honest boundary.** This is a plain null-handling bug. Post it as a lesson about
testing the tool itself, not as a clever insight.

**Source.** `EVAL_FINDINGS.md:202` (finding 8)

---

### 7. "The judge is an AI that has never been checked against a human, and I quote it anyway."

**This is the post that makes the other six credible. Do not skip it.**

**The number.** The judge is an LLM scoring 1–5. It has **never been validated
against human labels**. It is also the **same model family** that writes the
answers being judged.

**The mechanism.** There is no label set. Validation was never done, so
`judge_overall` is a number with unknown error. It is still useful — it is
directionally stable, and the rule checks are deterministic — but it cannot be
quoted as an absolute quality measure.

**The rule.** An unvalidated grader is shippable **only if its errors point
down** — if it can only subtract from a score, not add. Then a bad score is a
reason to look, never a reason to celebrate.

**Honest boundary.** This whole post *is* the boundary. That is why it goes last.
By now the reader has seen six mechanisms; this one is about the instrument doing
the measuring, and it retroactively explains them.

**Source.** `SECURITY_FINDINGS.md` judge caveats, `README.md` "Known limits",
`EVAL_FINDINGS.md` finding 12 (a failed judge counted as a pass).

---

## Posts that are good but do not fit this series

Save them. They belong to other series, not this one.

| Finding | Why it does not fit | Where it belongs |
|---|---|---|
| **7** — expensive cases starved the cheap ones | It is a scheduling story, not a grading story | The LLMOps/cost series |
| **9** — the regression detector that could not detect | It is the *gate*, not the *grader* | Pairs with `DRIFT_FINDING.md`, post 6 of the original list |
| **11** — the timeout applied twice | It is a systems bug, no eval content | The agents/timeouts series |
| **13** — the eval graded the app's own error message | Real, but overlaps 2 and 6 | Fold into post 2 as one line |
| **14/15** — 108% of the meter | Quota and pacing, not grading | The LLMOps/cost series |
| **20** — 13 green nights | Trend, not grader | The `DRIFT_FINDING.md` post |

**This table is the messy-part filter.** A series stops being a series the moment
it borrows a post from a different argument.

---

## Cadence and format

**One a week, seven weeks.** Same day, same time.

Each post:

1. Number in the first two lines.
2. One image — 5 of the 7 need a card that does not exist yet.
3. 3–5 short paragraphs.
4. The rule, in one sentence, before the last line.
5. The honest boundary.
6. Link to `docs/EVAL_FINDINGS.md#finding-N` in the **first comment**.

---

## Assets needed

| Post | Asset | Exists? |
|---|---|---|
| 1 | two-number card: `75%` vs `4.43/5` | no |
| 2 | `number_trail_chart.png` | **yes** |
| 3 | `02-five-bugs.png` (covers 3, 4, 5) | **yes** |
| 4 | the invisible hyphen, rendered large | no |
| 5 | three runs, three failure messages | no |
| 6 | `83.3%` and a red X | no |
| 7 | the judge loop, drawn | no |

Build them from data, never by an image model. Every one is numbers and text.

---

## The first post's opening, so the tone is set

> My eval suite told me my AI answers scored 75%.
>
> The judge — a second AI that reads every answer — told me the same answers
> averaged 4.43 out of 5.
>
> Both numbers came from the same run. One of them was wrong, and it took me
> three weeks to find out which.

That is the whole series in three lines: **a disagreement between two instruments
is the finding, not a detail.**
