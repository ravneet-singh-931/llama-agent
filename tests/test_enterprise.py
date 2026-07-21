"""
Tests for NEW Enterprise Features
Tests: Database, Auth, WebSocket, Config
"""

import pytest
import os
import sys
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============ CONFIG TESTS ============

class TestConfig:
    """Test configuration management"""

    def test_config_loads_from_env(self):
        """Config should load from environment"""
        from modules.config import get_config, AppConfig
        
        config = AppConfig.from_env()
        assert config is not None
        assert config.environment.value in ["development", "staging", "production", "testing"]

    def test_database_config_defaults(self):
        """Database config should have defaults"""
        from modules.config import DatabaseConfig
        
        config = DatabaseConfig()
        assert "postgresql" in config.url
        assert config.pool_size > 0

    def test_jwt_config_defaults(self):
        """JWT config should have defaults"""
        from modules.config import JWTConfig
        
        config = JWTConfig()
        assert config.algorithm == "HS256"
        assert config.access_expire_minutes > 0

    def test_feature_flags(self):
        """Feature flags should load from env"""
        from modules.config import FeatureFlags
        
        flags = FeatureFlags.from_env()
        assert isinstance(flags.enable_websockets, bool)
        assert isinstance(flags.enable_auth, bool)


# ============ AUTH TESTS ============

class TestAuth:
    """Test authentication system"""

    def test_password_hashing(self):
        """Password should be hashed properly"""
        from modules.auth import password_manager
        
        password = "TestPassword123!"
        hashed = password_manager.hash(password)
        
        # Hash should be different from original
        assert hashed != password
        # Should verify correctly
        assert password_manager.verify(password, hashed) == True
        # Wrong password should fail
        assert password_manager.verify("WrongPassword", hashed) == False

    def test_password_strength(self):
        """Password strength checker should work"""
        from modules.auth import password_manager
        
        # Strong password
        strong = password_manager.check_strength("MyStr0ng!Pass")
        assert strong["valid"] == True
        assert strong["score"] >= 4
        
        # Weak password
        weak = password_manager.check_strength("123")
        assert weak["valid"] == False

    def test_jwt_token_creation(self):
        """JWT tokens should be created properly"""
        from modules.auth import jwt_manager
        
        data = {"sub": "user123", "role": "user"}
        token = jwt_manager.create_access_token(data)
        
        assert token is not None
        assert isinstance(token, str)
        # Token should have 3 parts (header.payload.signature)
        assert len(token.split(".")) == 3

    def test_jwt_token_verification(self):
        """JWT tokens should be verified properly"""
        from modules.auth import jwt_manager
        
        data = {"sub": "user123"}
        token = jwt_manager.create_access_token(data)
        
        # Verify valid token
        payload = jwt_manager.verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"
        
        # Verify invalid token
        invalid_payload = jwt_manager.verify_token("invalid.token.here")
        assert invalid_payload is None

    def test_api_key_generation(self):
        """API keys should be generated properly"""
        from modules.database import generate_api_key
        
        key1 = generate_api_key()
        key2 = generate_api_key()
        
        # Keys should be unique
        assert key1 != key2
        # Keys should start with sk-
        assert key1.startswith("sk-")
        assert key2.startswith("sk-")


# ============ DATABASE TESTS ============

class TestDatabase:
    """Test database models (without real DB)"""

    def test_user_model_creation(self):
        """User model should be creatable"""
        from modules.database import User, hash_password
        
        user = User(
            username="testuser",
            email="test@example.com",
            hashed_password=hash_password("TestPass123!"),
            full_name="Test User",
            is_active=True,
            is_admin=False,
        )
        
        assert user.username == "testuser"
        assert user.email == "test@example.com"
        assert user.is_active == True
        assert user.is_admin == False

    def test_user_password_verification(self):
        """User password verification should work"""
        from modules.database import User, hash_password
        
        user = User(
            username="testuser",
            email="test@example.com",
            hashed_password=hash_password("MyPass123!"),
        )
        
        assert user.verify_password("MyPass123!") == True
        assert user.verify_password("WrongPass") == False


# ============ WEBSOCKET TESTS ============

class TestWebSocket:
    """Test WebSocket components"""

    def test_message_creation(self):
        """WSMessage should be created properly"""
        from modules.websocket import WSMessage, MessageType
        
        msg = WSMessage(
            type=MessageType.CHAT,
            data={"content": "Hello!"},
        )
        
        assert msg.type == MessageType.CHAT
        assert msg.data["content"] == "Hello!"
        assert msg.message_id is not None
        assert msg.timestamp is not None

    def test_message_serialization(self):
        """Messages should serialize/deserialize properly"""
        from modules.websocket import WSMessage, MessageType
        
        original = WSMessage(
            type=MessageType.RESPONSE,
            data={"content": "Response text"},
        )
        
        # Serialize to JSON
        json_str = original.to_json()
        assert json_str is not None
        
        # Deserialize from JSON
        restored = WSMessage.from_json(json_str)
        assert restored.type == MessageType.RESPONSE
        assert restored.data["content"] == "Response text"

    def test_connection_manager_init(self):
        """ConnectionManager should initialize properly"""
        from modules.websocket import ConnectionManager
        
        manager = ConnectionManager()
        assert manager.active_connections == {}
        assert manager.connection_count == 0
        assert manager.user_count == 0


# ============ INTEGRATION TESTS ============

class TestIntegration:
    """Integration tests for new features"""

    def test_config_singleton(self):
        """Config should be a singleton"""
        from modules.config import get_config
        
        config1 = get_config()
        config2 = get_config()
        
        assert config1 is config2

    def test_services_singleton(self):
        """Services should be a singleton"""
        from modules.services import get_services
        
        services1 = get_services()
        services2 = get_services()
        
        assert services1 is services2


# ============ ENVIRONMENT TESTS ============

class TestEnvironment:
    """Test environment-specific behavior"""

    def test_development_mode(self):
        """Should detect development mode"""
        from modules.config import AppConfig, Environment
        
        # Default should be development
        config = AppConfig.from_env()
        assert config.environment == Environment.DEVELOPMENT
        assert config.is_development() == True
        assert config.is_production() == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
