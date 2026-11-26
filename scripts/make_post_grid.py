#!/usr/bin/env python3
"""The 60-square grid for post 1, dark.

Sixty squares, one per question: green = passed, red = failed, grey = never ran.
The point of a grid rather than two numbers is that the reader can COUNT the
denominator instead of being told it.

Layout matches the light version exactly, so the two can be swapped without
rewriting the post:

    rows 1-2   : 20 passed
    row  3     : 1 passed, 8 failed, 1 never ran   <- the quota dies at q29
    rows 4-6   : 30 never ran
    total      : 21 passed / 8 failed / 31 never ran = 60

Colours: green #3fb950, red #f85149, grey #30363d on #0d1117. Those are the
GitHub dark-theme values, so the card matches a README screenshot.

Usage:
    python scripts/make_post_grid.py [outdir]
"""

from __future__ import annotations

import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

BG = "#0d1117"
INK = "#e6edf3"
MUTED = "#8b949e"
FAINT = "#484f58"
GREEN = "#3fb950"
RED = "#f85149"
GREY = "#30363d"
RULE = "#21262d"

COLS, ROWS = 10, 6
# One cell per question, in order. q1..q28 ran; q29's quota expires; q30..q60 never ran.
CELLS = (["pass"] * 21) + (["fail"] * 8) + (["none"] * 31)
assert len(CELLS) == 60


def build(outdir: str) -> str:
    fig = plt.figure(figsize=(10.5, 5.75), dpi=200, facecolor=BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 10.5)
    ax.set_ylim(0, 5.75)
    ax.axis("off")

    # ---- headline (left) -------------------------------------------------
    ax.text(0.42, 5.32, "Same nightly run,", color=INK, fontsize=17,
            fontweight="bold", ha="left", va="top")
    ax.text(0.42, 4.94, "two very different numbers", color=INK, fontsize=17,
            fontweight="bold", ha="left", va="top")
    ax.text(0.42, 4.48, "60-question test suite. The API quota ran out at question 29.",
            color=MUTED, fontsize=8.6, ha="left", va="top")

    # ---- the grid --------------------------------------------------------
    cell, gap = 0.30, 0.075
    x0, y0 = 0.42, 1.02          # grid origin, bottom-left of cell 1
    for i, kind in enumerate(CELLS):
        col, row = i % COLS, i // COLS
        x = x0 + col * (cell + gap)
        y = y0 + (ROWS - 1 - row) * (cell + gap)
        face = {"pass": GREEN, "fail": RED, "none": GREY}[kind]
        edge = "none" if kind != "none" else "#3d444d"
        ax.add_patch(FancyBboxPatch((x, y), cell, cell,
                                    boxstyle="round,pad=0,rounding_size=0.045",
                                    facecolor=face, edgecolor=edge,
                                    linewidth=0.6 if kind == "none" else 0))

    # ---- legend ----------------------------------------------------------
    ly = 0.52
    for lx, colour, label in ((0.42, GREEN, "21 passed"),
                              (2.05, RED, "8 failed"),
                              (3.55, GREY, "31 never ran")):
        ax.add_patch(FancyBboxPatch((lx, ly), 0.20, 0.20,
                                    boxstyle="round,pad=0,rounding_size=0.03",
                                    facecolor=colour, edgecolor="none"))
        ax.text(lx + 0.30, ly + 0.10, label, color=MUTED, fontsize=8.4,
                ha="left", va="center")

    # ---- right column: the two numbers ----------------------------------
    rx = 6.30
    ax.plot([rx, rx], [4.98, 1.02], color=RULE, linewidth=1.1)

    ax.text(rx + 0.32, 4.86, "What the report showed", color=MUTED,
            fontsize=8.6, ha="left", va="top")
    ax.text(rx + 0.32, 4.52, "35%", color=RED, fontsize=34,
            fontweight="bold", ha="left", va="top")
    ax.text(rx + 0.32, 3.98, "21 ÷ 60", color=FAINT, fontsize=9.5,
            ha="left", va="top")

    ax.text(rx + 0.32, 3.38, "What actually happened", color=MUTED,
            fontsize=8.6, ha="left", va="top")
    ax.text(rx + 0.32, 3.04, "72.4%", color=GREEN, fontsize=34,
            fontweight="bold", ha="left", va="top")
    ax.text(rx + 0.32, 2.50, "21 ÷ 29", color=FAINT, fontsize=9.5,
            ha="left", va="top")
    ax.text(rx + 0.32, 2.22, "the ones that ran", color=FAINT, fontsize=9.5,
            ha="left", va="top")

    # ---- the clue --------------------------------------------------------
    ax.plot([rx + 0.32, 10.10], [1.82, 1.82], color=RULE, linewidth=1.0)
    ax.text(rx + 0.32, 1.62, "Clue: median response time (p50) fell",
            color=MUTED, fontsize=8.6, ha="left", va="top")
    ax.text(rx + 0.32, 1.34, "3.0 s → 0.9 s", color=INK, fontsize=11,
            fontweight="bold", ha="left", va="top")
    ax.text(rx + 0.32, 1.00, "Faster and worse at the same time means",
            color=FAINT, fontsize=8.2, ha="left", va="top")
    ax.text(rx + 0.32, 0.78, "most of the run is missing.", color=FAINT,
            fontsize=8.2, ha="left", va="top")

    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "post01-grid-dark.png")
    fig.savefig(path, facecolor=BG)
    plt.close(fig)
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "docs/posts/img"
    p = build(out)
    print(p)
    print(f"  {os.path.getsize(p):,} bytes")
