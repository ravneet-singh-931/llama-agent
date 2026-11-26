"""The absolute floors: a run must clear a bar, not just beat yesterday.

Background, because the test is only meaningful with it
-------------------------------------------------------
`compare()` answers "did this run get worse than the previous one?". Between
2026-11-14 and 2026-11-26 the nightly was green thirteen nights in a row while
the fabrication rate went 6.9% -> 12.9%. The largest single-step move was
+6.6 points, so no night looked like a regression and the gate never fired.

These tests pin the fix: `check_floors()` answers the OTHER question -- "is this
run acceptable on its own numbers?" -- and `--fail-on-floors` turns that into an
exit code.

Every test below is a case that would have been missed or, worse, a case that
would have failed for the wrong reason.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("evals_run_floors", ROOT / "evals" / "run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["evals_run_floors"] = module
    spec.loader.exec_module(module)
    return module


run = _load()

# The real series, 2026-11-14..11-26, transcribed from the stored artifacts:
# (date, pass_rate, completeness_pct, fabrication_rate)
SERIES = [
    ("11-14", 71.7, 80.0, 6.2),
    ("11-15", 45.0, 60.0, 11.1),
    ("11-16", 75.0, 80.0, 4.2),
    ("11-17", 68.3, 83.3, 6.0),
    ("11-18", 70.0, 83.3, 8.0),
    ("11-19", 63.3, 71.7, 4.8),
    ("11-20", 68.3, 83.3, 4.0),
    ("11-21", 70.0, 86.7, 9.6),
    ("11-22", 65.0, 78.3, 8.7),
    ("11-23", 71.7, 78.3, 14.9),
    ("11-24", 61.7, 85.0, 13.7),
    ("11-25", 66.7, 81.7, 8.2),
    ("11-26", 56.7, 78.3, 14.9),
]


def _summary(pass_rate, completeness, fabrication, **extra):
    s = {"pass_rate": pass_rate, "completeness_pct": completeness,
         "fabrication_rate": fabrication}
    s.update(extra)
    return s


# --- the drift it exists to catch -------------------------------------------

def test_the_drift_is_caught_three_nights_running():
    """The three nights where fabrication crossed 12%. A delta gate saw none."""
    hit = [d for d, p, c, f in SERIES
           if not run.check_floors(_summary(p, c, f))["ok"] and f > 12.0]
    assert hit == ["11-23", "11-24", "11-26"], hit


def test_floor_fires_on_four_of_the_thirteen_real_runs():
    """Loose enough to be a signal, not so tight that it is always red.

    4 of 13 is deliberate: a floor that fails most nights gets commented out.
    """
    fired = [d for d, p, c, f in SERIES
             if run.check_floors(_summary(p, c, f))["breached"]]
    assert fired == ["11-15", "11-23", "11-24", "11-26"], fired


def test_a_clean_run_passes_every_floor():
    result = run.check_floors(_summary(75.0, 85.0, 4.0))
    assert result["ok"] is True
    assert result["breached"] == []
    assert set(result["checked"]) == {"pass_rate", "completeness_pct", "fabrication_rate"}


# --- each floor, in isolation -----------------------------------------------

def test_pass_rate_floor_is_inclusive_at_the_boundary():
    """Exactly 55 passes. A floor written as ``<=`` would fail a passing run."""
    assert run.check_floors(_summary(55.0, 80.0, 5.0))["ok"] is True
    below = run.check_floors(_summary(54.9, 80.0, 5.0))
    assert below["ok"] is False
    assert [b["metric"] for b in below["breached"]] == ["pass_rate"]


def test_fabrication_ceiling_is_inclusive_at_the_boundary():
    assert run.check_floors(_summary(70.0, 80.0, 12.0))["ok"] is True
    above = run.check_floors(_summary(70.0, 80.0, 12.1))
    assert above["ok"] is False
    assert [b["metric"] for b in above["breached"]] == ["fabrication_rate"]


def test_coverage_floor_matches_the_number_summarize_already_uses():
    """70 is not invented here: summarize() uses it for "massive" loss.

    Two places deciding what "most of the suite did not run" means would drift
    apart, so the test pins the equality.
    """
    assert run.FLOORS["completeness_pct"]["min"] == 70.0
    assert run.check_floors(_summary(70.0, 70.0, 5.0))["ok"] is True
    assert run.check_floors(_summary(70.0, 69.9, 5.0))["ok"] is False


def test_multiple_breaches_are_all_reported():
    """11-15 breaches two floors. Reporting only the first would hide the other."""
    result = run.check_floors(_summary(45.0, 60.0, 11.1))
    assert result["ok"] is False
    assert {b["metric"] for b in result["breached"]} == {"pass_rate", "completeness_pct"}


# --- absent means unknown, not equal ----------------------------------------

def test_a_missing_metric_is_skipped_not_passed():
    """The rule the whole file is built on: absent != equal.

    A run whose report has no ``fabrication_rate`` must not be recorded as
    having passed the fabrication floor.
    """
    result = run.check_floors({"pass_rate": 70.0, "completeness_pct": 80.0})
    assert result["ok"] is True                    # nothing breached...
    assert any("fabrication_rate" in s for s in result["skipped"])  # ...but not checked
    assert "fabrication_rate" not in result["checked"]


def test_a_non_numeric_metric_is_skipped_not_compared():
    result = run.check_floors(_summary(70.0, 80.0, "n/a"))
    assert any("fabrication_rate" in s for s in result["skipped"])
    assert "fabrication_rate" not in result["checked"]


def test_a_boolean_is_not_a_number():
    """True would compare as 1 and silently pass a "max 12" ceiling.

    This is the same class of bug as a permissive default: a value that cannot
    be wrong because it is not a measurement at all.
    """
    result = run.check_floors(_summary(70.0, 80.0, True))
    assert "fabrication_rate" not in result["checked"]
    assert any("fabrication_rate" in s for s in result["skipped"])


def test_no_metrics_at_all_breaches_nothing_but_checks_nothing():
    result = run.check_floors({})
    assert result["ok"] is True
    assert result["checked"] == []
    assert len(result["skipped"]) == len(run.FLOORS)


# --- the wire-up: does compare() actually carry the floors? ------------------

def _report(pass_rate, completeness, fabrication, status="ok"):
    """A report shaped like the real ones.

    `by_category` lives INSIDE `summary` -- compare() reads
    `so["summary"]["by_category"]`, and a fixture that puts it at the top level
    only passes because compare() never reaches that line. Fixtures have to match
    the artefact, not the part of it a given test happens to touch.
    """
    return {
        "summary": {"pass_rate": pass_rate, "completeness_pct": completeness,
                    "fabrication_rate": fabrication, "status": status,
                    "judge_overall": 4.5, "grounding_rate": 0.5,
                    "latency_p50_s": 2.0, "latency_p95_s": 100.0,
                    "cases": 60, "pass_rate_graded": pass_rate,
                    "by_category": {}},
        "results": [],
    }


def test_compare_carries_floors_computed_from_the_new_run_only():
    """The floors are a fact about `new`, so `old` must not be able to move them."""
    diff = run.compare(_report(70.0, 80.0, 5.0), _report(60.0, 80.0, 14.0))
    assert diff["floors"]["breached"], "a 14% fabrication run must breach"
    assert [b["metric"] for b in diff["floors"]["breached"]] == ["fabrication_rate"]


def test_floors_and_regression_are_independent_facts():
    """A run can breach a floor without regressing, and that is the whole point.

    Measured, not assumed: on 11-22 -> 11-23 the fabrication rate *rose*
    8.7 -> 14.9, which the delta gate correctly calls a regression. So that pair
    cannot show independence.

    The pair that does: a suite sitting above the ceiling for many nights while
    each night is marginally *better* than the last. Every delta is favourable,
    `regression` is False, and the run is still unacceptable on its own numbers.
    This is the shape of a system stuck in a bad state, which is precisely what
    a delta-only gate cannot fail and cannot see.
    """
    diff = run.compare(_report(70.0, 80.0, 14.0), _report(72.0, 81.0, 13.0))
    assert diff["regression"] is False, "every metric improved, so no regression"
    assert diff["floors"]["breached"], "and 13% is still above the 12% ceiling"
    assert [b["metric"] for b in diff["floors"]["breached"]] == ["fabrication_rate"]


def test_render_compare_names_the_breach_in_words():
    diff = run.compare(_report(70.0, 80.0, 14.0), _report(72.0, 81.0, 13.0))
    text = run.render_compare(diff)
    assert "Absolute floors breached" in text
    assert "13.0 is above the ceiling of 12.0" in text


def test_render_compare_says_ok_when_floors_pass():
    diff = run.compare(_report(70.0, 80.0, 5.0), _report(72.0, 81.0, 4.0))
    text = run.render_compare(diff)
    assert "Floors: ok" in text
    assert "Absolute floors breached" not in text


def test_render_compare_names_floors_it_could_not_evaluate():
    """A skipped floor must appear in the output, or the reader assumes it ran."""
    old = _report(70.0, 80.0, 5.0)
    new = _report(72.0, 81.0, 4.0)
    del new["summary"]["fabrication_rate"]
    text = run.render_compare(run.compare(old, new))
    assert "Floors NOT evaluated" in text
    assert "fabrication_rate" in text


# --- the flag is opt-in ------------------------------------------------------

def test_fail_on_floors_is_a_flag_and_defaults_off():
    """Manual runs during development must not be blocked by a threshold.

    Pinned because the alternative -- a floor that is always armed -- is how a
    useful check gets deleted by the first person it inconveniences.
    """
    parser_defaults = run.build_parser().parse_args(["--compare", "a", "b"])
    assert parser_defaults.fail_on_floors is False
    armed = run.build_parser().parse_args(["--compare", "a", "b", "--fail-on-floors"])
    assert armed.fail_on_floors is True
