# The post series

Six posts, one spine, one a week. **Not six posts — one idea told six times.**

---

## The spine

> **A control that reports health while the thing is sick.**

Every post is one instance of that sentence, from a different layer of the
system. That is what makes a series instead of a scatter of bug reports: a reader
who sees two of them recognises the theme before you name it.

The theme is not invented for posting. It is the actual through-line of
`OBSERVABILITY_FINDINGS.md` (nine for nine), the eval gate (finding 20), and the
CI findings. The repo already argues it. The posts are just where it gets said in
public.

---

## What NOT to post

Read this first. Most posting failures are one of these.

| ❌ | Why |
|---|---|
| **"I built a multi-agent RAG platform with RAG and LLMOps"** | Every second post on the platform. Says what it is, proves nothing, gets scrolled. |
| **The 61-row bug table** | Reads as a bug list, not as engineering judgement. It is an appendix, not a headline. |
| **A screenshot of green Grafana panels** | A green dashboard is the problem, not the evidence. Several posts below exist *because* green dashboards lie. |
| **All six in one week** | Spam. Also, six at once says "look at my project"; one a week says "I am working, and I noticed this." |
| **Any number you have not re-verified in the last hour** | You will be checked. Every number in this file was read out of the source doc this session, and the file:line is noted so you can re-check. |
| **"I found these bugs"** | Most are documented in `docs/` from the audit PRs (#25–#53). Frame as **re-verified, measured, and fixed** — which is true and is still not something most people have done. |
| **An AI-generated image used as a chart** | It will invent numbers. Data charts come from data, via matplotlib, with the numbers checked in. |

---

## Mechanics (same for every post)

- **The number goes in the first two lines.** LinkedIn truncates around 140
  characters on mobile. If the number is in paragraph three, it is not read.
- **One image.** Not two, not a carousel.
- **3–5 short paragraphs.** No walls of text.
- **Link in the first comment**, not in the post body. External links in the body
  are suppressed in reach.
- **End on the lesson, not on a question.** "What do you think?" is filler; a
  one-sentence rule a reader can apply tomorrow is the thing that gets saved and
  shared.
- **Say what you cannot prove.** Every post below has an honest boundary. Saying
  it is what separates a post that survives scrutiny from one that does not.

---

## The six, in order

Order is deliberate: start with the idea that needs no context, end with the one
that needs all of it.

---

### 1. "My dashboard said 2 milliseconds. The user waited 2 seconds."

**Hook.** My APM reported the request took 0.002 s. A stopwatch said 2.0 s. Both
were correct — the timer stopped before the answer started.

**The number.** 0.002 s recorded vs 2.008 s actual — **a 1,000× understatement.**

**The mechanism.** The timer wrapped the handler, not the stream. A
`StreamingResponse` returns an object almost immediately and sends its body
afterwards, so the measurement covers the cheap part and none of the expensive part.

**The lesson.** Measure the interval the user waits for, not the interval the
framework returns in.

**Honest boundary.** One endpoint, one measurement, reproducible from
`OBSERVABILITY_FINDINGS.md:404`.

**Asset.** Needs a two-panel card: `recorded 0.002 s` / `actual 2.008 s`.

**Column:** `OBSERVABILITY_FINDINGS.md:77, 89, 404`

**Why this one first.** It needs zero context. Any backend engineer, in any
language, has this bug or has been bitten by it. Widest reach of the six.

---

### 2. "Error rate: 0%. Target: met. The service was down."

**Hook.** The SLO panel was green. 0% errors against a 95% target. Every request
was failing.

**The mechanism.** A divide-by-zero guard:

```python
return 0.0 if total == 0 else errors / total
```

For an *error rate* — a badness metric — 0.0 is the best possible score. So no
traffic at all renders as perfect health. The guard was written for a goods
metric and pasted into a badness one.

**The lesson.** A default is a claim. `.get(key, 0)` on a health check turns
absence into wellbeing.

**Honest boundary.** It is one rule out of nine with the same shape; the post can
say so, which is stronger than pretending it is unique.

**Asset.** Needs a card showing the panel state vs the truth.

**Column:** `OBSERVABILITY_FINDINGS.md:398` (row 9), `408`

**Why second.** Same theme, second layer. An SRE reading post 1 will read post 2.

---

### 3. "My eval suite published 35%. The real number was 72.4%."

**Hook.** The nightly went red at 35%, down from 75%. Every category down. Nothing
had regressed.

**The number.** 21 passed of 29 that actually ran = **72.4%**, against 75% the day
before. Within noise.

**The mechanism.** Cases that could not be graded were counted as *not passed*.
That rule is correct — without it a broken judge inflates the score — but those
31 zeros still entered the denominator.

**The tell.** p50 latency fell **3.0 s → 0.9 s**. Almost nothing was being
answered. A quality drop and a speed-up do not happen together.

**The lesson.** Any rate is a rate over whatever survived. Print the denominator
next to it, or it is not a number.

**Honest boundary.** The failure direction was *down* — the app looked worse than
it was. Say that; it is the reason it survived review for weeks.

**Asset.** `docs/posts/img/number_trail_chart.png` — already exists, seven real
runs, same harness.

**Column:** `EVAL_FINDINGS.md:143–177, 450`

**Why third.** This is where an AI/ML audience joins. It is also the first post
with a real chart, so it visually escalates.

---

### 4. "The reranker improved a metric that does not affect the answer."

**Hook.** Hit@6 was 35/38 in all three setups. The reranker changed the rank of
the right passage, not whether it was retrieved at all.

**The table.**

| setup | right passage #1 | in top 6 | MRR |
|---|---|---|---|
| vector only | 27/38 | 35/38 | 0.79 |
| hybrid | 28/38 | 35/38 | 0.80 |
| + reranker | **31/38** | **35/38** | **0.86** |

**The lesson.** If the answer model only ever sees the top 6, a reranker that
improves rank inside the top 6 is improving a property that does not reach the
answer. Publishing the negative result is what makes the positive one credible.

**Honest boundary.** Small corpus, 44 questions, 38 answerable, one pipeline. Say
so in one line — it costs nothing and buys everything.

**Asset.** Needs the three-row table rendered as an image.

**Column:** `RAG_FINDINGS.md:24–60`

**Why fourth.** Smallest audience of the six, but the highest signal for a
retrieval or ML-infra hiring manager. This is the post that gets saved rather
than liked.

---

### 5. "We cut memory 3× and the quality did not move. That was the point."

**Hook.** 1,630 MB → 481 MB. Reranker batch 16 → 4. The 65 KB upload that used to
OOMKill the pod now works with zero restarts.

**The table.**

| | before | after |
|---|---|---|
| pod after doc questions | 1208–1288 MB | 481 / 627 MB |
| 65 KB upload | ~1630 MB → OOMKilled, HTTP 502 | works, 0 restarts |
| hit@1 / top-3 / top-6 / MRR | 0.816 / 0.895 / 0.921 / 0.862 | **same** |

**The lesson.** The `same` row is the whole slide. A performance fix that does not
prove it preserved quality is half a fix — and the half that gets reverted.

**Asset.** Needs a two-column before/after card with the unchanged row highlighted.

**Column:** `KUBERNETES_CHECKS.md:24, 127–133`

**Why fifth.** Platform and ML-infra audience. By now a reader who has seen two
others will trust the `same` row instead of skimming past it.

---

### 6. "Thirteen green nights in a row. The thing was getting worse."

**Hook.** My nightly passed every gate for thirteen consecutive nights. Over the
same thirteen nights, the share of answers flagged as containing made-up figures
went from **6.9% to 12.9%**.

**The table.**

| | first 4 runs | last 4 runs |
|---|---|---|
| pass rate | 65.0 | 64.2 |
| fabrication flagged | **6.88%** | **12.93%** |

**The mechanism.** The gate compared each night to the previous night and nothing
else. The largest single step in the whole series was **+6.6 points**. No night
was a regression. Three of them together were a doubling.

**The fix.** Absolute floors: `pass_rate ≥ 55`, `fabrication ≤ 12`,
`coverage ≥ 70`. They fire on 4 of the 13 real runs — on the three nights the
drift was already visible.

**The lesson.**

> A gate that only compares itself to yesterday can tell you that you slipped
> today. It cannot tell you that you have been slipping for two weeks.

**Honest boundary — and this one is load-bearing.** The judge is an AI, not
validated against people. So this might be the app drifting, or the judge
drifting. Either way the gate could not see it, and either way it needed a floor.
**Say that in the post.** A senior reader will think it; better that you said it.

**Asset.** Needs a new chart: the 13-run series, pass rate flat, fabrication
rising. Built from the numbers, not drawn by hand.

**Column:** `docs/DRIFT_FINDING.md`, `EVAL_FINDINGS.md` finding 20

**Why last.** It is the most senior post and it needs the other five to land: by
post 6 the reader already believes that your dashboards lie, so a green gate being
wrong is not a surprise, it is a conclusion.

---

## Cadence

**One a week, six weeks.** Same day, same time — Tuesday or Wednesday morning,
their timezone. Six posts over six weeks builds a pattern; six over six days is a
burst nobody remembers.

Between posts, do not post other project content. It breaks the series.

---

## The first comment (for every post)

> Code, tests and the finding: `docs/XXX.md` — every number above is re-checked
> from that file, and the test that pins it is `tests/test_XXX.py`.

Link goes here, never in the body.

---

## What to do when someone challenges a post

| Challenge | Answer |
|---|---|
| "How do you know?" | Name the instrument and the file:line. Have it open. |
| "Isn't that a toy project?" | "Single-node cluster, free tier, 60 cases — all stated in the README. The measurement discipline is the work." |
| "That's just one case." | "Yes — one endpoint, one rule, one suite. It is not a claim about everything." |
| "Couldn't the judge be wrong?" | "Yes. That's caveat #1 in the write-up. It doesn't change that the gate couldn't see it." |
| "Isn't this just monitoring?" | "Monitoring reports a number. This is about whether the number answers the question you think it does." |

Never argue a number you cannot open. If you cannot open it, say "I'd have to
check" — that answer costs nothing and the other one costs everything.
