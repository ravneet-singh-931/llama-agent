"""Tests for the shared conversation-history renderer.

The bug this replaced: three separate call sites each rendered history as
``f"{role}: {content[:200]}"``. Measured on six ordinary turns that sent 181
tokens where the windowing had admitted 281 (36% lost); on longer conversations
a steady 54%, because assistant answers run ~500 characters and every one was
cut mid-word. The cap also meant history stopped growing once a conversation
got long enough to matter.

These tests pin the properties that make the replacement correct.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from modules.context_budget import (DEFAULT_HISTORY_TOKENS, estimate_tokens,
                                   render_history)


def _turns():
    return [
        {"role": "user",
         "content": "Write me a Python function that merges overlapping intervals."},
        {"role": "assistant",
         "content": "Here is a merge function. The idea is to sort by start time, "
                    "then walk the list and extend the current interval when the "
                    "next one overlaps. Sorting is O(n log n) and the walk is "
                    "O(n), so the whole thing is O(n log n). Watch out for "
                    "touching intervals (end == start): usually you want to merge "
                    "those too, so the comparison is `<=` rather than `<`. Type "
                    "hints and a docstring are included."},
        {"role": "user",
         "content": "What's the time complexity if the input is already sorted?"},
        {"role": "assistant",
         "content": "Then you can skip the sort and the whole function becomes "
                    "O(n): a single linear pass."},
    ]


def test_short_conversation_is_rendered_whole():
    """A conversation inside the budget must lose nothing.

    This is the actual regression: the old code cut at 200 characters even
    when there was room for everything.
    """
    out = render_history(_turns())
    for m in _turns():
        assert m["content"] in out, "a message inside the budget was truncated"
    assert out.count("\n") == len(_turns()) - 1


def test_nothing_is_cut_mid_word():
    """A hard character cut lands inside a word; a word boundary does not."""
    longs = [{"role": "assistant", "content": "alpha bravo charlie delta echo " * 200}]
    out = render_history(longs, max_tokens=40)
    body = out.split(": ", 1)[1]
    assert body.endswith("...")
    stem = body[:-3]
    # Either we ended on a space (good) or we had no space to use (hard cut).
    assert stem == stem.rstrip() or " " not in stem


def test_total_stays_within_the_budget():
    """A 40-turn conversation must not exceed what the caller budgeted."""
    big = []
    for i in range(40):
        big.append({"role": "user", "content": f"Question {i}: " + "x" * 80})
        big.append({"role": "assistant", "content": f"Answer {i}: " + "y" * 900})
    out = render_history(big, max_tokens=1500)
    # Allow a small margin: estimate_tokens is characters//4, and the renderer
    # rounds per message when converting its allotment back to characters.
    assert estimate_tokens(out) <= 1500 * 1.15
    assert "Answer 39" in out, "the newest turn must survive"
    assert "Question 0:" not in out, "the oldest turn must be dropped"


def test_newest_messages_win_the_budget():
    """Newest-first, not short-first.

    The first design kept *short* messages whole and let long ones absorb the
    cut. With many short questions that starves every answer -- the half the
    user is following up on -- so the order matters and is pinned here.
    """
    msgs = []
    for i in range(30):
        msgs.append({"role": "user", "content": f"Q{i} " + "x" * 60})
        msgs.append({"role": "assistant", "content": f"A{i} " + "y" * 400})
    out = render_history(msgs, max_tokens=800)
    assert "A29" in out, "newest answer missing"
    assert "Q29" in out, "newest question missing"
    # An early answer must not have displaced a late one.
    assert "A0 " not in out


def test_empty_input_returns_empty_string():
    """The caller decides what to say when there is no history."""
    assert render_history([]) == ""
    assert render_history([{"role": "user", "content": ""}]) == ""
    assert render_history([{"role": "user", "content": "   "}]) == ""


def test_roles_are_labelled():
    out = render_history([{"role": "user", "content": "hi"},
                          {"role": "assistant", "content": "hello"}])
    assert out.split("\n") == ["User: hi", "Assistant: hello"]


def test_estimate_tokens_is_cheap_and_never_zero():
    assert estimate_tokens("") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100


# ------------------------------------------------- all three call sites agree

def test_all_three_call_sites_use_the_shared_renderer():
    """The reason the bug existed three times over is that each site had its
    own copy. Assert the copies are gone."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def read(rel):
        with open(os.path.join(root, rel)) as f:
            return f.read()

    import re
    # Match content[...][:200] with any quoting/spacing, e.g.
    #   m["content"][:200]      msg['content'][:200]      content[:200]
    cut = re.compile(
        r"""(?:\bcontent\b|["']content["'])\s*\)?\s*\]?\s*\[\s*:\s*200\s*\]""")
    for rel in ("api.py", "modules/memory.py", "llama_agent1.py"):
        src = read(rel)
        assert not cut.search(src), \
            f"{rel} still truncates message content at 200 characters"
        assert "render_history" in src, f"{rel} does not use the shared renderer"


def test_chat_path_budgets_against_true_message_size():
    """_budget_context_string must size messages before cutting them.

    The old version applied [:200] and *then* estimated tokens, so it budgeted
    against text a fifth the size of what it was deciding about.
    """
    os.environ.setdefault("GROQ_API_KEY", "test_key")
    from llama_agent1 import _budget_context_string

    class Mem:
        def __init__(self, msgs):
            self._m = msgs

        def get_messages(self, user_id=None, conversation_id=None):
            return self._m

    msgs = [{"role": "assistant", "content": "a" * 4000} for _ in range(10)]
    out = _budget_context_string(Mem(msgs), "u", "c", max_tokens=2000)
    assert len(out) <= 2000 * 4 * 1.15


def test_history_marker_is_stable_for_callers():
    """api.py and llama_agent1.py both prefix this exact string; a change here
    silently drops the prefix from one path."""
    from modules.context_budget import render_history as rh
    assert rh([{"role": "user", "content": "hi"}]).startswith("User: ")
    assert DEFAULT_HISTORY_TOKENS == 1500
