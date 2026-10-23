"""
Numeric grounding check - are the figures in an answer backed by sources?

Language models invent plausible numbers ("55% of users prefer web apps").
This module is a deterministic, model-free guardrail used in two places:

* at runtime (multi_agent.py): figures in one agent's output that do not
  appear in the run's tool results are marked "(unverified)" before the
  next agent sees them, and every check is reported in the reasoning view;
* offline (evals/): the grounding rate is one of the evaluation metrics.

What counts as a figure ("claim"): a number with a currency sign or a unit
(%, k, million, bn, ...), a decimal number, or any number >= 10. Years
(1900-2100), list markers, times, numbers inside code, URLs and domain names
are ignored. A claim is "supported" when the same numeric value appears
anywhere in the sources (search results, documents, the user's request,
today's date). It is a recall-oriented heuristic: a supported number can
still be used wrongly, and a derived number (e.g. a monthly price computed
from a yearly one) is reported as "not found", never as false.
"""

import re
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

_FENCE_RE = re.compile(r"```.*?(?:```|\Z)", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
# Domain names / citations like (360iresearch.com) or mordorintelligence.com
_DOMAIN_RE = re.compile(r"\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|ai|co|gov|edu|info|"
                        r"dev|app|news|uk|in|de|fr|jp|cn|us|ca|au|me|tv|so)\b", re.I)
_LIST_MARKER_RE = re.compile(r"^\s*\d{1,2}[.)]\s", re.M)
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")

_CLAIM_RE = re.compile(
    r"(?<![\w.,])"                                   # not part of an identifier/number
    r"(?P<cur>[$€£₹¥])?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?:[-\u2013\u2014]|to)\s?[$€£₹¥]?(?P<num2>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?))?"
    r"(?:\s?(?P<unit>%|percent\b|per\s?cent\b|[kKmMbB]\b|bn\b|mn\b|million\b|"
    r"billion\b|trillion\b|thousand\b|lakh\b|crore\b|x\b))?"
    r"(?![\w])",
    re.I,
)
_NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")

UNVERIFIED = "(unverified)"


@dataclass
class GroundingReport:
    total: int = 0
    supported: int = 0
    unsupported: List[str] = field(default_factory=list)
    checked: bool = True          # False when there were no sources to check against

    @property
    def rate(self) -> float:
        return 1.0 if self.total == 0 else self.supported / self.total

    def summary(self, label: str = "answer") -> str:
        if not self.checked:
            return f"Fact check ({label}): no sources to check figures against"
        if self.total == 0:
            return f"Fact check ({label}): no figures to verify"
        msg = (f"Fact check ({label}): {self.supported}/{self.total} figures "
               f"found in the sources")
        if self.unsupported:
            shown = ", ".join(self.unsupported[:8])
            more = f" (+{len(self.unsupported) - 8} more)" if len(self.unsupported) > 8 else ""
            msg += f"; not found: {shown}{more}"
        return msg


def _normalize(num: str) -> str:
    n = num.replace(",", "")
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n.lstrip("0") or "0"


def _prose(text: str) -> str:
    """Text with code, URLs, domains, list markers and times blanked out."""
    for rx in (_FENCE_RE, _INLINE_CODE_RE, _URL_RE, _DOMAIN_RE, _TIME_RE):
        text = rx.sub(" ", text)
    return _LIST_MARKER_RE.sub(" ", text)


def extract_claims(text: str) -> List[str]:
    """Distinct numeric claims (as written) in the prose of `text`."""
    seen, out = set(), []
    for m in _CLAIM_RE.finditer(_prose(text or "")):
        num, cur, unit = m.group("num"), m.group("cur"), m.group("unit")
        value = float(num.replace(",", ""))
        num2 = m.group("num2")
        is_year = ("." not in num and 1900 <= value <= 2100 and not cur and not unit
                   and (not num2 or 1900 <= float(num2.replace(",", "")) <= 2100))
        significant = bool(cur or unit) or "." in num or value >= 10
        if is_year or not significant:
            continue
        raw = m.group(0).strip()
        if raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def source_numbers(sources: Iterable[str]) -> set:
    nums = set()
    for s in sources:
        for n in _NUM_RE.findall(s or ""):
            nums.add(_normalize(n))
    return nums


def _claim_values(raw: str) -> List[str]:
    return [_normalize(n) for n in _NUM_RE.findall(raw)]


def check(text: str, sources: Iterable[str], min_source_chars: int = 200) -> GroundingReport:
    """Which figures in `text` appear in `sources`?"""
    sources = [s for s in sources if s]
    if sum(len(s) for s in sources) < min_source_chars:
        return GroundingReport(checked=False)
    known = source_numbers(sources)
    claims = extract_claims(text)
    unsupported = [c for c in claims
                   if not all(v in known for v in _claim_values(c))]
    return GroundingReport(total=len(claims), supported=len(claims) - len(unsupported),
                           unsupported=unsupported)


def annotate(text: str, unsupported: List[str]) -> str:
    """Mark each unsupported figure "(unverified)" in the prose of `text`.

    Code blocks are left untouched; already-marked figures are skipped.
    """
    if not unsupported:
        return text
    pattern = re.compile(
        r"(?<![\w.,])(" + "|".join(re.escape(u) for u in
                                   sorted(set(unsupported), key=len, reverse=True))
        + r")(?![\w])(?!\s*\(unverified\))")

    def _mark(segment: str) -> str:
        return pattern.sub(lambda m: f"{m.group(1)} {UNVERIFIED}", segment)

    out, pos = [], 0
    for fence in _FENCE_RE.finditer(text):
        out.append(_mark(text[pos:fence.start()]))
        out.append(fence.group(0))
        pos = fence.end()
    out.append(_mark(text[pos:]))
    return "".join(out)


def ground(text: str, sources: Iterable[str],
           mark: bool = True) -> "tuple[str, GroundingReport]":
    """check() + optional annotate(); returns (text, report)."""
    report = check(text, sources)
    if mark and report.checked and report.unsupported:
        text = annotate(text, report.unsupported)
    return text, report


def run_sources(task: str, tool_outputs: Optional[Iterable[str]] = None) -> List[str]:
    """Standard source set: tool outputs + the user's request + today's date."""
    try:
        from modules.prompt_rules import today_str
        today = today_str()
    except Exception:
        today = ""
    tool_outputs = [t for t in (tool_outputs or []) if t]
    if sum(len(t) for t in tool_outputs) < 200:
        return []          # nothing was looked up -> nothing to check against
    return tool_outputs + [task or "", today]
