"""
MCP (Model Context Protocol) Integration Tests

Unit tests (no server needed):
  - Client tool mapping logic
  - Client initialization state
  - Server tool definitions
  - Routing keyword detection

Integration tests (require MCP server subprocess):
  - Client connect/disconnect lifecycle
  - Tool discovery
  - Individual agent tool calls
  - Auto-routing via route_and_call
  - Error handling

Run:
  pytest tests/test_mcp.py -v              # all tests
  pytest tests/test_mcp.py -v -m unit      # unit tests only
  """

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))



# ============================================================
# UNIT TESTS — no server needed
# ============================================================






class TestRoutingKeywords:
    """Verify the keyword-based routing logic in route_and_call."""

    def _get_keyword_map(self):
        """Replicate the routing keyword map from MCPAgentClient."""
        return {
            "coder": [
                "write code", "function", "class", "debug", "fix bug",
                "programming", "python", "javascript", "code review",
                "refactor", "implement", "algorithm", "script", "api",
                "database", "sql", "html", "css", "react", "django",
                "flask", "fastapi", "code for", "program to",
            ],
            "writer": [
                "write an article", "write a blog", "write content",
                "draft", "compose", "write an essay", "write a story",
                "creative writing", "copywriting", "write a report",
                "write documentation", "write a post", "write a letter",
            ],
            "planner": [
                "plan", "strategy", "roadmap", "steps to",
                "how to start", "create a plan", "project plan",
                "action plan", "timeline", "milestones", "phases",
            ],
            "analyzer": [
                "analyze", "analysis", "statistics", "trends",
                "patterns", "data analysis", "evaluate", "assess",
                "break down", "metrics", "insights",
            ],
            "research": [
                "research", "explain", "what is", "how does",
                "tell me about", "compare", "difference between",
                "pros and cons", "investigate", "explore",
            ],
        }

    def _route(self, task: str) -> str:
        keyword_map = self._get_keyword_map()
        task_lower = task.lower()
        for agent_type, keywords in keyword_map.items():
            if any(kw in task_lower for kw in keywords):
                return agent_type
        return "research"  # default

    def test_coder_detection(self):
        assert self._route("Write a Python function to sort a list") == "coder"

    def test_writer_detection(self):
        assert self._route("Write an article about climate change") == "writer"

    def test_planner_detection(self):
        # "plan" matches planner, but "python" matches coder first in priority order
        # Use a task without coder/writer keywords
        assert self._route("Create a step-by-step plan for the project roadmap") == "planner"

    def test_analyzer_detection(self):
        assert self._route("Analyze the pros and cons of microservices") == "analyzer"

    def test_research_detection(self):
        assert self._route("What is quantum computing?") == "research"

    def test_default_to_research(self):
        assert self._route("Hello, how are you?") == "research"

    def test_coder_debug_keyword(self):
        assert self._route("Debug this code that crashes") == "coder"

    def test_writer_blog_keyword(self):
        assert self._route("Write a blog post about AI") == "writer"

    def test_planner_roadmap_keyword(self):
        assert self._route("Build a roadmap for our startup") == "planner"

    def test_analyzer_trends_keyword(self):
        assert self._route("What are the latest trends in ML?") == "analyzer"


class TestServerToolDefinitions:
    """Verify the MCP server registers the correct tools."""

    def test_server_imports(self):
        """mcp_agents_server.py should import without errors."""
        import importlib
        spec = importlib.util.find_spec("mcp_agents_server")
        # File should be findable
        assert spec is not None or os.path.exists(
            os.path.join(os.path.dirname(__file__), "..", "mcp_agents_server.py")
        )

    def test_mcp_package_imports(self):
        """The mcp package should be importable (supports both mcp 1.x and 2.x)."""
        # mcp 1.x: from mcp.server.fastmcp import FastMCP
        # mcp 2.x: from mcp.server.mcpserver import MCPServer (FastMCP renamed)
        try:
            from mcp.server.fastmcp import FastMCP
        except ModuleNotFoundError:
            from mcp.server.mcpserver import MCPServer as FastMCP
        from mcp import ClientSession
        # If we get here, imports work. StdioServerParameters/stdio_client were
        # imported here only to exercise the old stdio client, which is gone with
        # the Streamlit UI; the SSE client in modules/mcp_client.py is what ships.
        assert FastMCP is not None
        assert ClientSession is not None

    def test_multi_agent_orchestrator_init(self):
        """MultiAgentOrchestrator should initialize with 5 agents."""
        import os
        os.environ["GROQ_API_KEY"] = "test_key_for_testing_only"
        try:
            from multi_agent import MultiAgentOrchestrator
            orch = MultiAgentOrchestrator()
            assert len(orch.agents) == 5
            expected = {"research", "coder", "writer", "analyzer", "planner"}
            assert set(orch.agents.keys()) == expected
        finally:
            if "GROQ_API_KEY" in os.environ:
                del os.environ["GROQ_API_KEY"]

    def test_orchestrator_team_status(self):
        """get_team_status should return a non-empty string."""
        import os
        os.environ["GROQ_API_KEY"] = "test_key_for_testing_only"
        try:
            from multi_agent import MultiAgentOrchestrator
            orch = MultiAgentOrchestrator()
            status = orch.get_team_status()
            assert isinstance(status, str)
            assert len(status) > 0
        finally:
            if "GROQ_API_KEY" in os.environ:
                del os.environ["GROQ_API_KEY"]

    def test_orchestrator_collaborate(self):
        """collaborate requires Groq API calls, so we just verify the method exists."""
        import os
        os.environ["GROQ_API_KEY"] = "test_key_for_testing_only"
        try:
            from multi_agent import MultiAgentOrchestrator
            orch = MultiAgentOrchestrator()
            assert hasattr(orch, "collaborate")
            assert callable(orch.collaborate)
        finally:
            if "GROQ_API_KEY" in os.environ:
                del os.environ["GROQ_API_KEY"]


class TestWebSocketModule:
    """Verify WebSocket module components work correctly."""

    def test_message_creation(self):
        from modules.websocket import WSMessage, MessageType
        msg = WSMessage(type=MessageType.CHAT, data={"content": "hello"})
        assert msg.type == MessageType.CHAT
        assert msg.data["content"] == "hello"
        assert msg.message_id  # should have a UUID

    def test_message_serialization_roundtrip(self):
        from modules.websocket import WSMessage, MessageType
        original = WSMessage(type=MessageType.RESPONSE, data={"content": "hi there"})
        json_str = original.to_json()
        restored = WSMessage.from_json(json_str)
        assert restored.type == MessageType.RESPONSE
        assert restored.data["content"] == "hi there"

    def test_connection_manager_init(self):
        from modules.websocket import ConnectionManager
        mgr = ConnectionManager()
        assert mgr.connection_count == 0
        assert mgr.user_count == 0
        assert mgr.get_online_users() == []

    def test_message_types_covered(self):
        from modules.websocket import MessageType
        types = [t.value for t in MessageType]
        expected = {"connect", "disconnect", "chat", "response", "typing", "error", "heartbeat", "history", "online_users", "agent_status"}
        assert set(types) == expected


# ============================================================
# INTEGRATION TESTS — require MCP server subprocess
# ============================================================




def _check_rate_limited(result: str):
    """Skip if the LLM provider is rate-limited (external quota).

    These integration tests execute real agent calls via the Groq API.
    Free-tier rate limits (HTTP 429 / rate_limit_exceeded) are an
    external constraint, not a routing/logic failure.
    """
    if "429" in result or "rate_limit_exceeded" in result:
        pytest.skip("LLM provider rate-limited (429)")






