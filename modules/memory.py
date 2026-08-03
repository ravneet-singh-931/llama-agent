"""
Memory Module - Remembers past conversations
This gives your agent a MEMORY so it remembers what you talked about before.

Storage model (v2 - per-user conversations):
{
  "conversations": {
    "<conversation_id>": {
      "id": "<conversation_id>",
      "user_id": "<user_id>",
      "title": "<first user message (truncated)>",
      "created_at": "...",
      "updated_at": "...",
      "messages": [
        {"role": "user|assistant", "content": "...", "timestamp": "...", "tool_used": null},
        ...
      ]
    }
  }
}

Old flat-list files (v1) are migrated to this format automatically on load,
and the default file location moved to memory/ so it can live on a Docker
volume and survive container re-creates / deployments.
"""

import json
import os
import uuid
from datetime import datetime
from typing import List, Dict, Optional


class ConversationMemory:
    """Stores per-user conversation history in a JSON file."""

    def __init__(self, memory_file: str = "memory/conversation_memory.json",
                 max_history: int = 50):
        self.memory_file = memory_file
        self.max_history = max_history  # messages kept per conversation
        self.data = self._load()

    # ---------------- persistence ----------------

    def _load(self) -> Dict:
        if os.path.exists(self.memory_file):
            try:
                with open(self.memory_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                return self._migrate(raw)
            except (json.JSONDecodeError, IOError):
                pass
        return {"conversations": {}}

    def _migrate(self, raw) -> Dict:
        # v2 format already
        if isinstance(raw, dict) and "conversations" in raw:
            return raw
        # v1: flat list of messages -> one "default" conversation
        if isinstance(raw, list):
            conv = {
                "id": "default",
                "user_id": "default",
                "title": "Previous conversation",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "messages": raw[-self.max_history:],
            }
            return {"conversations": {"default": conv}}
        return {"conversations": {}}

    def _save(self):
        os.makedirs(os.path.dirname(self.memory_file) or ".", exist_ok=True)
        try:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except UnicodeEncodeError:
            with open(self.memory_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=True)

    # ---------------- conversations ----------------

    def _ensure(self, user_id: str, conversation_id: str):
        if conversation_id not in self.data["conversations"]:
            now = datetime.now().isoformat()
            self.data["conversations"][conversation_id] = {
                "id": conversation_id,
                "user_id": user_id,
                "title": "New chat",
                "created_at": now,
                "updated_at": now,
                "messages": [],
            }

    def _resolve_conversation_id(self, user_id: str, conversation_id: str) -> str:
        """Return the conversation id this user should write to.

        A conversation id may only ever belong to ONE user. If the requested
        id already exists but belongs to a DIFFERENT user (e.g. two users both
        sending their first message without a conversation_id), we must NOT
        rebind or pollute it - instead create a brand-new conversation for the
        current user. This is what kept conversations from appearing after
        signup: a signed-up user's first message was silently appended to
        another user's "default" conversation and its owner was overwritten.
        """
        conv = self.data["conversations"].get(conversation_id)
        if conv is None:
            self._ensure(user_id, conversation_id)
            return conversation_id
        if conv["user_id"] == user_id:
            return conversation_id
        # Ownership conflict -> create a fresh conversation for this user.
        return self.create_conversation(user_id, title="New chat")["id"]

    def add_message(self, role: str, content: str, tool_used: str = None,
                    user_id: str = "guest", conversation_id: str = "default"):
        """Add a message to a conversation (auto-creates the conversation).

        Never overwrites the owner of an existing conversation: if the given
        conversation_id belongs to another user, a fresh conversation is
        created for this user instead.

        Returns the conversation_id the message was actually written to
        (useful when a caller mirrors the message into PostgreSQL).
        """
        conversation_id = self._resolve_conversation_id(user_id, conversation_id)
        conv = self.data["conversations"][conversation_id]
        conv["messages"].append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "tool_used": tool_used,
        })
        if len(conv["messages"]) > self.max_history:
            conv["messages"] = conv["messages"][-self.max_history:]
        if role == "user":
            title = content.strip().replace("\n", " ")
            conv["title"] = title[:60] if title else "New chat"
        conv["updated_at"] = datetime.now().isoformat()
        self._save()
        return conversation_id

    def create_conversation(self, user_id: str = "guest", title: str = "New chat") -> Dict:
        """Create a new empty conversation and return its summary.

        Conversation ids are real UUIDs so they map 1:1 to the PostgreSQL
        conversations table (which stores ids as UUID).
        """
        conversation_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        self.data["conversations"][conversation_id] = {
            "id": conversation_id,
            "user_id": user_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self._save()
        return {
            "id": conversation_id,
            "user_id": user_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "message_count": 0,
            "last_message": "",
        }

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        conv = self.data["conversations"].get(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return False
        del self.data["conversations"][conversation_id]
        self._save()
        return True

    def get_conversation(self, user_id: str, conversation_id: str) -> Optional[Dict]:
        conv = self.data["conversations"].get(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return None
        return {
            "id": conv["id"],
            "user_id": conv["user_id"],
            "title": conv["title"],
            "created_at": conv["created_at"],
            "updated_at": conv["updated_at"],
            "message_count": len(conv["messages"]),
            "last_message": conv["messages"][-1]["content"][:100] if conv["messages"] else "",
        }

    def get_conversations(self, user_id: str) -> List[Dict]:
        """All conversations for a user, newest first."""
        convs = [c for c in self.data["conversations"].values()
                 if c["user_id"] == user_id]
        convs.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return [
            {
                "id": c["id"],
                "user_id": c["user_id"],
                "title": c["title"],
                "created_at": c["created_at"],
                "updated_at": c["updated_at"],
                "message_count": len(c["messages"]),
                "last_message": c["messages"][-1]["content"][:100] if c["messages"] else "",
            }
            for c in convs
        ]

    def get_messages(self, user_id: str, conversation_id: str) -> List[Dict]:
        conv = self.data["conversations"].get(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return []
        return list(conv["messages"])

    # ---------------- backward-compatible accessors ----------------

    def _all_messages(self, user_id: Optional[str] = None,
                      conversation_id: Optional[str] = None) -> List[Dict]:
        messages = []
        for c in self.data["conversations"].values():
            if user_id is not None and c["user_id"] != user_id:
                continue
            if conversation_id is not None and c["id"] != conversation_id:
                continue
            messages.extend(c["messages"])
        messages.sort(key=lambda m: m.get("timestamp", ""))
        return messages

    def get_recent(self, n: int = 10, user_id: Optional[str] = None,
                   conversation_id: Optional[str] = None) -> List[Dict]:
        """Get last N messages (optionally filtered by user/conversation)."""
        return self._all_messages(user_id, conversation_id)[-n:]

    def get_by_role(self, role: str, user_id: Optional[str] = None) -> List[Dict]:
        return [m for m in self._all_messages(user_id) if m["role"] == role]

    def search(self, keyword: str, user_id: Optional[str] = None) -> List[Dict]:
        return [m for m in self._all_messages(user_id)
                if keyword.lower() in m["content"].lower()]

    def get_context_string(self, n: int = 10, user_id: Optional[str] = None,
                           conversation_id: Optional[str] = None) -> str:
        """Get recent conversation as a string for the AI to read."""
        recent = self.get_recent(n, user_id=user_id, conversation_id=conversation_id)
        if not recent:
            return "No conversation history yet."

        context = "Recent conversation history:\n"
        for msg in recent:
            role = "User" if msg["role"] == "user" else "Assistant"
            context += f"{role}: {msg['content'][:200]}\n"
        return context

    def clear(self, user_id: Optional[str] = None):
        """Clear all memory (or just one user's conversations)."""
        if user_id is None:
            self.data = {"conversations": {}}
        else:
            self.data["conversations"] = {
                cid: c for cid, c in self.data["conversations"].items()
                if c["user_id"] != user_id
            }
        self._save()

    def get_stats(self) -> Dict:
        """Get memory statistics (backward compatible)."""
        all_msgs = self._all_messages()
        user_msgs = len([m for m in all_msgs if m["role"] == "user"])
        assistant_msgs = len([m for m in all_msgs if m["role"] == "assistant"])
        return {
            "total_messages": len(all_msgs),
            "user_messages": user_msgs,
            "assistant_messages": assistant_msgs,
            "memory_file": self.memory_file,
            "conversations": len(self.data["conversations"]),
        }


class FactMemory:
    """
    Stores important facts the agent learns about the user.
    Example: "User's name is Ravneet", "User prefers Python"
    """

    def __init__(self, facts_file: str = "memory/fact_memory.json"):
        self.facts_file = facts_file
        self.facts = self._load()

    def _load(self) -> Dict:
        if os.path.exists(self.facts_file):
            try:
                with open(self.facts_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save(self):
        os.makedirs(os.path.dirname(self.facts_file) or ".", exist_ok=True)
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
