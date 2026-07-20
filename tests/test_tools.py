"""
Tests for Enhanced Llama Agent
Run with: pytest tests/ -v
"""

import math
import datetime
import sys
import os
import json
import tempfile

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# ============ TEST CALCULATOR ============

def calculator(expression: str) -> str:
    try:
        allowed_names = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        return f"Result: {result}"
    except Exception as e:
        return f"Error calculating: {str(e)}"


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
    result = text_analyzer("Hello world")
    assert "Words: 2" in result

def test_text_analyzer_empty():
    result = text_analyzer("")
    assert "Words: 0" in result


# ============ TEST UNIT CONVERTER ============

def unit_converter(value: float, from_unit: str, to_unit: str) -> str:
    conversions = {
        ("km", "miles"): lambda x: x * 0.621371,
        ("miles", "km"): lambda x: x * 1.60934,
        ("kg", "pounds"): lambda x: x * 2.20462,
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

def test_unsupported_conversion():
    assert "not supported" in unit_converter(1, "liters", "gallons")


# ============ TEST STRING OPERATIONS ============

def string_operations(text: str, operation: str) -> str:
    operations = {
        "uppercase": text.upper(),
        "lowercase": text.lower(),
        "reverse": text[::-1],
    }
    op = operation.lower()
    if op in operations:
        return f"Result: {operations[op]}"
    return f"Operation not supported."


def test_uppercase():
    assert "HELLO" in string_operations("hello", "uppercase")

def test_lowercase():
    assert "hello" in string_operations("HELLO", "lowercase")

def test_reverse():
    assert "cba" in string_operations("abc", "reverse")


# ============ TEST MEMORY MODULE ============

def test_memory_add_and_retrieve():
    """Test that memory can store and retrieve messages"""
    from modules.memory import ConversationMemory
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    
    try:
        mem = ConversationMemory(memory_file=temp_file)
        mem.add_message("user", "Hello!")
        mem.add_message("assistant", "Hi there!")
        
        recent = mem.get_recent(2)
        assert len(recent) == 2
        assert recent[0]["role"] == "user"
        assert recent[1]["role"] == "assistant"
    finally:
        os.unlink(temp_file)


def test_memory_search():
    """Test that memory search works"""
    from modules.memory import ConversationMemory
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    
    try:
        mem = ConversationMemory(memory_file=temp_file)
        mem.add_message("user", "I like Python programming")
        mem.add_message("assistant", "Python is great!")
        
        results = mem.search("Python")
        assert len(results) == 2
    finally:
        os.unlink(temp_file)


def test_memory_stats():
    """Test memory statistics"""
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
    """Test that facts can be stored and recalled"""
    from modules.memory import FactMemory
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    
    try:
        fact_mem = FactMemory(facts_file=temp_file)
        fact_mem.remember("name", "Ravneet")
        
        result = fact_mem.recall("name")
        assert result == "Ravneet"
    finally:
        os.unlink(temp_file)


def test_fact_recall_unknown():
    """Test recalling unknown fact"""
    from modules.memory import FactMemory
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_file = f.name
    
    try:
        fact_mem = FactMemory(facts_file=temp_file)
        result = fact_mem.recall("unknown")
        assert "don't remember" in result
    finally:
        os.unlink(temp_file)


# ============ TEST RAG MODULE ============

def test_rag_add_text():
    """Test adding text to RAG"""
    from modules.rag import SimpleRAG
    
    rag = SimpleRAG()
    result = rag.add_text("This is a test document about Python programming.")
    assert "Added" in result
    assert rag.get_stats()["total_chunks"] > 0


def test_rag_search():
    """Test searching documents"""
    from modules.rag import SimpleRAG
    
    rag = SimpleRAG()
    rag.add_text("Python is a programming language. It is used for AI and web development.")
    
    results = rag.search("Python programming")
    assert len(results) > 0


def test_rag_list_documents():
    """Test listing documents"""
    from modules.rag import SimpleRAG
    
    rag = SimpleRAG()
    rag.add_text("Test content", source="test.txt")
    
    docs = rag.list_documents()
    assert "test.txt" in docs


# ============ TEST MONITORING ============

def test_monitor_health():
    """Test health check"""
    from modules.monitoring import PerformanceMonitor
    
    monitor = PerformanceMonitor()
    monitor.track_request()
    health = monitor.get_health()
    
    assert health["status"] == "healthy"
    assert health["total_requests"] == 1


def test_logger_metrics():
    """Test logger metrics"""
    from modules.monitoring import AgentLogger
    
    with tempfile.TemporaryDirectory() as temp_dir:
        logger = AgentLogger(log_dir=temp_dir)
        logger.log_interaction("test input", "test output", ["calculator"], 0.5)
        
        metrics = logger.get_metrics()
        assert metrics["total_queries"] == 1
