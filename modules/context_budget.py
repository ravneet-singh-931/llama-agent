"""Turn a list of messages into a bounded history block for the prompt.

Why this is its own module
--------------------------
The same line used to be copy-pasted in THREE places::

    f"{role}: {content[:200]}"

``api._with_context`` (the Redis/Postgres path), ``memory.get_context_string``
(the JSON fallback) and ``llama_agent1._budget_context_string`` (the path that
actually serves chat) each had it, each with slightly different surrounding
code. That is a bug with two halves:

1. **It contradicts the budget.** ``_window_messages`` deliberately admits up
   to ``CONTEXT_TOKEN_BUDGET`` (1500) estimated tokens of history, then the
   200-character per-message cap throws roughly half of it away. Measured on a
   conversation of six ordinary turns: 281 tokens admitted, 181 sent - 36%
   lost. On longer conversations it is a steady 54%, because assistant answers
   run ~500 characters and every one is cut to 200.

2. **It truncates the wrong thing.** The cap is per *message*, so the history
   stops growing once the conversation is long enough to matter, and it cuts
   mid-word ("...the sorti" / "ng is O(n log n)"). A summary needs whole
   sentences; a keyword the user asked about can sit past character 200.

The third copy was worse still: it applied the 200-character cut *before*
estimating the message's token cost, so the budget it was enforcing was
measured against truncated text - it admitted ~2.5x more turns than actually
fitted and then sent a fifth of each.

The fix is to spend one budget across all messages instead of the same tiny cap
on each, spending it newest-first. All three call sites now use this module, so
they cannot drift apart again.
"""

from __future__ import annotations

from typing import Dict, List, Sequence

__all__ = ["render_history", "DEFAULT_HISTORY_TOKENS", "estimate_tokens"]

# Estimated tokens for the whole history block. Matches the default of
# ``api._window_messages`` so the caller's windowing and this rendering agree:
# windowing decides WHICH messages are eligible, this decides how much of each
# to show. At ~4 chars/token this is ~4,800 characters of history, which is a
# small fraction of a free-tier per-minute input budget.
DEFAULT_HISTORY_TOKENS = 1500

def estimate_tokens(text: str) -> int:
    """Cheap token estimate: ~4 characters per token, minimum 1.

    Matches ``api._window_messages`` so the two budgets are comparable.
    """
    return max(1, len(text or "") // 4)


def _cut(text: str, max_chars: int) -> str:
    """Shorten `text` to `max_chars`, preferring a word boundary.

    Falls back to a hard cut when there is no space in range, so this can never
    return something longer than asked for.
    """
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    window = text[: max_chars - 3]
    space = window.rfind(" ")
    # Only use the word boundary if it does not throw away too much.
    if space >= (max_chars - 3) * 0.6:
        window = window[:space]
    return window.rstrip() + "..."


def render_history(messages: Sequence[Dict],
                   max_tokens: int = DEFAULT_HISTORY_TOKENS) -> str:
    """Render ``messages`` as a bounded ``"User: ..."`` block.

    Every message is shown whole when it fits. When the total exceeds
    ``max_tokens``, the shortest messages are kept entire and the remaining
    room is shared out to the long ones, newest first, so the most recent
    exchange (the one the user is actually following up on) survives intact
    and older answers degrade to their opening.

    Returns "" for an empty list; the caller decides what to say instead.
    """
    if not messages:
        return ""

    texts = [("User" if m.get("role") == "user" else "Assistant",
              (m.get("content") or "").strip()) for m in messages]
    texts = [(r, t) for r, t in texts if t]
    if not texts:
        return ""

    # Room available for message bodies, once role labels are accounted for.
    overhead = sum(estimate_tokens(f"{r}: ") + 1 for r, _ in texts)
    budget = max(0, max_tokens - overhead)

    sizes = [estimate_tokens(t) for _, t in texts]
    if sum(sizes) <= budget:
        return "\n".join(f"{r}: {t}" for r, t in texts)

    # Spend the budget NEWEST FIRST, over all messages equally.
    #
    # Not "short messages whole, then the rest": twenty short questions are
    # small individually and collectively huge, and keeping them whole starves
    # every answer, which is the half the user is following up on. Newest-first
    # is the only order that matches what the next question refers to, and it
    # degrades the same way a person skimming a long chat would.
    allot = [0] * len(texts)
    remaining = budget
    for i in range(len(texts) - 1, -1, -1):
        if remaining <= 0:
            break
        want = min(sizes[i], remaining)
        allot[i] = want
        remaining -= want

    lines = []
    for (role, text), want in zip(texts, allot):
        if want <= 0:
            continue
        # Tokens -> characters, matching estimate_tokens().
        lines.append(f"{role}: {_cut(text, max(1, want * 4))}")
    return "\n".join(lines)
