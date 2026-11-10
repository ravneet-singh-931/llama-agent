"""
Deterministic answer checks for the evaluation suite.

Every check is input-agnostic: the same rules run on every case, and a case
only declares WHAT it expects (needs sources, contains code of a language,
must mention X), never HOW a specific answer should look.
"""

import ast
import json
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Dict, List, Optional

from modules import grounding

_FENCE_BLOCK_RE = re.compile(r"```([\w+#.-]*)[^\n]*\n(.*?)(?:```|\Z)", re.S)
_TEAM_HEADER_RE = re.compile(r"^### .+ Agent\s*$", re.M)

# Self-referential process talk that should never reach a user. Generic -
# it describes the failure mode, not a particular answer.
# Self-referential process talk that should never reach a user. Generic -
# it describes the failure mode, not a particular answer.
#
# The bare nouns ("handoff", "team members") are deliberately NOT listed on
# their own. They are ordinary words in real content -- "the handoff between
# phases", "team members get access on day 3" -- and matching them flagged
# correct answers as leaks. What must never appear is an agent describing the
# *pipeline it is part of*: the patterns below all carry that context, so they
# fire on "pass this to the other agents" and stay quiet on "the handoff
# between phases".
META_PATTERNS = [
    r"\bmy (?:step|part|role|assignment)\b",
    r"\b(?:other|another|the other|remaining) agents?\b",
    r"\b(?:(?:the|this|my|our) agent team|my team)\b",
    r"\bprevious (?:step|agent)\b",
    r"\bsource material \d\b",
    r"\b(?:pass(?:ing)? (?:this|it|these) (?:to|on to)|hand(?:ing)? (?:this|it) off to)\b",
    r"\b(?:as|being) (?:the|a|an) (?:\w+ )?agent\b",
    r"\bnot (?:my|part of my) (?:job|task|step|responsibility)\b",
    r"\b(?:outside|beyond) (?:the scope of )?my (?:step|task)\b",
]
_META_RE = re.compile("|".join(META_PATTERNS), re.I)

_NON_LATIN_RE = re.compile(
    "[\u0400-\u04ff\u0600-\u06ff\u0900-\u097f\u3040-\u30ff\u3400-\u4dbf"
    "\u4e00-\u9fff\uac00-\ud7af]")
_LATIN_RE = re.compile(r"[A-Za-z]")

FAILURE_MARKERS = [
    "couldn't complete that task",
    "could not complete this step",
    "stream timed out",
    "returned an empty answer",
    "i couldn't produce an answer",
    "so i can't answer right now",         # llm_guard.friendly_llm_error (429)
    "error while generating response",
]

# The app's own notices when the model provider failed (llm_guard.friendly_llm_error:
# rate limit or outage). Such an answer says nothing about quality, so the case
# is not graded (and not passed) instead of being judged.
PROVIDER_FAILURE_MARKERS = [
    "so i can't answer right now",
    "the ai model is unavailable right now",
]


def is_provider_failure(answer: str) -> bool:
    low = (answer or "").lower()
    return any(m in low for m in PROVIDER_FAILURE_MARKERS)


_LANG_ALIASES = {
    "python": {"python", "py", "python3"},
    "javascript": {"javascript", "js", "jsx", "ts", "typescript", "html"},
    "html": {"html", "htm"},
    "sql": {"sql", "postgresql", "postgres", "mysql", "sqlite"},
    "json": {"json"},
}
_STRUCTURAL_TAGS = {"html", "head", "body", "script", "style", "table", "thead",
                    "tbody", "ul", "ol", "form", "section", "header", "footer",
                    "main", "nav", "div", "button", "select"}


def prose(text: str) -> str:
    """Answer text without code blocks and without the UI's team headers."""
    return _TEAM_HEADER_RE.sub(" ", _FENCE_BLOCK_RE.sub(" ", text or ""))


_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def inline_code_spans(text: str) -> List[str]:
    """Single-backtick snippets, e.g. ``use `len(x) == 0` to test it``."""
    return [m.group(1) for m in _INLINE_CODE_RE.finditer(text or "")]


def _parses_as_python(snippet: str) -> bool:
    """True when `snippet` is a real Python expression/statement."""
    snippet = (snippet or "").strip()
    if not snippet:
        return False
    try:
        ast.parse(snippet)
    except SyntaxError:
        return False
    return True


def code_blocks(text: str) -> List[Dict[str, str]]:
    return [{"lang": (m.group(1) or "").lower(), "code": m.group(2)}
            for m in _FENCE_BLOCK_RE.finditer(text or "")]


# ------------------------------------------------------------------ checks

def check_completed(answer: str) -> Dict:
    low = (answer or "").lower()
    hit = next((m for m in FAILURE_MARKERS if m in low), None)
    ok = bool((answer or "").strip()) and hit is None
    return {"pass": ok, "detail": "" if ok else (f"failure marker: {hit}" if hit else "empty answer")}


def check_english(answer: str, max_ratio: float = 0.01) -> Dict:
    text = prose(answer)
    other = len(_NON_LATIN_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    ratio = other / max(1, other + latin)
    return {"pass": ratio <= max_ratio, "detail": f"non-Latin letters: {other}" if other else ""}


def check_no_meta(answer: str) -> Dict:
    hits = sorted({m.group(0).lower() for m in _META_RE.finditer(prose(answer))})
    return {"pass": not hits, "detail": ", ".join(hits)}


def check_fences(answer: str) -> Dict:
    n = (answer or "").count("```")
    return {"pass": n % 2 == 0, "detail": "" if n % 2 == 0 else "unclosed code block"}


class _TagBalance(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: List[str] = []
        self.errors: List[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _STRUCTURAL_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag not in _STRUCTURAL_TAGS:
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.errors.append(f"<{self.stack.pop()}> not closed")
            self.stack.pop()
        else:
            self.errors.append(f"stray </{tag}>")


def validate_code(lang: str, code: str) -> Optional[str]:
    """Return an error string, or None when the code looks valid."""
    lang = (lang or "").lower()
    if lang in _LANG_ALIASES["python"]:
        try:
            ast.parse(code)
        except SyntaxError as e:
            return f"python syntax error line {e.lineno}: {e.msg}"
    elif lang in _LANG_ALIASES["json"]:
        try:
            json.loads(code)
        except ValueError as e:
            return f"invalid json: {e}"
    elif lang in _LANG_ALIASES["html"]:
        p = _TagBalance()
        p.feed(code)
        p.close()
        errors = p.errors + [f"<{t}> not closed" for t in p.stack]
        if errors:
            return "html: " + "; ".join(errors[:4])
    return None


def check_code(answer: str, expected_lang: Optional[str]) -> Dict:
    blocks = code_blocks(answer)
    errors = [e for b in blocks if (e := validate_code(b["lang"], b["code"]))]
    if expected_lang:
        aliases = _LANG_ALIASES.get(expected_lang, {expected_lang})
        if not any(b["lang"] in aliases or not b["lang"] for b in blocks):
            # A short answer may put the snippet inline ("use `len(x) == 0`")
            # instead of fenced. The judge scores those answers 5.0 -- the
            # request WAS answered -- so calling it a failure grades formatting,
            # not correctness. Judge an inline span as code when it parses.
            inline = inline_code_spans(answer)
            if expected_lang == "python" and any(_parses_as_python(i) for i in inline):
                return {"pass": True, "detail": ""}
            return {"pass": False, "detail": f"no {expected_lang} code block"}
    return {"pass": not errors, "detail": "; ".join(errors)}


def _today_tokens() -> Dict[str, str]:
    tz = os.getenv("APP_TIMEZONE", "Asia/Kolkata")
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo(tz))
    except Exception:
        now = datetime.now(timezone.utc)
    return {"year": str(now.year), "month": now.strftime("%B"), "day": str(now.day)}


def check_includes(answer: str, any_of: List[str]) -> Dict:
    tokens = _today_tokens()
    wanted = [w.format(**tokens) for w in any_of]
    low = (answer or "").lower()
    ok = any(w.lower() in low for w in wanted)
    return {"pass": ok, "detail": "" if ok else f"none of {wanted}"}


def check_excludes(answer: str, none_of: List[str]) -> Dict:
    hits = [w for w in none_of if w.lower() in (answer or "").lower()]
    return {"pass": not hits, "detail": ", ".join(hits)}


def check_length(answer: str, max_words: int) -> Dict:
    n = len(prose(answer).split())
    return {"pass": n <= max_words, "detail": f"{n} words (max {max_words})"}


def check_min_length(answer: str, min_words: int) -> Dict:
    """A floor on length: an essay case cannot pass on one sentence.

    Without this, "write an essay" graded on keywords alone passes a 12-word
    answer that name-drops the right vocabulary.
    """
    n = len(prose(answer).split())
    return {"pass": n >= min_words, "detail": f"{n} words (min {min_words})"}


def check_sources(tool_calls: List[Dict]) -> Dict:
    searched = [t for t in tool_calls if t.get("name") == "web_search"]
    return {"pass": bool(searched), "detail": f"{len(searched)} web searches"}


def check_grounding(answer: str, prompt: str, tool_calls: List[Dict],
                    threshold: float) -> Optional[Dict]:
    srcs = grounding.run_sources(prompt, [t.get("result", "") for t in tool_calls])
    report = grounding.check(prose(answer), srcs)
    if not report.checked or report.total == 0:
        return None                      # nothing to measure for this case
    return {"pass": report.rate >= threshold, "rate": round(report.rate, 3),
            "total": report.total, "unsupported": report.unsupported[:12],
            "detail": f"{report.supported}/{report.total} figures found in sources"}


def check_route(route: Optional[str], expected: str) -> Dict:
    """The request reached the intended handler (general / agent:x / team)."""
    ok = route == expected
    return {"pass": ok, "detail": "" if ok else f"routed to {route}, expected {expected}"}


def evaluate(case: Dict, answer: str, tool_calls: List[Dict],
             grounding_threshold: float = 0.7,
             route: Optional[str] = None) -> Dict[str, Dict]:
    """Run every applicable check for a case; returns {name: result}."""
    spec = case.get("checks", {})
    results = {
        "completed": check_completed(answer),
        "english": check_english(answer),
        "no_meta": check_no_meta(answer),
        "fences_closed": check_fences(answer),
        "code_valid": check_code(answer, spec.get("code")),
    }
    if spec.get("must_include_any"):
        results["includes"] = check_includes(answer, spec["must_include_any"])
    if spec.get("must_not_include"):
        results["excludes"] = check_excludes(answer, spec["must_not_include"])
    if spec.get("max_words"):
        results["length"] = check_length(answer, spec["max_words"])
    if spec.get("min_words"):
        results["min_length"] = check_min_length(answer, spec["min_words"])
    if case.get("route") and route is not None:
        results["route"] = check_route(route, case["route"])
    if spec.get("needs_sources"):
        results["used_search"] = check_sources(tool_calls)
    # Grounding only means something for a case that is *about* facts from
    # retrieved sources. It was applied to every case that happened to have
    # tool output, which quietly graded planning answers against a web search
    # they were never supposed to rely on: a migration plan's budget figures,
    # headcount and timelines are derived from the request, so "0/13 figures
    # found in sources" is not a defect in the answer. It failed 7 cases that
    # the judge scored 4-5, and made the grounding rate (0.28) meaningless.
    if spec.get("needs_sources"):
        g = check_grounding(answer, case.get("prompt", ""), tool_calls, grounding_threshold)
        if g is not None:
            results["grounding"] = g
    return results
