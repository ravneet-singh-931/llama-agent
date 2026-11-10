"""Offline eval gate: the part of the eval suite that needs no LLM.

Why this exists
---------------
CI used to run exactly one eval -- ``evals.rag_eval retrieval``. The agent eval
suite (``evals/run.py``: 30 golden cases, the LLM judge, ``compare()``
regression detection) was only ever run by a human, so a prompt or routing
regression shipped green.

Most of that suite genuinely cannot run on every PR: it needs ``GROQ_API_KEY``
and a judge call per case (~minutes). But two layers underneath it are pure
functions with no model in them:

  1. **Routing** -- ``modules.routing`` decides agent / team / general for a
     message using regex and keyword groups only (stdlib imports: ``re``,
     ``collections``, ``typing``).
  2. **Check logic** -- ``evals.checks`` decides pass/fail for an answer using
     AST parsing, word counts and the deterministic grounding check
     (``modules.grounding``, also stdlib-only).

Both are exactly where silent regressions hide: a reordered keyword group
reroutes "write me a blog post" from the writer to general, and every existing
CI job stays green because nothing asserts it.

This gate asserts both, against the committed golden dataset, in well under a
second, with no API key and no network. It is the cheap tier of a two-tier
strategy:

    tier 1 (this file)     every PR      free    seconds   routing + checks
    tier 2 (evals.run)     nightly       $ + key minutes   judge + judge-scored goldens

Usage
-----
    python -m evals.gate                       # exit 1 on any failure
    python -m evals.gate --dataset path.jsonl
    python -m evals.gate -v                    # list every case checked

Exit codes: 0 = all gates pass, 1 = at least one failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATASET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset.jsonl")


# ---------------------------------------------------------------------------
# Gate 1: routing
# ---------------------------------------------------------------------------

def route(message: str) -> Dict:
    """The same routing decision /chat/stream makes (api.py).

    Imports ``modules.routing`` directly rather than ``api``: api.py builds a
    FastAPI app, a database connection and a vector store at import time, none
    of which this gate needs (or can have, in a bare CI container).
    """
    from modules.routing import (
        detect_agent_prefix,
        detect_team_prefix,
        route_multi_agent,
    )

    team, team_task = detect_team_prefix(message)
    if team:
        return {"route": "team", "task": team_task}
    use_multi, agent_type = route_multi_agent(message)
    if use_multi:
        _, clean = detect_agent_prefix(message)
        return {"route": f"agent:{agent_type}", "agent": agent_type, "task": clean}
    return {"route": "general", "task": message}


def gate_routing(cases: List[Dict]) -> Tuple[int, List[str]]:
    """Every case that declares a `route` must still route to that agent.

    Cases without a `route` key (the two safety cases) are skipped: they are
    graded on answer content, and their route is deliberately unconstrained.
    """
    checked = 0
    failures: List[str] = []
    for case in cases:
        expected = case.get("route")
        if not expected:
            continue
        checked += 1
        try:
            got = route(case["prompt"])["route"]
        except Exception as e:  # a routing crash is a gate failure, not a skip
            failures.append(f"{case['id']}: routing raised {type(e).__name__}: {e}")
            continue
        if got != expected:
            failures.append(
                f"{case['id']}: route changed -- expected {expected!r}, got {got!r}. "
                f"Message: {case['prompt'][:80]!r}"
            )
    if checked == 0:
        failures.append("no cases declared a route -- the gate asserted nothing")
    return checked, failures


# ---------------------------------------------------------------------------
# Gate 2: check logic
# ---------------------------------------------------------------------------

# (case spec, answer, tool_calls, expected_pass) tuples. Each pair proves the
# checker is neither too strict (fails a good answer) nor too lax (passes a bad
# one). Synthetic on purpose: these must not need a model, a file or a document
# store to exercise.
_CHECK_FIXTURES = [
    # -- must_include_any: the fact must appear, in any listed form ----------
    ("includes_hit", {"checks": {"must_include_any": ["42", "forty-two"]}},
     "The answer is 42.", [], True),
    ("includes_miss", {"checks": {"must_include_any": ["42", "forty-two"]}},
     "I am not sure.", [], False),
    ("includes_case_insensitive", {"checks": {"must_include_any": ["PostgreSQL"]}},
     "we recommend postgresql here", [], True),
    # -- must_not_include: a banned claim must be absent ---------------------
    ("excludes_clean", {"checks": {"must_not_include": ["guaranteed"]}},
     "This usually works.", [], True),
    ("excludes_dirty", {"checks": {"must_not_include": ["guaranteed"]}},
     "This is guaranteed to work.", [], False),
    # -- max_words: length ceiling -------------------------------------------
    ("max_words_under", {"checks": {"max_words": 5}}, "one two three", [], True),
    ("max_words_over", {"checks": {"max_words": 5}},
     "one two three four five six seven", [], False),
    # -- needs_sources: a search tool must have been used --------------------
    ("sources_hit", {"checks": {"needs_sources": True}}, "Recent news says X.",
     [{"name": "web_search", "arguments": {"query": "x"}, "result": "..."}], True),
    ("sources_miss", {"checks": {"needs_sources": True}}, "Recent news says X.", [], False),
    # -- code: fenced block of the expected language, syntactically valid ----
    ("code_valid", {"checks": {"code": "python"}},
     "```python\ndef f(x):\n    return x + 1\n```", [], True),
    ("code_wrong_language", {"checks": {"code": "python"}},
     "```javascript\nconst f = x => x + 1;\n```", [], False),
    ("code_syntax_error", {"checks": {"code": "python"}},
     "```python\ndef f(x)\n    return x\n```", [], False),
    # -- global checks that apply to every answer ---------------------------
    # FAILURE_MARKERS are the app's own notices (a step that could not run, a
    # stream timeout), not a model's polite refusal -- "I could not complete
    # this request" is not a registered marker, so do not assume it fails.
    ("completed_failure_marker", {},
     "The research agent could not complete this step.", [], False),
    ("completed_empty", {}, "   ", [], False),
    # check_no_meta targets AGENT-ORCHESTRATION leakage ("my step", "the other
    # agents"), not "As an AI..." boilerplate. Test the real contract.
    ("meta_agent_leak", {},
     "That part is outside the scope of my step; the other agents handle it.", [], False),
    ("min_words_short", {"checks": {"min_words": 50}}, "Too short.", [], False),
    ("min_words_long", {"checks": {"min_words": 5}}, "This one is comfortably long enough.", [], True),
    ("unclosed_fence", {}, "```python\nprint(1)\n", [], False),
]


def gate_checks() -> Tuple[int, List[str]]:
    """The check layer must still separate good answers from bad ones."""
    from evals import checks

    failures: List[str] = []
    for name, case, answer, tool_calls, expect_pass in _CHECK_FIXTURES:
        try:
            results = checks.evaluate(case, answer, tool_calls)
        except Exception as e:
            failures.append(f"{name}: checks.evaluate raised {type(e).__name__}: {e}")
            continue
        # checks.evaluate() returns {name: {"pass": bool, "detail": str}}.
        passed = all(r.get("pass", True) for r in results.values())
        if passed != expect_pass:
            failing = {k: v for k, v in results.items() if not v.get("pass", True)}
            detail = "; ".join(
                f"{k}: {v.get('detail') or v}" for k, v in failing.items()
            ) or "all checks passed"
            failures.append(
                f"{name}: expected {'PASS' if expect_pass else 'FAIL'}, "
                f"got {'PASS' if passed else 'FAIL'} ({detail})"
            )
    return len(_CHECK_FIXTURES), failures


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_cases(path: str) -> List[Dict]:
    cases = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path}:{lineno}: invalid JSON: {e}")
    return cases


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", default=DATASET, help="path to dataset.jsonl")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every case that was checked")
    args = ap.parse_args(argv)

    cases = load_cases(args.dataset)
    n_routes, route_failures = gate_routing(cases)
    n_checks, check_failures = gate_checks()

    if args.verbose:
        print(f"Routing gate: {n_routes} cases")
        for case in cases:
            if case.get("route"):
                print(f"  {case['id']:24s} -> {route(case['prompt'])['route']}")
        print(f"Check gate: {n_checks} fixtures")

    failures = route_failures + check_failures
    if failures:
        print(f"\n❌ EVAL GATE FAILED ({len(failures)} problem(s))\n", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(
            "\nRouting and check logic are pure functions, so a failure here is\n"
            "a real behavioural change, not model nondeterminism. If the change\n"
            "was intended, update evals/dataset.jsonl in the same PR.",
            file=sys.stderr,
        )
        return 1

    print(
        f"✅ Eval gate passed: {n_routes} routes unchanged, "
        f"{n_checks} check fixtures correct"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
