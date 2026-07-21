"""
Llama Agent - Web Interface (Streamlit)
Run with: streamlit run app.py

Now with Multi-Agent System via MCP!

Architecture:
  User -> Streamlit (app.py)
              |
              |--> Single Agent (llama_agent1.py) for simple tasks
              |      (math, time, memory, facts, documents)
              |
              |--> MCP Client (mcp_client_helper.py) for complex tasks
                     |
                     |--> MCP Server (mcp_agents_server.py)
                            |-- Research Agent
                            |-- Coder Agent
                            |-- Writer Agent
                            |-- Analyzer Agent
                            |-- Planner Agent
"""

import streamlit as st
import asyncio
import os
import sys
from datetime import datetime

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

# Page config
st.set_page_config(
    page_title="Llama Agent",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .stApp {
        background-color: #0e1117;
    }
    .chat-message {
        padding: 1rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
    }
    .user-message {
        background-color: #1e3a5f;
        border-left: 4px solid #00d4ff;
    }
    .assistant-message {
        background-color: #1a1a2e;
        border-left: 4px solid #00ff88;
    }
    .agent-badge {
        background-color: #2d2d44;
        padding: 0.25rem 0.5rem;
        border-radius: 0.25rem;
        font-size: 0.75rem;
        color: #00ff88;
    }
</style>
""", unsafe_allow_html=True)


# ============ MCP CLIENT MANAGEMENT ============

def connect_mcp():
    """Connect to the MCP Agent Server. Returns (success, tools_or_error)."""
    try:
        from mcp_client_helper import MCPAgentClient
        client = MCPAgentClient()
        tools = asyncio.run(client.connect())
        st.session_state.mcp_tools = tools
        st.session_state.mcp_connected = True
        return True, client
    except Exception as e:
        st.session_state.mcp_connected = False
        return False, str(e)


async def call_mcp_agent(agent_type: str, task: str) -> str:
    """
    Create a fresh MCP connection, call an agent, then disconnect.
    Fresh connection each time = reliable (no stale subprocess).
    """
    from mcp_client_helper import MCPAgentClient

    client = MCPAgentClient()
    try:
        await client.connect()

        if agent_type == "team_status":
            result = await client.call_agent("team_status", "")
        elif agent_type == "collaborate":
            result = await client.call_agent("collaborate", task)
        else:
            result = await client.call_agent(agent_type, task)

        return result
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# ============ SESSION STATE ============

def init_session_state():
    """Initialize session state variables"""
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "memory" not in st.session_state:
        st.session_state.memory = {}
    if "documents" not in st.session_state:
        st.session_state.documents = []
    if "facts" not in st.session_state:
        st.session_state.facts = {}
    if "last_agent" not in st.session_state:
        st.session_state.last_agent = None


# ============ SIDEBAR ============

def display_sidebar():
    """Display sidebar with MCP agent status and options"""
    with st.sidebar:
        st.image("https://img.icons8.com/fluency/96/robot-2.png", width=80)
        st.title("Llama Agent")
        st.markdown("---")

        # ---- MCP Agent Team Status ----
        st.subheader("Agent Team (MCP)")

        if st.session_state.get("mcp_connected"):
            st.success("MCP Server: Connected")

            tools = st.session_state.get("mcp_tools", [])
            if tools:
                for tool_name in tools:
                    st.write(f"  - {tool_name}")
            else:
                st.write("  No tools discovered")

            if st.button("Team Status"):
                st.session_state.messages.append({
                    "role": "user",
                    "content": "/team_status",
                    "timestamp": datetime.now().strftime("%H:%M:%S")
                })
                st.rerun()
        else:
            st.warning("MCP Server: Not connected")
            if st.button("Connect MCP Server"):
                with st.spinner("Connecting to Agent Server..."):
                    success, result = connect_mcp()
                    if success:
                        st.success(f"Connected! {len(st.session_state.mcp_tools)} agents available.")
                        st.rerun()
                    else:
                        st.error(f"Failed: {result}")

        st.markdown("---")

        # ---- Agent Selection ----
        st.subheader("Pick an Agent")
        agent_cols = st.columns(2)
        with agent_cols[0]:
            if st.button("Research"):
                st.session_state.force_agent = "research"
            if st.button("Coder"):
                st.session_state.force_agent = "coder"
            if st.button("Analyzer"):
                st.session_state.force_agent = "analyzer"
        with agent_cols[1]:
            if st.button("Writer"):
                st.session_state.force_agent = "writer"
            if st.button("Planner"):
                st.session_state.force_agent = "planner"
            if st.button("All Agents"):
                st.session_state.force_agent = "collaborate"

        st.markdown("---")

        # ---- Memory section ----
        st.subheader("Memory")
        if st.session_state.facts:
            for key, value in st.session_state.facts.items():
                st.info(f"**{key}**: {value}")
        else:
            st.write("No facts stored yet.")

        st.markdown("---")

        # ---- Actions ----
        st.subheader("Actions")
        if st.button("Clear Chat"):
            st.session_state.messages = []
            st.rerun()

        if st.button("Clear Memory"):
            st.session_state.memory = {}
            st.session_state.facts = {}
            st.rerun()

        st.markdown("---")

        # ---- Stats ----
        st.subheader("Stats")
        st.metric("Messages", len(st.session_state.messages))
        st.metric("Facts", len(st.session_state.facts))
        st.metric("Documents", len(st.session_state.documents))


# ============ MESSAGE PROCESSING ============

async def process_message(user_input: str) -> str:
    """
    Process user message for the MCP path only.
    Special commands and streaming are handled elsewhere.
    """
    try:
        from modules.memory import ConversationMemory, FactMemory

        memory = ConversationMemory()
        facts = FactMemory()

        memory.add_message("user", user_input)

        # ---- MCP AGENT TEAM ----
        force_agent = st.session_state.pop("_mcp_force_agent", None)

        if force_agent:
            st.session_state.last_agent = force_agent
            result = await call_mcp_agent(force_agent, user_input)
        else:
            # Auto-route using keyword detection
            task_lower = user_input.lower()
            agent = "research"

            if any(kw in task_lower for kw in ["write code", "function", "class", "debug", "python", "script", "code"]):
                agent = "coder"
            elif any(kw in task_lower for kw in ["write an article", "write a blog", "draft", "compose", "write content"]):
                agent = "writer"
            elif any(kw in task_lower for kw in ["plan", "strategy", "roadmap", "steps to", "how to start"]):
                agent = "planner"
            elif any(kw in task_lower for kw in ["analyze", "analysis", "evaluate", "assess", "patterns", "insights"]):
                agent = "analyzer"

            st.session_state.last_agent = agent
            result = await call_mcp_agent(agent, user_input)

        memory.add_message("assistant", str(result))
        return str(result)

    except Exception as e:
        return f"Error: {str(e)}"


def _route_command(user_input: str) -> str:
    """
    Check whether a message is a special command, MCP command, or plain single-agent task.
    Returns one of:
      "special"  — handled locally, returns immediate text
      "mcp"      — MCP agent handles it, returns immediate text
      "stream"   — single-agent streaming, caller should use st.write_stream
    Also stores the immediate result in st.session_state.processed_result
    for "special" and "mcp" paths.
    """
    from modules.memory import ConversationMemory, FactMemory
    from modules.rag import SimpleRAG

    memory = ConversationMemory()
    facts = FactMemory()
    rag = SimpleRAG()

    # ---- SPECIAL COMMANDS (handled locally, no agent needed) ----

    if user_input.lower().startswith("remember "):
        content = user_input[9:].strip()
        if "=" in content:
            key, value = content.split("=", 1)
            facts.remember(key.strip(), value.strip())
            st.session_state.facts[key.strip()] = value.strip()
            st.session_state.processed_result = f"I'll remember that: {key.strip()} = {value.strip()}"
        else:
            st.session_state.processed_result = "Usage: remember key = value"
        return "special"

    elif user_input.lower().startswith("recall "):
        key = user_input[7:].strip()
        st.session_state.processed_result = facts.recall(key)
        return "special"

    elif user_input.lower() == "show facts":
        all_facts = facts.get_all_facts()
        if all_facts:
            result = "Stored facts:\n"
            for k, v in all_facts.items():
                result += f"  - {k}: {v}\n"
            st.session_state.processed_result = result
        else:
            st.session_state.processed_result = "No facts stored yet."
        return "special"

    elif user_input.lower().startswith("read "):
        file_path = user_input[5:].strip()
        result = rag.add_document(file_path)
        if "Added" in result:
            st.session_state.documents.append(file_path)
        st.session_state.processed_result = result
        return "special"

    elif user_input.lower() == "show documents":
        docs = rag.list_documents()
        if docs:
            st.session_state.processed_result = "Documents:\n" + "\n".join(f"  - {d}" for d in docs)
        else:
            st.session_state.processed_result = "No documents loaded."
        return "special"

    elif user_input.lower().startswith("search "):
        query = user_input[7:].strip()
        st.session_state.processed_result = rag.get_context(query)
        return "special"

    # ---- CHECK FACTS ----
    all_facts = facts.get_all_facts()
    lower_input = user_input.lower()
    for key, value in all_facts.items():
        key_lower = key.lower()
        if (key_lower in lower_input and
            any(phrase in lower_input for phrase in ["what", "know", "recall", "tell", "remind"])):
            st.session_state.processed_result = f"Your {key} is **{value}**."
            return "special"

    # ---- MCP AGENT TEAM (complex tasks) ----
    force_agent = st.session_state.pop("force_agent", None)

    if force_agent or st.session_state.get("mcp_connected"):
        st.session_state._mcp_force_agent = force_agent
        return "mcp"

    # ---- SINGLE AGENT (streaming) ----
    return "stream"


# ============ MAIN UI ============

def main():
    init_session_state()
    display_sidebar()

    # Main chat area
    st.title("Llama Agent")
    st.markdown("*AI Agent with Memory, RAG, and a Multi-Agent Team via MCP*")

    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if "agent" in message:
                st.caption(f"Agent: {message['agent']}")
            elif "timestamp" in message:
                st.caption(f"{message['timestamp']}")

    # Chat input
    if prompt := st.chat_input("Ask me anything..."):
        # Add user message
        timestamp = datetime.now().strftime("%H:%M:%S")
        st.session_state.messages.append({
            "role": "user",
            "content": prompt,
            "timestamp": timestamp
        })

        with st.chat_message("user"):
            st.markdown(prompt)
            st.caption(f"{timestamp}")

        # Get AI response
        with st.chat_message("assistant"):
            # Decide which path to take
            route = _route_command(prompt)
            assistant_time = datetime.now().strftime("%H:%M:%S")

            if route == "special":
                # Instant local result (remember, recall, show facts, etc.)
                response = st.session_state.pop("processed_result", "Done.")
                st.markdown(response)
                agent_used = st.session_state.get("last_agent")
                label = "Local Command"
                st.caption(f"Agent: {label} | {assistant_time}")

            elif route == "mcp":
                # MCP agent (non-streaming, returns full text)
                with st.spinner("Thinking..."):
                    response = asyncio.run(process_message(prompt))
                st.markdown(response)
                agent_used = st.session_state.get("last_agent", "mcp")
                agent_labels = {
                    "research": "Research Agent (MCP)",
                    "coder": "Coder Agent (MCP)",
                    "writer": "Writer Agent (MCP)",
                    "analyzer": "Analyzer Agent (MCP)",
                    "planner": "Planner Agent (MCP)",
                    "collaborate": "All Agents (MCP)",
                    "team_status": "Team Status",
                }
                label = agent_labels.get(agent_used, agent_used)
                st.caption(f"Agent: {label} | {assistant_time}")

            else:
                # STREAMING single-agent — tokens appear one by one
                from modules.memory import ConversationMemory, FactMemory
                from llama_agent1 import run_agent_stream

                memory_obj = ConversationMemory()
                facts_obj = FactMemory()
                memory_obj.add_message("user", prompt)

                facts_context = facts_obj.get_context_string()
                enhanced_input = f"{prompt}\n\n{facts_context}\n\nIMPORTANT: Always respond in English."

                st.session_state.last_agent = "single_agent"
                # st.write_stream yields tokens in real-time
                full_response = st.write_stream(run_agent_stream(enhanced_input))
                response = full_response if full_response else ""
                label = "Single Agent (Streaming)"
                st.caption(f"Agent: {label} | {assistant_time}")

        # Add assistant message to chat history
        st.session_state.messages.append({
            "role": "assistant",
            "content": response,
            "agent": st.session_state.get("last_agent"),
            "timestamp": assistant_time
        })

    # Quick commands
    st.markdown("---")
    st.subheader("Quick Commands")

    cols = st.columns(4)
    with cols[0]:
        if st.button("Memory Stats"):
            st.session_state.messages.append({
                "role": "user",
                "content": "get_memory_stats",
                "timestamp": datetime.now().strftime("%H:%M:%S")
            })
            st.rerun()

    with cols[1]:
        if st.button("Health Check"):
            st.session_state.messages.append({
                "role": "user",
                "content": "get_agent_health",
                "timestamp": datetime.now().strftime("%H:%M:%S")
            })
            st.rerun()

    with cols[2]:
        if st.button("Show Facts"):
            st.session_state.messages.append({
                "role": "user",
                "content": "show facts",
                "timestamp": datetime.now().strftime("%H:%M:%S")
            })
            st.rerun()

    with cols[3]:
        if st.button("Show Documents"):
            st.session_state.messages.append({
                "role": "user",
                "content": "show documents",
                "timestamp": datetime.now().strftime("%H:%M:%S")
            })
            st.rerun()


if __name__ == "__main__":
    main()
