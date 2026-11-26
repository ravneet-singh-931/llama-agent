#!/usr/bin/env python3
"""Render the chart for post 1 (the 35% / 72.4% post).

Dark, minimal, one idea. Reads nothing at runtime — the two numbers are written
into the source deliberately, so a change to them is a change you can see in a
diff rather than something a data file can drift into.

The numbers, and where they come from:

    21 of 60 graded            -> 35.0%   (the number the report published)
    21 of 29 that actually ran -> 72.4%   (the number that describes the work)

29 = 21 passed + 8 failed. The other 31 never ran: the free tier's daily token
allowance ran out at case 29. Source: docs/EVAL_FINDINGS.md finding 6, and
tests/test_evals.py::test_a_half_run_report_is_inconclusive_not_a_pass_rate.

Usage:
    python scripts/make_post_chart.py [outdir]     # default: docs/posts/img
"""

from __future__ import annotations

import os
import sys

import matplotlib

matplotlib.use("Agg")  # no display needed
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

# --- the data -------------------------------------------------------------
PUBLISHED = 35.0        # 21 / 60, counting the 31 that never ran as failures
ACTUAL = 72.4           # 21 / 29, among the cases that were graded
PASSED, GRADED, RAN, TOTAL = 21, 29, 29, 60

# --- palette: dark, low chroma, colour used only to separate the two ideas --
BG = "#0d1117"          # page
PANEL = "#161b22"       # card
INK = "#e6edf3"         # primary text
MUTED = "#7d8590"       # secondary text
HIDDEN = "#30363d"      # the part that never ran
BAD = "#f85149"         # the number that lied
GOOD = "#3fb950"        # the number that held


def build(outdir: str) -> str:
    fig = plt.figure(figsize=(12, 7.5), dpi=200, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 7.5)
    ax.axis("off")

    # --- title ------------------------------------------------------------
    ax.text(0.85, 6.85, "One night. Two denominators.",
            color=INK, fontsize=27, fontweight="bold", ha="left", va="top")
    ax.text(0.85, 6.25, "The 31 cases that never ran were counted as failures.",
            color=MUTED, fontsize=13.5, ha="left", va="top")

    # --- the two cards ----------------------------------------------------
    def card(x, w, tag, tag_colour, big, small, sub, bar_frac, bar_colour):
        y, h = 2.62, 3.05
        ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.12",
            facecolor=PANEL, edgecolor="#21262d", linewidth=1.2))
        # tag
        ax.text(x + 0.34, y + h - 0.34, tag, color=tag_colour,
                fontsize=10.5, fontweight="bold", ha="left", va="top")
        # big number
        ax.text(x + 0.34, y + h - 1.12, big, color=tag_colour,
                fontsize=44, fontweight="bold", ha="left", va="top")
        # what the number is
        ax.text(x + 0.34, y + h - 1.92, small, color=INK,
                fontsize=11.5, ha="left", va="top")
        # the run bar: filled = what the percentage is over
        bx, bw, by, bh = x + 0.34, w - 0.68, y + 0.62, 0.11
        ax.add_patch(FancyBboxPatch((bx, by), bw, bh,
                     boxstyle="round,pad=0,rounding_size=0.055",
                     facecolor=HIDDEN, edgecolor="none"))
        ax.add_patch(FancyBboxPatch((bx, by), bw * bar_frac, bh,
                     boxstyle="round,pad=0,rounding_size=0.055",
                     facecolor=bar_colour, edgecolor="none"))
        ax.text(bx, by - 0.30, sub, color=MUTED, fontsize=10.5,
                ha="left", va="top")

    card(0.85, 4.85, "WHAT THE REPORT PRINTED", BAD,
         f"{PUBLISHED:.0f}%", f"{PASSED} passed  ÷  {TOTAL} cases",
         f"the 31 that never ran are in the denominator  ·  {GRADED}/{TOTAL} graded",
         PASSED / TOTAL, BAD)

    card(6.30, 4.85, "WHAT THE RUN ACTUALLY SHOWS", GOOD,
         f"{ACTUAL}%", f"{PASSED} passed  ÷  {RAN} that ran",
         "of the cases that were graded  ·  the night before was 75%",
         PASSED / RAN, GOOD)

    # --- the one line that carries the post -------------------------------
    ax.text(0.85, 1.62,
            "The clue was in the same report:  median response time fell 3.0 s → 0.9 s.",
            color=INK, fontsize=13.5, ha="left", va="top")
    ax.text(0.85, 1.16,
            "Almost nothing was being answered. Quality does not fall while speed improves.",
            color=MUTED, fontsize=11.5, ha="left", va="top")

    # --- footer -----------------------------------------------------------
    ax.text(0.85, 0.42,
            "60-question suite, one nightly run.  "
            "Source: docs/EVAL_FINDINGS.md, finding 6.",
            color="#484f58", fontsize=9.5, ha="left", va="top")

    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "post01-two-denominators-dark.png")
    fig.savefig(path, facecolor=BG, bbox_inches=None)
    plt.close(fig)
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/posts/img"
    written = build(out)
    print(written)
    print(f"  {os.path.getsize(written):,} bytes")
