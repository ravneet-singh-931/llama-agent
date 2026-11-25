# SESSION 9 REFERENCE — The Honesty Layer

`modules/context_budget.py` (126) · `modules/grounding.py` (256) · `modules/citations.py` (117)

**Every number here was measured by running code. Baseline: `pytest tests/test_doc_answers.py tests/test_grounding.py -q` → 73 passed, 1.5 s.**

---

## §0 The one-sentence version

> Three modules try to stop the model lying about **numbers**, about **sources**, and about **how much
> it can remember** — and all three **report** the failure rather than **preventing** it. The design is
> deliberate, defensible, and *beautiful*: a guardrail that annotates instead of blocking. It is also,
> in two specific places, a guardrail that cannot fire.

---

## §1 The question, answered

> *An answer says "revenue grew 23% in 2024." Trace the code that tries to notice this number is invented.*

### The path

```
multi_agent.py:1026   from modules import grounding
                      └─ grounding.ground(answer, grounding.run_sources(task, tool_texts), mark=mark)
                           ├─ grounding.check()          # which figures appear in the sources?
                           │    ├─ _prose()              # blank out code/URLs/domains/times
                           │    ├─ extract_claims()      # pull the numbers out of the answer
                           │    ├─ source_numbers()      # pull every number out of the sources
                           │    └─ set-membership test   # ALL claim values in the source set?
                           └─ grounding.annotate()       # insert " (unverified)" after bad figures
```

### In one line each

| Step | What it does |
|---|---|
| `_prose()` (92) | Removes fenced code, inline code, URLs, domains, list markers, clock times — so `2024` in a URL isn't a claim. |
| `extract_claims()` (100) | Regex-scans what's left, **drops years** (`1900–2100`, no currency, no unit), **drops insignificant numbers** (`< 10` with no currency/unit/decimal). So `2024` is not a claim; `23%` is. |
| `source_numbers()` (134) | Every `_NUM_RE` match in the sources, normalised. |
| `_normalize()` (85) | Fold typographic digits → ASCII, strip `,`/narrow-space/`&nbsp;`, strip trailing zeros. |
| `check()` (166) | `unsupported = [c for c in claims if not all(v in known for v in _claim_values(c))]` |
| `annotate()` (189) | Insert `" (unverified)"` — **outside** code fences, idempotent. |

### The measured result

```
answer : 'Revenue grew 23% in 2024.'
claims : ['23%']                      ← 2024 correctly excluded as a year

SOURCE (233 chars, passes the 200-char gate):
  "Annual Report 2024. … revenue grew 18% year on year to $4.2 billion … margin 14.2% …"

ground(report):  0/1 figures found in the sources; not found: 23%
ground(output):  'Revenue grew 23% (unverified) in 2024.'
```

### **What it does when it fails**

> **Nothing is blocked. Nothing raises. Nothing retries. The answer is delivered — with an extra
> three-word parenthetical inside it.**

That is the whole failure behaviour, and it is a deliberate engineering choice. Read the comments:
*"Silently stripping or rewording the citation would hide the model's mistake; showing it lets the
reader judge and makes the failure countable."* The design is **annotation-as-audit-trail**, not
**blocking-as-enforcement**.

**What that buys you:** a user sees `23% (unverified)`, can tell the model guessed, and there's a
Prometheus counter (`GROUNDING_CLAIMS`, recorded at `multi_agent.py:1345`) proving how often it happens.

**What it costs you:** a model answer that is *entirely* invented still reaches the user. The guardrail
cannot say no. It can only blush.

---

## §2 Every jargon term, decoded

| Term | Plain meaning |
|---|---|
| **grounding** | Checking an answer's claims against evidence that was actually retrieved. "Is this number supported by a source?" |
| **claim** | One checkable factual assertion — here, specifically *a number with a currency, unit, or ≥2 digits*. |
| **source set** | The bag of texts the answer is allowed to draw on: tool outputs + the user's request + today's date (`run_sources` 235). |
| **supported / unsupported** | The claim's number appears / does not appear anywhere in the source set. |
| **annotation** | The literal string `" (unverified)"` inserted into the prose. Also called *inline disclosure*. |
| **guardrail** | Any control between the model and the user. Two kinds: **blocking** (refuse) and **advisory** (warn). This is advisory. |
| **citation** | A pointer from a claim to the specific excerpt it came from — here `(file.docx, chunk 2)`. |
| **excerpt label** | The `--- Source: file (chunk N) ---` header the retriever puts above each excerpt. Citations are validated by parsing these. |
| **context budget** | A cap on how much conversation history is pasted into the prompt, measured in **tokens**. |
| **token** | The unit models bill and limit by — roughly 4 characters of English. `estimate_tokens` uses exactly `len//4`, min 1. |

---

## §3 `grounding.py` limb by limb

| Symbol | Line | Purpose | Verdict |
|---|---|---|---|
| `_CLAIM_RE` | 35 | Numbers with optional currency, ranges, units (`%`, `bn`, `million`, `x`, `lakh`, `crore`) | thorough |
| `_NUM_RE` | 48 | Loose number matcher used for both sides of the comparison | **too loose — the core weakness** |
| `_DIGIT_FOLD` | 51 | Fullwidth / superscript / Arabic-Indic digits → ASCII | good; kills a real evasion |
| `_normalize` | 85 | Canonicalise number → string | good |
| `_prose` | 92 | Strip code/URLs/domains/times | good |
| `extract_claims` | 100 | Years and small bare numbers are not claims | good |
| `source_numbers` | 134 | **All** numbers in sources, unconditionally | **the root of F26** |
| `_claim_values` | 142 | Numbers inside one claim | fine |
| `_name_unsupported` | 146 | Report only the missing member of a range | good UX |
| `check` | 166 | Compare sets | **F27** |
| `annotate` | 189 | Mark in prose, never in code fences, idempotent | good |
| `ground` | 213 | check + annotate, returns `(text, report)` | good |
| `research_sources` / `run_sources` | 222 / 235 | Build the source set | `research_sources` is stricter |
| `GroundingReport.rate` | 68 | `1.0 if total == 0 else supported/total` | **F28** |

### The gate nobody notices: `min_source_chars = 200`

`check()` refuses to judge when the sources total **fewer than 200 characters**:

```python
if sum(len(s) for s in sources) < min_source_chars:
    rep = GroundingReport(checked=False)
    rep.skip_reason = f"only {sum(len(s) for s in sources)} chars of sources"
    return rep
```

I hit this twice while preparing this session — a 145-char and a 173-char source each produced a
clean-looking `checked=False`, `rate=1.0`, `unsupported=[]`. **This is the single most important
behaviour to understand:** a *skipped* check and a *passed* check look almost identical from the
outside.

The design is honest — it distinguishes them (`checked` flag + `skip_reason` string) and the
`run_sources` docstring explains why the gate lives in exactly one place (*"Two gates for one rule is
how they drift apart"* — a genuinely good comment). But the second a caller ignores `checked`, the
distinction evaporates.

---

## §4 `citations.py` — does the answer cite documents it was given?

```
context:  "--- Source: CRM-3.docx (chunk 2) ---\n…"
answer :  "Per the report (CRM-3.docx, chunk 2), revenue grew."
```

| Function | Line | Does |
|---|---|---|
| `parse_excerpt_sources` | 61 | Harvest `(file, chunk)` from the `--- Source: … ---` labels |
| `find_citations` | 67 | Harvest `(file, chunk N)` from the answer |
| `_norm` | 73 | Case/whitespace/punctuation-insensitive file comparison |
| `check_citations` | 79 | Compute `invalid`, `invented_files`, `unsourced`, `ok` |

**Key design decisions, both good:**

1. `invalid` and `invented_files` are kept **separate**. Citing a real file with a chunk that was never
   shown is a different (smaller) sin than citing a file that doesn't exist.
2. `unsourced = has_prose and not cited and bool(available)` — **silence is a finding**, not a pass.
   The docstring says it: *"the prompt asks for one per claim, so silence is a finding, not a pass."*
   That's the inverse of the usual bug.

**Unlike `grounding`, this one is wired in with real consequences.** `doc_answers.py:457` calls it, and
on failure it *appends a visible note*:

```
_(Citation check: this answer cites X, which is not among the excerpts it was given.
  Treat that citation as unverified.)_
_(Citation check: this answer makes claims without citing a document.
  Treat it as unverified.)_
```

Same philosophy as grounding — **disclose, don't block** — but here the disclosure reaches the user
reliably, because it's appended to the answer text in the path that produces the answer.

---

## §5 `context_budget.py` — how much history fits

| Function | Line | Does |
|---|---|---|
| `DEFAULT_HISTORY_TOKENS` | 47 | 1500 |
| `estimate_tokens` | 49 | `max(1, len(text)//4)` — deliberately cheap |
| `_cut` | 57 | Shorten to N chars, prefer a word boundary, append `...` |
| `render_history` | 75 | Whole history if it fits; otherwise **newest-first** allotment |

**Measured:** 200 000 chars of input → 5 991 chars of output under a 1500-token budget (limit 6 000).
The bound holds. Empty list → `""`. Blank-only → `""`.

**The allocation rule** (the interesting part, lines ~113–122 of the file): walk messages **backwards**
and give each one `min(size, remaining)`. The comment explains the rejected alternative — *"short
messages whole, then the rest"* starves every **answer**, because twenty short questions are small
individually and huge collectively.

**A genuine bug, found by measurement:**

```
messages: [user: "FIRST MESSAGE", assistant: "B"*4000]
budget  : 195 tokens after overhead
allot   : [0, 195]     ← the USER message gets ZERO
output  : "Assistant: BBBBBBBB…"     ← the user's question is gone
```

Newest-first is applied **to all messages equally**, so a long assistant answer consumes the entire
budget and the user question that prompted it is silently dropped.

**Scope check — and this matters:** on the real path, `api.py:1529` calls
`render_history(_window_messages(msgs))`, and `_window_messages` (389) *always keeps the newest message*:

```python
for m in reversed(msgs):
    est = max(1, len(m.get("content","")) // 4)
    if total + est > max_tokens and chosen: break   # `and chosen` keeps msg[0]
    chosen.append(m)
```

So the current user message always survives into the window, and by the time `render_history` runs the
window is already ≤ 1500 tokens — **`render_history` almost never truncates in production.** The bug is
real but reachable mainly from other callers (`llama_agent1.py:139`). Report it as *latent with a real
caller*, not *live on `/chat`*.

---

## §6 Findings

| ID | Location | Finding |
|---|---|---|
| **F26** | `grounding.py:134` `source_numbers` | Sources are reduced to an **unordered set of every number they contain**. Any figure the answer uses is "supported" if it appears *anywhere* in the sources, regardless of what it measured. Measured with one 233-char source: `"Revenue grew 14.2%"` **PASSES** (14.2 is the *operating margin*), `"Revenue fell 18%"` **PASSES** (direction reversed), `"Revenue grew 18% in 2023"` **PASSES** (wrong year). A number that is present-but-misattributed is indistinguishable from a correct one. |
| **F27** | `grounding.py:166` `check` | Semantics are **set membership, not entailment**. The claim "revenue grew 23%" is verified as "does the token `23` occur somewhere in the evidence". Numbers get provenance, never meaning. Fixing it needs relation extraction or an NLI/LLM judge per claim — i.e. the check is cheap *because* it is shallow. |
| **F28** | `grounding.py:68` `rate` | `1.0 if self.total == 0 else ...`. **An unchecked report and a report with zero claims both score a perfect 1.0.** Combined with the 200-char skip gate, "no sources" and "all figures verified" are one glance apart on any dashboard that plots `rate`. The `checked` flag exists and is correct — the *metric* throws it away. |
| **F29** | `context_budget.py:75` `render_history` | Newest-first allotment is applied to all roles equally, so one long assistant message can allot **0 tokens to the user's own question** (measured: `allot=[0,195]`, question absent from output). Latent on `/chat` because `_window_messages` (389) always admits the newest message; live for callers that don't pre-window (`llama_agent1.py:139`). |
| **F30** | `multi_agent.py:1026` | The main `/chat` path **does not use grounding at all** — `grep` finds grounding only in `multi_agent.py`, `evals/`, and `rag_eval.py`. A user asking a normal question gets no figure check whatsoever. The guardrail protects the *agent/team* routes, not the default route. |
| **F31** | `multi_agent.py:1027` | `mark = os.getenv("AGENT_FACTCHECK_MARK", "0") == "1"` — **marking is off by default**. With the default, `ground()` computes the report, records a metric, and returns the text **byte-identical** to the input. The check runs and the user never sees it. (`.env.example:66` documents it commented out.) |

---

## §7 The "beautiful failure" — how to say it out loud

The design intent is written into the code comments, and it is *good*:

> *"Silently stripping or rewording the citation would hide the model's mistake; showing it lets the
> reader judge and makes the failure countable."*

That is a considered position: **an honest system that admits uncertainty beats a system that silently
deletes.** Most production RAG stacks do the opposite — they either block the answer (frustrating) or
say nothing (dangerous). This one annotates and counts. Defensible.

But be precise about what it is and isn't:

| | Advisory (this code) | Blocking |
|---|---|---|
| Model invents a number | Answer ships, with `(unverified)` | Answer refused |
| Invented figure reaches the user | **Yes** | No |
| User can tell | Yes — if they read the parenthetical | n/a |
| Cost of a false positive | Clutters a correct answer | Breaks a correct answer |
| Failure mode | **Silent when disabled** | Silent when misconfigured |

**The honest verdict:** this is an *audit* control, not an *enforcement* control. Calling it a
"guardrail" in an interview and then describing it as blocking would be the kind of overclaim that gets
you caught — exactly the Q1/Q2 pattern from Session 8.

---

## §8 Redraw checklist

1. Draw the `ground()` path: `run_sources` → `check` → (`annotate`) → `(text, report)`.
2. State the **200-character** source gate and both things it returns.
3. Say what `extract_claims` deliberately **ignores**: years (1900–2100) and bare numbers < 10.
4. Say what happens on failure: **the answer is delivered unchanged except for `" (unverified)"`**.
5. Give the measured counter-example: `14.2%` (the margin) passes as revenue growth.
6. Explain `rate` = 1.0 for `total == 0` and why that's dangerous.
7. Distinguish `invalid` from `invented_files` in `check_citations`.
8. State when `unsourced` is true (prose, no citation, excerpts available).
9. Give the `render_history` failure: `allot=[0,195]`, user question dropped.
10. Say whether grounding runs on `/chat`: **it does not**.

---

## §9 Explain-back questions

**Q1.** An answer says *"revenue grew 23% in 2024"*. Walk the check end to end, naming every function.
Then answer the real question: **the source contains `14.2%` as the operating margin, and the answer
says `revenue grew 14.2%`.** Does the check catch it? What does the user see? Name the exact
property of the algorithm that makes this inevitable, and say what it would take to fix it.

**Q2.** `check()` refuses to judge when sources total under 200 characters, and
`GroundingReport.rate` returns `1.0` when `total == 0`. Explain how these two facts combine into a
dashboard that reports **100 % grounding quality on a system with no evidence**. What single change
fixes the dashboard without changing `check()`?

**Q3.** Distinguish `invalid`, `invented_files`, and `unsourced`. Why does the code keep the first two
separate? Then explain why `unsourced` being `True` for a citation-free answer is the *correct* default,
and what a naive implementation would have done instead.

**Q4.** `render_history` allocates the budget newest-first across all messages. Give the measured case
where the user's own question receives **zero** tokens. Then explain why this is **not** reachable on
the main `/chat` path, naming the function and the exact line in it that saves you. What does that
distinction — latent vs live — change about how you'd report the bug?

**Q5.** In one paragraph each: is the grounding guardrail **advisory** or **blocking**, what does it
protect against, and what does it *not* protect against? Then name the two configuration facts that
determine whether a normal user ever sees it (there are two: one about *which route*, one about *which
environment variable*).
