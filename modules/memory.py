"""
Memory Module - Remembers past conversations
This gives your agent a MEMORY so it remembers what you talked about before.
"""

import json
import os
from datetime import datetime
from typing import List, Dict


class ConversationMemory:
    """
    Stores conversation history in a JSON file.
    The agent can remember past conversations!
    """
    
    def __init__(self, memory_file: str = "conversation_memory.json"):
        self.memory_file = memory_file
        self.max_history = 50  # Keep last 50 messages
        self.conversations = self._load()
    
    def _load(self) -> List[Dict]:
        """Load conversation history from file"""
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []
        return []
    
    def _save(self):
        """Save conversation history to file"""
        try:
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump(self.conversations, f, indent=2, ensure_ascii=False)
        except UnicodeEncodeError:
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                json.dump(self.conversations, f, indent=2, ensure_ascii=True)
    
    def add_message(self, role: str, content: str, tool_used: str = None):
        """Add a message to memory"""
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "tool_used": tool_used
        }
        self.conversations.append(message)
        
        # Keep only last N messages
        if len(self.conversations) > self.max_history:
            self.conversations = self.conversations[-self.max_history:]
        
        self._save()
    
    def get_recent(self, n: int = 10) -> List[Dict]:
        """Get last N messages"""
        return self.conversations[-n:]
    
    def get_by_role(self, role: str) -> List[Dict]:
        """Get all messages from a specific role (user/assistant)"""
        return [m for m in self.conversations if m["role"] == role]
    
    def search(self, keyword: str) -> List[Dict]:
        """Search memory for a keyword"""
        return [m for m in self.conversations if keyword.lower() in m["content"].lower()]
    
    def get_context_string(self, n: int = 10) -> str:
        """Get recent conversation as a string for the AI to read"""
        recent = self.get_recent(n)
        if not recent:
            return "No conversation history yet."
        
        context = "Recent conversation history:\n"
        for msg in recent:
            role = "User" if msg["role"] == "user" else "Assistant"
            context += f"{role}: {msg['content'][:200]}\n"
        return context
    
    def clear(self):
        """Clear all memory"""
        self.conversations = []
        self._save()
    
    def get_stats(self) -> Dict:
        """Get memory statistics"""
        user_msgs = len(self.get_by_role("user"))
        assistant_msgs = len(self.get_by_role("assistant"))
        return {
            "total_messages": len(self.conversations),
            "user_messages": user_msgs,
            "assistant_messages": assistant_msgs,
            "memory_file": self.memory_file
        }


class FactMemory:
    """
    Stores important facts the agent learns about the user.
    Example: "User's name is Ravneet", "User prefers Python"
    """
    
    def __init__(self, facts_file: str = "fact_memory.json"):
        self.facts_file = facts_file
        self.facts = self._load()
    
    def _load(self) -> Dict:
        """Load facts from file"""
        if os.path.exists(self.facts_file):
            try:
                with open(self.facts_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}
    
    def _save(self):
        """Save facts to file"""
        try:
            with open(self.facts_file, 'w', encoding='utf-8') as f:
                json.dump(self.facts, f, indent=2, ensure_ascii=False)
        except UnicodeEncodeError:
            # Fallback: ensure ASCII only
            with open(self.facts_file, 'w', encoding='utf-8') as f:
                json.dump(self.facts, f, indent=2, ensure_ascii=True)
    
    def remember(self, key: str, value: str):
        """Remember a fact"""
        self.facts[key] = {
            "value": value,
            "timestamp": datetime.now().isoformat()
        }
        self._save()
    
    def recall(self, key: str) -> str:
        """Recall a fact"""
        if key in self.facts:
            return self.facts[key]["value"]
        return f"I don't remember anything about '{key}'"
    
    def get_all_facts(self) -> Dict:
        """Get all stored facts"""
        return {k: v["value"] for k, v in self.facts.items()}
    
    def get_context_string(self) -> str:
        """Get all facts as a string for the AI to read"""
        if not self.facts:
            return "No facts stored yet."
        
        context = "Things I remember about the user:\n"
        for key, data in self.facts.items():
            context += f"- {key}: {data['value']}\n"
        return context
    
    def forget(self, key: str):
        """Forget a specific fact"""
        if key in self.facts:
            del self.facts[key]
            self._save()
    
    def clear(self):
        """Clear all facts"""
        self.facts = {}
        self._save()
