"""Check that an answer cites documents it was actually given.

Why this exists
---------------
The document-answer prompt is emphatic::

    Cite the source for EVERY claim by appending the exact file name and
    chunk ... Never cite a file that is not in the excerpts.

Nothing verified either half of that. A model that loses track and writes
"(Q4-budget.xlsx, chunk 3)" for a question answered from a different document
produces something that *looks* rigorously sourced -- the citation format is
exactly right -- and a reader has no way to tell it was invented. The eval
suite's `grounding` check tests whether numbers appear in the sources; it does
not test whether the *named* source exists.

Scope, stated honestly
----------------------
This validates the citation against what the model was shown:

  * the file name must be one of the excerpts it received
  * the chunk index must be one of that file's excerpted chunks

It does NOT verify that the sentence next to the citation is supported by that
chunk. That needs a judge (``OptimizedRAG.verify_answer`` does the
claim-level version for the RAG path) and is deliberately out of scope here.

This is a detector, not a blocker: it returns a report the caller can act on
(annotate the answer, log a metric, fail an eval). Quietly rewriting or
stripping citations would hide the model's mistake instead of surfacing it.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple

__all__ = ["parse_excerpt_sources", "find_citations", "check_citations", "CitationReport"]

# "--- Source: CRM-3.docx (chunk 2) ---"  -- the label api.py writes.
_SOURCE_LABEL_RE = re.compile(
    r"^---\s*Source:\s*(?P<file>.+?)\s*\(chunk\s*(?P<chunk>\d+)\)\s*---\s*$",
    re.M,
)

# "(CRM-3.docx, chunk 2)" and the looser "(CRM-3.docx chunk 2)".
_CITATION_RE = re.compile(
    r"\(\s*(?P<file>[^(),]+?)\s*[,;]?\s*chunk\s*(?P<chunk>\d+)\s*\)",
    re.I,
)

# A bare "according to CRM-3.docx" is not a citation we can check (no chunk),
# but citing it is still common; counted separately as "unnumbered".
_BARE_FILE_RE = re.compile(r"\b[\w][\w .\-()]{0,80}?\.(?:pdf|docx?|txt|md|csv|xlsx?|json|py)\b", re.I)


class CitationReport(dict):
    """dict subclass so callers can persist it to JSON and read it as a dict."""


def parse_excerpt_sources(context: str) -> List[Tuple[str, int]]:
    """Return [(file, chunk)] for every ``--- Source: ... ---`` label."""
    return [(m.group("file").strip(), int(m.group("chunk")))
            for m in _SOURCE_LABEL_RE.finditer(context or "")]


def find_citations(answer: str) -> List[Tuple[str, int]]:
    """Return [(file, chunk)] for every ``(file, chunk N)`` in the answer."""
    return [(m.group("file").strip(), int(m.group("chunk")))
            for m in _CITATION_RE.finditer(answer or "")]


def _norm(name: str) -> str:
    """Compare file names loosely: case, surrounding punctuation and path
    separators differ between the label and what a model writes."""
    return re.sub(r"[\s'\"`]+", " ", (name or "").strip().strip(".,;")).lower()


def check_citations(answer: str, context: str) -> CitationReport:
    """Validate the answer's citations against the excerpts in ``context``.

    Returns a report::

        {
          "cited":     [(file, chunk), ...],   # parsed from the answer
          "available": [(file, chunk), ...],   # labels in the context
          "invalid":   [(file, chunk), ...],   # cited but never shown
          "unsourced": bool,                   # claims but no citation at all
          "ok":        bool,
        }

    An answer with no citations at all reports ``unsourced: True`` -- the
    prompt asks for one per claim, so silence is a finding, not a pass.
    """
    available = parse_excerpt_sources(context)
    cited = find_citations(answer)

    avail_set = {(_norm(f), c) for f, c in available}
    avail_files = {_norm(f) for f, _ in available}

    invalid = [(f, c) for f, c in cited if (_norm(f), c) not in avail_set]
    # A file cited with a chunk number that was not shown for it is invalid;
    # a file that was not shown at all is the more serious case and is kept
    # distinct so the report says which happened.
    invented_files = [(f, c) for f, c in invalid if _norm(f) not in avail_files]

    has_prose = bool(re.sub(r"\([^)]*\)", "", answer or "").strip())
    unsourced = has_prose and not cited and bool(available)

    return CitationReport(
        cited=cited,
        available=available,
        invalid=invalid,
        invented_files=invented_files,
        unsourced=unsourced,
        ok=not invalid and not unsourced,
    )
