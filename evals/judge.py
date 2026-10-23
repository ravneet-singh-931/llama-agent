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
  When SOURCES are given, claims not supported by them count against correctness.
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


def build_messages(case: Dict, answer: str, sources: List[str], today: str) -> List[Dict]:
    parts = [f"TODAY: {today}", f"REQUEST:\n{case.get('prompt', '')}"]
    if case.get("reference"):
        parts.append(f"REFERENCE NOTE (what a good answer does):\n{case['reference']}")
    if sources:
        parts.append("SOURCES (excerpt of what the assistant retrieved):\n"
                     + _clip("\n---\n".join(sources), 5000))
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
