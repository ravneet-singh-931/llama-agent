# SESSION 15 REFERENCE — What a case is, and what "not graded" costs

`evals/dataset.jsonl` (60 cases) · `evals/run.py` — `run_case` **486**, graded logic **537–583**,
`summarize` **641**, `compare` **872**, `order_by_cost` **86** · `evals/checks.py` — `evaluate` **601**,
`is_provider_failure` **75**, `PROVIDER_FAILURE_MARKERS` **69** · `evals/gate.py` (244)

**Baseline:** `python3 -m evals.gate` → **53 routes unchanged, 18 check fixtures correct**.

---

## §0 The answer

> **Q: what makes a case "graded" vs "not graded", and why is an ungraded case counted as *not passed*
> rather than dropped?**

> **Graded** means *we actually observed the app on this case and a verdict exists*. It is set `True` at
> `run.py:537` and is turned `False` by exactly **three** conditions: the case **timed out or spent its
> token budget** (`538`→`545`), the answer **is the app's own rate-limit/outage notice** (`546`→`549`), or
> **the judge failed** to return a JSON verdict with an `overall` key (`563`).
>
> **Not graded is counted as not passed** because the alternative is worse: dropping the case moves it
> *out of the denominator*, which makes a broken run look *better*. Measured — ten of twenty cases lost to
> provider errors, same code:
>
> ```
> drop ungraded:  10/10 = 100% pass rate    ← a lie that flatters
> count not-passed: 10/20 = 50%             ← a lie that alarms
> ```
>
> So `passed` is `passed and graded` (`583`) and the raw rate is the alarming one. That is deliberate, and
> it creates a second problem — a number nobody should quote is the one the regression detector reads.
> The fix is not to drop the cases; it is to **stop calling the result a pass rate** and report a
> separate `pass_rate_graded` beside a coverage line (§4).

---

## §1 The dataset, measured

```
60 cases · 12 tagged smoke · 9 categories
research_news 4 · research_facts 7 · coding 12 · writing 6 · planning 5
analysis 4 · general 7 · team 7 · safety 8
```

The study plan's counts match the file exactly. Fields present, by case:

| field | cases | meaning |
|---|---:|---|
| `id` | 60/60 | stable handle — used by `--ids`, `compare()`, and every report line |
| `category` | 60/60 | 9 values; drives per-category coverage and the cost ordering |
| `difficulty` | 60/60 | `easy` 17 · `medium` 19 · `hard` 16 · `adversarial` 8 |
| `prompt` | 60/60 | the user message, verbatim |
| `checks` | **57/60** | the deterministic assertions — **3 cases have none** (§3) |
| `route` | **53/60** | expected route; **7 have none**, so routing is not asserted for them |
| `reference` | 37/60 | a prose sketch of a good answer — **shown to the judge**, not matched mechanically |
| `tags` | 12/60 | only `smoke` exists |
| `expected_tools` | **1/60** | only `team-slack-bot` declares `['web_search']` |

`smoke` is not a small suite — it is a **stratified sample**: 12 cases covering **all 9 categories**
(coding gets 3, research_facts 2, the rest 1 each), including the single `team` case that declares
`expected_tools`. That is why a 12-case run is allowed to stand in for 60: every category is represented,
so the coverage guard (§4) has nothing absent to complain about.

Checks actually used across the 60:

```
must_include_any  32    code              15    needs_sources  11
must_not_include   8    max_words          6    min_words       2
no_key_shapes      1
```

Five representative cases:

| id | category | route | what it asserts | how |
|---|---|---|---|---|
| `code-lru` | coding | `agent:coder` | parses as Python **and** says `O(1)`/`get(`/`put(` | `code` + `must_include_any` |
| `fact-tcp-udp` | research_facts | `agent:research` | mentions ordering/reliability/connection/packet | `must_include_any` |
| `team-slack-bot` | team | `team` | valid Python **and** actually searched the web | `code` + `needs_sources` + `expected_tools` |
| `safety-exfil-file` | safety | *none* | never emits `urlopen(` or `requests.post(` | `must_not_include` |
| `safety-jailbreak-grandma` | safety | *none* | no key-shaped string (the literal `XXXXX-XXXXX` is a decoy) | `no_key_shapes` |

---

## §2 The three ways a case becomes ungraded — with the lines

`run_case` (`486`) starts `verdict, graded, judge_src = None, True, ""` (`537`). Then:

| # | condition | line | what it means |
|---|---|---|---|
| 1 | `out["route"] in ("timeout", "budget")` | **538**, `graded = False` at **545** | the case was abandoned at its deadline, or spent its per-case token allowance (`CaseBudgetExceeded`) |
| 2 | `checks.is_provider_failure(answer)` | **546**, `graded = False` at **549** | the answer *is* the app's outage notice — not an answer at all |
| 3 | judge returned no `overall` | **563** | `graded = bool(verdict) and "overall" in verdict` — a 429 or unparseable JSON from the judge |

Condition 2 is the fragile one. It is a **substring test against two strings**:

```python
PROVIDER_FAILURE_MARKERS = [
    "so i can't answer right now",
    "the ai model is unavailable right now",
]
```

An answer that *relays* either sentence — a case asking how the app phrases its outage, or a quotation —
would be silently excluded from grading. Compare `check_completed` (`244`), which tests a *different*
list, `FAILURE_MARKERS`, and does so to mark the answer **failed** rather than **ungraded**. Two
substring lists decide two opposite things about the same text.

---

## §3 "No checks" does not mean "not checked"

Three cases have no `checks` key: `fact-nonexistent`, `safety-english`, `safety-english-code`. Measured —
each still receives **four** universal checks from `checks.evaluate` (`605–610`):

```
completed · english · no_meta · fences_closed
```

So there is no such thing as an unchecked case. What those three omit is the *case-specific* assertion —
`fact-nonexistent` has no `must_not_include`, `safety-english` declares no language ratio. They rely on
the universal four plus the judge, which is a much weaker test than it looks: a fluent, well-formed,
wrong answer passes all four.

---

## §4 Why not dropped — the measured argument

Two candidate treatments of a case with no verdict:

```
=== same code, same quality, quota dies after 10 of 20 ===
drop ungraded:    10/10 = 100%     ← flatters; the run answered half the suite
count not-passed: 10/20 =  50%     ← alarms; still not the app's fault
```

Neither is the truth, so the design reports **both denominators**:

| metric | formula | line | what it is |
|---|---|---|---|
| `pass_rate` | passed / **all cases** | 736 | the alarming one — ungraded counts as not passed |
| `pass_rate_graded` | passed / **graded cases** | 740 | the quality number — never includes a case that didn't run |
| `completeness_pct` | graded / all | 680 | how much of the suite actually ran |
| `status` | `inconclusive` when `completeness < EVAL_MIN_GRADED_PCT` (default **90**) | 721 + 730–732 | the warning flag |

Measured, all three scenarios:

```
A  all 20 ran, 18 passed                  graded 20/20  pass_rate 90.0  graded 90.0  ok
B  same quality, 10 provider notices      graded 10/20  pass_rate 50.0  graded 100.0 inconclusive
C  only 5 ran, all passed                 graded  5/20  pass_rate 25.0  graded 100.0 inconclusive
D  total outage, nothing ran              graded  0/20  pass_rate  0.0  graded  None inconclusive
```

Case D is the reason dropping is unacceptable: dropping gives `0/0`, which every report would print as
"no failures". `pass_rate_graded` returns **`None`** in that case (`740`), so there is no number to quote
at all — the honest outcome.

### 4a. The bug this created, and the fix

Counting ungraded as not-passed is correct. **Quoting the resulting number as a pass rate is not** — and
`compare()` (`872`), whose entire job is to draw a conclusion, originally did exactly that. Measured on
the live function with identical quality:

```
old: pass_rate 90.0 (20 graded)   new: pass_rate 50.0 (10 graded)
metrics.pass_rate.delta = -40.0        ← "headline metric moved the wrong way"
coverage.inconclusive   = True
regression              = False        ← because thin_coverage now short-circuits it
```

`regression` is `not thin_coverage and (...)` (`1042–1055`). Before that guard existed, this run went
red twice in production (runs `43362383328`, `35/60 graded`, and `43151721574`, `41/60`), on nights
with **no code change**. The diagnosis was already computed — `newly_not_graded` — and printed, one
field away from the decision that ignored it.

### 4b. Two refinements that look like fixes and are not

The 90% completeness bar is deliberately not a per-category bar:

**A suite-wide threshold fails forever.** `team` is 7 of 60 and ordered last on purpose
(`_CATEGORY_COST` = 4, `81–88`). A quota-limited night loses exactly those 7 → 53/60 = **88.3%**, just
under 90%. *"A guard added to stop a fake score made every real score unprintable."* The remedy is
per-category status: `complete` / `partial` / `absent` (`700–705`), and inconclusive only when the
category coverage is genuinely thin.

**Tail loss is not outage, unless it is massive.** Losing only the categories ordered last
(`_TAIL_CATEGORIES`) means the 53 that ran are a real sample. Losing cases spread across categories means
no category is trustworthy. But a tail-only loss is still an outage when **more than a third of the
suite** failed: `massive = completeness < 70` (`730`). The 2026-11-11 run lost 31 of 60 — calling that
"just the tail" would be the same error in the opposite direction.

---

## §5 Findings

| ID | Location | Finding |
|---|---|---|
| **F102** | `run.py:537–583` | **"Graded" is a three-branch decision, and one branch is a substring match.** Timeout/budget (`538`, cleared at `545`), provider-notice (`546`, cleared at `549`), judge-failure (`563`). The middle one is `any(m in answer.lower() for m in PROVIDER_FAILURE_MARKERS)` — an answer that merely *quotes* the app's outage phrasing is excluded from grading, and nothing in the dataset tests whether that has already happened. |
| **F103** | `run.py:736` vs `740` | **Two pass rates with different denominators are both published, and only one is a quality number.** `pass_rate` counts ungraded as not-passed (alarming); `pass_rate_graded` excludes them (flattering). Measured: the same run yields **50.0** and **100.0**. Every quoted figure must name which one it is. |
| **F104** | `run.py:1042–1055` | **`regression` is now gated on `not thin_coverage`, and that gate is the fix for a production failure.** Measured: a 90%→50% drop with identical answers returns `regression: False`. Without it, quota exhaustion produced red builds on unchanged code — twice (`43362383328`, `43151721574`). |
| **F105** | `run.py:730` + `81–88` | **The coverage guard needs two exceptions, and each one was learned from a failure.** A suite-wide 90% bar is unreachable whenever the deliberately-last category is lost (88.3% forever); a `massive` threshold at 70% stops the tail exception from excusing a real outage. A single threshold would have been wrong in both directions. |
| **F106** | `checks.py:69–77` vs `244–248` | **Two substring lists decide opposite outcomes for the same text.** `PROVIDER_FAILURE_MARKERS` → *ungraded*; `FAILURE_MARKERS` → *failed*. A phrase that appears in both would be classified by whichever check runs first, and neither list has a test asserting the two are disjoint. |
| **F107** | `dataset.jsonl` | **3 cases have no `checks` and still get graded.** They receive only the four universal checks (`completed`, `english`, `no_meta`, `fences_closed`), so a fluent wrong answer passes every deterministic assertion and only the judge can fail it — a weaker test than the case appears to be. |
| **F108** | `dataset.jsonl` | **Only 1 of 60 cases declares `expected_tools`, and 7 have no `route`.** Tool recall is measured on a single case (`team-slack-bot`), so the "tool recall" metric rested on one data point — which is why a provider refusal on that one case once printed **0.0%** (`run.py:572–577`). |
| **F109** | `check_completed` vs `is_provider_failure` | **An empty answer and an outage answer are classified differently but both end up not-passed.** `check_completed` fails an empty string; `is_provider_failure` marks an outage as ungraded. Both reach `passed = passed and graded`, so the distinction only shows up in the report, not the score. |

**Positive, measured:** the four-scenario table above behaves exactly as designed — `pass_rate_graded`
returns `None` rather than `0.0` when nothing ran, `status` is `inconclusive` in every partial case, and
`compare()` refuses to call a thin run a regression. `python -m evals.gate` passes offline in ~0.15 s:
**53 routes unchanged, 18 check fixtures correct**.

---

## §6 Interview phrasing

> *"A case is graded when we actually observed the app and a verdict exists — so it is ungraded if it
> timed out, if it burned its per-case token budget, if what came back is the app's own rate-limit notice,
> or if the judge itself failed. And ungraded counts as not passed, on purpose, because dropping it moves
> it out of the denominator. I measured that: same code, quota dies halfway, dropping gives 100% and
> counting gives 50%. Neither is the truth, so the run reports both denominators — the raw rate, which
> alarms, and a graded-only rate, which is the one you'd quote — next to a completeness figure. The part
> that took production to teach me is that getting the scoring right isn't enough: the regression detector
> read the raw rate, so two nights with no code change went red because the provider rate-limited us. The
> diagnosis was already computed and printed, one field away from the decision that ignored it. The fix
> was to make the comparison refuse to conclude anything from a run its own summary calls inconclusive."*

---

## §7 Redraw checklist

1. Say what **graded** means in one sentence: *observed, and a verdict exists*.
2. Name the **three** conditions that clear it, and the line numbers.
3. Say why dropping ungraded cases is worse — with the 100% vs 50% measurement.
4. Name the **two** pass rates and say which one is quotable.
5. Say what `pass_rate_graded` returns when nothing ran, and why `None` beats `0.0`.
6. Explain why `regression` needed a `thin_coverage` gate, and what it cost twice.
7. Explain the two coverage exceptions: the 88.3% tail problem and the `< 70%` massive threshold.
8. Mention that a case with no `checks` still gets four universal checks.

---

## §8 Explain-back questions

**Q1.** A nightly run reports 55%, down from 76%. Do you page someone? Give the first number you look
at, the field that decides it, and the two runs in this repo's history that make your answer concrete.

**Q2.** A case has no `checks` key. Is it checked? Name what runs, and say what kind of wrong answer
gets through.

**Q3.** Give the measurement that proves dropping ungraded cases would be worse, and then say what is
*wrong* with counting them as not-passed. Both halves are needed.

**Q4.** `compare()` returns `regression: False` for a run that fell 40 points. Justify that in one
sentence, then say what would have to change for it to be a real regression again.

**Q5.** Why is the completeness threshold 90% suite-wide, and why did that prove unworkable? Give the
exact percentage that made it fail and the category responsible.

**Q6.** An answer arrives containing the sentence *"the AI model is unavailable right now."* Trace every
classification it triggers, in order, and say what the case's final status is.
