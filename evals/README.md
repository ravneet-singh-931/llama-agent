# Evaluation suite

Judge prompt and agent changes by **scores across many varied prompts**, not by
reading one answer. A change ships only when the overall scores go up and no
category gets worse.

## What it measures

| Signal | How | Where |
|---|---|---|
| Completed | no empty answer, timeout or failure message | `checks.py` |
| English only | non-Latin letters in the prose | `checks.py` |
| No meta-talk | generic patterns ("my step", "other agents", "source material 2") | `checks.py` |
| Code validity | Python parses, HTML tags balance, JSON loads, fences closed | `checks.py` |
| Used search | at least one `web_search` call when the case needs live data | `checks.py` |
| **Grounding rate** | share of figures in the answer found in the tool results | `modules/grounding.py` |
| Case expectations | `must_include_any` / `must_not_include` / `max_words` | `dataset.jsonl` |
| Quality (1-5) | LLM judge rubric: correctness, completeness, relevance, clarity, fabrication. If the judge call fails (e.g. the daily token limit), the case is **not graded**: it doesn't count as a pass and is listed separately. The report keeps the exact source text the judge saw (`judge_sources`), so each verdict can be checked by hand | `judge.py` |
| Cost / LLMOps | latency, input/output tokens, USD, guard actions (compacted / downgraded / waited / retried) | Prometheus counters |

All checks run on every case. A case only declares *what* it expects (for
example "needs live sources" or "contains Python code"). It never declares
how a specific answer should look.

## Dataset

`dataset.jsonl` has **60 cases in 9 categories** (it started at 30 and doubled —
see the README's [Agent eval section](../README.md#agent-eval-30-60-cases) for what
changed and why the pass rate moved):

| Category | Cases |
|---|---|
| coding | 12 |
| safety (prompt injection, jailbreak, exfil, PII, language rule) | 8 |
| general | 7 |
| agent team (`@team`, 7 domains) | 7 |
| research: news | 6 |
| writing | 6 |
| research: facts | 5 |
| planning | 5 |
| analysis | 4 |

The fact cases include one about a prize that doesn't exist. 11 cases are tagged
`smoke` for a quick, cheap run that still covers most categories.

A case may declare `needs_sources` — only those are checked for figure grounding,
because a plan's budget and timeline come from the request, not from a search
result. A case may also declare `expected_tools`; calling them is then part of
passing (`tool_recall`).

Every case goes through the same routing as `/chat/stream`: `@team` goes to the
agent team, keyword or `@Agent` routing goes to a specialist, and everything
else goes to the general agent.

## Tiers: what runs when

The suite is split by what a check *costs*, because that decides where it can run.

| Tier | What | Needs | Runs |
|---|---|---|---|
| **Offline gate** (`evals/gate.py`) | 53 routing decisions + 18 check fixtures | nothing (stdlib) | every PR, in the `test` job, under 1 s |
| **RAG retrieval** (`evals/rag_eval.py`) | hit@6 on the committed corpus, cross-user leaks | embedding model, no LLM | every PR |
| **Agent goldens** (`evals/run.py`) | 60 cases: real answers, real judge, tool recall, trajectory | `GROQ_API_KEY` + a judge call per case | nightly (`.github/workflows/nightly-evals.yml`) |

The nightly job is not a leaderboard. It runs on one commit, fetches the previous run's
report from its artifacts, and diffs: `compare()` reports which cases flipped, which
category moved, whether latency or fabrication got worse, and whether the **prompts
changed** between the two runs (`prompt_changed`, from a hash of the prompts in force). It
exits non-zero only on a real regression — a missing baseline or a missing key produces a
notice, not a red build, because a job must not go red for reasons unrelated to the product.

## Running it

Run it inside the API pod. That's where the keys and the deployed code are.

```bash
# Quick smoke run (9 cases, about 10-15 min on the Groq free tier)
kubectl -n llama-agent exec deploy/llama-agent-api -- \
  python -m evals.run --tags smoke --out /tmp/eval-smoke.json

# Copy the report out
kubectl -n llama-agent exec deploy/llama-agent-api -- cat /tmp/eval-smoke.json > eval-smoke.json

# Full run (60 cases) - cheaper variants: --no-judge, --no-dsa, --category team coding
kubectl -n llama-agent exec deploy/llama-agent-api -- \
  python -m evals.run --out /tmp/eval-full.json

# Compare two runs (exit 1 on regression)
python -m evals.run --compare baseline.json new.json --fail-on-regression
```

`--pause` (default 10 s) spaces the cases out so the free tier's per-minute
token window can refill. Free-tier **daily** token caps also apply, so prefer
`--tags smoke` for routine checks.

Note: `kubectl exec` runs in one pod, and its report lives in that pod's `/tmp`.
Copy it out before the pod restarts.

## RAG eval (`rag_eval.py`)

Measures document retrieval and document answers on a fixed sample corpus in
`evals/rag/`, through the same code users hit. Everything is indexed into a
throw-away in-memory ChromaDB, so real users' documents are never touched.

**Corpus:** 6 made-up documents (HR handbook, API spec, board report, plus three
distractors that share vocabulary: a services contract, a stove manual, meeting
notes) for one user, and a "confidential" memo owned by a **second user**.
**Questions** (`questions.jsonl`): 38 answerable (17 keyword, 21 paraphrase) and
6 unanswerable. Each answerable question names its file and a passage that must
be in a retrieved chunk. The set is fixed and was written before any results
were seen. Change the code, not the questions.

| Mode | What it measures | LLM | Where |
|---|---|---|---|
| `retrieval` | hit@1/3/6 and MRR of the 6 excerpts the answer model sees, for **vector only**, **hybrid (BM25 + vector)** and **hybrid + reranker (deployed)**, split by keyword/paraphrase; p50/p95 latency; **cross-user leaks** (fails the run) | none | CI (every run) and on demand |
| `answers` | expected fact present, source file cited, "not found" on unanswerable questions, false refusals, figures found in the documents, CRAG "unverified" note, answers served from the semantic cache, latency, tokens/cost | Groq | on demand, in the API pod |

```bash
# Retrieval (free, about a minute; needs the reranker model for the deployed setup)
python -m evals.rag_eval retrieval --out /tmp/rag-retrieval.json

# Inside the image it is already there:
kubectl -n llama-agent exec deploy/llama-agent-api -- \
  python -m evals.rag_eval retrieval --out /tmp/rag-retrieval.json

# Answers (44 questions, about 25-35 min on the free tier; --ids / --limit for less)
kubectl -n llama-agent exec deploy/llama-agent-api -- \
  python -m evals.rag_eval answers --out /tmp/rag-answers.json
```

Notes:
- Both modes index plain chunks: contextual summaries are off by default
  (`RAG_CONTEXTUAL`), as in production. The retrieval mode measures search and
  reranking only; the answers mode adds the answer model, the CRAG check and the
  semantic cache.
- CI's hit@6 floor is 0.89 (`--min-hit6`): the measured baseline is 35/38 =
  0.921, so one question may slip before a retrieval regression fails the build.
  Run the same command locally before a manual deploy while CI is not running.
  The measured numbers are in the README's "Results" section.
- The corpus is small (about 28 chunks for the user), so these are regression
  signals for this pipeline, not benchmark scores to compare with other systems.
