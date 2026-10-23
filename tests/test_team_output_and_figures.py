"""
v3.4.4 - from a real "Whole Team" run (paragraph on pollution + 5 years of data):
- the final Writer step rewrote the paragraph and the table that the chat had
  already shown, so the user read everything twice;
- the data mixed a world average with single-country values in one series, and
  stated differences that did not match the table (only "is the figure in a
  source" was checked).
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _orch():
    from multi_agent import MultiAgentOrchestrator
    return MultiAgentOrchestrator()


def test_combining_final_step_is_told_not_to_repeat_inputs():
    orch = _orch()
    step = {"id": "s3", "agent": "writer", "instruction": "Combine the paragraph and the data.",
            "depends_on": ["s1", "s2"]}
    p = orch._build_step_prompt(step, {"s1": "A paragraph.", "s2": "A table."}, "the request",
                                is_final=True)
    assert "already shown to the user" in p and "do not repeat" in p
    assert "The user's original request (context): the request" in p


def test_standalone_and_intermediate_steps_unchanged():
    orch = _orch()
    alone = {"id": "s1", "agent": "writer", "instruction": "Write a paragraph.", "depends_on": []}
    assert "already shown to the user" not in orch._build_step_prompt(alone, {}, "req", is_final=True)
    mid = {"id": "s2", "agent": "writer", "instruction": "Edit it.", "depends_on": ["s1"]}
    p = orch._build_step_prompt(mid, {"s1": "draft"}, "req", is_final=False)
    assert "already shown to the user" not in p and "original request" not in p


def test_every_agent_gets_the_consistent_figures_rule():
    from multi_agent import ResearchAgent, WriterAgent
    for agent in (ResearchAgent(), WriterAgent()):
        sp = agent._system_prompt()
        assert "same metric, unit, place and scope" in sp
        assert "must match the numbers you give" in sp


def test_critic_checks_arithmetic_and_mixed_series(monkeypatch):
    from multi_agent import ResearchAgent
    agent = ResearchAgent()
    captured = []

    def _chat(messages, **kw):
        captured.append(messages)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))])

    monkeypatch.setattr(agent, "_chat", _chat)
    agent.critique("CO2 trend?", "2023: 420.8, 2025: 427.0 - 2.3 ppm higher.")
    system = captured[0][0]["content"]
    assert "own arithmetic" in system and "measure the same thing" in system
