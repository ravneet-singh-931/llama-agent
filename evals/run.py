"""
Evaluation runner - scores the real agents on a fixed, varied dataset.

Run inside the API pod (it has the Groq/Tavily keys and the exact code that
serves users):

    kubectl -n llama-agent exec deploy/llama-agent-api -- \\
        python -m evals.run --tags smoke --out /tmp/eval.json

Each case is routed exactly like /chat/stream routes a message (@team ->
agent team, keyword/@Agent routing -> a specialist agent, otherwise the
general agent). For every case the runner records the answer, every tool
call and its raw result, the reasoning events, latency, LLM tokens/cost and
LLMOps guard actions (from this process's Prometheus counters), then runs
the deterministic checks (evals/checks.py) and the LLM judge
(evals/judge.py). A case passes when every applicable check passes and the
judge's overall score is >= --judge-threshold. If the judge itself fails
(e.g. the daily token limit), the case is NOT GRADED: it does not count as a
pass, and the report lists it separately. The report also keeps the source
text the judge saw, so every verdict can be checked by hand.

Compare two runs (exit code 1 on regression with --fail-on-regression):

    python -m evals.run --compare baseline.json new.json
"""

import argparse
import json
import os
import statistics
import sys
import tempfile
import threading
import time
import uuid
from collections import defaultdict
from typing import Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals import checks  # noqa: E402

DATASET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset.jsonl")


# ------------------------------------------------------------------ dataset

def load_cases(path: str = DATASET, ids: Optional[List[str]] = None,
               categories: Optional[List[str]] = None, tags: Optional[List[str]] = None,
               limit: Optional[int] = None) -> List[Dict]:
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("//"):
                cases.append(json.loads(line))
    if ids:
        cases = [c for c in cases if c["id"] in ids]
    if categories:
        cases = [c for c in cases if c["category"] in categories]
    if tags:
        cases = [c for c in cases if set(tags) & set(c.get("tags", []))]
    if limit:
        cases = cases[:limit]
    return order_by_cost(cases)


# Cheapest first, so a quota-starved run loses the expensive cases rather than
# everything after them.
#
# The 2026-11-11 nightly put the five @team cases at positions 24-28. They took
# 74 s, 140 s, 39 s, and burned the free tier's token window; every case after
# position 29 came back as a 429 in ~0.1 s. Thirty-one ungraded cases, and the
# report published 35% -- a number that was mostly a description of the run
# order. Ordering by cost means a truncated run degrades at the far end, where
# the expensive cases are, instead of taking the cheap majority with it.
_CATEGORY_COST = {
    "general": 0, "writing": 1, "coding": 1,
    "analysis": 2, "planning": 2,
    "research_facts": 3, "research_news": 3,
    "team": 4,
}


def order_by_cost(cases: List[Dict]) -> List[Dict]:
    """Stable sort: same content, ordered cheapest-category-first."""
    return sorted(cases, key=lambda c: _CATEGORY_COST.get(c.get("category", ""), 2))


# ------------------------------------------------------------------ instrumentation

# A judge that never answers must not hold the run. It is one extra HTTP call
# per case, so a generous cap costs nothing when the provider is healthy.
_JUDGE_TIMEOUT_S = float(os.getenv("EVAL_JUDGE_TIMEOUT", "180"))

_CASE_SPEND: Dict[str, int] = {}

_ORIGINAL_DISPATCH = None


def restore_dispatch_tool() -> None:
    """Undo any ToolRecorder patches left behind by an abandoned case.

    ToolRecorder swaps `modules.agent_tools.dispatch_tool` for a recording
    wrapper and swaps it back in `__exit__`. A case abandoned at its deadline
    never gets there, so the wrapper stays installed -- and the next case's
    ToolRecorder captures *that* wrapper as its "original", stacking one
    wrapper per timeout. Every later case would then record into a recorder
    nobody reads, and tool_calls / steps / tool_recall would be wrong for the
    rest of the run.
    """
    try:
        from modules import agent_tools
    except Exception:                      # noqa: BLE001 - best effort
        return
    if _ORIGINAL_DISPATCH is not None:
        agent_tools.dispatch_tool = _ORIGINAL_DISPATCH


def _snapshot_dispatch_tool() -> None:
    """Record the unpatched dispatcher once, before any case runs."""
    global _ORIGINAL_DISPATCH
    if _ORIGINAL_DISPATCH is not None:
        return
    try:
        from modules import agent_tools
        _ORIGINAL_DISPATCH = agent_tools.dispatch_tool
    except Exception:                      # noqa: BLE001 - best effort
        pass


class ToolRecorder:
    """Wraps modules.agent_tools.dispatch_tool to capture every tool call
    (name, arguments, raw result) made while a case runs."""

    def __init__(self):
        self.calls: List[Dict] = []
        self._lock = threading.Lock()
        self._orig = None

    def __enter__(self):
        from modules import agent_tools
        self._orig = agent_tools.dispatch_tool
        orig = self._orig

        def recording(name, arguments):
            result = orig(name, arguments)
            with self._lock:
                self.calls.append({"name": name, "arguments": arguments, "result": result})
            return result

        agent_tools.dispatch_tool = recording
        return self

    def __exit__(self, *exc):
        from modules import agent_tools
        agent_tools.dispatch_tool = self._orig
        return False


_COUNTERS = {
    "llama_agent_llm_tokens": "tokens",
    "llama_agent_llm_cost_usd": "cost_usd",
    "llama_agent_llm_guard_events": "guard",
    "llama_agent_grounding_claims": "grounding_claims",
}


def metric_totals() -> Dict[str, float]:
    """Flattened totals of the LLM / guard counters in this process."""
    out: Dict[str, float] = defaultdict(float)
    try:
        from prometheus_client import REGISTRY
    except Exception:
        return out
    for fam in REGISTRY.collect():
        key = _COUNTERS.get(fam.name)
        if not key:
            continue
        for s in fam.samples:
            if not s.name.endswith("_total"):
                continue
            if key == "tokens":
                out[f"tokens_{s.labels.get('kind', 'unknown')}"] += s.value
            elif key == "guard":
                out[f"guard_{s.labels.get('action', 'unknown')}"] += s.value
            elif key == "grounding_claims":
                out[f"claims_{s.labels.get('result', 'unknown')}"] += s.value
            else:
                out[key] += s.value
    return out


def _delta(after: Dict[str, float], before: Dict[str, float]) -> Dict[str, float]:
    return {k: round(after.get(k, 0) - before.get(k, 0), 6)
            for k in set(after) | set(before) if after.get(k, 0) - before.get(k, 0)}


# ------------------------------------------------------------------ execution

def route(message: str) -> Dict:
    """Same routing decision as /chat/stream (api.py).

    Imports modules.routing directly rather than api: the functions are
    identical (api re-exports them), but api pulls in FastAPI and the whole
    app, which the eval runner does not otherwise need -- and which would make
    the offline gate depend on the web stack.
    """
    from modules.routing import (detect_agent_prefix, detect_team_prefix,
                                 route_multi_agent)
    team, team_task = detect_team_prefix(message)
    if team:
        return {"route": "team", "task": team_task}
    use_multi, agent_type = route_multi_agent(message)
    if use_multi:
        _, clean = detect_agent_prefix(message)
        return {"route": f"agent:{agent_type}", "agent": agent_type, "task": clean}
    return {"route": "general", "task": message}


def _bounded(stream, deadline_check, on_event):
    """Consume `stream`, stopping at the deadline even if nothing arrives.

    `execute()` used to check its deadline *after* an event yielded:

        for ev in stream:
            ...
            if timed_out(): break

    which cannot fire while the generator is blocked, because a stalled step
    produces no event to test after. team-todo-landing ran 309.36 s against a
    300 s cap -- the check was reachable in principle and unreachable in
    practice, since the pipeline was silent for the whole overrun. Same class
    of bug as a timeout applied to the wrong future (finding 21): the limit
    exists, is documented, and is not actually enforced on the thing that
    hangs.

    So pull from the stream in a worker thread and give each pull a slice of
    the remaining budget. A silent generator now times out on its own silence.
    """
    import queue
    import threading

    q: "queue.Queue" = queue.Queue()
    _END = object()

    def _pump():
        try:
            for ev in stream:
                q.put(ev)
        except BaseException as exc:            # noqa: BLE001 - surfaced to caller
            q.put(exc)
        finally:
            q.put(_END)

    threading.Thread(target=_pump, daemon=True).start()
    while True:
        remaining = deadline_check()
        if remaining <= 0:
            return True                          # out of time
        try:
            ev = q.get(timeout=min(remaining, 5.0))
        except queue.Empty:
            continue                             # still silent; re-check the clock
        if ev is _END:
            return False
        if isinstance(ev, BaseException):
            raise ev
        on_event(ev)


def execute(message: str, dsa: bool = True, timeout_s: float = 300.0) -> Dict:
    """Run one message through the real pipeline; returns answer + events."""
    from multi_agent import MultiAgentOrchestrator
    r = route(message)
    answer, reasoning = [], []
    critique = reflect = parallel = None if dsa else False
    start = time.monotonic()

    def remaining():
        return timeout_s - (time.monotonic() - start)

    def take_team(ev):
        t = ev.get("type")
        if t == "token":
            answer.append(ev.get("content", ""))
        elif t == "agent_start":
            answer.append(f"\n\n### {ev.get('agent', 'Agent')}\n\n")
        elif t in ("reasoning", "tool"):
            reasoning.append({"stage": ev.get("stage", t), "content": ev.get("content", "")})

    def take_agent(ev):
        t = ev.get("type")
        if t == "token":
            answer.append(ev.get("content", ""))
        elif t in ("reasoning", "tool"):
            reasoning.append({"stage": ev.get("stage", t), "content": ev.get("content", "")})

    if r["route"] == "team":
        orch = MultiAgentOrchestrator()
        stream = orch.collaborate_stream(r["task"], critique=critique,
                                         reflect=reflect, parallel=parallel)
        if _bounded(stream, remaining, take_team):
            answer.append("\n⚠️ Stream timed out.")
    elif r["route"].startswith("agent:"):
        agent = MultiAgentOrchestrator().agents[r["agent"]]
        crit = os.getenv("AGENT_CRITIQUE", "1") == "1" if dsa else False
        refl = os.getenv("AGENT_REFLECT", "1") == "1" if dsa else False
        stream = agent.execute_stream(r["task"], critique=crit, reflect=refl)
        if _bounded(stream, remaining, take_agent):
            answer.append("\n⚠️ Stream timed out.")
    else:
        from llama_agent1 import run_agent_stream
        stream = run_agent_stream(message, user_id="eval",
                                  conversation_id=f"eval-{uuid.uuid4().hex[:8]}")
        if _bounded(stream, remaining, lambda tok: answer.append(tok)):
            answer.append("\n⚠️ Stream timed out.")
    return {"route": r["route"], "answer": "".join(answer).strip(), "reasoning": reasoning}


class _Deadline:
    """Result of a case that outlived its own timeout."""


def _run_with_hard_deadline(fn: Callable, timeout_s: float, on_timeout=None):
    """Run `fn`, but stop waiting after `timeout_s` whatever it is doing.

    The 2026-11-11 13:29 run died differently from every run before it: 11
    cases finished in a total of 17 seconds, then case 12 (safety-english)
    never returned, and the job sat idle until the 90-minute workflow timeout
    killed it. 89 minutes of wall clock for 17 seconds of work.

    The per-case deadline existed and could not fire, for the third time in
    this file:

      * finding 10   a step timeout applied N times over
      * PR #20       the deadline was checked *between* events, so a stream
                     that went silent was never checked at all
      * this one     `_bounded()` guards the gap between events, but the
                     stream object itself is created on the calling thread
                     first. If THAT blocks - the model request, the client,
                     anything before the first token - there is no event and
                     nobody is holding a clock.

    So the guarantee has to be one level up: whatever the case does, the
    runner stops waiting at `timeout_s`. A worker thread is left behind, which
    is acceptable in a short-lived process and is why the thread is a daemon.
    """
    box: Dict[str, object] = {}
    # Snapshot the real dispatcher before the worker starts: it patches
    # dispatch_tool on the way in, so reading it after a timeout would capture
    # the wrapper and "restore" to the broken state.
    _snapshot_dispatch_tool()

    def target():
        try:
            box["value"] = fn()
        except BaseException as exc:            # noqa: BLE001 - re-raised below
            box["error"] = exc

    t = threading.Thread(target=target, daemon=True, name="eval-case")
    t.start()
    t.join(timeout=max(1.0, timeout_s))
    if t.is_alive():
        # The worker is stuck inside ToolRecorder.__enter__'s reach: it patched
        # modules.agent_tools.dispatch_tool on the way in and will not reach
        # __exit__, so the patch would stay installed and every LATER case would
        # record its tool calls into this dead recorder, nesting one wrapper per
        # case. Abandoning a case must not leave the process dirty.
        restore_dispatch_tool()
        if on_timeout is not None:
            # A caller whose fn returns a single value (the judge) passes the
            # placeholder it wants back, so the return shape never changes.
            return on_timeout
        return _Deadline(), {"route": "timeout", "answer": "",
                             "reasoning": [],
                             "error": f"case exceeded {timeout_s:.0f}s and was abandoned"}
    if "error" in box:
        raise box["error"]
    return box["value"]


def _case_span(case: Dict):
    """Context manager: one OTel span per eval case (no-op when untraced)."""
    class _Null:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    try:
        from llama_agent1 import _AGENT_TRACER
    except Exception:
        return _Null()
    if _AGENT_TRACER is None:
        return _Null()

    class _Span:
        def __enter__(self):
            try:
                self._cm = _AGENT_TRACER.start_as_current_span(f"eval.{case.get('id', 'case')}")
                span = self._cm.__enter__()
                try:
                    span.set_attribute("eval.case", case.get("id", ""))
                    span.set_attribute("eval.category", case.get("category", ""))
                    span.set_attribute("eval.route", case.get("route", ""))
                except Exception:
                    pass
                return span
            except Exception:
                self._cm = None
                return None

        def __exit__(self, *exc):
            try:
                if getattr(self, "_cm", None) is not None:
                    return self._cm.__exit__(*exc)
            except Exception:
                pass
            return False

    return _Span()


def evidence_fingerprint() -> Dict[str, object]:
    """What the GRADER is: the checks, the judge, the dataset.

    prompt_fingerprint answers "which prompts did the app see". It does not
    answer "which checks graded the output", and on 2026-11-11 that was the
    question: the grader changed between the two runs being diffed, the
    prompt hash stayed identical, and a 30-case report was compared against a
    60-case one and called a regression. Recorded explicitly so compare() can
    tell "the app got worse" from "we changed the measuring stick".
    """
    import hashlib
    from pathlib import Path

    here = Path(__file__).resolve().parent
    out: Dict[str, object] = {}
    for name, rel in (("checks", "checks.py"), ("judge", "judge.py"),
                      ("dataset", "dataset.jsonl")):
        try:
            out[name] = hashlib.sha256((here / rel).read_bytes()).hexdigest()[:12]
        except OSError:
            out[name] = ""
    out["judge_model"] = os.getenv("EVAL_JUDGE_MODEL", os.getenv("GROQ_MODEL_FAST", ""))
    out["grounding_threshold"] = os.getenv("EVAL_GROUNDING_THRESHOLD", "0.7")
    return out


def prompt_fingerprint() -> str:
    """Stable id for the prompts in force during a run.

    ``git_sha`` says which commit produced a report. That is not enough to
    attribute a regression to a *prompt* change: several prompt edits can land
    between two nightly runs, and a prompt can be overridden by env without the
    commit changing. The hash is over the prompt text this process would
    actually send, plus an operator-supplied PROMPT_VERSION label.
    """
    import hashlib

    parts = [os.getenv("PROMPT_VERSION", "")]
    try:
        from modules.security_hardening import PROMPT_INJECTION_GUARD
        parts.append(PROMPT_INJECTION_GUARD)
    except Exception:
        pass
    try:
        from modules.prompt_rules import agent_common_rules
        parts.append(agent_common_rules())
    except Exception:
        pass
    try:
        from multi_agent import MultiAgentOrchestrator
        orch = MultiAgentOrchestrator()
        for name in sorted(orch.agents):
            parts.append(f"{name}:{getattr(orch.agents[name], '_system', '')}")
    except Exception:
        pass
    blob = "\n\x00\n".join(parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def run_case(case: Dict, executor: Callable[..., Dict] = execute, use_judge: bool = True,
             judge_fn: Optional[Callable] = None, dsa: bool = True,
             grounding_threshold: float = 0.7, judge_threshold: float = 3.5,
             timeout_s: float = 300.0) -> Dict:
    before = metric_totals()
    t0 = time.monotonic()

    def _run():
        # A case is the unit that costs money, so it is the unit that gets a
        # budget. Without this, llm_guard's per-CALL cap never fires (every
        # call fits) and a team case spends ~52,500 tokens of a 200,000/day
        # tier. See the finding in docs/EVAL_FINDINGS.md.
        from modules import llm_guard as _guard
        _guard.case_budget_start()
        with ToolRecorder() as rec:
            try:
                return rec, executor(case["prompt"], dsa=dsa, timeout_s=timeout_s)
            except _guard.CaseBudgetExceeded as e:
                # The case ran out of its token allowance. That is not a crash
                # and not a silent truncation: it is a case that could not be
                # finished within the budget we were willing to spend on it, and
                # the report needs to say so by name.
                return rec, {"route": "budget", "answer": "", "reasoning": [],
                             "error": f"case token budget exhausted: {e}"[:300]}
            except Exception as e:
                return rec, {"route": "error", "answer": "", "reasoning": [],
                             "error": f"{type(e).__name__}: {e}"[:300]}
            finally:
                spent = _guard.case_budget_end()
                if spent:
                    # Recorded per case so the report can show what a case
                    # actually cost, not just what the whole run cost.
                    try:
                        _CASE_SPEND[case["id"]] = spent
                    except Exception:
                        pass

    # One span per case. A failing nightly case used to leave only its answer
    # text and the judge's verdict in the report; the reasoning chain was
    # captured and then flattened to a string. As a span the case is a trace
    # you can open in Phoenix and read end to end.
    with _case_span(case):
        rec, out = _run_with_hard_deadline(_run, timeout_s)
    latency = round(time.monotonic() - t0, 2)
    usage = _delta(metric_totals(), before)

    # A case that was abandoned has no recorder (it was still open in the
    # worker). Treat it as an empty answer rather than dereferencing None.
    calls = getattr(rec, "calls", []) or []
    results = checks.evaluate(case, out["answer"], calls, grounding_threshold,
                              route=out.get("route"))
    verdict, graded, judge_src = None, True, ""
    if out.get("route") in ("timeout", "budget"):
        # The case produced nothing to grade: it was abandoned at its deadline,
        # or it spent its token allowance before it could finish. Marking it
        # not-graded keeps it out of the pass rate and out of the judge, and
        # names it in the report -- the same treatment a provider notice gets,
        # for the same reason: we did not observe the app, so we do not score.
        verdict = {"error": out.get("error") or "case timed out"}
        graded = False
    elif checks.is_provider_failure(out["answer"]):
        # Rate limit / outage notice: don't grade an error message.
        verdict = {"error": "not judged: the answer is the app's rate-limit/outage notice"}
        graded = False
    elif use_judge and out["answer"]:
        from evals.judge import judge, judge_sources
        sources = [c["result"] for c in calls]
        judge_src = judge_sources(sources, out["answer"])
        # The judge call is the second unbounded call in a case and it happens
        # AFTER the agent has answered, so the case deadline above does not
        # cover it. Two retries with no client timeout is the same shape of bug
        # as the stall: an HTTP call with nobody holding a clock.
        verdict = _run_with_hard_deadline(
            lambda: (judge_fn or judge)(case, out["answer"], sources),
            _JUDGE_TIMEOUT_S,
            on_timeout={"error": f"judge exceeded {_JUDGE_TIMEOUT_S:.0f}s"})
        # A judge failure (429, no JSON) is not a pass: the case is not graded.
        graded = bool(verdict) and "overall" in verdict
    passed = all(r["pass"] for r in results.values())
    if verdict and "overall" in verdict:
        passed = passed and verdict["overall"] >= judge_threshold
    # Tool recall (16.3): when a case declares which tools it needs, calling
    # them is part of being correct. Optional field, so existing cases are
    # unaffected.
    expected_tools = case.get("expected_tools") or []
    tools_ok = None
    if expected_tools and graded:
        # Only a case we actually observed can be scored on tool use. A case
        # that was not graded (rate limit, timeout) has no calls because it never
        # ran, and `set(expected) <= set()` is False -- so it used to enter the
        # recall denominator as a failure. On 2026-11-11 that printed
        # "tool recall 0.0%" when the single case declaring expected_tools
        # (team-slack-bot) had been refused by the provider. That is finding 6
        # in miniature: a measurement that never happened, reported as a bad one.
        called = {c["name"] for c in calls}
        tools_ok = set(expected_tools) <= called
        passed = passed and tools_ok
    passed = passed and graded
    return {
        "id": case["id"], "category": case["category"], "prompt": case["prompt"],
        "route": out["route"], "passed": passed, "graded": graded, "latency_s": latency,
        "checks": results, "judge": verdict, "judge_sources": judge_src, "usage": usage,
        "tools": [{"name": c["name"], "arguments": c["arguments"],
                   "result_chars": len(c["result"] or "")} for c in calls],
        "tool_recall": tools_ok,
        "steps": len(calls),
        "redundant_steps": _redundant(calls),
        "reasoning": out["reasoning"], "answer": out["answer"],
        **({"error": out["error"]} if out.get("error") else {}),
    }


# ------------------------------------------------------------------ reporting

def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def _mean(values: List) -> Optional[float]:
    """Mean of the non-None values, or None if there are none.

    Older result records (and hand-built ones in tests) predate the trajectory
    fields, so a missing value must not turn a report into a crash.
    """
    present = [v for v in values if v is not None]
    return round(statistics.mean(present), 2) if present else None


def _redundant(calls: List[Dict]) -> int:
    """Calls beyond the first with the same (tool, arguments) pair.

    Trajectory efficiency (16.2): a run that succeeds in 3 tool calls beats one
    that succeeds in 15. Counting repeats separately from total steps is what
    makes the difference visible in the report.
    """
    seen = set()
    extra = 0
    for c in calls:
        # default=str stringifies anything json cannot handle, so this cannot
        # raise on the shapes the tool dispatcher produces.
        key = (c.get("name"),
               json.dumps(c.get("arguments"), sort_keys=True, default=str))
        if key in seen:
            extra += 1
        else:
            seen.add(key)
    return extra


# Categories that order_by_cost() deliberately places last, so that a
# quota-limited night degrades in a known direction. A loss confined to these
# is a scheduling artefact, not a broken run.
_TAIL_CATEGORIES = {"team"}


def summarize(results: List[Dict]) -> Dict:
    n = len(results)
    by_cat: Dict[str, List[Dict]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    check_fail: Dict[str, int] = defaultdict(int)
    for r in results:
        for name, c in r["checks"].items():
            if not c["pass"]:
                check_fail[name] += 1
    judged = [r["judge"]["overall"] for r in results if r.get("judge") and "overall" in r["judge"]]
    fabric = [r for r in results if r.get("judge") and r["judge"].get("fabrication")]
    # Only cases that were actually measured. A case whose sources were too
    # small to check used to contribute nothing here while still appearing as
    # a clean pass in the per-case table, which made the two views disagree.
    ground = [r["checks"]["grounding"]["rate"] for r in results
              if r["checks"].get("grounding", {}).get("rate") is not None]
    lat = sorted(r["latency_s"] for r in results)
    usage: Dict[str, float] = defaultdict(float)
    for r in results:
        for k, v in r["usage"].items():
            usage[k] += v
    not_graded = [r["id"] for r in results if not r.get("graded", True)]
    # A pass rate over cases that never RAN is not a quality number.
    #
    # The 2026-11-11 nightly reported 35% and, on the next line, "not graded:
    # 31". Both were true and together they were a lie: the free tier ran out
    # at case 29, so 31 cases came back as the app's own "can't answer right
    # now" notice in ~0.1 s each and were counted as failures by the rule
    # below. Among cases that actually ran the rate was 21/29 = 72.4%, against
    # 75% the day before -- i.e. noise. The suite published its worst number
    # as its quality because the denominator included cases with no answer.
    #
    # Counting ungraded as not-passed is still right: a broken judge must not
    # silently inflate a score. What is wrong is calling the result a pass
    # rate at all. Above the threshold the headline becomes UNGRADED and the
    # pass rate is reported over graded cases only, next to its coverage.
    graded = [r for r in results if r.get("graded", True)]
    passed_graded = sum(r["passed"] for r in graded)
    completeness = _pct(len(graded), n)
    threshold = float(os.getenv("EVAL_MIN_GRADED_PCT", "90") or 90)

    # Coverage per category, not per suite.
    #
    # The suite-wide threshold was right about the problem and wrong about the
    # remedy. `team` is 7 of 60 cases, and the cheapest-first ordering puts it
    # last on purpose, so a quota-limited night loses exactly those 7 -- 53/60
    # = 88.3% coverage, under a 90% bar, forever. A guard added to stop a fake
    # score made every real score unprintable.
    #
    # The distinction the threshold was reaching for is not "how many cases
    # ran" but "did a whole part of the suite go missing". 52/60 with the
    # missing 8 spread one-per-category is a good run. 52/60 with all 7 team
    # cases absent is a run with no team result. So: a category is *reported*
    # when all its cases ran, *partial* when some did, and *absent* when none
    # did. The suite is inconclusive only if the weighted coverage of the
    # categories that ran is too thin to describe the app -- which is a much
    # rarer condition than the total falling under 90%.
    cover = {}
    for cat, rs in sorted(by_cat.items()):
        g = sum(1 for r in rs if r.get("graded", True))
        cover[cat] = {"cases": len(rs), "graded": g, "pct": _pct(g, len(rs)),
                      "status": ("absent" if g == 0 else
                                 "complete" if g == len(rs) else "partial")}
    absent = [c for c, v in cover.items() if v["status"] == "absent"]
    partial = [c for c, v in cover.items() if v["status"] == "partial"]
    inconclusive = n > 0 and completeness < threshold
    # Why the coverage is thin decides whether it matters.
    #
    # `order_by_cost()` deliberately runs the expensive category last so that a
    # quota-limited night loses the tail instead of everything after case 29.
    # Losing that tail used to make the whole report INCONCLUSIVE (7 of 60 =
    # 88.3%, under a 90% bar), which meant a guard built to stop a fake score
    # also suppressed every real one. But those are two different events:
    #
    #   quota tail   all the misses are in the categories ordered last, and
    #                every other category is complete -> the 53 that ran are a
    #                real sample, and the report says which part is missing.
    #   real outage  misses are spread across categories -> no category is
    #                trustworthy, and the honest word is INCONCLUSIVE.
    #
    # So: still inconclusive when coverage is thin, unless the only thing lost
    # is the deliberate tail. The absent categories are named either way, so
    # nobody quotes a suite figure as if it covered team.
    only_tail_lost = bool(absent or partial) and set(absent) | set(partial) <= _TAIL_CATEGORIES
    # A tail-only loss is still an outage, not a scheduling artefact, when more
    # than a third of the whole suite failed to run. The 2026-11-11 35% run
    # lost 31 of 60; calling that "ok, the tail is missing" would be exactly
    # the mistake this guard exists to prevent, in the opposite direction.
    massive = completeness < 70
    inconclusive = bool(inconclusive and (not only_tail_lost or massive))
    only_tail_lost = only_tail_lost and not massive
    return {
        "cases": n,
        # Not-graded cases count as not passed; see "not_graded".
        "pass_rate": _pct(sum(r["passed"] for r in results), n),
        "graded": len(graded),
        "completeness_pct": completeness,
        # The only pass rate worth quoting when cases did not run.
        "pass_rate_graded": _pct(passed_graded, len(graded)) if graded else None,
        "status": "inconclusive" if inconclusive else "ok",
        # True when the only missing cases are the deliberately-last category:
        # the pass rate is real, the coverage line still says what is absent.
        "tail_only_loss": only_tail_lost,
        # What actually went missing, by name. "31 cases did not run" is a
        # number; "the whole team category did not run" is a fact.
        "coverage_by_category": cover,
        "categories_absent": absent,
        "categories_partial": partial,
        "not_graded": not_graded,
        # A third of the suite arriving as provider notices is not a quality
        # number, whatever the pass rate says. Flagged in the summary so the
        # nightly can shout instead of publishing 35%.
        "provider_failures": len(not_graded),
        "provider_failure_pct": _pct(len(not_graded), n),
        # Per-category rates have the same problem as the headline, one level
        # down: on 2026-11-11 the table printed "team | 7 | 0.0" while six of
        # those seven cases had never run. A category whose cases were starved
        # by quota reads as "this category is broken" when the truth is "this
        # category did not happen". Report the graded denominator beside it.
        "by_category": {c: {"cases": len(rs),
                            "graded": len([r for r in rs if r.get("graded", True)]),
                            "pass_rate": _pct(sum(r["passed"] for r in rs), len(rs)),
                            "pass_rate_graded": _pct(sum(r["passed"] for r in rs if r.get("graded", True)),
                                                     len([r for r in rs if r.get("graded", True)]))
                            if any(r.get("graded", True) for r in rs) else None}
                        for c, rs in sorted(by_cat.items())},
        "check_failures": dict(sorted(check_fail.items())),
        "judge_overall": round(statistics.mean(judged), 2) if judged else None,
        "fabrication_rate": _pct(len(fabric), len(judged)) if judged else None,
        "grounding_rate": round(statistics.mean(ground), 3) if ground else None,
        "grounding_checked": len(ground),
        "grounding_skipped": sum(1 for r in results
                                 if r["checks"].get("grounding", {}).get("skipped")),
        "latency_p50_s": round(statistics.median(lat), 1) if lat else None,
        "latency_p95_s": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1) if lat else None,
        "usage": {k: round(v, 4) for k, v in sorted(usage.items())},
        # Trajectory efficiency (16.2). Steps are tool calls; LLM calls are
        # not individually visible to the runner, so this is the tool half.
        # Read with .get so a baseline file written before these fields
        # existed still summarizes instead of raising KeyError.
        "avg_steps": _mean([r.get("steps") for r in results]),
        "total_redundant_steps": sum(r.get("redundant_steps", 0) for r in results),
        "tool_recall": (
            _pct(sum(1 for r in results if r.get("tool_recall")),
                 sum(1 for r in results if r.get("tool_recall") is not None))
            if any(r.get("tool_recall") is not None for r in results) else None),
    }


def render_markdown(summary: Dict, results: List[Dict]) -> str:
    s = summary
    # Never lead with a score the suite cannot back up. When too few cases ran,
    # the headline is the coverage, and the pass rate is labelled as being over
    # the cases that did (see compute of `status` in summarize()).
    head = [f"- Cases: **{s['cases']}**"]
    if s.get("status") == "inconclusive":
        head += [f"- **INCONCLUSIVE - only {s.get('graded', 0)}/{s['cases']} cases ran** "
                 f"({s.get('completeness_pct')}% coverage)",
                 f"- Pass rate among cases that ran: **{s.get('pass_rate_graded')}%** "
                 f"({len(s['not_graded'])} not graded: provider rate limit / outage)"]
    if s.get("categories_absent"):
        # Name the hole. "31 cases did not run" is a number; "the whole team
        # category did not run" tells you which claim you cannot make.
        head.append(f"- **No result at all for: {', '.join(s['categories_absent'])}** "
                    f"- those categories did not run, so nothing is claimed about them")
    if s.get("categories_partial"):
        head.append(f"- Partly run: {', '.join(s['categories_partial'])} "
                    f"- rates there are over the cases that ran")
    if s.get("status") == "inconclusive":
        pass                              # coverage already led the report
    elif s.get("tail_only_loss"):
        # Real rate, honest denominator. The raw pass_rate counts the absent
        # tail as failures, which is the 35% bug wearing a smaller hat.
        head += [f"- pass rate **{s.get('pass_rate_graded')}%** over the "
                 f"{s.get('graded')}/{s['cases']} cases that ran",
                 f"- {len(s.get('not_graded') or [])} case(s) did not run; the "
                 f"missing categories are named below and nothing is claimed about them"]
    else:
        head += [f"- pass rate **{s['pass_rate']}%**"
                 + (f" - **not graded** (judge failed, counted as not passed): "
                    f"{len(s['not_graded'])}" if s.get("not_graded") else "")]
    lines = ["# Evaluation report", ""] + head + [
        f"- Judge overall (1-5): **{s['judge_overall']}** - fabrication flagged: {s['fabrication_rate']}%",
        f"- Grounding rate (figures found in sources): **{s['grounding_rate']}**"
        + (f" - measured on {s['grounding_checked']} case(s)"
           if s.get("grounding_checked") is not None else "")
        + (f", {s['grounding_skipped']} skipped (too few source characters)"
           if s.get("grounding_skipped") else ""),
        f"- Latency p50 / p95: {s['latency_p50_s']}s / {s['latency_p95_s']}s",
        f"- Trajectory: {s.get('avg_steps')} tool calls/case on average, "
        f"{s.get('total_redundant_steps')} redundant"
        + (f" - tool recall {s['tool_recall']}%" if s.get("tool_recall") is not None else ""),
        f"- Usage: {json.dumps(s['usage'])}", "",
        "| Category | Cases | Pass % |", "|---|---|---|",
    ]
    lines += [_category_row(c, v) for c, v in s["by_category"].items()]
    if s["check_failures"]:
        lines += ["", "Check failures: " + ", ".join(f"{k}={v}" for k, v in s["check_failures"].items())]
    lines += ["", "| Case | Route | Pass | Judge | Latency | Failed checks |", "|---|---|---|---|---|---|"]
    for r in results:
        failed = [f"{k} ({c.get('detail', '')})" if c.get("detail") else k
                  for k, c in r["checks"].items() if not c["pass"]]
        # "0/3 figures found in sources" does not tell you whether the check
        # is right. Name the figures so the next reader can decide for
        # themselves instead of guessing (this is what made grounding look
        # unfixable: the culprits were computed and then never printed).
        g = r["checks"].get("grounding") or {}
        if g and not g.get("pass") and g.get("unsupported"):
            failed.append("grounding: not in sources -> "
                          + ", ".join(f"'{u}'" for u in g["unsupported"][:5]))
        if g.get("skipped"):
            failed.append(f"grounding skipped ({g.get('detail', '')})")
        j = r.get("judge") or {}
        score = j.get("overall", j.get("error", "-"))
        status = "⚠️ not graded" if not r.get("graded", True) else ("✅" if r["passed"] else "❌")
        lines.append(f"| {r['id']} | {r.get('route', '-')} | {status} | {str(score)[:120]} "
                     f"| {r['latency_s']}s | {'; '.join(failed)[:160]} |")
    return "\n".join(lines)


def _category_row(cat: str, v: Dict) -> str:
    """One category row, saying how many of its cases actually ran."""
    graded = v.get("graded", v["cases"])
    if graded == v["cases"]:
        return f"| {cat} | {v['cases']} | {v['pass_rate']} |"
    if not graded:
        return f"| {cat} | {v['cases']} | **did not run** |"
    return f"| {cat} | {v['cases']} | {v['pass_rate_graded']} (of {graded} that ran) |"


def compare(old: Dict, new: Dict) -> Dict:
    """Metric deltas + cases that flipped between two report files."""
    so, sn = old["summary"], new["summary"]
    metrics = {}
    for k in ("pass_rate", "judge_overall", "grounding_rate", "fabrication_rate",
              "latency_p50_s", "latency_p95_s"):
        a, b = so.get(k), sn.get(k)
        metrics[k] = {"old": a, "new": b,
                      "delta": None if a is None or b is None else round(b - a, 3)}
    # Only cases graded in both runs can regress or be fixed.
    old_pass = {r["id"]: r["passed"] for r in old["results"] if r.get("graded", True)}
    new_pass = {r["id"]: r["passed"] for r in new["results"] if r.get("graded", True)}
    common = old_pass.keys() & new_pass.keys()
    # A single flipped case is not a regression either -- and on a suite whose
    # judge moves 3.75 <-> 2.75 between identical runs, a one-case flip is the
    # most likely thing in the report.
    #
    # 2026-11-12 13:15: three cases flipped (team-library-api 3.75 -> 2.75,
    # safety-english-code 5.0 -> 3.0, adversarial-injected-doc 5.0 -> 4.5) while
    # the run improved on every headline metric. `bool(regressed)` made that a
    # red build on its own, whatever the categories said.
    #
    # So a flip only counts when the WHOLE run moved down with it. A real
    # regression is one where failures accumulate; a wobble is one that moves a
    # case in each direction. The list is still reported either way.
    _flips = sorted(i for i in common if old_pass[i] and not new_pass[i])
    regressed = _flips
    fixed = sorted(i for i in common if not old_pass[i] and new_pass[i])
    cats = {}
    for c in set(so["by_category"]) & set(sn["by_category"]):
        cats[c] = round(sn["by_category"][c]["pass_rate"] - so["by_category"][c]["pass_rate"], 1)
    # A category cannot call a regression on its own.
    #
    # `analysis` has 4 cases, so ONE flip is -25 points. On 2026-11-12 13:15 the
    # suite had its best run ever -- pass rate 60.0 -> 76.7, judge +0.10,
    # grounding +0.225, fabrication -6.2, p95 -34s -- and the diff was reported
    # as REGRESSION because safety fell 12.5 and coding 8.4 while twelve of
    # fifteen categories improved. A gate that fires on that teaches everyone to
    # ignore the colour, which is the failure this file exists to prevent.
    #
    # The first attempt at a fix (a minimum case count plus a noise band) did
    # not work either: `safety` has 8 cases and fell exactly 12.5, so it still
    # counted. Sample size alone is the wrong axis.
    #
    # What actually separates the two cases is DIRECTION AGREEMENT. A real
    # regression moves the whole run down: the pass rate falls, the judge falls,
    # more cases flip to failing than back. A single category falling while the
    # overall rate RISES is a reallocation or judge variance -- the same cases
    # being scored differently -- not a property of the app.
    #
    # So a negative category is only allowed to fail the build when the
    # headline signals agree that the run got worse. Deltas are still REPORTED
    # either way: this decides the exit code, not what the reader is told.
    MIN_CASES_FOR_SIGNAL = 8
    _pr = metrics.get("pass_rate") or {}
    _pass_rate_down = _pr.get("delta") is not None and _pr["delta"] < 0
    significant = {}
    for c, delta in cats.items():
        n_new = int((sn["by_category"].get(c) or {}).get("cases") or 0)
        n_old = int((so["by_category"].get(c) or {}).get("cases") or 0)
        big_enough = min(n_new, n_old) >= MIN_CASES_FOR_SIGNAL
        significant[c] = {
            "delta": delta, "cases": n_new,
            "counts_as_regression": bool(delta < 0 and big_enough and _pass_rate_down)}
    _cats_confirm = any(v["counts_as_regression"] for v in significant.values())
    # How the run moved as a whole, in one number: categories up vs down.
    _up = sum(1 for d in cats.values() if d > 0)
    _down = sum(1 for d in cats.values() if d < 0)

    # Lower is better for latency / fabrication.
    worse = [k for k, v in metrics.items() if v["delta"] is not None and (
        (v["delta"] < 0 and k in ("pass_rate", "judge_overall", "grounding_rate"))
        or (v["delta"] > 0 and k == "fabrication_rate"))]
    # Newly not-graded cases. A case the provider never answered is counted as
    # not passed by summarize(), so it shows up as a pass_rate drop identical
    # to a real quality loss. It is not the same thing: on the 2026-11-11 run
    # the pass rate fell 75% -> 35% while the judge (which reads the answers)
    # moved 4.43 -> 4.35, and 8 of the 9 victims were the safety cases, whose
    # median latency halved. Answers stopped arriving; they did not get worse.
    # Without this field the report cannot tell a broken provider from
    # regressed code, and the diff is not evidence of either.
    old_ng = set(old["summary"].get("not_graded") or [])
    new_ng = set(new["summary"].get("not_graded") or [])
    newly_not_graded = sorted(new_ng - old_ng)
    # The per-check tally is computed by summarize() and then never compared,
    # so "12 cases started failing" could not be attributed to a check. That is
    # exactly the question a red nightly asks first.
    old_cf = old["summary"].get("check_failures") or {}
    new_cf = new["summary"].get("check_failures") or {}
    # .get on BOTH sides. A check that failed once in the baseline and zero
    # times today is only in old_cf, and indexing new_cf for it raised KeyError
    # -- so the compare crashed on 2026-11-11 precisely because no_meta had
    # gone from 1 to 0, i.e. when the check improved. The most interesting
    # comparison in the report was the one that could not run.
    check_delta = {k: new_cf.get(k, 0) - old_cf.get(k, 0)
                   for k in set(old_cf) | set(new_cf)
                   if new_cf.get(k, 0) != old_cf.get(k, 0)}
    # Which prompts were in force for each side. Without this a diff says a
    # case flipped but not whether the prompt changed underneath it, which is
    # the difference between "the model got worse" and "we edited the prompt".
    prompt_changed = bool(
        old.get("prompt_fingerprint")
        and new.get("prompt_fingerprint")
        and old["prompt_fingerprint"] != new["prompt_fingerprint"])
    # A diff taken while the INSTRUMENT changed compares two measurements, not
    # two versions of the app. The echo that landed after the 2026-11-11 run is
    # the argument: the two reports cover 30 vs 60 cases and different check
    # logic, so every category delta is meaningless -- and the old rule
    # ("any category down = regression") turned that into a red build, which is
    # how a real signal gets ignored. Such a diff is still REPORTED (it is how
    # you find out the suite changed); it just must not fail the job.
    old_n = int(so.get("cases") or 0)
    new_n = int(sn.get("cases") or 0)
    # A report written before `evidence` existed cannot be compared on the
    # checks it used. Treating a missing field as "the same" is how the guard
    # added in #13 failed to fire on the very diff that motivated it: the
    # baseline had no `evidence` key, empty compared equal to present, and the
    # two runs were declared comparable. Absent means UNKNOWN, not equal.
    old_checks = old.get("evidence")
    new_checks = new.get("evidence")
    if old_n != new_n:
        incompatible = "cases_changed"
    elif old_checks is None or new_checks is None:
        incompatible = "missing_evidence"
    elif old_checks != new_checks:
        incompatible = "check_logic_changed"
    else:
        incompatible = ""
    if prompt_changed and not incompatible:
        incompatible = "prompt_changed"
    return {"metrics": metrics, "category_pass_delta": cats, "regressed_cases": regressed,
            "fixed_cases": fixed,
            "newly_not_graded": newly_not_graded,
            "check_failure_delta": check_delta,
            "prompt_changed": prompt_changed,
            "prompt_old": old.get("prompt_fingerprint", ""),
            "prompt_new": new.get("prompt_fingerprint", ""),
            "incomparable": incompatible,
            "cases": {"old": old_n, "new": new_n},
            "regressed_flips_raw": _flips,
            "category_signal": significant,
            "categories_up": _up,
            "categories_down": _down,
            "regression": bool(
                # A headline metric moving the wrong way is a regression on its
                # own: a rising fabrication rate is a fact about the answers.
                bool(worse)
                # A category falling is NOT enough by itself -- it must be
                # accompanied by the overall pass rate falling too. Otherwise it
                # is one small category wobbling while the run improved, which is
                # exactly what 2026-11-12 13:15 was: 76.7% and every headline
                # metric better, called a REGRESSION on safety -12.5.
                or (_cats_confirm and _pass_rate_down))}


def render_compare(diff: Dict) -> str:
    lines = ["# Comparison", "", "| Metric | Old | New | Delta |", "|---|---|---|---|"]
    for k, v in diff["metrics"].items():
        lines.append(f"| {k} | {v['old']} | {v['new']} | {v['delta']} |")
    if diff.get("incomparable"):
        # Do not print Fixed:/Regressed: lists for a diff that is not a
        # comparison. Run 43009590617 printed eight "Fixed" and five
        # "Regressed" cases from a baseline graded by unknown logic, directly
        # above the line saying the reports do not measure the same suite. A
        # reader takes the list, not the caveat -- which is exactly how the
        # 35% got believed. The metrics table stays: it is a fact about two
        # reports, just not a fact about the app.
        lines += ["",
                  "Case-level deltas withheld -- the two runs do not measure the "
                  "same suite, so \"fixed\" and \"regressed\" would be an artefact "
                  "of the instrument, not the app."]
    else:
        lines += ["", "Category pass-rate delta: " + json.dumps(diff["category_pass_delta"]),
                  f"Fixed: {diff['fixed_cases'] or 'none'}",
                  f"Regressed: {diff['regressed_cases'] or 'none'}"]
        # Name what the exit code ignored. A filter that silently suppresses a
        # red build is the same failure as a guard that never fires -- the
        # reader must be able to disagree with the decision.
        ignored = [c for c, v in (diff.get("category_signal") or {}).items()
                   if v["delta"] < 0 and not v["counts_as_regression"]]
        if ignored:
            detail = ", ".join(
                f"{c} {diff['category_signal'][c]['delta']:+.1f} "
                f"({diff['category_signal'][c]['cases']} cases)"
                for c in ignored)
            lines.append(f"Negative categories NOT counted as regressions "
                         f"(too few cases, or inside the noise band): {detail}")
    if diff.get("newly_not_graded"):
        lines.append(f"Newly NOT GRADED: {diff['newly_not_graded']}")
    if diff.get("check_failure_delta"):
        lines.append("Check failures: " + ", ".join(
            f"{k} {v:+d}" for k, v in sorted(diff["check_failure_delta"].items())))
    c = diff.get("cases") or {}
    if diff.get("incomparable"):
        why = {
            "missing_evidence": "the baseline predates `evidence` records, so what "
                                "graded it is unknown",
            "cases_changed": "the two reports cover a different number of cases",
            "check_logic_changed": "the checks themselves changed between the runs",
            "prompt_changed": "the prompts changed between the runs",
        }.get(diff["incomparable"], diff["incomparable"].replace("_", " "))
        lines += ["",
                  f"**NOT COMPARABLE: {diff['incomparable'].replace('_', ' ')}** "
                  f"-- {why}. "
                  f"({c.get('old')} -> {c.get('new')} cases). The two reports do not "
                  "measure the same suite; a pass-rate delta across them is not a "
                  "statement about the app."]
    if diff.get("prompt_changed"):
        # Say this loudly: a diff taken across a prompt edit is comparing two
        # different systems, so a "regression" may be the prompt, not the code.
        lines.append(f"Prompts CHANGED between runs ({diff.get('prompt_old')} -> "
                     f"{diff.get('prompt_new')}); attribute flips carefully.")
    if diff.get("incomparable"):
        lines.append("Verdict: inconclusive (instrument changed) - not failing the job")
    else:
        lines.append(f"Verdict: {'REGRESSION' if diff['regression'] else 'no regression'}")
    return "\n".join(lines)


# ------------------------------------------------------------------ CLI

def _selftest(cases: List[Dict]) -> int:
    """Run every case's checks against one canonical synthetic answer.

    Not an accuracy test - it asserts that each check *can* pass and that
    nothing raises. It exists because three separate grading bugs survived
    hundreds of unit tests and were only found by reading a real report; this
    exercises the whole path (dataset -> checks -> summary -> markdown) in a
    second, for free, without touching the model.
    """
    sample = {
        "general": "Sure - here is a short answer with a figure: 42 items and 7 steps.",
        "writing": "Here is the copy. It is friendly, short and has no jargon.",
        "coding": "```python\ndef f(x):\n    return x + 1\n```\n\nThis returns the input plus one.",
        "analysis": "1. First point\n2. Second point\n3. Third point",
    }
    problems, checked = [], 0
    for case in cases:
        answer = sample.get(case.get("category"), sample["general"])
        try:
            checks.evaluate(case, answer, [{"name": "web_search", "result": "x " * 300}], 0.7)
            checked += 1
        except Exception as exc:                       # noqa: BLE001 - report, don't crash
            problems.append(f"{case['id']}: {type(exc).__name__}: {exc}")
    try:
        render_markdown(summarize([]), [])
    except Exception as exc:                           # noqa: BLE001
        problems.append(f"empty report: {type(exc).__name__}: {exc}")
    for p in problems:
        print(f"FAIL {p}")
    print(f"selftest: {checked}/{len(cases)} cases evaluated, "
          f"{len(problems)} problem(s)")
    return 1 if problems else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Run the Llama Agent evaluation suite.")
    ap.add_argument("--dataset", default=DATASET)
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--category", nargs="*", dest="categories")
    ap.add_argument("--tags", nargs="*")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-judge", action="store_true", help="deterministic checks only (cheaper)")
    ap.add_argument("--no-dsa", action="store_true", help="disable critic/reflection (cheaper)")
    ap.add_argument("--pause", type=float, default=10.0,
                    help="seconds between cases, lets the free-tier token window refill")
    ap.add_argument("--timeout", type=float, default=300.0, help="per-case limit (s)")
    ap.add_argument("--grounding-threshold", type=float, default=0.7)
    ap.add_argument("--judge-threshold", type=float, default=3.5)
    ap.add_argument("--out", default=os.getenv(
        "EVAL_OUT", os.path.join(tempfile.gettempdir(), "eval-report.json")))
    ap.add_argument("--compare", nargs=2, metavar=("OLD", "NEW"),
                    help="compare two report files instead of running")
    ap.add_argument("--fail-on-regression", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="exercise every check against synthetic answers, no API calls")
    ap.add_argument("--record", metavar="DIR",
                    help="record every model and tool call to DIR (spends quota once)")
    ap.add_argument("--replay", metavar="DIR",
                    help="replay a recorded run from DIR: no provider calls, no quota")
    args = ap.parse_args(argv)

    if args.compare:
        with open(args.compare[0]) as f:
            old = json.load(f)
        with open(args.compare[1]) as f:
            new = json.load(f)
        diff = compare(old, new)
        print(render_compare(diff))
        # An inconclusive diff must not fail the build. It is reported, and it
        # is still worth reading -- but "we changed the suite" is not "the app
        # regressed", and a job that goes red for the former teaches everyone
        # to ignore the colour.
        if diff.get("incomparable"):
            return 0
        return 1 if (args.fail_on_regression and diff["regression"]) else 0

    if args.selftest:
        return _selftest(load_cases(args.dataset, args.ids, args.categories,
                                    args.tags, args.limit))

    cases = load_cases(args.dataset, args.ids, args.categories, args.tags, args.limit)
    if not cases:
        print("No cases selected.")
        return 2

    # Record or replay. Both are opt-in, so the nightly path is untouched.
    #
    # A replay spends no tokens and answers from evals/recordings/, which is
    # the only way a run of this suite can be reproduced by someone who is not
    # the author: a full run needs ~314,330 input tokens and the free tier
    # allows 200,000 a day, so it cannot be re-run from scratch at all.
    rec = None
    if args.record or args.replay:
        from modules import recording as _rec
        if args.record and args.replay:
            print("--record and --replay are mutually exclusive.")
            return 2
        if args.replay:
            if not os.path.isdir(args.replay):
                print(f"No recording at {args.replay}.")
                return 2
            # Set before any agent module is imported: llama_agent1 exits at
            # import time without a key, and a replay has no use for one.
            os.environ["EVAL_RECORD_MODE"] = "replay"
            rec = _rec.Recording.begin(args.replay, "replay")
            print(f"Replaying {args.replay} - no provider calls, no quota.")
        else:
            os.environ["EVAL_RECORD_MODE"] = "record"
            rec = _rec.Recording.begin(args.record, "record")
            print(f"Recording to {args.record} - this run spends quota once.")

    print(f"Running {len(cases)} case(s)...", flush=True)
    results = []
    for i, case in enumerate(cases, 1):
        r = run_case(case, use_judge=not args.no_judge, dsa=not args.no_dsa,
                     grounding_threshold=args.grounding_threshold,
                     judge_threshold=args.judge_threshold, timeout_s=args.timeout)
        results.append(r)
        j = (r.get("judge") or {}).get("overall", "-")
        status = "PASS" if r["passed"] else ("NOT GRADED" if not r["graded"] else "FAIL")
        print(f"[{i}/{len(cases)}] {r['id']:<22} {r['route']:<16} "
              f"{status}  judge={j}  {r['latency_s']}s", flush=True)
        # The pause exists to let the free-tier token window refill. A replay
        # spends no tokens, so it would add 25 s per case to nothing: 60 cases
        # of pure waiting on a run that takes seconds.
        if i < len(cases) and args.pause > 0 and not args.replay:
            time.sleep(args.pause)

    replay_stats = None
    if rec is not None:
        replay_stats = rec.stats()
        rec._save_manifest()
        rec.__class__.stop()

    summary = summarize(results)
    report = {"created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
              "git_sha": os.getenv("GIT_SHA", ""),
              # Which prompts, not just which commit -- see prompt_fingerprint.
              "prompt_version": os.getenv("PROMPT_VERSION", ""),
              "prompt_fingerprint": prompt_fingerprint(),
              # What graded this run. compare() refuses to call a pass-rate
              # delta a regression when this differs between the two reports.
              "evidence": evidence_fingerprint(),
              "args": vars(args),
              # Which container served these answers: "provider" means the
              # model really ran, "replay" means they came from a recording.
              # A replayed report must never be mistaken for a live one.
              "served_by": ("replay" if args.replay
                            else "recorded" if args.record else "provider"),
              "replay_stats": replay_stats,
              "summary": summary, "results": results}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("\n" + render_markdown(summary, results))
    print(f"\nFull report: {args.out}")
    if summary.get("status") == "inconclusive":
        # Do not exit 0: a run this incomplete must be visible in the job list,
        # or it becomes a green tick that measured half a suite. It is also not
        # a regression (exit 1) -- nothing was compared. 3 = "ran, but not
        # enough of it to score". The workflow prints this as a notice.
        print(f"\n::warning title=Incomplete run::only {summary['graded']}/"
              f"{summary['cases']} cases were graded "
              f"({summary['completeness_pct']}%); the raw pass rate "
              f"{summary['pass_rate']} is NOT a quality score. Among cases that "
              f"ran: {summary['pass_rate_graded']}%")
        if summary.get("categories_absent"):
            print(f"::warning title=No result::nothing can be claimed about "
                  f"{', '.join(summary['categories_absent'])} - "
                  f"that category did not run at all")
        return 3
    if summary.get("tail_only_loss") and summary.get("categories_absent"):
        # Not a failure: the run is real and the rate is quotable. The missing
        # category is a scheduling fact, and it is a warning so it appears in
        # the job summary next to the number rather than in a log nobody reads.
        print(f"::warning title=Partial suite::nothing can be claimed about "
              f"{', '.join(summary['categories_absent'])} - that category did not "
              f"run (quota). The rest of the report is a real measurement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
