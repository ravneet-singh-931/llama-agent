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
    """Stores per-user conversation history.

    Backends:
    - Redis (when REDIS_URL is configured and reachable): every conversation
      is one JSON key with a TTL plus a per-user index set, so ALL API
      replicas see the same history. Guests (who are not stored in
      PostgreSQL) previously lost their chats whenever the load balancer sent
      them to the other pod, or a deploy replaced the pod.
    - JSON file (local dev, tests, Redis down): the original pod-local file.

    Pass ``use_redis=False`` to force the JSON file (tests do this implicitly
    by not setting REDIS_URL). ``CONV_MEMORY_BACKEND=json`` does the same.
    """

    KEY_PREFIX = "llama:conv:"
    INDEX_PREFIX = "llama:convs:"

    def __init__(self, memory_file: str = "memory/conversation_memory.json",
                 max_history: int = 50, use_redis: Optional[bool] = None):
        self.memory_file = memory_file
        self.max_history = max_history  # messages kept per conversation
        self.ttl = int(os.getenv("CONV_MEMORY_TTL", str(7 * 24 * 3600)))
        if use_redis is None:
            use_redis = os.getenv("CONV_MEMORY_BACKEND", "auto").lower() != "json"
        self.redis = _connect_redis("CONVERSATIONS") if use_redis else None
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

    # ---------------- storage primitives (Redis or JSON) ----------------

    def _redis_failed(self, e: Exception):
        print(f"[CONVERSATIONS] Redis error, falling back to local JSON: {e}")
        self.redis = None

    def _get(self, conversation_id: str) -> Optional[Dict]:
        if self.redis is not None:
            try:
                raw = self.redis.get(self.KEY_PREFIX + conversation_id)
                return json.loads(raw) if raw else None
            except Exception as e:
                self._redis_failed(e)
        return self.data["conversations"].get(conversation_id)

    def _put(self, conv: Dict):
        if self.redis is not None:
            try:
                idx = self.INDEX_PREFIX + conv["user_id"]
                pipe = self.redis.pipeline()
                pipe.set(self.KEY_PREFIX + conv["id"],
                         json.dumps(conv, ensure_ascii=False), ex=self.ttl)
                pipe.sadd(idx, conv["id"])
                pipe.expire(idx, self.ttl)
                pipe.execute()
                return
            except Exception as e:
                self._redis_failed(e)
        self.data["conversations"][conv["id"]] = conv
        self._save()

    def _delete(self, conv: Dict):
        if self.redis is not None:
            try:
                pipe = self.redis.pipeline()
                pipe.delete(self.KEY_PREFIX + conv["id"])
                pipe.srem(self.INDEX_PREFIX + conv["user_id"], conv["id"])
                pipe.execute()
                return
            except Exception as e:
                self._redis_failed(e)
        self.data["conversations"].pop(conv["id"], None)
        self._save()

    def _load_many(self, ids: List[str]) -> List[Dict]:
        if not ids:
            return []
        raws = self.redis.mget([self.KEY_PREFIX + i for i in ids])
        return [json.loads(r) for r in raws if r]

    def _convs(self, user_id: Optional[str] = None) -> List[Dict]:
        """All conversations (optionally only one user's)."""
        if self.redis is not None:
            try:
                if user_id is not None:
                    idx = self.INDEX_PREFIX + user_id
                    ids = sorted(self.redis.smembers(idx))
                    convs = self._load_many(ids)
                    alive = {c["id"] for c in convs}
                    expired = [i for i in ids if i not in alive]
                    if expired:  # prune ids whose conversation key expired
                        self.redis.srem(idx, *expired)
                    return [c for c in convs if c.get("user_id") == user_id]
                keys = list(self.redis.scan_iter(match=self.KEY_PREFIX + "*", count=500))
                return self._load_many([k[len(self.KEY_PREFIX):] for k in keys])
            except Exception as e:
                self._redis_failed(e)
        return [c for c in self.data["conversations"].values()
                if user_id is None or c["user_id"] == user_id]

    @staticmethod
    def _summary(c: Dict) -> Dict:
        return {
            "id": c["id"],
            "user_id": c["user_id"],
            "title": c["title"],
            "created_at": c["created_at"],
            "updated_at": c["updated_at"],
            "message_count": len(c["messages"]),
            "last_message": c["messages"][-1]["content"][:100] if c["messages"] else "",
        }

    # ---------------- conversations ----------------

    @staticmethod
    def _new_conv(user_id: str, conversation_id: str, title: str = "New chat") -> Dict:
        now = datetime.now().isoformat()
        return {
            "id": conversation_id,
            "user_id": user_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }

    def _ensure(self, user_id: str, conversation_id: str):
        if self._get(conversation_id) is None:
            self._put(self._new_conv(user_id, conversation_id))

    def _resolve_conversation(self, user_id: str, conversation_id: str) -> Dict:
        """Return the conversation this user should write to.

        A conversation id may only ever belong to ONE user. If the requested
        id already exists but belongs to a DIFFERENT user (e.g. two users both
        sending their first message without a conversation_id), we must NOT
        rebind or pollute it - instead create a brand-new conversation for the
        current user. This is what kept conversations from appearing after
        signup: a signed-up user's first message was silently appended to
        another user's "default" conversation and its owner was overwritten.
        """
        conv = self._get(conversation_id)
        if conv is None:
            return self._new_conv(user_id, conversation_id)
        if conv["user_id"] == user_id:
            return conv
        # Ownership conflict -> create a fresh conversation for this user.
        return self._new_conv(user_id, str(uuid.uuid4()))

    def _resolve_conversation_id(self, user_id: str, conversation_id: str) -> str:
        conv = self._resolve_conversation(user_id, conversation_id)
        if self._get(conv["id"]) is None:
            self._put(conv)
        return conv["id"]

    def add_message(self, role: str, content: str, tool_used: str = None,
                    user_id: str = "guest", conversation_id: str = "default"):
        """Add a message to a conversation (auto-creates the conversation).

        Never overwrites the owner of an existing conversation: if the given
        conversation_id belongs to another user, a fresh conversation is
        created for this user instead.

        Returns the conversation_id the message was actually written to
        (useful when a caller mirrors the message into PostgreSQL).
        """
        conv = self._resolve_conversation(user_id, conversation_id)
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
        self._put(conv)
        return conv["id"]

    def create_conversation(self, user_id: str = "guest", title: str = "New chat") -> Dict:
        """Create a new empty conversation and return its summary.

        Conversation ids are real UUIDs so they map 1:1 to the PostgreSQL
        conversations table (which stores ids as UUID).
        """
        conv = self._new_conv(user_id, str(uuid.uuid4()), title)
        self._put(conv)
        return self._summary(conv)

    def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        conv = self._get(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return False
        self._delete(conv)
        return True

    def get_conversation(self, user_id: str, conversation_id: str) -> Optional[Dict]:
        conv = self._get(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return None
        return self._summary(conv)

    def get_conversations(self, user_id: str) -> List[Dict]:
        """All conversations for a user, newest first."""
        convs = self._convs(user_id)
        convs.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return [self._summary(c) for c in convs]

    def get_messages(self, user_id: str, conversation_id: str) -> List[Dict]:
        conv = self._get(conversation_id)
        if not conv or conv["user_id"] != user_id:
            return []
        return list(conv["messages"])

    # ---------------- backward-compatible accessors ----------------

    def _all_messages(self, user_id: Optional[str] = None,
                      conversation_id: Optional[str] = None) -> List[Dict]:
        if conversation_id is not None:
            conv = self._get(conversation_id)
            convs = [conv] if conv and (user_id is None or conv["user_id"] == user_id) else []
        else:
            convs = self._convs(user_id)
        messages = []
        for c in convs:
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
        if self.redis is not None:
            for c in self._convs(user_id):
                self._delete(c)
            if self.redis is not None:
                return
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
        convs = self._convs()
        all_msgs = [m for c in convs for m in c["messages"]]
        user_msgs = len([m for m in all_msgs if m["role"] == "user"])
        assistant_msgs = len([m for m in all_msgs if m["role"] == "assistant"])
        return {
            "total_messages": len(all_msgs),
            "user_messages": user_msgs,
            "assistant_messages": assistant_msgs,
            "memory_file": self.memory_file,
            "backend": "redis" if self.redis is not None else "json",
            "conversations": len(convs),
        }


def _connect_redis(label: str):
    """Connect to Redis if configured; return None (use JSON) otherwise."""
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return None
    try:
        import redis
        client = redis.from_url(
            redis_url, decode_responses=True, socket_connect_timeout=2
        )
        client.ping()
        return client
    except Exception as e:
        print(f"[{label}] Redis unavailable, using local JSON file: {e}")
        return None


class FactMemory:
    """
    Stores important facts the agent learns about the user.
    Example: "User's name is Ravneet", "User prefers Python"

    Backend (multi-replica safe):
    - When REDIS_URL is configured AND reachable, facts live in a shared
      Redis hash so every API replica sees the same facts. This fixes the
      bug where remember_fact on replica A was invisible to list_facts on
      replica B (each pod previously wrote to its own pod-local JSON file).
    - Otherwise (local dev / CI tests / Redis down) it falls back to the
      original pod-local JSON file, keeping behavior and the test-suite
      unchanged.
    """

    REDIS_KEY = "llama:facts"

    def __init__(self, facts_file: str = "memory/fact_memory.json"):
        self.facts_file = facts_file
        self.facts = self._load()
        self.redis = self._init_redis()

    def _init_redis(self):
        """Connect to Redis if configured; return None (use JSON) otherwise."""
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return None
        try:
            import redis
            client = redis.from_url(
                redis_url, decode_responses=True, socket_connect_timeout=2
            )
            client.ping()
            return client
        except Exception as e:
            print(f"[FACTS] Redis unavailable, using local JSON file: {e}")
            return None

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

    def _read_all(self) -> Dict:
        """Return the canonical fact dict (Redis when available, else local)."""
        if self.redis is not None:
            try:
                raw = self.redis.hgetall(self.REDIS_KEY)
                result = {}
                for k, v in raw.items():
                    try:
                        result[k] = json.loads(v)
                    except (json.JSONDecodeError, TypeError):
                        # Tolerate plain-string values written by older code.
                        result[k] = {"value": v, "timestamp": ""}
                self.facts = result  # keep local mirror in sync
                return result
            except Exception as e:
                print(f"[FACTS] Redis read failed, using local mirror: {e}")
                return self.facts
        return self.facts

    @staticmethod
    def _norm_user(uid):
        """Normalize anonymous user buckets ('guest'/'default'/None) to 'default'."""
        if not uid or uid in ("guest", "default"):
            return "default"
        return uid

    def _fkey(self, key: str, user_id: str) -> str:
        """Per-user namespaced storage key so tenants never collide."""
        return f"{self._norm_user(user_id)}::{key}"

    def remember(self, key: str, value: str, user_id: str = "default"):
        """Remember a fact under a per-user namespaced key (multi-tenant isolation)."""
        fk = self._fkey(key, user_id)
        uid = self._norm_user(user_id)
        entry = {"value": value, "user_id": uid, "timestamp": datetime.now().isoformat()}
        self.facts[fk] = entry
        self._save()
        if self.redis is not None:
            try:
                self.redis.hset(self.REDIS_KEY, fk, json.dumps(entry))
            except Exception as e:
                print(f"[FACTS] Redis write failed (kept local): {e}")

    def recall(self, key: str, user_id: str = "default") -> str:
        """Recall a fact for a user (defaults to the 'default' tenant)."""
        fk = self._fkey(key, user_id)
        if self.redis is not None:
            try:
                raw = self.redis.hget(self.REDIS_KEY, fk)
                if raw:
                    try:
                        return json.loads(raw)["value"]
                    except (json.JSONDecodeError, TypeError):
                        return raw
            except Exception:
                pass
        if fk in self.facts:
            data = self.facts[fk]
            return data["value"] if isinstance(data, dict) else data
        # Backward-compat: legacy unprefixed single-tenant keys.
        if key in self.facts:
            data = self.facts[key]
            return data["value"] if isinstance(data, dict) else data
        return f"I don't remember anything about '{key}'"

    def get_all_facts(self, user_id: str = None) -> Dict:
        """Get all stored facts (optionally scoped to one user)."""
        all_facts = self._read_all()
        result: Dict = {}
        for k, v in all_facts.items():
            if "::" in k:
                uid, real_key = k.split("::", 1)
                if user_id is not None and self._norm_user(user_id) != uid:
                    continue
            else:
                real_key = k
                # Legacy unprefixed facts belong to the 'default' tenant.
                if user_id is not None and self._norm_user(user_id) != "default":
                    continue
            val = v["value"] if isinstance(v, dict) else v
            result[real_key] = val
        return result

    def get_context_string(self, user_id: str = None) -> str:
        """Get all facts as a string for the AI to read (optionally scoped)."""
        all_facts = self.get_all_facts(user_id=user_id)
        if not all_facts:
            return "No facts stored yet."

        context = "Things I remember about the user:\n"
        for key, value in all_facts.items():
            context += f"- {key}: {value}\n"
        return context

    def forget(self, key: str, user_id: str = "default"):
        """Forget a specific fact for a user (defaults to the 'default' tenant)."""
        fk = self._fkey(key, user_id)
        if fk in self.facts:
            del self.facts[fk]
            self._save()
        if self.redis is not None:
            try:
                self.redis.hdel(self.REDIS_KEY, fk)
            except Exception as e:
                print(f"[FACTS] Redis delete failed: {e}")

    def clear(self, user_id: str = None):
        """Clear all facts, or only one user's facts (per-user namespaced keys).

        Scoping by user_id prevents one tenant from wiping every other
        tenant's facts when facts live in a shared Redis hash.
        """
        if user_id is None:
            self.facts = {}
            self._save()
            if self.redis is not None:
                try:
                    self.redis.delete(self.REDIS_KEY)
                except Exception as e:
                    print(f"[FACTS] Redis clear failed: {e}")
            return
        uid = self._norm_user(user_id)
        prefix = f"{uid}::"
        keys_to_delete = [k for k in self.facts if k.startswith(prefix)]
        for k in keys_to_delete:
            del self.facts[k]
        self._save()
        if self.redis is not None:
            try:
                if keys_to_delete:
                    self.redis.hdel(self.REDIS_KEY, *keys_to_delete)
            except Exception as e:
                print(f"[FACTS] Redis clear failed: {e}")
