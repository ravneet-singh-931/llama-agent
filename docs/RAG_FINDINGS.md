# RAG findings

`modules/rag_optimizations.py` is **1,703 lines** — the largest module in this project —
and until this document it had **zero findings recorded**. It is also the module with
the best before/after numbers in the repo, all of which were buried in a table row in
the README.

This is the fourth findings document, and it covers the part of the system users touch
most: upload a document, ask a question, get a grounded answer.

| Document | Surface | Findings |
|---|---|---|
| [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md) | the measuring stick | 18 |
| [`AGENT_FINDINGS.md`](AGENT_FINDINGS.md) | the agents | 4 fixed, 5 open |
| [`OBSERVABILITY_FINDINGS.md`](OBSERVABILITY_FINDINGS.md) | dashboards and traces | 3 |
| [`KUBERNETES_CHECKS.md`](KUBERNETES_CHECKS.md) | the cluster | 5 checks |
| **this file** | **retrieval, ranking, caching, memory, the gate** | **6** |

**The through-line, and it is the sharpest one in the project:**

> **The components that were supposed to make retrieval better were silently disabled,
> and the metric that would have shown it was never measured.**

Two of the five findings below are the *same bug* — a quality feature that accepted a
tiny token budget, spent it on hidden reasoning, returned an empty string, and was
recorded as "working". Neither was caught by reading the code. Both were caught by
measuring an output that nobody had thought to check was non-empty.

---

## The retrieval table

`python -m evals.rag_eval retrieval`, no LLM, 6-document corpus, 48 questions of which
38 are answerable. "Right passage #N" means the chunk containing the answer was ranked
Nth among the 6 excerpts the answer model is given.

| Setup | Right passage #1 | In top 3 | In top 6 | MRR | p50 latency |
|---|---|---|---|---|---|
| Vector search only | 27/38 | 32/38 | 35/38 | 0.79 | 0.29 s |
| Hybrid (BM25 + vector) | 28/38 | 31/38 | 35/38 | 0.80 | 0.28 s |
| **Hybrid + ONNX reranker (deployed)** | **31/38** | **34/38** | **35/38** | **0.86** | 1.25 s |

**Three of the results in this table are negative, and they are the point.**

- **Hybrid search adds nothing measurable** over vector search on this set — one
  question either way, and it is *worse* at top-3 (31 vs 32). It is deployed anyway,
  because BM25 costs nothing and the corpus is too small to distinguish the two.
- **The reranker does not change *whether* the right passage is in the top 6.** All
  three setups score 35/38. What it changes is *rank*: +4 questions moved to position
  one, MRR 0.79 → 0.86. If the answer model only ever sees the top 6, the reranker is
  improving a property that does not affect the answer. That is worth knowing and is
  almost never published.
- **The 3 misses are paraphrases** — questions whose wording shares little with the
  document. No amount of reranking fixes a chunk that was never retrieved.

Publishing the negative results is what makes the positive one credible.

---

## R1. The "cross-encoder reranker" was keyword overlap

**Symptom.** Retrieval ranked the right passage first for 28 of 38 questions. The
README claimed a cross-encoder reranker was deployed.

**Story.** A cross-encoder reads the question and the chunk **together**, so it judges
relevance far better than vector similarity or keyword overlap. That is the entire
reason to have one. This project had a `CrossEncoderReranker` class with three
backends, tried in order:

1. ONNX (`RERANKER_MODEL_DIR`) — `ms-marco-MiniLM-L-6` via onnxruntime, ~23 MB, no PyTorch
2. `sentence-transformers`, if installed
3. a keyword-based reranker

`onnxruntime` was not in the image and `sentence-transformers` (which pulls PyTorch)
was never installed. **So backend 3 ran, silently, in production.** The class was
named `CrossEncoderReranker`. It was doing keyword overlap.

Nothing logged a warning, because a fallback that always succeeds is indistinguishable
from a backend that always works.

**Fix.** ONNX int8 cross-encoder, pinned and hash-checked on download; **the build fails
if it ranks a known-good pair wrongly**; the startup log names which backend is in use.

That last part is the actual fix. Not "make backend 1 work" — **make the silent
fallback impossible to be silent.**

**Proof.** `tests/test_reranker_onnx.py`; ranked-first **28/38 → 31/38** (+4 questions).

**The lesson.** A three-deep fallback chain where the last entry always succeeds is a
feature that cannot fail and therefore cannot be noticed. The guard was attached to
"does a reranker exist", not "is the reranker reranking".

---

## R2. Corrective RAG never rejected anything

**Symptom.** The CRAG self-correction loop was supposed to detect unsupported answers
and re-retrieve. It **never rejected anything** — the recorded behaviour, not an
inference from the code.

**Story.** Corrective RAG works by asking a second question: *is every sentence of this
answer supported by the retrieved chunks?* If not, re-retrieve.

The verifier was given a **5-token** completion budget. Five tokens is not enough for
any model to answer that question — and the models in use spend their first tokens on
hidden reasoning before emitting anything visible. So the reply was **always empty**.
An empty string parsed as "no objection", which meant "supported".

**The loop could not reject an answer.** It was structurally incapable of firing, and
it reported success 100% of the time.

**Fix.** 1,024-token budget (`RAG_CRAG_MAX_TOKENS`), robust verdict parsing, and a
verdict metric so the rejection rate is **observable** — before it was not. v3.4.6.

**Proof.** `llama_agent_rag_crag_verdicts_total` exists so the rejection rate can be
read at all. The claim measured is "it never rejected"; the claim *not* measured is
"it rejects the right things now", which no test establishes.

**A second bug in the same method, found by the same audit.** `retrieve_and_answer`
returned `"supported": True` **hardcoded** on the final return. So even after the token
fix, the caller's `if crag_result["supported"]` branch could never take its
"unsupported" path on that route — it reported success for answers it had just decided
were bad. Fixed to return the measured verdict.

**The lesson.** A budget of 5 tokens is not a guard, it is decoration — the same
finding as eval row 8 and row 9, in a third module. **If a check has never fired, that
is evidence about the check, not about the system.**

---

## R3. The semantic cache served answers to different questions

**Symptom.** A question about the **Team plan's cost** returned an answer about the
**Team plan's rate limit**. Two different questions, one cached answer.

**Story.** The semantic cache embedded each question and served any previously-cached
answer within a cosine distance threshold. That threshold was **0.3** — loose enough
that "how much does the Team plan cost" and "what is the Team plan rate limit" are
neighbours, because they share almost every token and differ only in the noun that
carries the entire meaning.

This is the failure mode of every semantic cache: similarity is computed over the
whole sentence, and the one distinguishing word is a rounding error in that average.

**Fix.** Two rules, both required:
- distance **≤ 0.05** (similarity ≥ 0.95), and
- **the same numbers, years and quarters** in both questions.

The second rule is the interesting one. "Cost in 2025" and "cost in 2026" are a
0.01-distance match and a completely wrong answer. A cache that matches on embedding
distance alone cannot see the digits.

**Proof.** Wrong cache answers **2 → 0**; RAG answers **39/44 → 41/44**.

**The lesson.** A cache hit is a *claim of equivalence*. At distance 0.3 the cache was
claiming two different questions were the same question. The threshold was not a
tuning parameter — it was an assertion about the corpus, and it was false.

---

## R4. Contextual chunk summaries were empty in 6 of 7 documents

**Symptom.** The feature existed, was configured on, and had produced a summary for
**1 of 7 documents**.

**Story.** Contextual chunking prepends a short summary of the whole document to each
chunk before embedding, so a chunk of a long contract still carries the fact that it is
a contract. It is a real quality improvement and it was **not running**.

The cause is R2's cause exactly: a **100-token** budget for a step that generates both
hidden reasoning and a ~60-word summary. The reasoning consumed the budget, the summary
came back empty, and an empty summary was prepended to every chunk as `[Document
Summary: ]`.

**Fix.** Budget 1,024 (`RAG_SUMMARY_MAX_TOKENS`), and the feature is **opt-in until it
is measured to help** — because "the summary is non-empty" and "the summary improves
retrieval" are different claims, and only the first one had been established.

**Proof.** RAG eval, summary coverage 1/7 → all documents.

**The lesson, and it is R2's lesson again.** Two quality features in one module, both
disabled by a budget too small to produce output, both reported as enabled. **The bug
is not the number 100. The bug is that nothing asserted the output was non-empty.**
A feature whose only success criterion is "the code ran" will run and do nothing.

---

## R5. A 65 KB upload returned HTTP 502 — the reranker's memory, not the upload's

**Symptom.** Uploading a 65 KB `.docx` returned 502. Reproducible.

**Story.** The obvious suspect is the upload. It was not.

One document question made the reranker score **16 long passages in one batch**. At 512
tokens each that is roughly **940 MB** peak — and `onnxruntime` **keeps that peak
reserved** after the call returns. So a single question lifted the API pod from ~260 MB
to **~1.2 GB** and left it there.

The next upload's embeddings need ~420 MB. 1.2 GB + 420 MB crosses the 1.5 GiB limit
and the pod is OOMKilled, and the user sees a 502 on an upload — with nothing in the
error that points at a question asked minutes earlier.

**Fix.** Reranker batch **16 → 4**. v3.5.5.

**Proof.** `tests/test_reranker_memory.py`. Prometheus: pod **261 → 1221 MB** after one
question, killed at the upload. Reranker alone, 8 questions in a row: **1019 → 354 MB**.
Live after the fix: 481 / 627 MB max, **0 restarts**, RAG eval unchanged.

**The lesson.** The failure was attributed to the wrong request. Memory reserved by work
that had *finished* killed an operation that had not started. **A resource graph with no
per-request attribution sends you to the wrong request** — which is an observability
finding wearing a RAG costume.

---

## R6. The CI gate could not see the reranker either

**Symptom.** Nothing. The gate was green, and it had been green every run. This
finding was found by reading the gate *after* writing R1, not by seeing it fail.

**Story.** R1 says a reranker was silently replaced by keyword overlap and nobody
noticed for months. The natural next question — the one this project asks of every
other component — is *what would have noticed?*

The answer was `main.yml`, which ran:

```yaml
python -m evals.rag_eval retrieval --min-hit6 0.89 --out rag-retrieval.json
```

`hit@6` is the fraction of questions whose answer chunk appears anywhere in the six
excerpts the model is given. From the table at the top of this document:

| Setup | top 6 |
|---|---|
| Vector only | 35/38 |
| Hybrid | 35/38 |
| **Hybrid + reranker (deployed)** | **35/38** |

**All three are identical.** `hit@6` is the one number the reranker does not move —
that is the honest negative result published above, and it was quietly the *only*
floor the gate had. **The gate would have passed on the day the reranker fell back
to keyword overlap.** It would have passed if the reranker had been deleted.

This is R1's own failure mode, one level up: a guard attached to "is the answer
findable" when the thing being protected is "is it ranked first". The document
describing the bug and the CI enforcing the bug were in the same repository, and
the second one was green.

**Fix — three floors and a printed blind spot**, v3.6.0:

| Floor | Vector | Hybrid | **Deployed** | Slack |
|---|---|---|---|---|
| `hit@6 ≥ 0.89` | 35/38 | 35/38 | 35/38 | 1 question — *reachability* |
| `hit@1 ≥ 0.75` | 27/38 | 28/38 | **31/38** | 2 questions — *ranking* |
| `MRR ≥ 0.82` | 0.79 | 0.80 | **0.86** | margin 0.04 |
| `--require-reranker` | — | — | backend must be `onnx` | exact |

`hit@6` stays, because "the answer is reachable" is a real question. The two new
floors are the ones the reranker actually moves. `--require-reranker` closes the
door completely: if the live backend is keyword overlap, the run fails no matter
how good the scores are.

**The part that is the actual fix.** The gate now measures *itself*:
`gate_sensitivity()` re-runs the pass/fail decision for **each setup as if it were
the deployed one**, and the job summary prints the verdicts.

```
**Gate sensitivity** — would the gate pass this setup alone?

| Setup | Gate verdict |
|---|---|
| vector ← correctly rejected | FAIL |
| hybrid ← correctly rejected | FAIL |
| hybrid+rerank (deployed) ← deployed | PASS |

Gate discriminates the reranker: **YES**
```

Under the old `hit@6`-only floor the same table reads:

```
| vector ← PASSES WITHOUT THE RERANKER | PASS |
| hybrid ← PASSES WITHOUT THE RERANKER | PASS |
Gate discriminates the reranker: **NO**
```

That is the fix. Not "add a floor" — **make a gate that cannot see its own component
say so out loud, in every run, in the summary a reviewer reads.** A gate that is
blind and silent is R1. A gate that is blind and announces it is a bug report.

**Proof.** Six tests in `tests/test_rag_eval.py`, and one of them is the bug:

- `test_hit6_alone_cannot_see_the_reranker` — reproduces the old gate, asserts it
  passes with **no reranker at all**, and asserts `discriminates is False`
- `test_hit1_and_mrr_floors_can_see_the_reranker` — same table, new floors, asserts
  both no-reranker setups **fail** and the deployed one passes
- `test_vector_only_setup_fails_the_deployed_gate` — the day-after-R1 scenario
- `test_require_reranker_rejects_a_keyword_fallback` — a perfect score with a
  keyword backend still fails
- `test_gate_sensitivity_is_printed_in_the_summary` — the blind spot reaches the
  job summary, not just the exit code
- `test_leaks_and_broken_labels_still_fail_any_gate` — the old guarantees hold

`tests/test_rag_eval.py`: **29 passed**.

**Live proof, not just a synthetic table.** The tests above run against the
published numbers. The gate itself was then exercised by CI on the real corpus in
run **43387791311** (`Test (Python 3.11)`, 2026-11-13). It **passed**, and with
`--require-reranker` and the two new floors in place, passing means all three of
these held on real data in that run:

- the live reranker backend was **ONNX**, not a fallback,
- `hit@1 ≥ 0.75` — at most 9 of 38 questions with the answer outside position one,
- `MRR ≥ 0.82`.

The floors were not tuned to make the build green; they were set from the
2026-10-25 table first, and the first real run happened to clear them. If it had
not, the honest outcome would have been to investigate, not to lower the floor.

**The lesson.** *A gate is a measuring instrument, and it needs the same suspicion
as a test suite.* R1's lesson was that a fallback chain that always succeeds cannot
be noticed; R6's is that the check meant to notice it was measuring a property that
does not change when the thing breaks. **The metric was real, the number was driven
from a real run, and it was measuring the wrong thing** — the observability part's
through-line, arrived at again from a different direction.

**Cost of finding it.** One question: *"what would have noticed?"* Asked of a green
check, not a red one. Worth asking of every other gate.

---

## The pattern, six for six

| # | What was claimed | What was true |
|---|---|---|
| R1 | a cross-encoder reranks | keyword overlap reranked |
| R2 | CRAG rejects unsupported answers | it could not reject anything, ever |
| R3 | the cache serves equivalent questions | it served different questions |
| R4 | contextual summaries improve chunks | 6 of 7 were empty strings |
| R5 | the upload failed | a question asked earlier had reserved the memory |
| R6 | CI gates retrieval quality | the gate could not detect the reranker disappearing |

**Five of the six are features that reported success while doing nothing, doing
something else, or refusing to look.** None threw an error. None looked wrong in code review. Each was found
by measuring an output rather than a code path — the ranking position, the rejection
count, the wrong answer, the summary length, the memory high-water mark.

**This is the same finding as the eval part of this project, moved down one layer.**
There, 16 of 18 findings were in the measuring stick rather than the thing measured.
Here, four of five are in quality features that were never measured at all. In both
cases the system was confident and wrong rather than broken and loud.

---

## What is not measured

Stated so it is not discovered by a reader first.

- **The corpus is small: 6 documents, 48 questions, 38 answerable.** Good for catching
  regressions in *this* pipeline. Not a benchmark against other systems, and the
  absolute numbers carry no meaning outside this repo.
- **The RAG eval cannot run in a bare sandbox.** It downloads the ONNX embedding model
  at index time, so `python -m evals.rag_eval retrieval` needs network access; in a
  restricted environment it fails at `chromadb` import or model fetch, not with a
  measurement.
- **`RAG_CONTEXTUAL` and `RAG_DECOMPOSE` are opt-in and have not been measured to
  help.** Non-empty and helpful are different claims.
- **Hybrid search's contribution is unresolved.** It is deployed; on this corpus it is
  indistinguishable from vector search. A larger corpus would settle it.
- **The reranker is CPU-only and costs ~1 s per query** (0.29 s → 1.25 s p50). Not
  measured: whether that second buys anything the answer model notices.
