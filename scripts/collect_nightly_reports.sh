#!/usr/bin/env bash
# Collect the nightly eval reports that GitHub still has, before they expire.
#
# Why not `gh run download -n NAME`?
#   It resolves artifact names differently across gh versions, and on some
#   versions a name that does not match falls through to the `--pattern` branch
#   and reports "no valid artifacts found to download" -- which says nothing
#   about whether the artifact exists. This script asks the REST API for the
#   artifact ID and downloads that ID, so name matching cannot silently fail.
#
# Usage
#   bash scripts/collect_nightly_reports.sh [LIMIT] [OUTDIR]
#     LIMIT   how many successful runs to try (default 40)
#     OUTDIR  where to put them (default reports)
#
# Then
#   python scripts/nightly_trend.py reports/
#
# Notes
#   * eval-report-* artifacts have 30-day retention. Runs older than that are
#     reported as "no artifact" and skipped, not treated as errors.
#   * Requires: gh (authenticated), unzip, python3.

set -uo pipefail

LIMIT="${1:-40}"
OUTDIR="${2:-reports}"
WORKFLOW="nightly-evals.yml"

command -v gh    >/dev/null || { echo "gh not installed"    >&2; exit 1; }
command -v unzip >/dev/null || { echo "unzip not installed" >&2; exit 1; }

REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null)
if [ -z "${REPO:-}" ]; then
  echo "cannot determine the repo -- run this from inside the clone" >&2
  exit 1
fi

echo "repo:     $REPO"
echo "workflow: $WORKFLOW"
echo "output:   $OUTDIR"
echo

mkdir -p "$OUTDIR"

# Successful runs only: a failed run has no report worth tabulating, and
# including one would put a gap in the series that reads as a data point.
# --status does not exist on gh 2.23 (it was added later), so filter in jq
# instead. This is the same version-portability trap as the download itself:
# a flag that exists on the author's machine and not on yours.
mapfile -t RUNS < <(gh run list --repo "$REPO" --workflow "$WORKFLOW" \
                      --limit "$LIMIT" \
                      --json databaseId,conclusion \
                      --jq '.[] | select(.conclusion == "success") | .databaseId')

if [ ${#RUNS[@]} -eq 0 ]; then
  echo "no successful runs found" >&2
  exit 2
fi

echo "found ${#RUNS[@]} successful runs"
echo

ok=0; missing=0; failed=0

for run in "${RUNS[@]}"; do
  name="eval-report-$run"
  dir="$OUTDIR/$run"

  # Ask for the artifact ID by exact name. `head -1` because a re-run can leave
  # two artifacts with the same name in rare cases; the newest is first.
  aid=$(gh api "repos/$REPO/actions/runs/$run/artifacts" \
          --jq ".artifacts[] | select(.name == \"$name\") | .id" 2>/dev/null | head -1)

  if [ -z "${aid:-}" ]; then
    echo "  no artifact   $run  (expired or never uploaded)"
    missing=$((missing + 1))
    continue
  fi

  mkdir -p "$dir"
  # The zip endpoint 302s to blob storage; follow it with -L.
  if gh api "repos/$REPO/actions/artifacts/$aid/zip" > "$dir/$name.zip" 2>/dev/null \
     && unzip -o -q "$dir/$name.zip" -d "$dir" 2>/dev/null \
     && [ -f "$dir/eval-today.json" ]; then
    rm -f "$dir/$name.zip"
    echo "  ok            $run  (artifact $aid)"
    ok=$((ok + 1))
  else
    rm -f "$dir/$name.zip"
    rmdir "$dir" 2>/dev/null || true
    echo "  download FAILED $run  (artifact $aid)"
    failed=$((failed + 1))
  fi
done

echo
echo "collected $ok, missing $missing, failed $failed"

if [ "$ok" -eq 0 ]; then
  echo
  echo "Nothing collected. If every line said 'no artifact', the 30-day" >&2
  echo "retention window has passed for all of them." >&2
  exit 2
fi

echo
echo "next: python scripts/nightly_trend.py $OUTDIR"
