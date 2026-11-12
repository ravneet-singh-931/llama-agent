# Figures

Two charts from the eval series. The posts themselves live on LinkedIn, not here.

---

## `img/number_trail_chart.png`

Seven real runs of the 60-case eval suite, same harness throughout.

The blue line is the pass rate over all 60 cases. The green line counts only the cases that
actually ran. The shaded gap between them is the cases the free tier's daily quota ate before they
were graded.

On the night the suite reported **35%**, 36 of 60 cases passed — but only 48 got graded, so the
real score was **72.4%**. The gap was 37.4 points, and the report showed one number.

The runs after it close the gap by shrinking the suite to fit the quota, not by making the agent
better. That is the point: for months the chart was measuring how much of the run survived, not how
good the answer was.

---

## `img/02-five-bugs.png`

The five grader bugs from one audit, as what each check measured against what it should have
measured.

| Check | Measured | Should measure | Case |
|---|---|---|---|
| Grounding | Do figures appear in a source? | — and was a source needed? | `plan-mvp`, 1 of 42 found, no judge score |
| Inline code | Is code in a fenced block? | Does it parse as Python? | `code-debug`, **5.0/5** |
| Hyphen | Is it ASCII? | Does the arithmetic work? | `team-slack-bot`, **4.75/5** |
| Regex | Is there a regex tag? | Is there a working pattern? | `code-regex-pin`, **5.0/5** |
| `no_meta` | Does the text match? | Did the agent narrate its pipeline? | `team-cafe`, one match in 60 cases |

All five graded the shape of the answer instead of the answer. Not one was a bug in the agent.
