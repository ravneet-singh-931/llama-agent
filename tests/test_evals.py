"""
Offline tests for the evaluation suite (evals/): dataset integrity,
deterministic checks, judge parsing, runner, summary and comparison.

Run with: pytest tests/test_evals.py -v
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("GROQ_API_KEY", "test-key")

from evals import checks, judge, run  # noqa: E402


# ============ DATASET ============

def test_dataset_is_valid_and_varied():
    cases = run.load_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)) >= 25
    cats = {c["category"] for c in cases}
    assert len(cats) >= 7 and {"team", "coding", "research_news", "safety"} <= cats
    allowed = {"must_include_any", "must_not_include", "needs_sources",
               "code", "max_words", "min_words"}
    for c in cases:
        assert c["prompt"].strip()
        assert set(c.get("checks", {})) <= allowed, c["id"]
    smoke = run.load_cases(tags=["smoke"])
    assert 5 <= len(smoke) < len(cases)
    assert len({c["category"] for c in smoke}) >= 6     # smoke still covers most areas


def test_dataset_filters():
    assert [c["id"] for c in run.load_cases(ids=["general-percent"])] == ["general-percent"]
    assert all(c["category"] == "coding" for c in run.load_cases(categories=["coding"]))
    assert len(run.load_cases(limit=3)) == 3


# ============ CHECKS ============

def test_english_check():
    assert checks.check_english("Plain English answer.")["pass"]
    assert not checks.check_english("Price is $5/月 per user 每月 计划 价格")["pass"]
    assert checks.check_english("text\n```\n# 中文 comment in code\n```")["pass"]


def test_meta_talk_check_is_generic():
    assert not checks.check_no_meta("My step is only research; other agents will code.")["pass"]
    assert not checks.check_no_meta("Based on Source material 2, the price is...")["pass"]
    ok = "### Coder Agent\n\nHere is the page. The user agent string is logged."
    assert checks.check_no_meta(ok)["pass"]


def test_code_checks():
    good = "```python\ndef f(x):\n    return x\n```"
    bad = "```python\ndef f(x)\n    return x\n```"
    assert checks.check_code(good, "python")["pass"]
    assert "syntax error" in checks.check_code(bad, "python")["detail"]
    assert not checks.check_code("no code here", "python")["pass"]
    html_bad = "```html\n<html><body><div><p>x</p></body></html>\n```"
    assert "div" in checks.check_code(html_bad, "html")["detail"]
    html_ok = "```html\n<html><body><div><img src=a><br></div></body></html>\n```"
    assert checks.check_code(html_ok, "html")["pass"]
    assert not checks.check_fences("```python\nx=1\n")["pass"]


def test_includes_supports_today_tokens():
    year = checks._today_tokens()["year"]
    assert checks.check_includes(f"Today is in {year}.", ["{year}"])["pass"]
    assert not checks.check_includes("no date", ["{year}"])["pass"]


def test_completed_and_sources_checks():
    assert not checks.check_completed("I couldn't complete that task right now.")["pass"]
    assert not checks.check_completed("  ")["pass"]
    assert checks.check_sources([{"name": "web_search", "result": "r"}])["pass"]
    assert not checks.check_sources([{"name": "list_uploaded_documents", "result": "r"}])["pass"]


def test_evaluate_includes_grounding_only_with_sources():
    case = {"id": "x", "prompt": "q", "checks": {"needs_sources": True}}
    src = [{"name": "web_search", "result": "The market is worth 1.27 billion dollars. " * 10}]
    res = checks.evaluate(case, "It is 1.27 billion; 55% growth.", src)
    assert res["grounding"]["rate"] == 0.5 and res["grounding"]["unsupported"] == ["55%"]
    assert not res["grounding"]["pass"] and res["used_search"]["pass"]
    assert "grounding" not in checks.evaluate({"id": "y", "prompt": "q"}, "55%", [])


def test_grounding_is_not_applied_to_cases_that_do_not_ask_for_sources():
    """A budget/timeline answer is not a factual claim to verify.

    This was the difference between a 75% and a meaningful pass rate. Every
    case that happened to produce tool output got a grounding check -- so a
    migration plan was graded against a web search it never relied on, and
    "0/13 figures found in sources" failed it. The planner's figures come from
    the request and its own arithmetic; there is no source they could match.
    """
    answer = ("Phase 1 costs 240,000 and takes 6 weeks. Phase 2 needs 4 engineers "
              "for 12 weeks. Total budget 350,000 across 2026.")
    tools = [{"name": "web_search", "result": "unrelated search result. " * 40}]

    planning = {"id": "plan", "prompt": "Plan the migration", "checks": {}}
    res = checks.evaluate(planning, answer, tools)
    assert "grounding" not in res, "a planning answer must not be graded against search results"

    # A case that *does* ask for sources is still checked, and can still fail.
    research = {"id": "news", "prompt": "What is the market size?", "checks": {"needs_sources": True}}
    res = checks.evaluate(research, answer, tools)
    assert "grounding" in res and not res["grounding"]["pass"]


def test_an_inline_snippet_counts_as_code_when_the_case_asks_for_python():
    """Formatting is not correctness.

    code-debug and code-regex-pin both scored 5.0 from the judge -- the
    request was answered -- and failed only because the snippet was written
    inline (`len(items) == 0`) rather than in a fenced block. A correct answer
    should not fail on where it put the backticks.
    """
    inline = "Use `len(items) == 0` to check for an empty list."
    assert checks.check_code(inline, "python")["pass"]

    # Prose with backticks but no code must still fail.
    assert not checks.check_code("Use `the empty check` instead.", "python")["pass"]
    # And a fenced block keeps working.
    assert checks.check_code("```python\nx = 1\n```", "python")["pass"]


def test_meta_check_flags_pipeline_talk_but_not_ordinary_words():
    """`handoff` is a normal word in a plan; it is not evidence of a leak.

    Broad noun patterns ("handoff", "team members") failed legitimate answers.
    What must never reach a user is an agent narrating the pipeline it is in.
    """
    for leak in (
        "I'll pass this to the other agents to continue.",
        "My step is complete; another agent will handle the rest.",
        "As the research agent, I found these results.",
        "I'll hand this off to the next stage.",
    ):
        assert not checks.check_no_meta(leak)["pass"], leak

    for fine in (
        "Step 2 takes the handoff from step 1.",
        "The handoff between phases takes one week.",
        "Our team members will get access on day 3.",
    ):
        assert checks.check_no_meta(fine)["pass"], fine


def test_grounding_still_catches_an_invented_figure_in_a_researched_answer():
    """The narrowing must not weaken fabrication detection where it applies."""
    case = {"id": "news", "prompt": "What is the market size?", "checks": {"needs_sources": True}}
    src = [{"name": "web_search", "result": "The market is worth 1.27 billion dollars. " * 10}]
    res = checks.evaluate(case, "It is 1.27 billion, growing 55% a year.", src)
    g = res["grounding"]
    assert g["unsupported"] == ["55%"] and g["rate"] == 0.5 and not g["pass"]


# ============ JUDGE ============

def test_parse_verdict():
    v = judge.parse_verdict('```json\n{"correctness": 4, "completeness": 5, "relevance": 5, '
                            '"clarity": 4, "fabrication": false, "comment": "none"}\n```')
    assert v["overall"] == 4.5 and v["fabrication"] is False
    assert judge.parse_verdict("no json") is None
    assert judge.parse_verdict('{"correctness": "x"}') is None
    assert judge.parse_verdict('{"correctness": 9, "completeness": 0, "relevance": 3, '
                               '"clarity": 3}')["correctness"] == 5


def test_judge_uses_rubric_reference_and_sources():
    seen = {}

    class _Msg:
        content = ('{"correctness": 2, "completeness": 3, "relevance": 4, "clarity": 4, '
                   '"fabrication": true, "comment": "invented winner"}')

    class _Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kw):
                    seen.update(kw)
                    return type("R", (), {"choices": [type("C", (), {"message": _Msg})]})

    case = {"prompt": "who won X", "reference": "There is no prize X."}
    v = judge.judge(case, "Alice won.", ["source text"], client=_Client, model="m")
    assert v["overall"] == 3.25 and v["fabrication"] and v["model"] == "m"
    user = seen["messages"][1]["content"]
    assert "REFERENCE NOTE" in user and "source text" in user and "TODAY:" in user


def test_judge_never_raises():
    class _Boom:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kw):
                    raise RuntimeError("429")
    assert "error" in judge.judge({"prompt": "p"}, "a", [], client=_Boom, model="m")


# ============ RUNNER ============

def test_route_matches_api_routing():
    assert run.route("@team build x")["route"] == "team"
    assert run.route("@Coder Agent write a function")["route"] == "agent:coder"
    assert run.route("What is 2+2?")["route"] == "general"


def test_run_case_records_tools_and_scores(monkeypatch):
    from modules import agent_tools

    def fake_executor(prompt, dsa=True, timeout_s=300):
        agent_tools.dispatch_tool("teleport", {})      # recorded via ToolRecorder
        return {"route": "agent:research", "answer": "Done in English.", "reasoning": []}

    case = {"id": "c1", "category": "research_facts", "prompt": "p",
            "checks": {"needs_sources": True}}
    r = run.run_case(case, executor=fake_executor, use_judge=True,
                     judge_fn=lambda c, a, s: {"overall": 4.0})
    assert r["tools"][0]["name"] == "teleport"
    assert not r["checks"]["used_search"]["pass"] and r["passed"] is False
    assert agent_tools.dispatch_tool.__name__ == "dispatch_tool"   # restored


def test_run_case_survives_executor_crash():
    def boom(prompt, **kw):
        raise RuntimeError("down")
    r = run.run_case({"id": "c", "category": "general", "prompt": "p"}, executor=boom,
                     use_judge=False)
    assert r["route"] == "error" and not r["passed"] and "down" in r["error"]


def _result(i, cat, passed, judge_score, ground=None, lat=1.0):
    res = {"id": i, "category": cat, "passed": passed, "latency_s": lat,
           "checks": {"completed": {"pass": passed}}, "judge": {"overall": judge_score},
           "usage": {"tokens_input": 100}}
    if ground is not None:
        res["checks"]["grounding"] = {"pass": True, "rate": ground}
    return res


def test_summary_compare_and_markdown(tmp_path):
    old = [_result("a", "x", True, 4.0, 0.9), _result("b", "y", False, 2.0)]
    new = [_result("a", "x", False, 3.0, 0.5), _result("b", "y", True, 4.5)]
    so, sn = run.summarize(old), run.summarize(new)
    assert so["pass_rate"] == 50.0 and so["grounding_rate"] == 0.9
    assert so["usage"]["tokens_input"] == 200
    diff = run.compare({"summary": so, "results": old}, {"summary": sn, "results": new})
    assert diff["regressed_cases"] == ["a"] and diff["fixed_cases"] == ["b"]
    assert diff["regression"]
    md = run.render_markdown(sn, new)
    assert "| a |" in md and "Pass" in md
    # CLI compare + exit code
    p_old, p_new = tmp_path / "o.json", tmp_path / "n.json"
    p_old.write_text(json.dumps({"summary": so, "results": old}))
    p_new.write_text(json.dumps({"summary": sn, "results": new}))
    assert run.main(["--compare", str(p_old), str(p_new), "--fail-on-regression"]) == 1
    assert run.main(["--compare", str(p_old), str(p_old), "--fail-on-regression"]) == 0


# ============ JUDGE FAILURES + AUDITABLE SOURCES (v3.4.9) ============
# The v3.4.8 agent eval hit the daily token limit: the judge returned a 429
# error for 16 cases, and 6 of them still counted as passes.

def _answering(prompt, dsa=True, timeout_s=300):
    from modules import agent_tools
    agent_tools.dispatch_tool("teleport", {})  # unknown tool: no network
    return {"route": "agent:writer", "answer": "A complete answer in English.", "reasoning": []}


def test_a_failed_judge_means_not_graded_not_passed():
    case = {"id": "w1", "category": "writing", "prompt": "p"}
    r = run.run_case(case, executor=_answering, use_judge=True,
                     judge_fn=lambda c, a, s: {"error": "RateLimitError: 429 tokens per day"})
    assert r["graded"] is False and r["passed"] is False
    s = run.summarize([r, {**_result("ok", "coding", True, 5.0), "graded": True}])
    assert s["not_graded"] == ["w1"] and s["pass_rate"] == 50.0
    md = run.render_markdown(s, [r])
    assert "not graded" in md and "| w1 |" in md and "⚠️ not graded" in md


def test_judged_and_no_judge_runs_are_graded():
    case = {"id": "w2", "category": "writing", "prompt": "p"}
    r = run.run_case(case, executor=_answering, use_judge=True,
                     judge_fn=lambda c, a, s: {"overall": 4.5})
    assert r["graded"] is True and r["passed"] is True
    r = run.run_case(case, executor=_answering, use_judge=False)
    assert r["graded"] is True and r["passed"] is True and r["judge_sources"] == ""
    assert run.summarize([r])["not_graded"] == []


def test_report_keeps_the_sources_the_judge_saw(monkeypatch):
    from modules import agent_tools
    monkeypatch.setattr(agent_tools, "dispatch_tool",
                        lambda name, args: "Singapore GP: 11 Oct 2026, won by Verstappen.")
    seen = {}

    def fake_judge(case, answer, sources):
        seen["msg"] = judge.build_messages(case, answer, sources, "2026-10-25")[1]["content"]
        return {"overall": 4.0}

    r = run.run_case({"id": "f1", "category": "research_news", "prompt": "p"},
                     executor=_answering, use_judge=True, judge_fn=fake_judge)
    assert "11 Oct 2026" in r["judge_sources"]
    assert r["judge_sources"] in seen["msg"]  # exactly the text the judge was shown


def test_compare_ignores_cases_that_were_not_graded():
    old = [_result("a", "x", True, 4.0)]
    new = [{**_result("a", "x", False, 4.0), "graded": False, "judge": {"error": "429"}}]
    diff = run.compare({"summary": run.summarize(old), "results": old},
                       {"summary": run.summarize(new), "results": new})
    assert diff["regressed_cases"] == []


# ----------------------------------------------------- nightly delta semantics
# The nightly workflow is only as good as compare(). These pin the four
# behaviours the workflow's pass/fail decision depends on.

def _report(sha, pass_map, latency=10.0, fab=0.0):
    results = [{"id": k, "category": "coding", "passed": v, "graded": True,
                "latency_s": latency} for k, v in pass_map.items()]
    rate = 100 * sum(pass_map.values()) / len(pass_map)
    return {"git_sha": sha, "created": sha, "results": results, "summary": {
        "cases": len(results), "pass_rate": rate, "judge_overall": 4.0,
        "grounding_rate": 0.9, "fabrication_rate": fab,
        "latency_p50_s": latency, "latency_p95_s": latency,
        "by_category": {"coding": {"cases": len(results), "pass_rate": rate}}}}


BASE = {"a": True, "b": True, "c": False, "d": True}


def test_compare_identical_runs_is_not_a_regression():
    d = run.compare(_report("y", BASE), _report("t", BASE))
    assert d["regression"] is False
    assert d["regressed_cases"] == []


def test_compare_flags_a_flipped_case_by_name():
    d = run.compare(_report("y", BASE), _report("t", {**BASE, "a": False}))
    assert d["regression"] is True
    assert d["regressed_cases"] == ["a"]


def test_compare_fixes_are_not_regressions():
    """A run that improved must never fail the nightly job."""
    d = run.compare(_report("y", BASE), _report("t", {**BASE, "c": True}))
    assert d["regression"] is False
    assert d["fixed_cases"] == ["c"]


def test_compare_treats_slower_and_fabricated_more_as_worse():
    slower = run.compare(_report("y", BASE, latency=10), _report("t", BASE, latency=15))
    # Latency is reported as a delta but is NOT a regression on its own:
    # a nightly run is allowed to be slower without failing CI.
    assert slower["regression"] is False
    assert slower["metrics"]["latency_p50_s"]["delta"] == 5
    # More fabrication IS a regression.
    fab = run.compare(_report("y", BASE, fab=0.0), _report("t", BASE, fab=8.0))
    assert fab["regression"] is True


# ------------------------------------------------------------------ compare()
# The 2026-11-11 nightly went red comparing a 30-case report against a 60-case
# one, after the checks themselves had changed. Every number in that diff was
# meaningless and the build still failed. These tests pin the two fields that
# make a diff readable, and the rule that an incomparable diff is reported but
# does not fail the job.

def _cmp_report(summary, **extra):
    return {"summary": summary, "results": extra.pop("results", []), **extra}


def _cmp_summary(cases, passed, **kw):
    s = {"cases": cases, "pass_rate": round(passed / cases * 100, 1),
         "by_category": {"coding": {"cases": cases, "pass_rate": round(passed / cases * 100, 1)}},
         "check_failures": {}, "not_graded": [],
         "judge_overall": 4.4, "fabrication_rate": 15.0, "grounding_rate": 0.3,
         "latency_p50_s": 3.0, "latency_p95_s": 99.0}
    s.update(kw)
    return s


def test_a_diff_across_a_changed_suite_is_not_called_a_regression():
    """30 cases vs 60 cases are two different measurements, not two versions."""
    old = _cmp_report(_cmp_summary(30, 26), evidence={"checks": "aaa", "dataset": "ddd"})
    new = _cmp_report(_cmp_summary(60, 21), evidence={"checks": "bbb", "dataset": "eee"})

    diff = run.compare(old, new)

    assert diff["incomparable"] == "cases_changed"
    assert diff["regression"] is True, "the delta is still reported honestly"
    assert "NOT COMPARABLE" in run.render_compare(diff)
    assert "inconclusive" in run.render_compare(diff)


def test_a_diff_across_a_changed_grader_is_incomparable_not_a_regression():
    """Same case count, different checks: the measuring stick moved."""
    old = _cmp_report(_cmp_summary(60, 45), evidence={"checks": "aaa", "dataset": "ddd"})
    new = _cmp_report(_cmp_summary(60, 21), evidence={"checks": "bbb", "dataset": "ddd"})

    diff = run.compare(old, new)

    assert diff["incomparable"] == "check_logic_changed"
    assert "inconclusive" in run.render_compare(diff)


def test_a_comparable_diff_still_fails_on_a_real_regression():
    """The guard must not disarm the detector it was added to protect."""
    old = _cmp_report(_cmp_summary(60, 45), evidence={"checks": "same", "dataset": "same"})
    new = _cmp_report(_cmp_summary(60, 40), evidence={"checks": "same", "dataset": "same"})

    diff = run.compare(old, new)

    assert diff["incomparable"] == ""
    assert diff["regression"] is True
    assert "Verdict: REGRESSION" in run.render_compare(diff)


def test_compare_names_the_checks_that_started_failing():
    """'12 cases regressed' is not actionable; 'no_meta +8' is."""
    old = _cmp_report(_cmp_summary(60, 45, check_failures={"no_meta": 2}))
    new = _cmp_report(_cmp_summary(60, 21, check_failures={"no_meta": 10, "english": 1}))

    diff = run.compare(old, new)
    text = run.render_compare(diff)

    assert diff["check_failure_delta"] == {"no_meta": 8, "english": 1}
    assert "no_meta +8" in text


def test_compare_separates_newly_not_graded_from_regressed():
    """A provider failure looks like a pass-rate drop unless it is named."""
    old = _cmp_report(_cmp_summary(60, 45, not_graded=[]))
    new = _cmp_report(_cmp_summary(60, 37, not_graded=["safety-x", "team-y"]))

    diff = run.compare(old, new)

    assert diff["newly_not_graded"] == ["safety-x", "team-y"]
    assert "Newly NOT GRADED" in run.render_compare(diff)


def test_a_run_where_a_third_of_cases_were_not_graded_says_so():
    """The summary must carry the count, not just bury it in a case list."""
    results = [{"id": f"c{i}", "category": "coding", "passed": False, "graded": False,
                "checks": {}, "usage": {}, "latency_s": 0.9,
                "judge": {"error": "provider"}, "route": "general"}
               for i in range(3)]
    s = run.summarize(results)

    assert s["provider_failures"] == 3
    assert s["provider_failure_pct"] == 100.0
