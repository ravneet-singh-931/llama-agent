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
# A number may be grouped with a comma (50,000) or with a *narrow* no-break
# space (50 000) - both appear in real search results and PDFs. Without this,
# a source reading "1 200 crates" did not back an answer that said "1200".
_NUM_RE = re.compile(r"\d{1,3}(?:[,\u202f\u00a0]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
# Typographic digits that a model sometimes emits (fullwidth, superscript,
# Arabic-Indic). They are folded to ASCII before any comparison.
_DIGIT_FOLD = str.maketrans("\uff10\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19"
                            "\u2070\u00b9\u00b2\u00b3\u2074\u2075\u2076\u2077\u2078\u2079"
                            "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669",
                            "01234567890123456789" "0123456789")

UNVERIFIED = "(unverified)"


@dataclass
class GroundingReport:
    total: int = 0
    supported: int = 0
    unsupported: List[str] = field(default_factory=list)
    checked: bool = True          # False when there were no sources to check against
    skip_reason: str = ""         # why `checked` is False, shown in the report

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
    n = num.translate(_DIGIT_FOLD).replace(",", "").replace("\u202f", "").replace("\u00a0", "")
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n.lstrip("0") or "0"


def _prose(text: str) -> str:
    """Text with code, URLs, domains, list markers and times blanked out."""
    text = (text or "").translate(_DIGIT_FOLD)
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


def _as_list(sources) -> List[str]:
    """Accept a list of documents *or* a single document.

    Passing one big string used to iterate it character by character: every
    source became 1 character long, the 200-character minimum was never met,
    and the check returned "not checked" instead of an answer. Silent, and in
    the safe direction - but it made a real report look clean.
    """
    if sources is None:
        return []
    if isinstance(sources, str):
        return [sources]
    return list(sources)


def source_numbers(sources: Iterable[str]) -> set:
    nums = set()
    for s in sources:
        for n in _NUM_RE.findall(s or ""):
            nums.add(_normalize(n))
    return nums


def _claim_values(raw: str) -> List[str]:
    return [_normalize(n) for n in _NUM_RE.findall(raw)]


def _name_unsupported(claim: str, known: set) -> str:
    """The specific figure(s) inside a claim that the sources do not contain.

    Keeps the unit and currency the answer used ("55%", "$1.2"): a bare "55"
    does not say what kind of number is missing, and the reader of the report
    is a human trying to decide whether the check is right.
    """
    values = _claim_values(claim)
    missing = [v for v in values if v not in known]
    if not missing:
        return claim
    if len(missing) == len(values):
        return claim                      # nothing in the claim was sourced
    out = []
    for part in re.split(r"\s*(?:[-–—]|to)\s*", claim):
        if any(_normalize(n) in missing for n in _NUM_RE.findall(part)):
            out.append(part.strip())
    return ", ".join(out) if out else ", ".join(missing)


def check(text: str, sources: Iterable[str], min_source_chars: int = 200) -> GroundingReport:
    """Which figures in `text` appear in `sources`?"""
    sources = [s for s in _as_list(sources) if s]
    if sum(len(s) for s in sources) < min_source_chars:
        # Not a pass and not a fail: there was nothing to check against.
        # Report the size so a reader can see *why* it was skipped instead of
        # reading a missing rate as a good one.
        rep = GroundingReport(checked=False)
        rep.skip_reason = f"only {sum(len(s) for s in sources)} chars of sources"
        return rep
    known = source_numbers(sources)
    claims = extract_claims(text)
    unsupported = [c for c in claims
                   if not all(v in known for v in _claim_values(c))]
    # A range like "40-50 million" is one claim in the regex but two facts to
    # a reader. Report the *unsupported member*, not the whole range: "1/2"
    # with "22 to 24" as the culprit was unreadable, and naming only "24"
    # (when 40 is sourced and 50 is not) tells you what to go and check.
    unsupported = [_name_unsupported(c, known) for c in unsupported]
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


def research_sources(tool_outputs: Optional[Iterable[str]] = None) -> List[str]:
    """Sources an answer *found* - tool outputs only, no task text.

    `run_sources` includes the user's request, because a figure the user gave
    you is legitimately "supported" in a budgeting answer. For a case whose
    whole point is "look it up", that is wrong: an answer that says "upgrade
    from 22 to 24" was credited for `22` because the *question* contained it,
    even though `22` came from the user and nothing was looked up. Use this
    when the question is about facts that must come from sources.
    """
    return [t for t in _as_list(tool_outputs) if t]


def run_sources(task: str, tool_outputs: Optional[Iterable[str]] = None) -> List[str]:
    """Standard source set: tool outputs + the user's request + today's date.

    The 200-character minimum used to live *here* as well as in `check()`.
    Two gates for one rule is how they drift apart: a 572-character web-search
    result was large enough to check at runtime (which passes tool outputs
    straight to `check()`) but not large enough to survive this function, so
    the eval skipped the check and reported a clean sheet. One gate, in
    `check()`, for both callers.
    """
    try:
        from modules.prompt_rules import today_str
        today = today_str()
    except Exception:
        today = ""
    tool_outputs = [t for t in _as_list(tool_outputs) if t]
    if not tool_outputs:
        # Nothing was looked up. Returning the task text here made a run with
        # zero tool output look like it had sources to check against, which is
        # how a budgeting answer got graded against its own prompt.
        return []
    return tool_outputs + [task or "", today]
