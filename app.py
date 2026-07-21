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
    """Process user message — route to Single Agent or MCP Agent Team"""
    try:
        from modules.memory import ConversationMemory, FactMemory
        from modules.rag import SimpleRAG

        memory = ConversationMemory()
        facts = FactMemory()
        rag = SimpleRAG()

        # Add user message to memory
        memory.add_message("user", user_input)

        # ---- SPECIAL COMMANDS (handled locally, no agent needed) ----

        if user_input.lower().startswith("remember "):
            content = user_input[9:].strip()
            if "=" in content:
                key, value = content.split("=", 1)
                facts.remember(key.strip(), value.strip())
                st.session_state.facts[key.strip()] = value.strip()
                return f"I'll remember that: {key.strip()} = {value.strip()}"
            else:
                return "Usage: remember key = value"

        elif user_input.lower().startswith("recall "):
            key = user_input[7:].strip()
            return facts.recall(key)

        elif user_input.lower() == "show facts":
            all_facts = facts.get_all_facts()
            if all_facts:
                result = "Stored facts:\n"
                for k, v in all_facts.items():
                    result += f"  - {k}: {v}\n"
                return result
            return "No facts stored yet."

        elif user_input.lower().startswith("read "):
            file_path = user_input[5:].strip()
            result = rag.add_document(file_path)
            if "Added" in result:
                st.session_state.documents.append(file_path)
            return result

        elif user_input.lower() == "show documents":
            docs = rag.list_documents()
            if docs:
                return "Documents:\n" + "\n".join(f"  - {d}" for d in docs)
            return "No documents loaded."

        elif user_input.lower().startswith("search "):
            query = user_input[7:].strip()
            return rag.get_context(query)

        # ---- CHECK FACTS ----
        all_facts = facts.get_all_facts()
        lower_input = user_input.lower()
        for key, value in all_facts.items():
            key_lower = key.lower()
            if (key_lower in lower_input and
                any(phrase in lower_input for phrase in ["what", "know", "recall", "tell", "remind"])):
                return f"Your {key} is **{value}**."

        # ---- MCP AGENT TEAM (complex tasks) ----
        force_agent = st.session_state.pop("force_agent", None)

        if force_agent or st.session_state.get("mcp_connected"):
            try:
                if force_agent:
                    st.session_state.last_agent = force_agent
                    result = await call_mcp_agent(force_agent, user_input)
                    return result
                else:
                    # Auto-route: use keyword detection to pick agent
                    task_lower = user_input.lower()
                    agent = "research"  # default

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
                    return result
            except Exception as mcp_error:
                # If MCP fails, fall back to single agent
                st.warning(f"MCP failed ({mcp_error}), using single agent...")

        # ---- SINGLE AGENT (simple tasks or MCP fallback) ----
        facts_context = facts.get_context_string()
        enhanced_input = f"{user_input}\n\n{facts_context}\n\nIMPORTANT: Always respond in English."
        from llama_agent1 import run_agent
        result = await run_agent(enhanced_input)

        memory.add_message("assistant", str(result))
        st.session_state.last_agent = "single_agent"
        return str(result)

    except Exception as e:
        return f"Error: {str(e)}"


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
            with st.spinner("Thinking..."):
                response = asyncio.run(process_message(prompt))

            st.markdown(response)
            assistant_time = datetime.now().strftime("%H:%M:%S")

            # Show which agent handled it
            agent_used = st.session_state.get("last_agent", None)
            if agent_used:
                agent_labels = {
                    "single_agent": "Single Agent (14 tools)",
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
                st.caption(f"{assistant_time}")

        # Add assistant message
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
