"""
LLM traffic guard - keeps every model call inside the provider's rate limits.

Groq's free ("on_demand") tier caps INPUT tokens per minute per model (e.g.
7,000 ITPM for qwen/qwen3.8-27b). Agent tool loops resend the whole
conversation every round, and rich search results make that grow fast, so a
single research answer could exceed the limit (HTTP 413 "Request too large")
or burn the minute's budget (HTTP 429). This module is applied at the one
choke point every completion passes through (InstrumentedGroqClient), and
does three things:

1. Token budgeting - estimate the request size and compact it to fit
   ``input_budget(model)``: old tool results first, then the longest
   context message, keeping the system prompt and the latest turn intact.
2. Rate-limit-aware routing - a sliding 60 s window of input tokens per
   model (shared across API replicas through Redis when configured). When
   the strong model would exceed its ITPM limit, the call is routed to the
   fast tier BEFORE the provider rejects it.
3. Observability - every action is counted in Prometheus
   (llama_agent_llm_guard_events_total / llama_agent_llm_tokens_trimmed_total)
   and recorded as a per-thread note that the agents surface in the UI's
   reasoning view.

Configuration (all optional):
  LLM_INPUT_BUDGET          max input tokens per request (default 6000)
  LLM_INPUT_BUDGET_STRONG   override for the strong tier
  LLM_INPUT_BUDGET_FAST     override for the fast tier
  LLM_ITPM_LIMIT_STRONG     strong-tier input tokens/minute (default 7000)
  LLM_ITPM_LIMIT_FAST       fast-tier input tokens/minute (default 8000)
  LLM_PROACTIVE_DOWNGRADE   1 = route to fast before hitting ITPM (default 1)
  LLM_GUARD_MAX_WAIT_S      max seconds to wait for the window to refill
                            when every tier is saturated (default 20)
"""

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

_log = logging.getLogger("llama_agent.llm_guard")

# Conservative: real tokenizers average ~4 chars/token for English, less for
# code/JSON. Under-estimating is what causes 413s, so err on the high side.
CHARS_PER_TOKEN = 3.3
_PER_MESSAGE_OVERHEAD = 6
_MIN_TOOL_CHARS = 500
_MIN_CONTEXT_CHARS = 1200
_TRIM_MARK = "\n[... {n} characters trimmed to fit the model's token budget ...]\n"


# ---------------------------------------------------------------- estimation

def _content_len(message: Dict) -> int:
    n = len(message.get("content") or "")
    if message.get("tool_calls"):
        n += len(json.dumps(message["tool_calls"], default=str))
    return n


def estimate_tokens(messages: List[Dict], tools: Optional[List[Dict]] = None) -> int:
    """Rough input-token estimate for a chat request (messages + tool schemas)."""
    chars = sum(_content_len(m) for m in messages)
    if tools:
        chars += len(json.dumps(tools, default=str))
    return int(chars / CHARS_PER_TOKEN) + _PER_MESSAGE_OVERHEAD * len(messages)


def _tier_of(model: str) -> str:
    from modules.model_tiers import resolve_model
    if model == resolve_model("strong"):
        return "strong"
    if model == resolve_model("fast"):
        return "fast"
    return "other"


def input_budget(model: str) -> int:
    """Max input tokens a single request to `model` may carry."""
    tier = _tier_of(model).upper()
    raw = os.getenv(f"LLM_INPUT_BUDGET_{tier}") or os.getenv("LLM_INPUT_BUDGET", "6000")
    try:
        return max(1000, int(raw))
    except ValueError:
        return 6000


def strong_itpm_limit() -> int:
    try:
        return max(1000, int(os.getenv("LLM_ITPM_LIMIT_STRONG", "7000")))
    except ValueError:
        return 7000


def itpm_limit(model: str) -> Optional[int]:
    """Input tokens/minute allowed for `model` (None = not tracked)."""
    tier = _tier_of(model)
    if tier == "strong":
        return strong_itpm_limit()
    if tier == "fast":
        try:
            return max(1000, int(os.getenv("LLM_ITPM_LIMIT_FAST", "8000")))
        except ValueError:
            return 8000
    return None


def _max_wait() -> float:
    try:
        return max(0.0, float(os.getenv("LLM_GUARD_MAX_WAIT_S", "20")))
    except ValueError:
        return 20.0


# ---------------------------------------------------------------- compaction

def _trim_middle(text: str, keep: int) -> str:
    """Keep the head and tail of `text` (60/40) within ~`keep` characters."""
    if len(text) <= keep:
        return text
    head = int(keep * 0.6)
    tail = max(0, keep - head)
    removed = len(text) - head - tail
    return text[:head] + _TRIM_MARK.format(n=removed) + (text[-tail:] if tail else "")


def fit_messages(messages: List[Dict], budget: int,
                 tools: Optional[List[Dict]] = None) -> Tuple[List[Dict], int]:
    """Return (messages_that_fit, tokens_trimmed).

    Never mutates the caller's list. Order of sacrifice:
      1. tool results, oldest first (search pages are the bulk of the size);
      2. the longest middle message (history / hand-off context);
      3. the latest user message's middle, down to a floor.
    The system prompt and the message structure (tool_call ids, roles) are
    always preserved so the provider accepts the conversation.
    """
    before = estimate_tokens(messages, tools)
    if before <= budget:
        return messages, 0
    msgs = [dict(m) for m in messages]

    def over() -> int:
        return estimate_tokens(msgs, tools) - budget

    # 1. Tool results, oldest first; two passes (1500 chars, then the floor).
    tool_idx = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
    for keep in (1500, _MIN_TOOL_CHARS):
        for i in tool_idx:
            if over() <= 0:
                break
            msgs[i]["content"] = _trim_middle(msgs[i].get("content") or "", keep)

    # 2. Longest non-system message other than the last one, repeatedly.
    last = len(msgs) - 1
    for _ in range(len(msgs)):
        excess = over()
        if excess <= 0:
            break
        candidates = [i for i in range(len(msgs))
                      if i != last and msgs[i].get("role") not in ("system", "tool")
                      and len(msgs[i].get("content") or "") > _MIN_CONTEXT_CHARS]
        if not candidates:
            break
        i = max(candidates, key=lambda j: len(msgs[j].get("content") or ""))
        cur = msgs[i]["content"]
        target = max(_MIN_CONTEXT_CHARS, len(cur) - int(excess * CHARS_PER_TOKEN) - 200)
        msgs[i]["content"] = _trim_middle(cur, target)

    # 3. The latest message itself (e.g. a step prompt with large hand-offs).
    excess = over()
    if excess > 0 and msgs and msgs[last].get("role") != "system":
        cur = msgs[last].get("content") or ""
        target = max(_MIN_CONTEXT_CHARS, len(cur) - int(excess * CHARS_PER_TOKEN) - 200)
        msgs[last]["content"] = _trim_middle(cur, target)

    return msgs, max(0, before - estimate_tokens(msgs, tools))


def is_request_too_large(error: Exception) -> bool:
    text = str(error).lower()
    return "413" in text or "request too large" in text or "reduce your message size" in text


# ---------------------------------------------------------------- ITPM window

class TokenWindow:
    """Input tokens per model over the last 60 s.

    Uses Redis (shared by every API replica - the provider limit is per
    organisation, not per pod) when REDIS_URL is reachable, otherwise an
    in-process sliding window.
    """

    WINDOW = 60.0

    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._local: Dict[str, deque] = {}
        self._lock = threading.Lock()

    def _rkey(self, model: str, bucket: int) -> str:
        return f"llmops:itpm:{model}:{bucket}"

    def used(self, model: str, now: Optional[float] = None) -> int:
        now = time.time() if now is None else now
        if self._redis is not None:
            try:
                # 10 s buckets covering the last minute.
                cur = int(now // 10)
                keys = [self._rkey(model, b) for b in range(cur - 5, cur + 1)]
                return sum(int(v or 0) for v in self._redis.mget(keys))
            except Exception as e:
                _log.warning("TokenWindow Redis read failed, using local: %s", e)
                self._redis = None
        with self._lock:
            q = self._local.get(model)
            if not q:
                return 0
            while q and now - q[0][0] > self.WINDOW:
                q.popleft()
            return sum(t for _, t in q)

    def record(self, model: str, tokens: int, now: Optional[float] = None) -> None:
        if tokens <= 0:
            return
        now = time.time() if now is None else now
        if self._redis is not None:
            try:
                key = self._rkey(model, int(now // 10))
                pipe = self._redis.pipeline()
                pipe.incrby(key, int(tokens))
                pipe.expire(key, 90)
                pipe.execute()
                return
            except Exception as e:
                _log.warning("TokenWindow Redis write failed, using local: %s", e)
                self._redis = None
        with self._lock:
            self._local.setdefault(model, deque()).append((now, int(tokens)))


_window: Optional[TokenWindow] = None
_window_lock = threading.Lock()


def get_window() -> TokenWindow:
    global _window
    with _window_lock:
        if _window is None:
            client = None
            if os.getenv("REDIS_URL"):
                try:
                    from modules.memory import _connect_redis
                    client = _connect_redis("LLM-GUARD")
                except Exception:
                    client = None
            _window = TokenWindow(client)
        return _window


def set_window(window: Optional[TokenWindow]) -> None:
    """Testing hook: install (or reset with None) the shared window."""
    global _window
    with _window_lock:
        _window = window


# ---------------------------------------------------------------- notes + metrics

_notes = threading.local()


def note(text: str) -> None:
    """Record a guard action for the current thread (agents show these)."""
    if not hasattr(_notes, "items"):
        _notes.items = []
    _notes.items.append(text)


def drain_notes() -> List[str]:
    items = getattr(_notes, "items", None) or []
    _notes.items = []
    return items


def _metric(model: str, action: str, trimmed: int = 0) -> None:
    try:
        from modules.prometheus_metrics import LLM_GUARD_EVENTS, LLM_TOKENS_TRIMMED
        LLM_GUARD_EVENTS.labels(model=model or "unknown", action=action).inc()
        if trimmed:
            LLM_TOKENS_TRIMMED.labels(model=model or "unknown").inc(trimmed)
    except Exception:
        pass


# ---------------------------------------------------------------- the guard

_sleep = time.sleep  # patched in tests


def _has_room(window: "TokenWindow", model: str, need: int) -> bool:
    limit = itpm_limit(model)
    return limit is None or window.used(model) + need <= limit


def _wait_for_room(window: "TokenWindow", model: str, need: int) -> float:
    """Block (bounded) until `model`'s window can take `need` tokens.

    Waiting a few seconds is better than an HTTP 429 that kills the answer.
    Returns the seconds waited.
    """
    limit = itpm_limit(model)
    if limit is None or need > limit:
        return 0.0
    deadline = time.monotonic() + _max_wait()
    waited = 0.0
    while window.used(model) + need > limit and time.monotonic() < deadline:
        _sleep(1.0)
        waited += 1.0
    return waited


def prepare_call(model: str, messages: List[Dict],
                 tools: Optional[List[Dict]] = None) -> Tuple[str, List[Dict], int]:
    """Pick the model and fit the messages before a completion call.

    Returns (model, messages, estimated_input_tokens).
    """
    from modules.model_tiers import resolve_model
    strong, fast = resolve_model("strong"), resolve_model("fast")

    est = estimate_tokens(messages, tools)
    window = get_window()
    proactive = os.getenv("LLM_PROACTIVE_DOWNGRADE", "1") == "1"
    if proactive and model == strong and strong != fast:
        used = window.used(strong)
        if used + min(est, input_budget(strong)) > strong_itpm_limit():
            if _has_room(window, fast, min(est, input_budget(fast))):
                _log.info("ITPM guard: %s at %d/%d tokens/min -> routing to %s",
                          strong, used, strong_itpm_limit(), fast)
                note(f"Strong model near its free-tier limit ({used:,}/"
                     f"{strong_itpm_limit():,} tokens/min) - switched to the fast model")
                _metric(strong, "downgraded")
                model = fast

    # Every candidate tier is saturated: wait briefly for the window to
    # slide instead of sending a request the provider will reject.
    waited = _wait_for_room(window, model, min(est, input_budget(model)))
    if waited:
        note(f"Free-tier token limit reached - waited {waited:.0f}s for capacity")
        _metric(model, "waited")

    fitted, trimmed = fit_messages(messages, input_budget(model), tools)
    if trimmed:
        note(f"Context compacted {est:,} → {est - trimmed:,} tokens to fit the "
             f"{input_budget(model):,}-token budget")
        _metric(model, "compacted", trimmed)
    final_est = est - trimmed
    return model, fitted, final_est


def record_call(model: str, input_tokens: int) -> None:
    get_window().record(model, input_tokens)


def shrink_for_retry(model: str, messages: List[Dict],
                     tools: Optional[List[Dict]] = None) -> Tuple[List[Dict], int]:
    """After a 413: refit to 60% of the budget for one retry."""
    budget = int(input_budget(model) * 0.6)
    fitted, trimmed = fit_messages(messages, budget, tools)
    note(f"Request too large for {model} - shrank context to {budget:,} tokens and retried")
    _metric(model, "too_large_retry", trimmed)
    return fitted, estimate_tokens(fitted, tools)
