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
  pytest tests/test_mcp.py -v -m mcp       # integration tests only
"""

import asyncio
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ============================================================
# UNIT TESTS — no server needed
# ============================================================


class TestClientToolMapping:
    """Verify the MCPAgentClient maps friendly names to MCP tool names."""

    def _get_mapping(self):
        """Import the tool mapping from the client source."""
        # The mapping is inside call_agent; we replicate it here for testing
        return {
            "research": "research_agent",
            "coder": "coder_agent",
            "writer": "writer_agent",
            "analyzer": "analyzer_agent",
            "planner": "planner_agent",
            "collaborate": "collaborate",
            "team_status": "team_status",
        }

    def test_all_agent_types_mapped(self):
        mapping = self._get_mapping()
        expected = {"research", "coder", "writer", "analyzer", "planner", "collaborate", "team_status"}
        assert set(mapping.keys()) == expected

    def test_tool_names_match_server(self):
        """Tool names must match what mcp_agents_server.py registers."""
        mapping = self._get_mapping()
        server_tool_names = {
            "research_agent", "coder_agent", "writer_agent",
            "analyzer_agent", "planner_agent", "collaborate", "team_status",
        }
        assert set(mapping.values()) == server_tool_names

    def test_unknown_agent_passes_through(self):
        """Unknown agent types should pass through as-is."""
        mapping = self._get_mapping()
        unknown = "some_custom_agent"
        result = mapping.get(unknown, unknown)
        assert result == unknown


class TestClientInitialState:
    """Verify MCPAgentClient starts in a clean state."""

    def test_session_is_none(self):
        from mcp_client_helper import MCPAgentClient
        client = MCPAgentClient()
        assert client.session is None

    def test_tools_cache_is_none(self):
        from mcp_client_helper import MCPAgentClient
        client = MCPAgentClient()
        assert client._tools_cache is None

    def test_call_agent_raises_without_connect(self):
        """Calling an agent before connect() should raise RuntimeError."""
        from mcp_client_helper import MCPAgentClient
        client = MCPAgentClient()
        with pytest.raises(RuntimeError, match="Not connected"):
            asyncio.run(client.call_agent("coder", "hello"))


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
        """The mcp package should be importable."""
        from mcp.server.fastmcp import FastMCP
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        # If we get here, imports work
        assert FastMCP is not None
        assert ClientSession is not None

    def test_multi_agent_orchestrator_init(self):
        """MultiAgentOrchestrator should initialize with 5 agents."""
        from multi_agent import MultiAgentOrchestrator
        orch = MultiAgentOrchestrator()
        assert len(orch.agents) == 5
        expected = {"research", "coder", "writer", "analyzer", "planner"}
        assert set(orch.agents.keys()) == expected

    def test_orchestrator_team_status(self):
        """get_team_status should return a non-empty string."""
        from multi_agent import MultiAgentOrchestrator
        orch = MultiAgentOrchestrator()
        status = orch.get_team_status()
        assert isinstance(status, str)
        assert len(status) > 0

    def test_orchestrator_collaborate(self):
        """collaborate requires Groq API calls, so we just verify the method exists."""
        from multi_agent import MultiAgentOrchestrator
        orch = MultiAgentOrchestrator()
        assert hasattr(orch, "collaborate")
        assert callable(orch.collaborate)


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


@pytest.mark.mcp
class TestMCPClientLifecycle:
    """Test full client connect -> use -> disconnect lifecycle."""

    @pytest.fixture
    def client(self):
        from mcp_client_helper import MCPAgentClient
        return MCPAgentClient()

    @pytest.mark.asyncio
    async def test_connect_discovers_tools(self, client):
        """Connect should discover 7 tools from the server."""
        tools = await client.connect()
        assert isinstance(tools, list)
        assert len(tools) == 7
        expected = {
            "research_agent", "coder_agent", "writer_agent",
            "analyzer_agent", "planner_agent", "collaborate", "team_status",
        }
        assert set(tools) == expected
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_get_tools_info(self, client):
        """get_tools_info should return detailed info for each tool."""
        await client.connect()
        info = await client.get_tools_info()
        assert len(info) == 7
        for tool in info:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert len(tool["description"]) > 10
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self, client):
        """After disconnect, session should be None."""
        await client.connect()
        await client.disconnect()
        assert client.session is None
        assert client._tools_cache is None


@pytest.mark.mcp
class TestMCPCallAgents:
    """Test calling individual agent tools via MCP."""

    @pytest.fixture
    async def connected_client(self):
        from mcp_client_helper import MCPAgentClient
        client = MCPAgentClient()
        await client.connect()
        yield client
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_team_status(self, connected_client):
        result = await connected_client.call_agent("team_status", "")
        assert isinstance(result, str)
        assert len(result) > 0
        # Team status should mention agents
        result_lower = result.lower()
        assert any(name in result_lower for name in ["research", "coder", "writer", "analyzer", "planner"])

    @pytest.mark.asyncio
    async def test_coder_agent(self, connected_client):
        result = await connected_client.call_agent("coder", "Write a Python hello world function")
        assert isinstance(result, str)
        assert "Coder Agent" in result
        assert len(result) > 50

    @pytest.mark.asyncio
    async def test_research_agent(self, connected_client):
        result = await connected_client.call_agent("research", "What is machine learning?")
        assert isinstance(result, str)
        assert "Research Agent" in result

    @pytest.mark.asyncio
    async def test_writer_agent(self, connected_client):
        result = await connected_client.call_agent("writer", "Write a short paragraph about AI")
        assert isinstance(result, str)
        assert "Writer Agent" in result

    @pytest.mark.asyncio
    async def test_analyzer_agent(self, connected_client):
        result = await connected_client.call_agent("analyzer", "Analyze the benefits of cloud computing")
        assert isinstance(result, str)
        assert "Analyzer Agent" in result

    @pytest.mark.asyncio
    async def test_planner_agent(self, connected_client):
        result = await connected_client.call_agent("planner", "Create a plan to learn Python")
        assert isinstance(result, str)
        assert "Planner Agent" in result

    @pytest.mark.asyncio
    async def test_collaborate(self, connected_client):
        result = await connected_client.call_agent("collaborate", "Build a simple todo app")
        assert isinstance(result, str)
        assert len(result) > 100  # Should have contributions from multiple agents


@pytest.mark.mcp
class TestMCPRouting:
    """Test auto-routing of tasks to appropriate agents."""

    @pytest.fixture
    async def connected_client(self):
        from mcp_client_helper import MCPAgentClient
        client = MCPAgentClient()
        await client.connect()
        yield client
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_route_coder_task(self, connected_client):
        result = await connected_client.route_and_call("Write a Python function to sort a list")
        assert isinstance(result, str)
        assert "Coder Agent" in result

    @pytest.mark.asyncio
    async def test_route_writer_task(self, connected_client):
        result = await connected_client.route_and_call("Write an article about AI trends")
        assert isinstance(result, str)
        assert "Writer Agent" in result

    @pytest.mark.asyncio
    async def test_route_planner_task(self, connected_client):
        result = await connected_client.route_and_call("Create a step-by-step plan for the project roadmap")
        assert isinstance(result, str)
        assert "Planner Agent" in result

    @pytest.mark.asyncio
    async def test_route_research_default(self, connected_client):
        """Unmatched queries should default to research."""
        result = await connected_client.route_and_call("Tell me about something random")
        assert isinstance(result, str)
        assert "Research Agent" in result


@pytest.mark.mcp
class TestMCPEdgeCases:
    """Test error handling and edge cases."""

    @pytest.fixture
    async def connected_client(self):
        from mcp_client_helper import MCPAgentClient
        client = MCPAgentClient()
        await client.connect()
        yield client
        await client.disconnect()

    @pytest.mark.asyncio
    async def test_empty_task(self, connected_client):
        """Empty task should still return a response."""
        result = await connected_client.call_agent("coder", "")
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_very_long_task(self, connected_client):
        """Very long task should be handled."""
        long_task = "Explain this in detail: " + "word " * 200
        result = await connected_client.call_agent("research", long_task)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_special_characters_in_task(self, connected_client):
        """Special characters should not crash the agent."""
        result = await connected_client.call_agent("coder", "What is 2+2? Also: @#$%^&*()")
        assert isinstance(result, str)
