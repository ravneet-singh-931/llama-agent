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
