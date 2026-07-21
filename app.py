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
import streamlit.components.v1 as components
import asyncio
import os
import sys
import uuid
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

        # ---- Chat Mode ----
        st.subheader("Chat Mode")
        chat_mode = st.radio(
            "Mode",
            ["Standard (Streaming)", "Real-time (WebSocket)"],
            label_visibility="collapsed",
            index=0 if st.session_state.get("chat_mode", "standard") == "standard" else 1,
        )
        st.session_state.chat_mode = "standard" if "Standard" in chat_mode else "websocket"
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

def _render_standard_chat():
    """Render the standard streaming chat UI"""
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
                # STREAMING single-agent -- tokens appear one by one
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


def _render_websocket_chat():
    """Render real-time WebSocket chat using an embedded HTML/JS client."""

    # Connection status
    ws_port = st.session_state.get("ws_port", 8000)
    ws_host = st.session_state.get("ws_host", "localhost")

    col1, col2 = st.columns([3, 1])
    with col2:
        st.caption("WebSocket Settings")
        ws_host = st.text_input("API Host", value=ws_host, key="ws_host_input")
        ws_port = st.number_input("API Port", value=ws_port, key="ws_port_input")
        st.session_state.ws_host = ws_host
        st.session_state.ws_port = ws_port

    user_id = st.session_state.get("ws_user_id", str(uuid.uuid4())[:8])
    st.session_state.ws_user_id = user_id
    ws_url = f"ws://{ws_host}:{ws_port}/ws/{user_id}"

    st.info(f"Connecting to: `{ws_url}`  |  Your User ID: `{user_id}`")

    # Embed the WebSocket chat client as HTML/JS
    ws_chat_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; padding: 10px; background: #0e1117; color: #e0e0e0; }}
        #chat-box {{ height: 420px; overflow-y: auto; border: 1px solid #333; border-radius: 8px; padding: 12px; margin-bottom: 10px; background: #1a1a2e; }}
        .msg {{ margin-bottom: 10px; padding: 8px 12px; border-radius: 8px; max-width: 85%; }}
        .msg.user {{ background: #1e3a5f; border-left: 3px solid #00d4ff; margin-left: auto; }}
        .msg.assistant {{ background: #2d2d44; border-left: 3px solid #00ff88; }}
        .msg.system {{ background: #333; border-left: 3px solid #888; font-style: italic; text-align: center; max-width: 100%; }}
        .msg .sender {{ font-size: 11px; color: #888; margin-bottom: 2px; }}
        .msg .text {{ font-size: 14px; line-height: 1.4; white-space: pre-wrap; }}
        .msg .time {{ font-size: 10px; color: #666; margin-top: 3px; }}
        #input-area {{ display: flex; gap: 8px; }}
        #msg-input {{ flex: 1; padding: 10px; border: 1px solid #444; border-radius: 6px; background: #1a1a2e; color: #fff; font-size: 14px; }}
        #msg-input:focus {{ outline: none; border-color: #00ff88; }}
        #send-btn {{ padding: 10px 20px; background: #00ff88; color: #000; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 14px; }}
        #send-btn:hover {{ background: #00cc6a; }}
        #send-btn:disabled {{ background: #555; cursor: not-allowed; }}
        #status {{ padding: 6px 12px; border-radius: 4px; margin-bottom: 8px; font-size: 12px; }}
        .connected {{ background: #1a3d1a; color: #00ff88; }}
        .disconnected {{ background: #3d1a1a; color: #ff4444; }}
        .thinking {{ background: #3d3d1a; color: #ffaa00; }}
        #online-users {{ font-size: 11px; color: #888; margin-bottom: 6px; }}
    </style>
    </head>
    <body>
    <div id="status" class="disconnected">Disconnected</div>
    <div id="online-users"></div>
    <div id="chat-box"></div>
    <div id="input-area">
        <input type="text" id="msg-input" placeholder="Type a message..." disabled />
        <button id="send-btn" disabled onclick="sendMessage()">Send</button>
    </div>

    <script>
        const WS_URL = "{ws_url}";
        const USER_ID = "{user_id}";
        let ws = null;
        let reconnectTimer = null;

        const chatBox = document.getElementById('chat-box');
        const msgInput = document.getElementById('msg-input');
        const sendBtn = document.getElementById('send-btn');
        const statusEl = document.getElementById('status');
        const onlineEl = document.getElementById('online-users');

        function addMessage(sender, text, type) {{
            const div = document.createElement('div');
            div.className = 'msg ' + type;
            const time = new Date().toLocaleTimeString();
            div.innerHTML = '<div class="sender">' + sender + '</div>' +
                           '<div class="text">' + escapeHtml(text) + '</div>' +
                           '<div class="time">' + time + '</div>';
            chatBox.appendChild(div);
            chatBox.scrollTop = chatBox.scrollHeight;
        }}

        function escapeHtml(text) {{
            const d = document.createElement('div');
            d.textContent = text;
            return d.innerHTML;
        }}

        function setStatus(text, cls) {{
            statusEl.textContent = text;
            statusEl.className = cls;
        }}

        function connect() {{
            if (ws && ws.readyState === WebSocket.OPEN) return;

            setStatus('Connecting...', 'thinking');
            ws = new WebSocket(WS_URL);

            ws.onopen = function() {{
                setStatus('Connected (user: ' + USER_ID + ')', 'connected');
                msgInput.disabled = false;
                sendBtn.disabled = false;
                msgInput.focus();
            }};

            ws.onmessage = function(event) {{
                const msg = JSON.parse(event.data);
                handleServerMessage(msg);
            }};

            ws.onclose = function() {{
                setStatus('Disconnected - reconnecting...', 'disconnected');
                msgInput.disabled = true;
                sendBtn.disabled = true;
                reconnectTimer = setTimeout(connect, 3000);
            }};

            ws.onerror = function() {{
                setStatus('Connection error', 'disconnected');
            }};
        }}

        function handleServerMessage(msg) {{
            switch(msg.type) {{
                case 'connect':
                    addMessage('System', msg.data.message, 'system');
                    if (msg.data.online_users) updateOnlineUsers(msg.data.online_users);
                    break;
                case 'response':
                    addMessage('Agent', msg.data.content, 'assistant');
                    break;
                case 'chat':
                    addMessage(msg.data.sender || 'User', msg.data.content, 'user');
                    break;
                case 'typing':
                    if (msg.data.is_typing) {{
                        setStatus('Agent is typing...', 'thinking');
                    }} else {{
                        setStatus('Connected (user: ' + USER_ID + ')', 'connected');
                    }}
                    break;
                case 'online_users':
                    updateOnlineUsers(msg.data.users);
                    break;
                case 'agent_status':
                    if (msg.data.status === 'thinking') {{
                        setStatus('Agent is thinking...', 'thinking');
                    }} else {{
                        setStatus('Connected (user: ' + USER_ID + ')', 'connected');
                    }}
                    break;
                case 'error':
                    addMessage('Error', msg.data.error, 'system');
                    setStatus('Connected (user: ' + USER_ID + ')', 'connected');
                    break;
                case 'heartbeat':
                    break;
            }}
        }}

        function updateOnlineUsers(users) {{
            if (!users || users.length === 0) {{
                onlineEl.textContent = '';
                return;
            }}
            const names = users.map(u => u.username || u.user_id).join(', ');
            onlineEl.textContent = 'Online: ' + names;
        }}

        function sendMessage() {{
            const text = msgInput.value.trim();
            if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

            addMessage('You', text, 'user');

            const outMsg = {{
                type: 'chat',
                data: {{ content: text }},
                timestamp: new Date().toISOString()
            }};

            ws.send(JSON.stringify(outMsg));
            msgInput.value = '';
            msgInput.focus();
        }}

        msgInput.addEventListener('keydown', function(e) {{
            if (e.key === 'Enter' && !e.shiftKey) {{
                e.preventDefault();
                sendMessage();
            }}
        }});

        // Auto-connect
        connect();
    </script>
    </body>
    </html>
    """

    components.html(ws_chat_html, height=560, scrolling=False)


if __name__ == "__main__":
    main()
