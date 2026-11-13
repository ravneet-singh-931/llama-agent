"""
WebSocket Module - Real-time Chat

Features:
- Live bidirectional communication
- Connection management (multiple users)
- Message broadcasting
- Typing indicators
- Connection health monitoring
- Automatic reconnection
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Set, Optional, Any, List
from dataclasses import dataclass, field
from enum import Enum

from fastapi import WebSocket, WebSocketDisconnect


# ============ MESSAGE TYPES ============


class MessageType(str, Enum):
    """WebSocket message types"""
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    CHAT = "chat"
    RESPONSE = "response"
    TYPING = "typing"
    ERROR = "error"
    HEARTBEAT = "heartbeat"
    HISTORY = "history"
    ONLINE_USERS = "online_users"
    AGENT_STATUS = "agent_status"


@dataclass
class WSMessage:
    """WebSocket message structure"""
    type: MessageType
    data: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_json(self) -> str:
        return json.dumps({
            "type": self.type.value,
            "data": self.data,
            "timestamp": self.timestamp,
            "message_id": self.message_id,
        })

    @classmethod
    def from_json(cls, json_str: str) -> "WSMessage":
        data = json.loads(json_str)
        return cls(
            type=MessageType(data.get("type", "chat")),
            data=data.get("data", {}),
        )


# ============ CONNECTION MANAGER ============


class ConnectionManager:
    """
    Manages WebSocket connections for multiple users
    
    Features:
    - Track connected users
    - Send messages to specific users
    - Broadcast to all users
    - Handle user disconnections
    """

    def __init__(self):
        # user_id -> set of WebSocket connections
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        # user_id -> user info
        self.user_info: Dict[str, Dict[str, Any]] = {}
        # connection_id -> user_id
        self.connection_users: Dict[str, str] = {}

    async def connect(
        self,
        websocket: WebSocket,
        user_id: str,
        username: str,
    ) -> str:
        """
        Accept and register a new WebSocket connection
        
        Returns:
            Connection ID
        """
        await websocket.accept()
        
        connection_id = str(uuid.uuid4())
        
        # Track connection
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        self.active_connections[user_id].add(websocket)
        
        # Store user info
        self.user_info[user_id] = {
            "username": username,
            "connected_at": datetime.now(timezone.utc).isoformat(),
            "connection_count": len(self.active_connections[user_id]),
        }
        
        # Map connection to user
        self.connection_users[connection_id] = user_id
        
        # Send welcome message
        welcome = WSMessage(
            type=MessageType.CONNECT,
            data={
                "connection_id": connection_id,
                "message": f"Welcome {username}! You are connected.",
            }
        )
        await websocket.send_text(welcome.to_json())

        # Privacy: presence (who is online) is NOT broadcast to other users.
        
        print(f"✅ WebSocket connected: {username} (user_id={user_id})")
        return connection_id

    def disconnect(self, websocket: WebSocket, user_id: str):
        """Remove a connection"""
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            
            # Remove user if no more connections
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
                if user_id in self.user_info:
                    del self.user_info[user_id]
        
        # Remove from connection mapping (only when the user has no
        # connections left - other tabs of the same user stay mapped)
        if user_id not in self.active_connections:
            for conn_id, uid in list(self.connection_users.items()):
                if uid == user_id:
                    del self.connection_users[conn_id]
        
        print(f"❌ WebSocket disconnected: user_id={user_id}")

    async def send_personal(self, user_id: str, message: WSMessage):
        """Send message to a specific user (all their connections)"""
        if user_id in self.active_connections:
            for ws in self.active_connections[user_id]:
                try:
                    await ws.send_text(message.to_json())
                except Exception:
                    # Connection might be dead
                    pass

    async def broadcast(
        self,
        message: WSMessage,
        exclude_user: Optional[str] = None,
    ):
        """Send message to all connected users"""
        for user_id, connections in self.active_connections.items():
            if user_id != exclude_user:
                for ws in connections:
                    try:
                        await ws.send_text(message.to_json())
                    except Exception:
                        pass

    async def send_typing(self, user_id: str, is_typing: bool):
        """Send typing indicator to the user's OWN other connections (tabs).

        Chats are private 1:1 conversations with the agent, so typing state
        is never broadcast to other users.
        """
        message = WSMessage(
            type=MessageType.TYPING,
            data={
                "user_id": user_id,
                "is_typing": is_typing,
            }
        )
        await self.send_personal(user_id, message)

    def get_online_users(self) -> List[Dict[str, Any]]:
        """Get list of online users"""
        users = []
        for user_id, info in self.user_info.items():
            users.append({
                "user_id": user_id,
                "username": info["username"],
                "connection_count": info["connection_count"],
            })
        return users

    def is_online(self, user_id: str) -> bool:
        """Check if user is online"""
        return user_id in self.active_connections

    @property
    def connection_count(self) -> int:
        """Total number of connections"""
        return sum(len(conns) for conns in self.active_connections.values())

    @property
    def user_count(self) -> int:
        """Total number of connected users"""
        return len(self.active_connections)


# ============ WEBSOCKET HANDLER ============


class WebSocketHandler:
    """
    Main WebSocket handler for chat functionality
    
    Handles:
    - Message routing
    - Agent integration
    - Chat history
    - Error handling
    """

    def __init__(self):
        self.manager = ConnectionManager()
        # Chat history per user (last 50 messages)
        self.chat_history: Dict[str, List[WSMessage]] = {}

    async def handle_connection(
        self,
        websocket: WebSocket,
        user_id: str,
        username: str,
    ):
        """
        Handle a WebSocket connection lifecycle
        
        This is the main entry point for WebSocket connections.
        """
        connection_id = await self.manager.connect(websocket, user_id, username)
        
        try:
            while True:
                # Receive message
                data = await websocket.receive_text()
                message = WSMessage.from_json(data)
                
                # Handle message by type
                await self._process_message(websocket, user_id, message)
                
        except WebSocketDisconnect:
            self.manager.disconnect(websocket, user_id)
            print(f"👋 User {username} disconnected")
            
        except Exception as e:
            print(f"❌ WebSocket error for {username}: {e}")
            error_msg = WSMessage(
                type=MessageType.ERROR,
                data={"error": "Internal error"}
            )
            try:
                await websocket.send_text(error_msg.to_json())
            except Exception:
                pass
            self.manager.disconnect(websocket, user_id)

    async def _process_message(
        self,
        websocket: WebSocket,
        user_id: str,
        message: WSMessage,
    ):
        """Route and process incoming messages"""
        
        if message.type == MessageType.CHAT:
            # Regular chat message
            await self._handle_chat(websocket, user_id, message)
            
        elif message.type == MessageType.TYPING:
            # Typing indicator
            await self.manager.send_typing(user_id, message.data.get("is_typing", False))
            
        elif message.type == MessageType.HEARTBEAT:
            # Keepalive response
            pong = WSMessage(
                type=MessageType.HEARTBEAT,
                data={"status": "pong"}
            )
            await websocket.send_text(pong.to_json())
            
        elif message.type == MessageType.HISTORY:
            # Request chat history
            history = self.chat_history.get(user_id, [])
            history_msg = WSMessage(
                type=MessageType.HISTORY,
                data={
                    "messages": [
                        {"type": m.type.value, "data": m.data, "timestamp": m.timestamp}
                        for m in history[-50:]  # Last 50 messages
                    ]
                }
            )
            await websocket.send_text(history_msg.to_json())

    async def _handle_chat(
        self,
        websocket: WebSocket,
        user_id: str,
        message: WSMessage,
    ):
        """
        Handle chat message:
        1. Store user message
        2. Send to agent
        3. Stream response back
        4. Store assistant response
        """
        user_message = message.data.get("content", "")
        conversation_id = message.data.get("conversation_id")
        
        if not user_message.strip():
            return
        
        # Store user message in history (bounded per user)
        history = self.chat_history.setdefault(user_id, [])
        history.append(message)
        if len(history) > 200:
            del history[:-200]
        
        # Send typing indicator
        typing_msg = WSMessage(
            type=MessageType.AGENT_STATUS,
            data={"status": "thinking"}
        )
        await websocket.send_text(typing_msg.to_json())
        
        try:
            # Import and use the agent
            from llama_agent1 import run_agent
            
            # Run the agent AS this user: their memory, facts and documents
            # (never the shared "guest" bucket).
            response = await run_agent(
                user_message,
                user_id=user_id,
                conversation_id=conversation_id or f"ws-{user_id}",
            )
            
            # Create response message
            response_msg = WSMessage(
                type=MessageType.RESPONSE,
                data={
                    "content": response,
                    "conversation_id": conversation_id,
                    "agent": "llama-agent",
                }
            )
            
            # Store assistant response
            self.chat_history[user_id].append(response_msg)
            
            # Send response
            await websocket.send_text(response_msg.to_json())
            
        except Exception as e:
            error_msg = WSMessage(
                type=MessageType.ERROR,
                data={
                    "error": f"Agent error: {str(e)}",
                    "conversation_id": conversation_id,
                }
            )
            await websocket.send_text(error_msg.to_json())


# ============ SINGLETON INSTANCE ============

# Global WebSocket handler instance
_ws_handler: Optional[WebSocketHandler] = None


def get_ws_handler() -> WebSocketHandler:
    """Get or create the WebSocket handler singleton"""
    global _ws_handler
    if _ws_handler is None:
        _ws_handler = WebSocketHandler()
    return _ws_handler

