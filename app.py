"""
Llama Agent - Web Interface (Streamlit)
Run with: streamlit run app.py
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
    page_title="🤖 Llama Agent",
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
    .tool-badge {
        background-color: #2d2d44;
        padding: 0.25rem 0.5rem;
        border-radius: 0.25rem;
        font-size: 0.75rem;
    }
</style>
""", unsafe_allow_html=True)


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


def display_sidebar():
    """Display sidebar with options"""
    with st.sidebar:
        st.image("https://img.icons8.com/fluency/96/robot-2.png", width=80)
        st.title("🤖 Llama Agent")
        st.markdown("---")
        
        # Memory section
        st.subheader("🧠 Memory")
        if st.session_state.facts:
            for key, value in st.session_state.facts.items():
                st.info(f"**{key}**: {value}")
        else:
            st.write("No facts stored yet.")
        
        st.markdown("---")
        
        # Documents section
        st.subheader("📄 Loaded Documents")
        if st.session_state.documents:
            for doc in st.session_state.documents:
                st.success(f"✅ {doc}")
        else:
            st.write("No documents loaded.")
        
        st.markdown("---")
        
        # Actions
        st.subheader("⚡ Actions")
        if st.button("🗑️ Clear Chat"):
            st.session_state.messages = []
            st.rerun()
        
        if st.button("🧠 Clear Memory"):
            st.session_state.memory = {}
            st.session_state.facts = {}
            st.rerun()
        
        st.markdown("---")
        
        # Stats
        st.subheader("📊 Stats")
        st.metric("Messages", len(st.session_state.messages))
        st.metric("Facts", len(st.session_state.facts))
        st.metric("Documents", len(st.session_state.documents))


async def process_message(user_input: str) -> str:
    """Process user message and get response"""
    try:
        # Import here to avoid startup issues
        from modules.memory import ConversationMemory, FactMemory
        from modules.rag import SimpleRAG
        from modules.monitoring import AgentLogger
        
        # Initialize modules
        memory = ConversationMemory()
        facts = FactMemory()
        rag = SimpleRAG()
        logger = AgentLogger()
        
        # Add user message to memory
        memory.add_message("user", user_input)
        
        # Check for special commands
        if user_input.lower().startswith("remember "):
            # Parse "remember key = value"
            content = user_input[9:].strip()
            if "=" in content:
                key, value = content.split("=", 1)
                facts.remember(key.strip(), value.strip())
                st.session_state.facts[key.strip()] = value.strip()
                return f"✅ I'll remember that: {key.strip()} = {value.strip()}"
            else:
                return "Usage: remember key = value"
        
        elif user_input.lower().startswith("recall "):
            key = user_input[7:].strip()
            return facts.recall(key)
        
        elif user_input.lower() == "show facts":
            all_facts = facts.get_all_facts()
            if all_facts:
                result = "📝 Stored facts:\n"
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
                return "📚 Documents:\n" + "\n".join(f"  - {d}" for d in docs)
            return "No documents loaded."
        
        elif user_input.lower().startswith("search "):
            query = user_input[7:].strip()
            return rag.get_context(query)
        
        # Regular AI response
        from llama_agent1 import run_agent
        result = await run_agent(user_input)
        
        # Add to memory
        memory.add_message("assistant", str(result))
        
        return str(result)
        
    except Exception as e:
        return f"❌ Error: {str(e)}"


def main():
    init_session_state()
    display_sidebar()
    
    # Main chat area
    st.title("🤖 Llama Agent")
    st.markdown("*AI Agent with Memory, RAG, and 14 Tools*")
    
    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            if "timestamp" in message:
                st.caption(f"⏰ {message['timestamp']}")
    
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
            st.caption(f"⏰ {timestamp}")
        
        # Get AI response
        with st.chat_message("assistant"):
            with st.spinner("Thinking..."):
                response = asyncio.run(process_message(prompt))
            
            st.markdown(response)
            assistant_time = datetime.now().strftime("%H:%M:%S")
            st.caption(f"⏰ {assistant_time}")
        
        # Add assistant message
        st.session_state.messages.append({
            "role": "assistant",
            "content": response,
            "timestamp": assistant_time
        })
    
    # Quick commands
    st.markdown("---")
    st.subheader("⚡ Quick Commands")
    
    cols = st.columns(4)
    with cols[0]:
        if st.button("📊 Memory Stats"):
            st.session_state.messages.append({
                "role": "user",
                "content": "get_memory_stats",
                "timestamp": datetime.now().strftime("%H:%M:%S")
            })
            st.rerun()
    
    with cols[1]:
        if st.button("🏥 Health Check"):
            st.session_state.messages.append({
                "role": "user",
                "content": "get_agent_health",
                "timestamp": datetime.now().strftime("%H:%M:%S")
            })
            st.rerun()
    
    with cols[2]:
        if st.button("📋 Show Facts"):
            st.session_state.messages.append({
                "role": "user",
                "content": "show facts",
                "timestamp": datetime.now().strftime("%H:%M:%S")
            })
            st.rerun()
    
    with cols[3]:
        if st.button("📚 Show Documents"):
            st.session_state.messages.append({
                "role": "user",
                "content": "show documents",
                "timestamp": datetime.now().strftime("%H:%M:%S")
            })
            st.rerun()


if __name__ == "__main__":
    main()
