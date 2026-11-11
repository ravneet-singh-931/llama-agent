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


def execute(message: str, dsa: bool = True, timeout_s: float = 300.0) -> Dict:
    """Run one message through the real pipeline; returns answer + events."""
    from multi_agent import MultiAgentOrchestrator
    r = route(message)
    answer, reasoning = [], []
    critique = reflect = parallel = None if dsa else False
    start = time.monotonic()

    def timed_out():
        return time.monotonic() - start > timeout_s

    if r["route"] == "team":
        orch = MultiAgentOrchestrator()
        for ev in orch.collaborate_stream(r["task"], critique=critique, reflect=reflect,
                                          parallel=parallel):
            t = ev.get("type")
            if t == "token":
                answer.append(ev.get("content", ""))
            elif t == "agent_start":
                answer.append(f"\n\n### {ev.get('agent', 'Agent')}\n\n")
            elif t in ("reasoning", "tool"):
                reasoning.append({"stage": ev.get("stage", t), "content": ev.get("content", "")})
            if timed_out():
                answer.append("\n⚠️ Stream timed out.")
                break
    elif r["route"].startswith("agent:"):
        agent = MultiAgentOrchestrator().agents[r["agent"]]
        crit = os.getenv("AGENT_CRITIQUE", "1") == "1" if dsa else False
        refl = os.getenv("AGENT_REFLECT", "1") == "1" if dsa else False
        for ev in agent.execute_stream(r["task"], critique=crit, reflect=refl):
            t = ev.get("type")
            if t == "token":
                answer.append(ev.get("content", ""))
            elif t in ("reasoning", "tool"):
                reasoning.append({"stage": ev.get("stage", t), "content": ev.get("content", "")})
            if timed_out():
                answer.append("\n⚠️ Stream timed out.")
                break
    else:
        from llama_agent1 import run_agent_stream
        for tok in run_agent_stream(message, user_id="eval",
                                    conversation_id=f"eval-{uuid.uuid4().hex[:8]}"):
            answer.append(tok)
            if timed_out():
                answer.append("\n⚠️ Stream timed out.")
                break
    return {"route": r["route"], "answer": "".join(answer).strip(), "reasoning": reasoning}


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
        with ToolRecorder() as rec:
            try:
                return rec, executor(case["prompt"], dsa=dsa, timeout_s=timeout_s)
            except Exception as e:
                return rec, {"route": "error", "answer": "", "reasoning": [],
                             "error": f"{type(e).__name__}: {e}"[:300]}

    # One span per case. A failing nightly case used to leave only its answer
    # text and the judge's verdict in the report; the reasoning chain was
    # captured and then flattened to a string. As a span the case is a trace
    # you can open in Phoenix and read end to end.
    with _case_span(case):
        rec, out = _run()
    latency = round(time.monotonic() - t0, 2)
    usage = _delta(metric_totals(), before)

    results = checks.evaluate(case, out["answer"], rec.calls, grounding_threshold,
                              route=out.get("route"))
    verdict, graded, judge_src = None, True, ""
    if checks.is_provider_failure(out["answer"]):
        # Rate limit / outage notice: don't grade an error message.
        verdict = {"error": "not judged: the answer is the app's rate-limit/outage notice"}
        graded = False
    elif use_judge and out["answer"]:
        from evals.judge import judge, judge_sources
        sources = [c["result"] for c in rec.calls]
        judge_src = judge_sources(sources, out["answer"])
        verdict = (judge_fn or judge)(case, out["answer"], sources)
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
    if expected_tools:
        called = {c["name"] for c in rec.calls}
        tools_ok = set(expected_tools) <= called
        passed = passed and tools_ok
    passed = passed and graded
    return {
        "id": case["id"], "category": case["category"], "prompt": case["prompt"],
        "route": out["route"], "passed": passed, "graded": graded, "latency_s": latency,
        "checks": results, "judge": verdict, "judge_sources": judge_src, "usage": usage,
        "tools": [{"name": c["name"], "arguments": c["arguments"],
                   "result_chars": len(c["result"] or "")} for c in rec.calls],
        "tool_recall": tools_ok,
        "steps": len(rec.calls),
        "redundant_steps": _redundant(rec.calls),
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
    ground = [r["checks"]["grounding"]["rate"] for r in results if "grounding" in r["checks"]]
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
    inconclusive = n > 0 and completeness < threshold
    return {
        "cases": n,
        # Not-graded cases count as not passed; see "not_graded".
        "pass_rate": _pct(sum(r["passed"] for r in results), n),
        "graded": len(graded),
        "completeness_pct": completeness,
        # The only pass rate worth quoting when cases did not run.
        "pass_rate_graded": _pct(passed_graded, len(graded)) if graded else None,
        "status": "inconclusive" if inconclusive else "ok",
        "not_graded": not_graded,
        # A third of the suite arriving as provider notices is not a quality
        # number, whatever the pass rate says. Flagged in the summary so the
        # nightly can shout instead of publishing 35%.
        "provider_failures": len(not_graded),
        "provider_failure_pct": _pct(len(not_graded), n),
        "by_category": {c: {"cases": len(rs), "pass_rate": _pct(sum(r["passed"] for r in rs), len(rs))}
                        for c, rs in sorted(by_cat.items())},
        "check_failures": dict(sorted(check_fail.items())),
        "judge_overall": round(statistics.mean(judged), 2) if judged else None,
        "fabrication_rate": _pct(len(fabric), len(judged)) if judged else None,
        "grounding_rate": round(statistics.mean(ground), 3) if ground else None,
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
    else:
        head += [f"- pass rate **{s['pass_rate']}%**"
                 + (f" - **not graded** (judge failed, counted as not passed): "
                    f"{len(s['not_graded'])}" if s.get("not_graded") else "")]
    lines = ["# Evaluation report", ""] + head + [
        f"- Judge overall (1-5): **{s['judge_overall']}** - fabrication flagged: {s['fabrication_rate']}%",
        f"- Grounding rate (figures found in sources): **{s['grounding_rate']}**",
        f"- Latency p50 / p95: {s['latency_p50_s']}s / {s['latency_p95_s']}s",
        f"- Trajectory: {s.get('avg_steps')} tool calls/case on average, "
        f"{s.get('total_redundant_steps')} redundant"
        + (f" - tool recall {s['tool_recall']}%" if s.get("tool_recall") is not None else ""),
        f"- Usage: {json.dumps(s['usage'])}", "",
        "| Category | Cases | Pass % |", "|---|---|---|",
    ]
    lines += [f"| {c} | {v['cases']} | {v['pass_rate']} |" for c, v in s["by_category"].items()]
    if s["check_failures"]:
        lines += ["", "Check failures: " + ", ".join(f"{k}={v}" for k, v in s["check_failures"].items())]
    lines += ["", "| Case | Route | Pass | Judge | Latency | Failed checks |", "|---|---|---|---|---|---|"]
    for r in results:
        failed = [f"{k} ({c.get('detail', '')})" if c.get("detail") else k
                  for k, c in r["checks"].items() if not c["pass"]]
        j = r.get("judge") or {}
        score = j.get("overall", j.get("error", "-"))
        status = "⚠️ not graded" if not r.get("graded", True) else ("✅" if r["passed"] else "❌")
        lines.append(f"| {r['id']} | {r.get('route', '-')} | {status} | {str(score)[:120]} "
                     f"| {r['latency_s']}s | {'; '.join(failed)[:160]} |")
    return "\n".join(lines)


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
    regressed = sorted(i for i in common if old_pass[i] and not new_pass[i])
    fixed = sorted(i for i in common if not old_pass[i] and new_pass[i])
    cats = {}
    for c in set(so["by_category"]) & set(sn["by_category"]):
        cats[c] = round(sn["by_category"][c]["pass_rate"] - so["by_category"][c]["pass_rate"], 1)
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
    check_delta = {k: new_cf[k] - old_cf.get(k, 0)
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
            "regression": bool(regressed) or bool(worse) or any(v < 0 for v in cats.values())}


def render_compare(diff: Dict) -> str:
    lines = ["# Comparison", "", "| Metric | Old | New | Delta |", "|---|---|---|---|"]
    for k, v in diff["metrics"].items():
        lines.append(f"| {k} | {v['old']} | {v['new']} | {v['delta']} |")
    lines += ["", "Category pass-rate delta: " + json.dumps(diff["category_pass_delta"]),
              f"Fixed: {diff['fixed_cases'] or 'none'}",
              f"Regressed: {diff['regressed_cases'] or 'none'}"]
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

    cases = load_cases(args.dataset, args.ids, args.categories, args.tags, args.limit)
    if not cases:
        print("No cases selected.")
        return 2
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
        if i < len(cases) and args.pause > 0:
            time.sleep(args.pause)

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
              f"({summary['completeness_pct']}%); pass rate {summary['pass_rate']} "
              f"is NOT a quality score. Among graded cases: "
              f"{summary['pass_rate_graded']}%")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
