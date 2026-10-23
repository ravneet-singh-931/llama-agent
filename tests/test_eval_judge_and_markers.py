"""
v3.4.3 - found by the research_news eval run:
- the judge saw a blind 5,000-char head+tail clip of 11,000+ chars of search
  results and judged recent news by its memory -> sourced answers flagged as
  fabricated (incl. a correct F1 result it "knew" from an older calendar);
- answers written by the wrap-up path skipped the fact check;
- gpt-oss citation markers ("【4†L1-L3】") leaked into answers.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ------------------------------------------------------------------ judge --

def _result(i, title, body):
    return f"{i}. {title}\n   https://example.com/{i}\n   {body}"


def test_judge_gets_the_passages_the_answer_uses():
    from evals.judge import relevant_sources
    filler = [_result(i, f"Unrelated story {i}", "lorem ipsum " * 50) for i in range(1, 9)]
    key = _result(9, "Verstappen wins Singapore Grand Prix",
                  "Max Verstappen won the Singapore Grand Prix on 11 October 2026.")
    # The relevant result sits in the MIDDLE - a head+tail clip loses it.
    src = "\n\n".join(filler[:4] + [key] + filler[4:])
    answer = "Winner: Max Verstappen, Singapore Grand Prix, 11 October 2026."
    out = relevant_sources([src], answer, limit=2000)
    assert "Verstappen won the Singapore Grand Prix" in out
    assert len(out) <= 2000 + 10 * len("\n---\n")


def test_small_sources_are_passed_whole_and_in_order():
    from evals.judge import relevant_sources
    a, b = _result(1, "A", "first"), _result(2, "B", "second")
    out = relevant_sources([a + "\n\n" + b], "anything", limit=5000)
    assert out.index("first") < out.index("second")


def test_rubric_judges_against_sources_not_memory():
    from evals.judge import build_messages
    msgs = build_messages({"prompt": "Who won the last F1 race?"}, "Verstappen",
                          ["1. Verstappen wins\n   u\n   Verstappen won."], "Friday, 23 October 2026")
    system, user = msgs[0]["content"], msgs[1]["content"]
    assert "not against your memory" in system
    assert "Verstappen won." in user and "TODAY: Friday, 23 October 2026" in user


# ---------------------------------------------------------------- markers --

def test_strip_citation_markers():
    from multi_agent import strip_citation_markers
    assert strip_citation_markers("RaceFans (11 Oct)【4†L1-L3】【4†L11-L12】") == "RaceFans (11 Oct)"
    assert strip_citation_markers("no markers [1] here") == "no markers [1] here"


def _chunk(text):
    delta = SimpleNamespace(content=text)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _agent_with_wrapup(monkeypatch, stream_parts, tool_text="Max Verstappen won; 62 laps."):
    """Agent whose tool round ends without an answer -> wrap-up stream."""
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "tool", "content": tool_text * 20}]
    monkeypatch.setattr("modules.agent_tools.run_tool_rounds",
                        lambda c, m, ms, tools, **kw: (msgs, [], ""))
    monkeypatch.setattr(agent.client.chat.completions, "create",
                        lambda **kw: iter([_chunk(p) for p in stream_parts]))
    return agent


def test_stream_strips_marker_split_across_chunks(monkeypatch):
    agent = _agent_with_wrapup(monkeypatch, ["Verstappen won ", "【4†L1", "-L3】", " in 62 laps."])
    out = list(agent._stream_with_tools("Who won?", sources=[]))
    text = "".join(o["content"] for o in out if o["type"] == "token")
    assert "【" not in text and "】" not in text
    assert text == "Verstappen won  in 62 laps."


def test_wrapup_answer_is_fact_checked(monkeypatch):
    agent = _agent_with_wrapup(monkeypatch, ["Verstappen won by a ", "12.5% margin and 75% of fans cheered."],
                               tool_text="Max Verstappen won by a 12.5% margin. ")
    out = list(agent._stream_with_tools("Who won the last F1 race and how many laps?"))
    notes = [o["content"] for o in out if o.get("stage") == "grounding"]
    assert notes, out
    assert "1/2 figures" in notes[0]
