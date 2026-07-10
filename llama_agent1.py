import asyncio
import os
import datetime
import math
from typing import Any, Iterable, Sequence

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False

from groq import Groq
from llama_index.core.agent import ReActAgent
from llama_index.core.llms.llm import LLM
from llama_index.core.base.llms.types import (
    ChatMessage,
    ChatResponse,
    CompletionResponse,
    ChatResponseGen,
    CompletionResponseGen,
    LLMMetadata,
    MessageRole,
)
from llama_index.core.bridge.pydantic import ConfigDict, PrivateAttr
from llama_index.core.tools import FunctionTool

# ===================== SETUP =====================
# Load environment from .env (if present)
load_dotenv()

# Read Groq API key from environment
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise SystemExit("GROQ_API_KEY not set. Add it to your environment or a .env file.")


class GroqLLM(LLM):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    model: str
    api_key: str
    base_url: str | None = None
    _client: Groq = PrivateAttr()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = Groq(api_key=self.api_key, base_url=self.base_url)

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(
            context_window=8192,
            num_output=1024,
            is_chat_model=True,
            is_function_calling_model=False,
            model_name=self.model,
            system_role=MessageRole.SYSTEM,
        )

    def _format_messages(self, messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
        formatted = []
        for message in messages:
            role = message.role.value if isinstance(message.role, MessageRole) else message.role
            formatted.append({"role": role, "content": message.content})
        return formatted

    def _parse_response(self, response: Any) -> ChatResponse:
        choice = response.choices[0]
        message = choice.message
        text = message.content or ""
        return ChatResponse(
            message=ChatMessage(role=MessageRole.ASSISTANT, content=text),
            raw=response,
        )

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._format_messages(messages),
            **kwargs,
        )
        return self._parse_response(response)

    async def achat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return await asyncio.to_thread(self.chat, messages, **kwargs)

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        response = self.chat([ChatMessage(role=MessageRole.USER, content=prompt)], **kwargs)
        return CompletionResponse(text=response.message.content or "", raw=response)

    async def acomplete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        return await asyncio.to_thread(self.complete, prompt, formatted=formatted, **kwargs)

    def stream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponseGen:
        raise NotImplementedError("Streaming chat is not supported by GroqLLM in this script")

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
        raise NotImplementedError("Streaming completion is not supported by GroqLLM in this script")

    async def astream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponseGen:
        return await asyncio.to_thread(self.stream_chat, messages, **kwargs)

    async def astream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
        return await asyncio.to_thread(self.stream_complete, prompt, formatted=formatted, **kwargs)


# Initialize Groq LLM
llm = GroqLLM(model="llama-3.3-70b-versatile", api_key=GROQ_API_KEY)

# ===================== TOOLS =====================

def calculator(expression: str) -> str:
    """
    Evaluates a mathematical expression and returns the result.
    Example: '2 + 2', '10 * 5', 'sqrt(16)'
    """
    try:
        # Safe evaluation with math functions
        allowed_names = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
        result = eval(expression, {"__builtins__": {}}, allowed_names)
        return f"Result: {result}"
    except Exception as e:
        return f"Error calculating: {str(e)}"


def get_current_datetime() -> str:
    """
    Returns the current date and time.
    """
    now = datetime.datetime.now()
    return f"Current Date & Time: {now.strftime('%Y-%m-%d %H:%M:%S')}"


def text_analyzer(text: str) -> str:
    """
    Analyzes the given text and returns word count, character count, and sentence count.
    """
    words = len(text.split())
    characters = len(text)
    characters_no_spaces = len(text.replace(" ", ""))
    sentences = text.count('.') + text.count('!') + text.count('?')
    
    return (
        f"📊 Text Analysis:\n"
        f"  - Words: {words}\n"
        f"  - Characters (with spaces): {characters}\n"
        f"  - Characters (without spaces): {characters_no_spaces}\n"
        f"  - Sentences: {sentences}"
    )


def unit_converter(value: float, from_unit: str, to_unit: str) -> str:
    """
    Converts between common units.
    Supports: km/miles, kg/pounds, celsius/fahrenheit, meters/feet
    """
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


def string_operations(text: str, operation: str) -> str:
    """
    Performs string operations on text.
    Operations: uppercase, lowercase, reverse, capitalize, title
    """
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
        return f"Operation '{operation}' not supported. Available: {', '.join(operations.keys())}"


def simple_notes(action: str, note: str = "") -> str:
    """
    Simple note-taking tool. Actions: add, view, clear
    """
    notes_file = "notes.txt"
    
    if action == "add":
        with open(notes_file, "a") as f:
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"[{timestamp}] {note}\n")
        return f"✅ Note added: '{note}'"
    
    elif action == "view":
        try:
            with open(notes_file, "r") as f:
                content = f.read()
            return f"📝 Your Notes:\n{content}" if content else "No notes found."
        except FileNotFoundError:
            return "No notes found."
    
    elif action == "clear":
        with open(notes_file, "w") as f:
            f.write("")
        return "🗑️ All notes cleared."
    
    else:
        return "Invalid action. Use: add, view, or clear"


# ===================== CREATE TOOLS =====================

calculator_tool = FunctionTool.from_defaults(
    fn=calculator,
    name="calculator",
    description="Useful for performing mathematical calculations. Input should be a mathematical expression."
)

datetime_tool = FunctionTool.from_defaults(
    fn=get_current_datetime,
    name="get_datetime",
    description="Get the current date and time."
)

text_analyzer_tool = FunctionTool.from_defaults(
    fn=text_analyzer,
    name="text_analyzer",
    description="Analyze text to get word count, character count, and sentence count."
)

unit_converter_tool = FunctionTool.from_defaults(
    fn=unit_converter,
    name="unit_converter",
    description="Convert between units like km/miles, kg/pounds, celsius/fahrenheit, meters/feet."
)

string_ops_tool = FunctionTool.from_defaults(
    fn=string_operations,
    name="string_operations",
    description="Perform string operations like uppercase, lowercase, reverse, capitalize, title."
)

notes_tool = FunctionTool.from_defaults(
    fn=simple_notes,
    name="notes",
    description="Simple note-taking tool. Can add, view, or clear notes."
)

# ===================== CREATE AGENT =====================

tools = [
    calculator_tool,
    datetime_tool,
    text_analyzer_tool,
    unit_converter_tool,
    string_ops_tool,
    notes_tool,
]

agent = ReActAgent(
    tools=tools,
    llm=llm,
    verbose=True,  # Set to False to hide agent's thinking process
    streaming=False,
)

# ===================== MAIN CHAT LOOP =====================

async def run_agent(user_input: str):
    handler = agent.run(user_input)
    output = await handler
    return output


def main():
    print("=" * 60)
    print("        🤖 LlamaIndex Agent with Groq API")
    print("=" * 60)
    print("Available capabilities:")
    print("  ✅ Mathematical calculations")
    print("  ✅ Current date & time")
    print("  ✅ Text analysis")
    print("  ✅ Unit conversion")
    print("  ✅ String operations")
    print("  ✅ Note taking")
    print("  ✅ General conversation")
    print("\nType 'quit' or 'exit' to stop")
    print("=" * 60)
    
    while True:
        try:
            user_input = input("\n👤 You: ").strip()
            
            if not user_input:
                print("Please enter something!")
                continue
            
            if user_input.lower() in ["quit", "exit", "bye"]:
                print("👋 Goodbye! Have a great day!")
                break
            
            print("\n🤖 Agent: Thinking...")
            result = asyncio.run(run_agent(user_input))
            print(f"\n🤖 Agent: {result}")
            
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!")
            break
        except Exception as e:
            print(f"\n❌ Error: {str(e)}")


if __name__ == "__main__":
    main()