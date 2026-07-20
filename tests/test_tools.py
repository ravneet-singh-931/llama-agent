"""
Tests for Enhanced Llama Agent (Full Stack)
Run with: pytest tests/ -v
"""

import math
import datetime
import sys
import os
import json
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============ TEST CALCULATOR ============

def calculator(expression: str) -> str:
    try:
        allowed_names = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        return f"Result: {result}"
    except Exception as e:
        return f"Error: {str(e)}"


def test_calculator_addition():
    assert calculator("2 + 2") == "Result: 4"

def test_calculator_multiplication():
    assert calculator("10 * 5") == "Result: 50"

def test_calculator_sqrt():
    assert calculator("sqrt(16)") == "Result: 4.0"

def test_calculator_error():
    assert "Error" in calculator("invalid")


# ============ TEST TEXT ANALYZER ============

def text_analyzer(text: str) -> str:
    words = len(text.split())
    characters = len(text)
    sentences = text.count('.') + text.count('!') + text.count('?')
    return f"Words: {words}, Characters: {characters}, Sentences: {sentences}"


def test_text_analyzer_words():
    assert "Words: 2" in text_analyzer("Hello world")

def test_text_analyzer_empty():
    assert "Words: 0" in text_analyzer("")


# ============ TEST UNIT CONVERTER ============

def unit_converter(value: float, from_unit: str, to_unit: str) -> str:
    conversions = {
        ("km", "miles"): lambda x: x * 0.621371,
        ("celsius", "fahrenheit"): lambda x: (x * 9/5) + 32,
    }
    key = (from_unit.lower(), to_unit.lower())
    if key in conversions:
        result = conversions[key](value)
        return f"{value} {from_unit} = {result:.4f} {to_unit}"
    return f"Conversion not supported."


def test_km_to_miles():
    assert "0.6214" in unit_converter(1, "km", "miles")

def test_celsius_to_fahrenheit():
    assert "212.0000" in unit_converter(100, "celsius", "fahrenheit")


# ============ TEST STRING OPERATIONS ============

def string_operations(text: str, operation: str) -> str:
    operations = {"uppercase": text.upper(), "lowercase": text.lower(), "reverse": text[::-1]}
    return f"Result: {operations.get(operation.lower(), 'Operation not supported.')}"


def test_uppercase():
    assert "HELLO" in string_operations("hello", "uppercase")

def test_reverse():
    assert "cba" in string_operations("abc", "reverse")


# ============ TEST MEMORY MODULE ============

def test_memory_add_and_retrieve():
    from modules.memory import ConversationMemory
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    try:
        mem = ConversationMemory(memory_file=temp_file)
        mem.add_message("user", "Hello!")
        mem.add_message("assistant", "Hi there!")
        recent = mem.get_recent(2)
        assert len(recent) == 2
    finally:
        os.unlink(temp_file)


def test_memory_search():
    from modules.memory import ConversationMemory
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    try:
        mem = ConversationMemory(memory_file=temp_file)
        mem.add_message("user", "I like Python programming")
        results = mem.search("Python")
        assert len(results) == 1
    finally:
        os.unlink(temp_file)


def test_memory_stats():
    from modules.memory import ConversationMemory
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    try:
        mem = ConversationMemory(memory_file=temp_file)
        mem.add_message("user", "Test")
        stats = mem.get_stats()
        assert stats["total_messages"] == 1
    finally:
        os.unlink(temp_file)


# ============ TEST FACT MEMORY ============

def test_fact_remember_and_recall():
    from modules.memory import FactMemory
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    try:
        facts = FactMemory(facts_file=temp_file)
        facts.remember("name", "Ravneet")
        assert facts.recall("name") == "Ravneet"
    finally:
        os.unlink(temp_file)


def test_fact_recall_unknown():
    from modules.memory import FactMemory
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    try:
        facts = FactMemory(facts_file=temp_file)
        assert "don't remember" in facts.recall("unknown")
    finally:
        os.unlink(temp_file)


# ============ TEST RAG MODULE ============

def test_rag_add_text():
    from modules.rag import SimpleRAG
    rag = SimpleRAG()
    result = rag.add_text("This is a test document about Python programming.")
    assert "Added" in result
    assert rag.get_stats()["total_chunks"] > 0


def test_rag_search():
    from modules.rag import SimpleRAG
    rag = SimpleRAG()
    rag.add_text("Python is a programming language used for AI.")
    results = rag.search("Python programming")
    assert len(results) > 0


def test_rag_list_documents():
    from modules.rag import SimpleRAG
    rag = SimpleRAG()
    rag.add_text("Test content", source="test.txt")
    assert "test.txt" in rag.list_documents()


# ============ TEST MONITORING ============

def test_monitor_health():
    from modules.monitoring import PerformanceMonitor
    monitor = PerformanceMonitor()
    monitor.track_request()
    health = monitor.get_health()
    assert health["status"] == "healthy"
    assert health["total_requests"] == 1


def test_logger_metrics():
    from modules.monitoring import AgentLogger
    with tempfile.TemporaryDirectory() as temp_dir:
        logger = AgentLogger(log_dir=temp_dir)
        logger.log_interaction("test", "response", ["tool"], 0.5)
        metrics = logger.get_metrics()
        assert metrics["total_queries"] == 1


# ============ TEST MULTI-AGENT ============

def test_multi_agent_orchestrator_init():
    """Test multi-agent system initialization (no API key needed)"""
    import os
    # Set a dummy key for testing
    os.environ["GROQ_API_KEY"] = "test_key_for_testing_only"
    
    try:
        from multi_agent import MultiAgentOrchestrator
        orchestrator = MultiAgentOrchestrator()
        assert len(orchestrator.agents) == 5
        assert "research" in orchestrator.agents
        assert "coder" in orchestrator.agents
        assert "writer" in orchestrator.agents
        assert "analyzer" in orchestrator.agents
        assert "planner" in orchestrator.agents
    finally:
        # Clean up
        if "GROQ_API_KEY" in os.environ:
            del os.environ["GROQ_API_KEY"]


def test_multi_agent_team_status():
    """Test multi-agent team status (no API key needed)"""
    import os
    os.environ["GROQ_API_KEY"] = "test_key_for_testing_only"
    
    try:
        from multi_agent import MultiAgentOrchestrator
        orchestrator = MultiAgentOrchestrator()
        status = orchestrator.get_team_status()
        assert "Agent Team Status" in status
        assert "Research Agent" in status
        assert "Coder Agent" in status
    finally:
        if "GROQ_API_KEY" in os.environ:
            del os.environ["GROQ_API_KEY"]


def test_multi_agent_agent_names():
    """Test that all agents have correct names"""
    import os
    os.environ["GROQ_API_KEY"] = "test_key_for_testing_only"
    
    try:
        from multi_agent import MultiAgentOrchestrator
        orchestrator = MultiAgentOrchestrator()
        
        expected_names = {
            "research": "Research Agent",
            "coder": "Coder Agent",
            "writer": "Writer Agent",
            "analyzer": "Analyzer Agent",
            "planner": "Planner Agent"
        }
        
        for key, expected_name in expected_names.items():
            assert orchestrator.agents[key].name == expected_name
    finally:
        if "GROQ_API_KEY" in os.environ:
            del os.environ["GROQ_API_KEY"]
