"""
Services Layer - Dependency Injection

This module provides a clean interface for accessing services.
It uses dependency injection pattern for testability.
"""

from typing import Optional
from modules.config import get_config, AppConfig
from modules.database import get_db_manager, DatabaseManager
from modules.auth import jwt_manager, user_manager, JWTManager, UserManager
from modules.websocket import get_ws_handler, WebSocketHandler


class Services:
    """
    Central service registry
    
    Usage:
        services = Services()
        db = services.database
        auth = services.auth
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self._database: Optional[DatabaseManager] = None
        self._websocket: Optional[WebSocketHandler] = None

    @property
    def database(self) -> DatabaseManager:
        """Get database manager"""
        if self._database is None:
            self._database = DatabaseManager(self.config.database.url)
        return self._database

    @property
    def auth(self) -> UserManager:
        """Get auth manager"""
        return user_manager

    @property
    def jwt(self) -> JWTManager:
        """Get JWT manager"""
        return jwt_manager

    @property
    def websocket(self) -> WebSocketHandler:
        """Get WebSocket handler"""
        if self._websocket is None:
            self._websocket = get_ws_handler()
        return self._websocket

    async def initialize(self):
        """Initialize all services"""
        # Initialize database
        await self.database.init_db()
        print("✅ Database initialized")

    async def shutdown(self):
        """Cleanup all services"""
        if self._database:
            await self._database.close()
        print("👋 Services shut down")


# ============ DEPENDENCY INJECTION ============

# FastAPI dependency provider
_services: Optional[Services] = None


def get_services() -> Services:
    """Get or create services singleton"""
    global _services
    if _services is None:
        _services = Services()
    return _services


def reset_services():
    """Reset services (for testing)"""
    global _services
    _services = None
