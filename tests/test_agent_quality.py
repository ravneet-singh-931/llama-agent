"""
Tests for CHANGELOG 3.3.5 (agent answer quality):

- Tavily is called with "Authorization: Bearer" (no api_key in the body) and
  failures are logged before falling back to DuckDuckGo.
- Every agent prompt carries today's date and an English-only rule.
- Answer budgets: 4096 tokens by default, 6144 for the Coder, env-tunable.
- Coder asks for compact, complete code; Writer may not invent details.

All offline. Run with: pytest tests/test_agent_quality.py -v
"""

import io
import json
import logging
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("GROQ_API_KEY", "test-key")


# ============ TAVILY ============

class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_tavily_uses_bearer_header_not_body_key(monkeypatch):
    from modules import web_tools
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data.decode())
        return _Resp(json.dumps({"results": [
            {"title": "Python 3.14", "url": "https://python.org", "content": "Released"}
        ]}).encode())

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test123")
    monkeypatch.delenv("TAVILY_SEARCH_DEPTH", raising=False)
    monkeypatch.setattr(web_tools.urllib.request, "urlopen", fake_urlopen)

    out = web_tools.web_search("python 3.14 features")
    assert seen["url"] == "https://api.tavily.com/search"
    assert seen["auth"] == "Bearer tvly-test123"
    assert "api_key" not in seen["body"]  # deprecated body auth is gone
    assert seen["body"]["search_depth"] == "basic"
    assert "Python 3.14" in out and "https://python.org" in out


def test_tavily_search_depth_is_configurable(monkeypatch):
    from modules import web_tools
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["body"] = json.loads(req.data.decode())
        return _Resp(b'{"results": []}')

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-x")
    monkeypatch.setattr(web_tools.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("TAVILY_SEARCH_DEPTH", "advanced")
    web_tools.web_search("q")
    assert seen["body"]["search_depth"] == "advanced"
    monkeypatch.setenv("TAVILY_SEARCH_DEPTH", "bogus")
    web_tools.web_search("q")
    assert seen["body"]["search_depth"] == "basic"


def test_tavily_failure_is_logged_and_falls_back(monkeypatch, caplog):
    from modules import web_tools

    def boom(req, timeout=None):
        raise OSError("HTTP Error 401: Unauthorized")

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-bad")
    monkeypatch.setattr(web_tools.urllib.request, "urlopen", boom)
    monkeypatch.setattr(web_tools, "_search_duckduckgo", lambda q, n: "1. DDG result")
    with caplog.at_level(logging.WARNING, logger="llama_agent.web_tools"):
        out = web_tools.web_search("q")
    assert "Tavily web search failed" in out and "DDG result" in out
    assert any("401" in m for m in caplog.messages)


# ============ DATE + ENGLISH RULES ============

def test_common_rules_have_date_and_english(monkeypatch):
    from modules import prompt_rules
    monkeypatch.setenv("APP_TIMEZONE", "Asia/Kolkata")
    rules = prompt_rules.common_rules()
    assert prompt_rules.today_str() in rules
    assert "(Asia/Kolkata)" in rules
    assert "English only" in rules


def test_bad_timezone_falls_back_to_utc(monkeypatch):
    from modules import prompt_rules
    monkeypatch.setenv("APP_TIMEZONE", "Not/AZone")
    assert prompt_rules.today_str().endswith("(UTC)")


def test_every_agent_system_prompt_has_rules():
    from multi_agent import MultiAgentOrchestrator
    from modules.prompt_rules import today_str
    orch = MultiAgentOrchestrator()
    for agent in orch.agents.values():
        prompt = agent._system_prompt()
        assert prompt.startswith(agent._system)
        assert today_str() in prompt and "English only" in prompt
        assert "UNTRUSTED_DATA" in prompt  # injection guard still present


def test_stream_and_tool_calls_send_rules_and_budget(monkeypatch):
    """The streaming tool loop (what the chat uses) sends the date/English
    system prompt and the agent's token budget."""
    from multi_agent import CoderAgent
    agent = CoderAgent()
    seen = {}

    def fake_rounds(client, model, messages, tools, **kw):
        seen["system"] = messages[0]["content"]
        seen["max_tokens"] = kw.get("max_tokens")
        return messages, [], "done"

    monkeypatch.setattr("modules.agent_tools.run_tool_rounds", fake_rounds)
    monkeypatch.delenv("AGENT_CODER_MAX_TOKENS", raising=False)
    list(agent._stream_with_tools("write code"))
    assert "English only" in seen["system"] and "Today's date" in seen["system"]
    assert seen["max_tokens"] == 6144


# ============ ANSWER BUDGETS ============

def test_answer_token_budgets(monkeypatch):
    from multi_agent import CoderAgent, ResearchAgent, WriterAgent
    for var in ("AGENT_MAX_TOKENS", "AGENT_CODER_MAX_TOKENS"):
        monkeypatch.delenv(var, raising=False)
    assert ResearchAgent().answer_tokens == 4096
    assert WriterAgent().answer_tokens == 4096
    assert CoderAgent().answer_tokens == 6144
    monkeypatch.setenv("AGENT_MAX_TOKENS", "3000")
    monkeypatch.setenv("AGENT_CODER_MAX_TOKENS", "8000")
    assert ResearchAgent().answer_tokens == 3000
    assert CoderAgent().answer_tokens == 8000
    monkeypatch.setenv("AGENT_MAX_TOKENS", "garbage")
    assert ResearchAgent().answer_tokens == 4096


def test_chat_uses_agent_budget_unless_overridden(monkeypatch):
    from multi_agent import WriterAgent
    agent = WriterAgent()
    calls = []

    def create(**kw):
        calls.append(kw["max_tokens"])
        msg = types.SimpleNamespace(content="ok")
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    agent.client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.delenv("AGENT_MAX_TOKENS", raising=False)
    agent._chat([{"role": "user", "content": "hi"}])
    agent._chat([{"role": "user", "content": "hi"}], max_tokens=400)  # critic
    assert calls == [4096, 400]


def test_general_chat_budget(monkeypatch):
    from modules.prompt_rules import chat_max_tokens
    monkeypatch.delenv("CHAT_MAX_TOKENS", raising=False)
    assert chat_max_tokens() == 4096
    monkeypatch.setenv("CHAT_MAX_TOKENS", "6000")
    assert chat_max_tokens() == 6000


# ============ CODER / WRITER INSTRUCTIONS ============

def test_coder_and_writer_instructions():
    from multi_agent import CoderAgent, WriterAgent
    assert "never stop mid-file" in CoderAgent._task_prompt
    assert "under 250 lines" in CoderAgent._task_prompt
    assert "never invent" in WriterAgent._task_prompt
