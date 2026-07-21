"""
MCP Server - Exposes Specialized Agents as Tools

This is an MCP (Model Context Protocol) server that makes our 5 specialized
agents available as tools that any MCP client can discover and use.

Agents exposed as tools:
  - research_agent: Gathers and analyzes information
  - coder_agent: Writes, reviews, and debugs code
  - writer_agent: Creates clear, engaging content
  - analyzer_agent: Interprets data and finds patterns
  - planner_agent: Creates actionable plans and strategies
  - collaborate: Runs multiple agents together on a complex task
  - team_status: Shows status of all agents

Run: python mcp_agents_server.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

load_dotenv()

from mcp.server.fastmcp import FastMCP

# Import our multi-agent system
from multi_agent import MultiAgentOrchestrator
from modules.secrets import get_secret

# Load GROQ_API_KEY from Docker secret file or env var
_groq_key = get_secret("GROQ_API_KEY", default=os.getenv("GROQ_API_KEY", ""))
if _groq_key:
    os.environ["GROQ_API_KEY"] = _groq_key

# Create the MCP server
mcp = FastMCP("Llama Agent Multi-Agent Team")

# Create a shared orchestrator (initialized once when server starts)
orchestrator = MultiAgentOrchestrator()


@mcp.tool()
def research_agent(task: str) -> str:
    """
    Research agent: Gathers and analyzes information on any topic.

    Use this tool when the user needs:
    - In-depth research on a topic
    - Facts, explanations, or "what is" questions
    - Comparisons between concepts
    - Investigating or exploring a subject

    Args:
        task: The research task or question to investigate

    Returns:
        Comprehensive research findings
    """
    agent = orchestrator.agents["research"]
    result = agent.execute(task)
    return f"**Research Agent** handled this task:\n\n{result}"


@mcp.tool()
def coder_agent(task: str) -> str:
    """
    Coder agent: Writes, reviews, and debugs code in any programming language.

    Use this tool when the user needs:
    - Write code, functions, classes, scripts
    - Debug or fix bugs
    - Code review or refactoring
    - Programming explanations or algorithms
    - API endpoints, database queries, HTML/CSS/JS

    Args:
        task: The coding task (e.g., "write a Python function to sort a list")

    Returns:
        Working code with comments and examples
    """
    agent = orchestrator.agents["coder"]
    result = agent.execute(task)
    return f"**Coder Agent** handled this task:\n\n{result}"


@mcp.tool()
def writer_agent(task: str) -> str:
    """
    Writer agent: Creates clear, engaging written content.

    Use this tool when the user needs:
    - Articles, blog posts, essays
    - Reports, documentation, summaries
    - Creative writing (stories, poems)
    - Professional letters or emails
    - Any content creation task

    Args:
        task: The writing task (e.g., "write a blog post about AI trends")

    Returns:
        Well-structured, engaging content
    """
    agent = orchestrator.agents["writer"]
    result = agent.execute(task)
    return f"**Writer Agent** handled this task:\n\n{result}"


@mcp.tool()
def analyzer_agent(task: str) -> str:
    """
    Analyzer agent: Interprets data and finds patterns and insights.

    Use this tool when the user needs:
    - Data analysis or trend identification
    - Pattern recognition
    - Evaluate pros and cons
    - Assess performance or metrics
    - Break down complex information

    Args:
        task: The analysis task (e.g., "analyze the pros and cons of microservices")

    Returns:
        Thorough analysis with insights and recommendations
    """
    agent = orchestrator.agents["analyzer"]
    result = agent.execute(task)
    return f"**Analyzer Agent** handled this task:\n\n{result}"


@mcp.tool()
def planner_agent(task: str) -> str:
    """
    Planner agent: Creates actionable plans and strategies.

    Use this tool when the user needs:
    - Project plans or roadmaps
    - Step-by-step guides
    - Strategy or action plans
    - Timeline and milestones
    - How to start something

    Args:
        task: The planning task (e.g., "create a plan to learn Python in 3 months")

    Returns:
        Detailed, actionable plan with steps and timeline
    """
    agent = orchestrator.agents["planner"]
    result = agent.execute(task)
    return f"**Planner Agent** handled this task:\n\n{result}"


@mcp.tool()
def collaborate(task: str) -> str:
    """
    Multi-agent collaboration: Runs multiple specialized agents together on a complex task.

    Use this tool when the user needs:
    - A comprehensive solution combining research, code, and planning
    - A complex project that requires multiple perspectives
    - Something that needs both analysis and content creation

    Args:
        task: The complex task that benefits from multiple agent perspectives

    Returns:
        Combined results from Planner, Research, Coder, Writer, and Analyzer agents
    """
    result = orchestrator.collaborate(task)
    return result


@mcp.tool()
def team_status() -> str:
    """
    Shows the current status of all agents in the team, including how many tasks each has handled.

    Use this tool when the user asks:
    - "What agents do you have?"
    - "Show team status"
    - "How many tasks has each agent handled?"

    Returns:
        Status of all 5 agents with task counts
    """
    return orchestrator.get_team_status()


if __name__ == "__main__":
    print("=" * 60)
    print("  MCP Agent Server - Starting...")
    print("  Agents: Research, Coder, Writer, Analyzer, Planner")
    print("  Transport: stdio")
    print("=" * 60)

    # Run the MCP server with stdio transport
    mcp.run(transport="stdio")
