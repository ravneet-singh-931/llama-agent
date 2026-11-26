#!/usr/bin/env python3
"""Turn N nightly `eval-today.json` reports into one table.

Why this exists
---------------
The nightly uploads `eval-today.json` as an artifact with **30-day retention**. So
the historical series is not stored anywhere: on day 31 the series for day 1 is
gone, and the only run that has ever been compared is *today vs the run before
it*. That comparison answers "did it move?" -- it cannot answer "where is it?".

A nightly that is green 13 nights running, with a pass rate that slid from 88% to
80%, is not a healthy system. It is a system whose gate only ever asked about the
last step. This script recovers the series while the artifacts still exist.

Usage
-----
Download the reports first (they live 30 days), then point this at the folder::

    # 1. collect -- one folder per run, each containing eval-today.json
    gh run list --workflow nightly-evals.yml --status success \\
        --limit 40 --json databaseId \\
        --jq '.[].databaseId' | while read id; do
      gh run download "$id" --pattern 'eval-report-*' --dir "reports/$id" || true
    done

    # 2. tabulate
    python scripts/nightly_trend.py reports/

Output is a markdown table: one row per run, oldest first, with the deltas the
nightly itself never prints.

Exit codes
----------
0  table produced (a trend is not a verdict -- this script never fails a build)
2  no usable reports found
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

# The summary fields worth tracking. Anything missing from a report is shown as
# "-" rather than skipped: a column that silently disappears when a field is
# absent is how a trend line flatters itself.
COLUMNS: List[tuple[str, str, str]] = [
    # (summary key, heading, direction)  "up" = higher is better
    ("pass_rate", "pass", "up"),
    ("pass_rate_graded", "graded%", "up"),
    ("completeness_pct", "cov%", "up"),
    ("judge_overall", "judge", "up"),
    ("grounding_rate", "ground", "up"),
    ("fabrication_rate", "fab%", "down"),
    ("latency_p50_s", "p50", "down"),
    ("latency_p95_s", "p95", "down"),
    ("cases", "cases", "up"),
]


def _find_report(root: str) -> Optional[str]:
    """Locate eval-today.json anywhere under `root`.

    `gh run download --pattern` leaves it at an unpredictable depth because the
    artifact name varies per run, so walk instead of guessing a path.
    """
    for dirpath, _dirnames, filenames in os.walk(root):
        if "eval-today.json" in filenames:
            return os.path.join(dirpath, "eval-today.json")
    return None


def _load(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data.get("summary") if isinstance(data, dict) else None


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # Two decimals is enough to see a move; more invites false precision on
        # a suite of 60 cases where one case is 1.67 points.
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _delta(old: Any, new: Any, direction: str) -> str:
    if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
        return ""
    if isinstance(old, bool) or isinstance(new, bool):
        return ""
    diff = new - old
    if abs(diff) < 1e-9:
        return "  ="
    good = (diff > 0) if direction == "up" else (diff < 0)
    return f" {'+' if diff > 0 else ''}{diff:.2f} {'ok' if good else 'WORSE'}"


def collect(root: str) -> List[Dict[str, Any]]:
    """One entry per run directory, oldest first.

    Ordering: the report's own `run_date` when present, else the directory name.
    A run id sorts correctly because GitHub's are monotonic.
    """
    rows: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path):
            continue
        report = _find_report(path)
        if not report:
            continue
        summary = _load(report)
        if summary is None:
            continue
        rows.append({"run": name, "summary": summary, "path": report})

    def _key(row: Dict[str, Any]) -> str:
        date = row["summary"].get("run_date") or ""
        return f"{date}-{row['run']}"

    rows.sort(key=_key)
    return rows


def render(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No reports found."

    head = ["run"] + [h for _k, h, _d in COLUMNS]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]

    previous: Optional[Dict[str, Any]] = None
    for row in rows:
        summary = row["summary"]
        label = summary.get("run_date") or row["run"][-8:]
        cells = [str(label)]
        for key, _heading, direction in COLUMNS:
            value = summary.get(key)
            cell = _fmt(value)
            if previous is not None:
                cell += _delta(previous.get(key), value, direction)
            cells.append(cell)
        out.append("| " + " | ".join(cells) + " |")
        previous = summary

    return "\n".join(out)


def headline(rows: List[Dict[str, Any]]) -> str:
    """First vs last, on the two numbers that decide whether a gate is honest."""
    if len(rows) < 2:
        return ""
    first, last = rows[0]["summary"], rows[-1]["summary"]
    lines = ["", "## First to last", ""]
    for key, heading, direction in COLUMNS:
        old, new = first.get(key), last.get(key)
        if not isinstance(old, (int, float)) or not isinstance(new, (int, float)):
            continue
        if isinstance(old, bool) or isinstance(new, bool):
            continue
        diff = new - old
        good = (diff > 0) if direction == "up" else (diff < 0)
        verdict = "improved" if good and abs(diff) > 1e-9 else (
            "unchanged" if abs(diff) < 1e-9 else "regressed")
        lines.append(f"- {heading}: {_fmt(old)} -> {_fmt(new)} "
                     f"({'+' if diff > 0 else ''}{diff:.2f}) **{verdict}**")

    pass_rates = [r["summary"].get("pass_rate") for r in rows]
    pass_rates = [p for p in pass_rates if isinstance(p, (int, float))]
    if len(pass_rates) >= 3:
        best, worst = max(pass_rates), min(pass_rates)
        lines += [
            "",
            f"- pass_rate across {len(pass_rates)} runs: "
            f"best **{_fmt(best)}**, worst **{_fmt(worst)}**, "
            f"range **{_fmt(best - worst)}** points",
        ]
        if best - worst > 10:
            lines.append(
                "  A range that wide under a green gate means the gate is "
                "measuring *movement*, not *level*.")

    lines += [
        "",
        "## What this table cannot tell you",
        "",
        "- Whether any single move is noise. The suite is 60 cases; one case is "
        "1.67 points, and the judge has been observed moving 3.75 <-> 2.75 "
        "between identical runs.",
        "- Whether the code changed. A prompt or check edit moves these numbers "
        "with no app change at all -- that is what `evidence_fingerprint` "
        "guards, and it is not in the summary.",
        "- Anything at all if the run was not graded. A `pass_rate` below 90% "
        "coverage is not a quality score; check the cov% column first.",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tabulate nightly eval reports into one trend table.")
    parser.add_argument("root", help="folder containing one subfolder per run")
    args = parser.parse_args(argv)

    if not os.path.isdir(args.root):
        print(f"not a directory: {args.root}", file=sys.stderr)
        return 2

    rows = collect(args.root)
    if not rows:
        print(f"no eval-today.json found under {args.root}", file=sys.stderr)
        return 2

    print(f"# Nightly eval trend\n\n{len(rows)} reports under `{args.root}`.\n")
    print(render(rows))
    print(headline(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
