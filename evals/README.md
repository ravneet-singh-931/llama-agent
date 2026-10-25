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
| Quality (1-5) | LLM judge rubric: correctness, completeness, relevance, clarity, fabrication | `judge.py` |
| Cost / LLMOps | latency, input/output tokens, USD, guard actions (compacted / downgraded / waited / retried) | Prometheus counters |

All checks run on every case. A case only declares *what* it expects (for
example "needs live sources" or "contains Python code"). It never declares
how a specific answer should look.

## Dataset

`dataset.jsonl` has 30 cases in 9 categories:
- research (news and facts)
- coding
- writing
- planning
- analysis
- general chat
- agent team (5 different domains)
- safety (prompt injection, language rule)

The fact cases include one about a prize that doesn't exist. 9 cases are tagged
`smoke` for a quick, cheap run that still covers 7 categories.

Every case goes through the same routing as `/chat/stream`: `@team` goes to the
agent team, keyword or `@Agent` routing goes to a specialist, and everything
else goes to the general agent.

## Running it (on demand)

Run it inside the API pod. That's where the keys and the deployed code are.

```bash
# Quick smoke run (9 cases, about 10-15 min on the Groq free tier)
kubectl -n llama-agent exec deploy/llama-agent-api -- \
  python -m evals.run --tags smoke --out /tmp/eval-smoke.json

# Copy the report out
kubectl -n llama-agent exec deploy/llama-agent-api -- cat /tmp/eval-smoke.json > eval-smoke.json

# Full run (30 cases) - cheaper variants: --no-judge, --no-dsa, --category team coding
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
- The retrieval mode indexes chunks **without** contextual summaries (no LLM),
  so it measures search and reranking only. The answers mode uses the fast model
  for summaries and the CRAG check, as in production.
- CI's hit@6 floor is 0 (report only) until a baseline has been measured. Then
  it is set a little below the baseline (`--min-hit6`), so a retrieval
  regression fails the build.
- The corpus is small (about 28 chunks for the user), so these are regression
  signals for this pipeline, not benchmark scores to compare with other systems.
