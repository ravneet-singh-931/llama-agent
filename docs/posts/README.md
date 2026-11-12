# Posts — the eval series

Four posts on building an evaluation system for a multi-agent LLM assistant, written from
[`docs/EVAL_FINDINGS.md`](../EVAL_FINDINGS.md) and the README's bugs table.

Every number here was checked against a committed file. Each post file ends with a fact-check
table and a list of the corrections made to earlier drafts, including the ones that were wrong.

## The posts

| # | File | Hook | Length | Image |
|---|---|---|---|---|
| 1 | [`01-eval.md`](01-eval.md) | My eval suite reported 35%. The real number was 72.4%. | 2,973 (or 2,474) | [`img/number_trail_chart.png`](img/number_trail_chart.png) |
| 2 | [`02-five-bugs.md`](02-five-bugs.md) | Five bugs in one night. Not one was in the agent. | 2,996 | [`img/02-five-bugs.png`](img/02-five-bugs.png) |
| 3 | [`03-detector.md`](03-detector.md) | My regression detector ran for weeks and could not detect a regression. | 2,997 | none |
| 4 | [`04-broke-my-own-fix.md`](04-broke-my-own-fix.md) | 110 tests passed. The bug shipped anyway. | 2,993 | none |

All four are under LinkedIn's 3,000-character limit and are **plain text** — no markdown, because
LinkedIn renders `**` and backticks literally.

## Before publishing

1. **Post in order 1 → 2 → 3 → 4.** Posts 2, 3 and 4 open by referring to the previous one.
2. **Two to three days apart**, not one. Four posts on one project inside a week reads as a dump.
3. **Repo link goes in the first comment**, not the post — LinkedIn suppresses reach on posts with
   external links.
4. **Post 4 needs a new opening line.** It currently opens identically to 1, 2 and 3; lead with the
   thesis instead (`I added a per-case token budget to its eval harness. It removed the cap it was
   supposed to protect.`) — it is shorter, so the character count stays safe.

## Two claims to check before you publish

Both are flagged in the post files, and neither is wrong in the repo — they are wrong *here*:

- **Post 2: "five bugs in one night".** The findings doc endorses this hook but nothing in the repo
  dates all five to one night. Soften it if you cannot.
- **Post 3: which safety case hung.** The draft said `safety-english`; both `safety-english` and
  `safety-english-code` exist in `evals/dataset.jsonl`.

## The claim that was cut

Post 4's original draft ended with a story about `safety-exfil-file`: that a broken token budget
surfaced the first genuine security finding. **It is backwards.** In the repo the case went
**1.75, FAILED** (it wrote `requests.post()`) to **5.0, PASS** — and it passed *despite* the bug,
because it does not depend on how many tokens a request carried. The full correction is at the
bottom of `04-broke-my-own-fix.md`.

## The figures

`img/number_trail_chart.png` — seven real runs of the 60-case suite. The blue line is the pass rate
over all 60 cases; the green line counts only the cases that actually ran; the shaded gap between
them is the cases the free tier ate. On the 35% night that gap was 37.4 points.

`img/02-five-bugs.png` — the five grader bugs as a two-column table: what each check measured
against what it should have measured.
