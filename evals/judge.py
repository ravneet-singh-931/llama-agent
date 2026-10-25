"""
LLM-as-judge for the evaluation suite.

A fixed rubric scored 1-5 by the FAST model (through the same guarded
InstrumentedGroqClient as the agents, so it respects the free-tier token
budget). The judge sees the request, the answer, an excerpt of the sources
the agents actually retrieved, today's date and - when a case has one - a
short reference note. Deterministic checks (evals/checks.py) cover what
rules can decide; the judge covers what they cannot (correctness,
completeness, relevance).
"""

import json
import os
import re
from typing import Dict, List, Optional

CRITERIA = ("correctness", "completeness", "relevance", "clarity")

RUBRIC = """You are a strict evaluator of an AI assistant's answer. Score each criterion 1-5:
- correctness: facts, code and reasoning are right; no invented facts, figures or sources.
  When SOURCES are given, judge factual claims against them, not against your memory:
  events after your training data are expected, and schedules, results and prices change.
  Never call a claim wrong or fabricated only because it differs from what you remember;
  "fabrication" means a specific claim that the SOURCES do not support (or contradict).
- completeness: every part of the request is answered.
- relevance: stays on what was asked; no filler, no talk about its own process.
- clarity: well organised and easy to use.
Reply with ONLY a JSON object, no prose:
{"correctness": n, "completeness": n, "relevance": n, "clarity": n,
 "fabrication": true|false, "comment": "<one sentence on the main problem, or 'none'>"}"""


def _clip(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    return text[:head] + "\n[...]\n" + text[-(limit - head):]


_SALIENT_RE = re.compile(r"\d[\d,.:%]*|[A-Z][\w&.-]+|[a-z]{6,}")


def _salient(text: str) -> set:
    """Names, numbers, dates and long words - what a factual claim is made of."""
    return {t.lower().strip(".,") for t in _SALIENT_RE.findall(text or "")}


def relevant_sources(sources: List[str], answer: str, limit: int) -> str:
    """The source passages that share the most names/numbers with the answer,
    up to ``limit`` chars, in their original order. A blind head+tail clip of
    several web searches hid the passages the answer was built from, so the
    judge flagged sourced facts as fabricated."""
    passages = [p.strip() for src in sources or []
                for p in re.split(r"\n\s*\n", src or "")  # one web result each
                if p and p.strip()]
    if sum(len(p) for p in passages) <= limit:
        return "\n---\n".join(passages)
    want = _salient(answer)
    ranked = sorted(range(len(passages)),
                    key=lambda i: len(_salient(passages[i]) & want), reverse=True)
    keep, used = set(), 0
    for i in ranked:
        size = min(len(passages[i]), 1500)
        if used + size > limit:
            continue
        keep.add(i)
        used += size
    return "\n---\n".join(passages[i][:1500] for i in sorted(keep))


def judge_sources(sources: List[str], answer: str) -> str:
    """Exactly the SOURCES text the judge is shown (also saved in the eval
    report, so every verdict - e.g. a fabrication flag - can be checked)."""
    if not sources:
        return ""
    return relevant_sources(sources, answer, int(os.getenv("EVAL_JUDGE_SOURCE_CHARS", "8000")))


def build_messages(case: Dict, answer: str, sources: List[str], today: str) -> List[Dict]:
    parts = [f"TODAY: {today}", f"REQUEST:\n{case.get('prompt', '')}"]
    if case.get("reference"):
        parts.append(f"REFERENCE NOTE (what a good answer does):\n{case['reference']}")
    if sources:
        parts.append("SOURCES (the passages of what the assistant retrieved that "
                     "relate to its answer):\n" + judge_sources(sources, answer))
    parts.append(f"ANSWER:\n{_clip(answer, 9000)}")
    return [{"role": "system", "content": RUBRIC},
            {"role": "user", "content": "\n\n".join(parts)}]


def parse_verdict(text: str) -> Optional[Dict]:
    """Extract and validate the judge's JSON (tolerates fences/prose)."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    scores = {}
    for c in CRITERIA:
        try:
            scores[c] = max(1, min(5, int(round(float(data.get(c))))))
        except (TypeError, ValueError):
            return None
    return {**scores,
            "overall": round(sum(scores.values()) / len(CRITERIA), 2),
            "fabrication": bool(data.get("fabrication", False)),
            "comment": str(data.get("comment", ""))[:300]}


def judge(case: Dict, answer: str, sources: List[str], client=None,
          model: Optional[str] = None) -> Dict:
    """Score one answer. Never raises: failures come back as {'error': ...}."""
    try:
        from modules.model_tiers import resolve_model
        from modules.prompt_rules import today_str
        if client is None:
            from multi_agent import InstrumentedGroqClient
            client = InstrumentedGroqClient(api_key=os.environ["GROQ_API_KEY"])
        model = model or os.getenv("EVAL_JUDGE_MODEL") or resolve_model("fast")
        messages = build_messages(case, answer, sources, today_str())
        for _ in range(2):  # one retry on unparsable output
            resp = client.chat.completions.create(
                model=model, messages=messages, temperature=0,
                # Reasoning models think before the JSON; 700 tokens ran out
                # on long answers and left no verdict.
                max_tokens=int(os.getenv("EVAL_JUDGE_MAX_TOKENS", "2000")))
            verdict = parse_verdict(resp.choices[0].message.content or "")
            if verdict:
                verdict["model"] = model
                return verdict
        return {"error": "judge returned no valid JSON"}
    except Exception as e:  # judge failures must not abort the run
        return {"error": f"{type(e).__name__}: {e}"[:300]}
