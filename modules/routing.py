"""
Intent routing for the multi-agent chat path.

Extracted verbatim from api.py (which had grown to ~3.5k lines) so the
routing rules can be read and unit-tested on their own. Nothing here needs
a FastAPI app, a database or a vector store: it is pure pattern matching
over the incoming message text.

What lives here:
  * detect_agent_prefix / detect_team_prefix  - explicit "@agent" / "@team"
    prefixes, which always win over keyword routing.
  * AhoCorasick + the keyword groups          - multi-pattern keyword
    matching for implicit routing.
  * route_multi_agent                         - the decision function used by
    both /chat and the evals.
  * extract_name_from_message / is_name_question - the small "what is my
    name" helpers used by the chat path.

api.py re-exports every public name below, so `from api import
route_multi_agent` keeps working.
"""

import re
from collections import deque
from typing import Optional

_NAME_TOKEN = r"[a-z][a-z0-9'.-]*"
_NAME_EXTRACT_PATTERNS = [
    # Strong signals - accept whatever name-like phrase follows.
    re.compile(r"my name is\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})", re.IGNORECASE),
    re.compile(r"call me\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})", re.IGNORECASE),
    re.compile(r"i am called\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})", re.IGNORECASE),
    # Weak signals - "i am X" / "i'm X" can catch verbs ("i am happy"),
    # so these are vetoed against the stopword set below.
    re.compile(r"\bi am\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r")?)", re.IGNORECASE),
    re.compile(r"\bi'm\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r")?)", re.IGNORECASE),
]


_NAME_STOPWORDS = {
    "asking", "wondering", "trying", "hoping", "planning", "working",
    "using", "building", "testing", "running", "starting", "checking",
    "looking", "interested", "learning", "going", "gonna", "wanting",
    "willing", "ready", "here", "back", "sorry", "happy", "fine", "good",
    "great", "new", "not", "just", "a", "an", "the", "my", "your", "his",
    "her", "this", "that", "these", "those", "in", "on", "at", "from",
    "with", "for", "to", "of", "and", "or", "but", "me", "we", "you",
    "they", "it", "am", "is", "are", "was", "were", "be", "have", "has",
    "had", "do", "does", "did", "very", "really", "actually", "also",
    "too", "now", "today", "ok", "okay", "sure", "please", "thanks",
    "need", "wish", "want", "know", "someone", "something",
}


_NAME_QUESTION_PATTERNS = [
    re.compile(r"what.{0,8}(?:is|'s)\s+my name", re.IGNORECASE),
    re.compile(r"what'?s\s+my name", re.IGNORECASE),
    re.compile(r"do you (?:know|remember) my name", re.IGNORECASE),
    re.compile(r"what do you call me", re.IGNORECASE),
    re.compile(r"(?:tell|remind).{0,12}my name", re.IGNORECASE),
    re.compile(r"who am i", re.IGNORECASE),
]


def extract_name_from_message(message: str) -> Optional[str]:
    """Return a name if the message introduces one, otherwise None."""
    for i, pattern in enumerate(_NAME_EXTRACT_PATTERNS):
        match = pattern.search(message)
        if not match:
            continue
        tokens = [t for t in match.group(1).split()]
        # Trim trailing stopwords: "call me Mary Jane please" -> "Mary Jane"
        while tokens and tokens[-1].lower() in _NAME_STOPWORDS:
            tokens.pop()
        if not tokens:
            continue
        # First token must not be a stopword: "my name is the best" -> reject
        if tokens[0].lower() in _NAME_STOPWORDS:
            continue
        if i >= 3:  # weak "i am X" / "i'm X" patterns - veto any stopword
            if any(t.lower() in _NAME_STOPWORDS for t in tokens):
                continue
        name = " ".join(tokens).strip(".,!?;:()").strip()
        return name[:50]
    return None


def is_name_question(message: str) -> bool:
    """True if the message asks the agent what the user's name is."""
    return any(p.search(message) for p in _NAME_QUESTION_PATTERNS)


# Agent prefix routing: "@AgentType rest of message" explicitly routes
# to a specific agent. Supported prefixes (case-insensitive):
#   @Research Agent, @Coder Agent, @Writer Agent, @Planner Agent, @Analyzer Agent
_AGENT_PREFIX_MAP = {
    "research": "research",
    "coder": "coder",
    "writer": "writer",
    "planner": "planner",
    "analyzer": "analyzer",
}
_AGENT_PREFIX_RE = re.compile(
    r"^@\s*(" + "|".join(re.escape(k) for k in _AGENT_PREFIX_MAP) + r")\s+agent\s+(.*)",
    re.IGNORECASE,
)


def detect_agent_prefix(message: str) -> tuple:
    """If the message starts with @AgentType Agent, return
    (agent_type, stripped_message). Otherwise return (None, message).

    '@Research Agent tell me the latest news' → ('research', 'tell me the latest news')
    '@Coder Agent write a function' → ('coder', 'write a function')
    """
    m = _AGENT_PREFIX_RE.match(message.strip())
    if m:
        return _AGENT_PREFIX_MAP[m.group(1).lower()], m.group(2)
    return None, message


_TEAM_PREFIX_RE = re.compile(r"^@\s*team\b[\s:,-]*(.*)", re.IGNORECASE | re.DOTALL)


def detect_team_prefix(message: str) -> tuple:
    """'@team build a launch plan' -> (True, 'build a launch plan')."""
    m = _TEAM_PREFIX_RE.match(message.strip())
    if m and m.group(1).strip():
        return True, m.group(1).strip()
    return False, message


# ---------- shared multi-agent routing keywords (tight, intent-based) ---------
# Only clearly specialized requests are sent to the multi-agent system. Loose
# words like "what is", "how does", "python", "api" etc. are NOT enough — those
# go to the normal agent, which gives clean, concise answers.
CODE_KEYWORDS = ["write code", "write a function", "write python", "write a script",
                 "code to ", "code for ", "debug", "fix bug", "code review",
                 "refactor", "implement", "program to", "create a function",
                 "write a class", "build a script"]
WRITER_KEYWORDS = ["write an article", "write a blog", "write content",
                   "write an essay", "write a story", "creative writing",
                   "copywriting", "write a report", "write documentation",
                   "write a post", "write a letter"]
PLANNER_KEYWORDS = ["create a plan", "make a plan", "project plan", "action plan",
                    "roadmap", "strategy", "milestones", "timeline"]
ANALYZER_KEYWORDS = ["analyze", "analysis", "trends", "data analysis",
                     "evaluate", "assess", "insights"]
RESEARCH_KEYWORDS = ["research", "compare", "difference between",
                     "pros and cons", "investigate"]


# Coding intent as a pattern, not a phrase list: a build verb, up to three
# filler words, then a code noun or language ("write a Python function",
# "write SQL to...", "create a React component", "give me a bash script").
# Phrase lists missed every request that put a word between the verb and the
# noun. Topic words (about/on/regarding/for/of) break the match, so "write an
# essay on functional programming" stays with the writer; an explicit writer or
# planner phrase also wins over the pattern ("write documentation for a CLI").
_CODE_LANGS = (r"python|javascript|typescript|js|ts|sql|bash|shell|powershell|go|golang|rust"
               r"|java|kotlin|swift|c\+\+|c#|php|ruby|html|css|react|vue|regex|dockerfile")
_CODE_NOUNS = (r"functions?|class(?:es)?|scripts?|programs?|methods?|snippets?|code|algorithms?"
               r"|regexp?|quer(?:y|ies)|components?|(?:unit )?tests?|endpoints?|cli|decorators?"
               r"|hooks?|modules?")

CODE_INTENT_RE = re.compile(
    r"\b(?:write|create|build|implement|generate|make|code|give me)\b"
    r"(?:\s+(?!(?:about|on|regarding|for|of)\b)[\w+#.-]+){0,3}?"
    rf"\s+(?:{_CODE_LANGS}|{_CODE_NOUNS})(?![\w+#])",
    re.I,
)


# Requests about CURRENT/FRESH information. These must reach the research
# agent (which can call the web_search tool) instead of the general ReAct
# agent, which answers from static knowledge and would return stale data.
# Intent-based only - no message text is hardcoded.
RECENCY_KEYWORDS = [
    "latest", "recent", "breaking", "news", "headline", "headlines",
    "current events", "what's new", "what is new", "update on",
    "this week", "this month", "this year", "today's", "today's news",
    "news today", "just happened", "announced", "released today",
]


class AhoCorasick:
    """Aho-Corasick multi-pattern matcher for efficient intent routing.

    Builds a trie of all routing keywords (lowercased) with failure links so a
    single O(text) scan finds every keyword occurrence. Replaces the previous
    O(patterns * text) linear ``any(kw in message)`` scans in route_multi_agent
    and makes adding new trigger phrases O(1) at build time.
    """

    def __init__(self):
        self._next = [{}]
        self._fail = [0]
        self._out = [set()]
        self._patterns = []
        self._built = False

    def add(self, pattern: str, payload) -> None:
        pattern = pattern.lower()
        node = 0
        for ch in pattern:
            nxt = self._next[node]
            if ch not in nxt:
                nxt[ch] = len(self._next)
                self._next.append({})
                self._fail.append(0)
                self._out.append(set())
            node = nxt[ch]
        idx = len(self._patterns)
        self._patterns.append((pattern, payload))
        self._out[node].add(idx)
        self._built = False

    def build(self) -> None:
        from collections import deque
        q = deque()
        for ch, nxt in self._next[0].items():
            self._fail[nxt] = 0
            q.append(nxt)
        while q:
            r = q.popleft()
            for ch, s in self._next[r].items():
                q.append(s)
                f = self._fail[r]
                while f and ch not in self._next[f]:
                    f = self._fail[f]
                self._fail[s] = self._next[f].get(ch, 0)
                self._out[s] |= self._out[self._fail[s]]
        self._built = True

    def find(self, text: str):
        """Yield (pattern, payload, end_index) for every match in text."""
        if not self._built:
            self.build()
        text = text.lower()
        node = 0
        for i, ch in enumerate(text):
            while node and ch not in self._next[node]:
                node = self._fail[node]
            node = self._next[node].get(ch, 0)
            for idx in self._out[node]:
                pattern, payload = self._patterns[idx]
                yield pattern, payload, i

    def find_payloads(self, text: str):
        """Return the set of distinct payloads matched anywhere in text."""
        return {payload for _, payload, _ in self.find(text)}


# Build the routing automaton once from the keyword groups above.
_ROUTING_AC = AhoCorasick()

for _kw in CODE_KEYWORDS:
    _ROUTING_AC.add(_kw, "coder")
for _kw in WRITER_KEYWORDS:
    _ROUTING_AC.add(_kw, "writer")
for _kw in PLANNER_KEYWORDS:
    _ROUTING_AC.add(_kw, "planner")
for _kw in ANALYZER_KEYWORDS:
    _ROUTING_AC.add(_kw, "analyzer")
for _kw in RESEARCH_KEYWORDS:
    _ROUTING_AC.add(_kw, "research")
for _kw in RECENCY_KEYWORDS:
    _ROUTING_AC.add(_kw, "research")
_ROUTING_AC.build()


# Priority when several agent types match (highest priority first).
_ROUTING_PRIORITY = ("coder", "writer", "planner", "analyzer", "research")


def route_multi_agent(message: str):
    """Return (use_multi_agent, agent_type) for a message.

    Checks @Agent prefix first (e.g. '@Research Agent ...'), then routes via the
    Aho-Corasick keyword automaton (all matches collected, highest-priority
    agent wins), then falls back to the general ReAct agent.
    """
    # @Agent prefix: explicit routing
    agent_type, _ = detect_agent_prefix(message)
    if agent_type is not None:
        return True, agent_type

    matched = _ROUTING_AC.find_payloads(message.lower())
    if (CODE_INTENT_RE.search(message)
            and not matched & {"writer", "planner"}):
        matched.add("coder")
    if not matched:
        return False, "research"
    for agent in _ROUTING_PRIORITY:
        if agent in matched:
            return True, agent
    return False, "research"


# Agent team (static definitions - names match multi_agent.py + the frontend)
AGENT_KEYS = ["research", "coder", "writer", "analyzer", "planner"]
# MCP tool name -> agent key(s) it drives. "collaborate" runs every agent.
_MCP_TOOL_TO_AGENT = {
    "research_agent": ["research"],
    "coder_agent": ["coder"],
    "writer_agent": ["writer"],
    "analyzer_agent": ["analyzer"],
    "planner_agent": ["planner"],
    "collaborate": AGENT_KEYS,
}
