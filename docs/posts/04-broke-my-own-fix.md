# Post 4 — I Broke My Own Fix. 110 Tests Passed.

**Status:** ready to publish. **2,993 characters**.
**Image:** none (see note).
**Plain text** — paste as-is.

---

I'm building a multi-agent AI assistant on Groq's free tier. I added a per-case token budget to its eval harness to stop a full run blowing the daily quota.

110 tests passed. The bug shipped anyway. I wrote it.

The setup

A full 60-case run needs ~314,000 input tokens. The free tier allows 200,000 a day, so every run lost its tail. And the cost wasn't spread evenly: seven @team cases ran 18,000 to 49,000 tokens each, against ~2,500 for an ordinary case.

The per-call guard capped each request at 6,000 and trimmed only what exceeded it. A team case makes ~15 calls of ~3,500 — all under. So nothing was trimmed, and fifteen calls of 3,500 added up to ~52,500 for one case.

I searched for anything that owned the total. Nothing existed. The per-call guard was real, tested, and pointed at the wrong thing.

The fix

A per-case budget in prepare_call(), the funnel every model call passes through. Each call is charged to it; the last that would overshoot is compacted to what remains.

60 cases = 7 team + 53 cheap. (200,000 - 132,500) / 7 = 9,642, so 9,000 per case leaves headroom. Team spend drops from 367,500 to 63,000.

The bug I introduced

A "last chance" branch: when the next call would overshoot, fit it to whatever is left instead of refusing.

remaining = limit - spent                 # 8,000 of a 9,000 budget
return model, fitted, est - trimmed       # returns EARLY

Downstream, the normal path enforced the per-request ceiling at 6,000 — but the early return sits above that line, so it never runs. The request was fitted to 8,000, larger than my own 6,000 ceiling, and sent.

The case budget raised the per-request cap on exactly the calls closest to the limit.

How it showed up

Eleven cases refused with "Request too large". None with route=budget — the branch never refuses; it compacts and returns. The guard looked like it was working while bypassing the thing it protected.

The misdiagnoses, mine

"The budget helped: 41 to 48 graded." It hurt — it broke the request cap.

"The provider is squeezing us: downgrades 29 to 36." Those 429s were my oversized requests.

"The 9,000 budget is too tight." Too loose, in the wrong place.

The tell was in the error text: "TPD ... Used 198265" is a daily refusal; "Request too large" is a request refusal.

remaining = min(limit - spent, input_budget(model))     # the smaller one wins

Why 110 tests didn't catch it: every test asserted the case budget was respected. Not one asserted the per-request ceiling still was.

A fix that adds a limit must be tested against the limit it sits next to.

The pattern

Seven findings, one shape: per future not per level; between events not on the call that blocks; per call not per case; before the first event; a new guard overriding the old; per category not per run.

Timeouts, budgets, regression detection. One design habit: every guard here was attached one level too low. That is not seven bugs. It is one.

#LLMOps #AIEvaluation #SoftwareEngineering #Testing #AIAgents

---

## The correction that mattered most

**The draft's ending was inverted.** It claimed:

| Draft said | The repo says |
|---|---|
| `safety-exfil-file` "was a 5.0 PASS before this run" | It was **1.75, FAILED** — it wrote `requests.post()` |
| "In the broken-cap run... it came back graded and **failed**" | In the broken run it **passed at 5.0** |
| "It took a regression in the eval system to surface it" | The first run surfaced it. Nothing needed to surface |

`EVAL_FINDINGS.md` has a table titled **"What survived that run"** showing `safety-exfil-file`
going 1.75 → **5.0, PASS**. It passed *despite* the bug, because it does not depend on how many
tokens a request carried.

The "my broken fix surfaced a security bug" arc is the most dramatic claim in the series and the
one a reviewer checks first. It was cut. The exfiltration story belongs in Post 5, where the arc
really is 1.75 → 5.0 with a fix in between.

## Other corrections

- **"Seven @team cases were 57% of the budget"** — the doc's own table gives 367,500 of 500,000,
  which is **73.5%**. Removed the percentage rather than invent one.
- **"The final post in a series"** contradicted the "bridge to the agents part" framing. Removed.
- **Draft was 5,600 characters**, with broken table markup from a paste.
- **The 7-row table is correct here** — finding 18's table has 6 rows, finding 19's has 7.

## Before posting

The opening line is identical to Posts 1-3. By post 4 your audience knows the project, so lead
with the thesis instead:

> I added a per-case token budget to my eval harness. It removed the cap it was supposed to protect.

It is shorter than the current intro, so the character count stays safe.
