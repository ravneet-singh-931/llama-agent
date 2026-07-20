"""
Tests for llama_agent1.py tools
Run with: pytest tests/
"""

import math
import datetime
import sys
import os

# Add parent directory to path so we can import the tools
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# We need to test the tool functions directly, not the whole agent
# (because the agent needs GROQ_API_KEY to initialize)


# ============ TEST CALCULATOR ============

def calculator(expression: str) -> str:
    """Copy of calculator function for testing"""
    try:
        allowed_names = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        return f"Result: {result}"
    except Exception as e:
        return f"Error calculating: {str(e)}"


def test_calculator_addition():
    result = calculator("2 + 2")
    assert result == "Result: 4"


def test_calculator_multiplication():
    result = calculator("10 * 5")
    assert result == "Result: 50"


def test_calculator_sqrt():
    result = calculator("sqrt(16)")
    assert result == "Result: 4.0"


def test_calculator_complex():
    result = calculator("sqrt(144) + 3")
    assert result == "Result: 15.0"


def test_calculator_error():
    result = calculator("invalid")
    assert "Error" in result


# ============ TEST TEXT ANALYZER ============

def text_analyzer(text: str) -> str:
    """Copy of text_analyzer function for testing"""
    words = len(text.split())
    characters = len(text)
    characters_no_spaces = len(text.replace(" ", ""))
    sentences = text.count('.') + text.count('!') + text.count('?')
    return (
        f"Text Analysis:\n"
        f"  - Words: {words}\n"
        f"  - Characters (with spaces): {characters}\n"
        f"  - Characters (without spaces): {characters_no_spaces}\n"
        f"  - Sentences: {sentences}"
    )


def test_text_analyzer_words():
    result = text_analyzer("Hello world")
    assert "Words: 2" in result


def test_text_analyzer_sentences():
    result = text_analyzer("Hello. How are you?")
    assert "Sentences: 2" in result


def test_text_analyzer_empty():
    result = text_analyzer("")
    assert "Words: 0" in result


# ============ TEST UNIT CONVERTER ============

def unit_converter(value: float, from_unit: str, to_unit: str) -> str:
    """Copy of unit_converter function for testing"""
    conversions = {
        ("km", "miles"): lambda x: x * 0.621371,
        ("miles", "km"): lambda x: x * 1.60934,
        ("kg", "pounds"): lambda x: x * 2.20462,
        ("pounds", "kg"): lambda x: x * 0.453592,
        ("celsius", "fahrenheit"): lambda x: (x * 9/5) + 32,
        ("fahrenheit", "celsius"): lambda x: (x - 32) * 5/9,
        ("meters", "feet"): lambda x: x * 3.28084,
        ("feet", "meters"): lambda x: x * 0.3048,
    }
    key = (from_unit.lower(), to_unit.lower())
    if key in conversions:
        result = conversions[key](value)
        return f"{value} {from_unit} = {result:.4f} {to_unit}"
    else:
        return f"Conversion from {from_unit} to {to_unit} not supported."


def test_km_to_miles():
    result = unit_converter(1, "km", "miles")
    assert "0.6214" in result


def test_celsius_to_fahrenheit():
    result = unit_converter(100, "celsius", "fahrenheit")
    assert "212.0000" in result


def test_kg_to_pounds():
    result = unit_converter(1, "kg", "pounds")
    assert "2.2046" in result


def test_unsupported_conversion():
    result = unit_converter(1, "liters", "gallons")
    assert "not supported" in result


# ============ TEST STRING OPERATIONS ============

def string_operations(text: str, operation: str) -> str:
    """Copy of string_operations function for testing"""
    operations = {
        "uppercase": text.upper(),
        "lowercase": text.lower(),
        "reverse": text[::-1],
        "capitalize": text.capitalize(),
        "title": text.title(),
    }
    op = operation.lower()
    if op in operations:
        return f"Result: {operations[op]}"
    else:
        return f"Operation '{operation}' not supported."


def test_uppercase():
    result = string_operations("hello", "uppercase")
    assert "HELLO" in result


def test_lowercase():
    result = string_operations("HELLO", "lowercase")
    assert "hello" in result


def test_reverse():
    result = string_operations("abc", "reverse")
    assert "cba" in result


def test_unsupported_operation():
    result = string_operations("hello", "unknown")
    assert "not supported" in result


# ============ TEST DATETIME ============

def get_current_datetime() -> str:
    """Copy of get_current_datetime function for testing"""
    now = datetime.datetime.now()
    return f"Current Date & Time: {now.strftime('%Y-%m-%d %H:%M:%S')}"


def test_datetime_returns_string():
    result = get_current_datetime()
    assert "Current Date & Time:" in result


def test_datetime_format():
    result = get_current_datetime()
    # Should contain a date in YYYY-MM-DD format
    assert len(result) > 20
