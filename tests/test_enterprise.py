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

    def test_dead_service_locator_stays_deleted(self):
        """`modules/services.py` was a service locator nothing imported.

        84 lines, zero importers, zero references outside this test — which
        only existed to import it. Deleting the test alone would have deleted
        the reason it looked used, so the module is gone too and this asserts
        it does not come back as a second path to the same singletons.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        assert not (root / "modules/services.py").exists()


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


# ============ ENTERPRISE MODULES TESTS ============

class TestEnterpriseModules:
    """Smoke tests for the enterprise security/monitoring/operations modules
    that are now wired into api.py (security_hardening, advanced_monitoring,
    cicd_improvements, database_improvements)."""

    # ---- Security hardening ----

    def test_sanitize_input_removes_scripts(self):
        from modules.security_hardening import sanitize_input

        clean = sanitize_input('<script>alert(1)</script> hello')
        assert '<script' not in clean
        assert 'hello' in clean

    def test_sanitize_input_removes_sql_patterns(self):
        from modules.security_hardening import sanitize_input

        clean = sanitize_input("DROP TABLE users; select * from x")
        assert 'DROP' not in clean.upper()

    def test_sanitize_filename_blocks_traversal(self):
        from modules.security_hardening import sanitize_filename

        assert '/' not in sanitize_filename('../../etc/passwd')
        assert '\\' not in sanitize_filename('..\\..\\windows\\system32')
        assert sanitize_filename('report.pdf') == 'report.pdf'

    def test_brute_force_lockout(self):
        from modules.security_hardening import BruteForceProtection

        protection = BruteForceProtection(max_attempts=3)
        for _ in range(3):
            protection.record_failure("attacker-ip")
        locked, _ = protection.is_locked("attacker-ip")
        assert locked is True

        # Success resets the counter
        protection.record_success("attacker-ip")
        locked, _ = protection.is_locked("attacker-ip")
        assert locked is False

    def test_security_headers_present(self):
        from modules.security_hardening import get_security_headers

        headers = get_security_headers()
        assert "Content-Security-Policy" in headers
        assert "X-Frame-Options" in headers
        assert "Strict-Transport-Security" in headers

    def test_audit_logger_writes_events(self, tmp_path):
        from modules.security_hardening import AuditLogger

        logger = AuditLogger(log_file=str(tmp_path / "audit.log"))
        logger.log_login_attempt("alice", "127.0.0.1", success=False, reason="bad password")
        logger.log_file_upload("evil.pdf", "user-1", success=False, reason="blocked extension")

        assert len(logger.events) == 2
        assert logger.events[0]["event_type"] == "LOGIN_FAILURE"
        assert logger.events[1]["event_type"] == "FILE_UPLOAD_FAILURE"
        assert (tmp_path / "audit.log").exists()

    # ---- Advanced monitoring ----

    def test_business_metrics_recording(self):
        from modules.advanced_monitoring import BusinessMetrics

        metrics = BusinessMetrics()
        metrics.record_question(user_id="u1", success=True, response_time=0.5, query_type="research")
        metrics.record_question(user_id="u1", success=False, response_time=2.0, query_type="research")
        metrics.record_document_upload("u1")

        summary = metrics.get_metrics()
        assert summary["total_questions"] == 2
        assert summary["success_rate"] == 50.0
        assert summary["documents_uploaded"] == 1
        assert summary["total_users"] == 1

    def test_sla_monitor_status(self):
        from modules.advanced_monitoring import SLAMonitor

        sla = SLAMonitor()
        sla.record_response_time(0.3)
        sla.record_response_time(0.7)
        status = sla.get_sla_status()
        assert status["overall_status"] in ("COMPLIANT", "NON-COMPLIANT")
        assert "uptime" in status["compliance"]

    def test_alert_manager_triggers(self):
        from modules.advanced_monitoring import AlertManager

        manager = AlertManager()
        triggered = manager.check_alerts({"error_rate": 99, "avg_response_time": 0.1})
        assert any(a["type"] == "high_error_rate" for a in triggered)

    def test_default_models_have_explicit_prices(self, monkeypatch):
        """Both tier defaults are priced explicitly (no silent default-price guess).

        This used to read ``CostTracker.DEFAULT_PRICING``. That class is gone
        -- its table moved to modules.prometheus_metrics.PRICING, which is now
        the single source for both the Grafana counter and the dashboard. The
        intent is unchanged: a tier default with no explicit price would be
        silently costed at the fallback rate.
        """
        monkeypatch.delenv("GROQ_PRICING", raising=False)
        from modules.prometheus_metrics import PRICING, _price
        from modules.model_tiers import DEFAULT_FAST_MODEL, DEFAULT_STRONG_MODEL

        assert DEFAULT_STRONG_MODEL in PRICING
        assert DEFAULT_FAST_MODEL in PRICING
        # qwen/qwen3.8-27b on Groq: $0.80 in / $4.00 out per 1M tokens
        assert abs(_price(DEFAULT_STRONG_MODEL, 1_000_000, 1_000_000) - 4.80) < 1e-9

    def test_monitoring_dashboard_shape(self):
        from modules.advanced_monitoring import get_monitoring_dashboard

        dashboard = get_monitoring_dashboard()
        assert "business" in dashboard
        assert "costs" in dashboard
        assert "sla" in dashboard
        assert "alerts" in dashboard

    # ---- Feature flags (cicd_improvements) ----

    def test_feature_flags_enabled(self):
        from modules.cicd_improvements import feature_flags

        # Unknown flags are always disabled
        assert feature_flags.is_enabled("does_not_exist") is False
        # Known, enabled flags evaluate as booleans
        assert isinstance(feature_flags.is_enabled("enable_mcp_agents"), bool)

    def test_every_shipped_flag_is_actually_read(self):
        """No flag may be configured that no code ever asks about.

        `config/feature_flags.json` used to carry eight flags. Seven of them —
        enable_advanced_rag, enable_phoenix_tracing, enable_caching,
        enable_rate_limiting, enable_websockets, enable_file_upload,
        enable_beta_features — had zero `is_enabled(...)` call sites anywhere in
        the codebase. Setting them to false changed nothing, while reading them
        as if they were live controls. They were deleted; this test stops the
        same thing from quietly coming back.
        """
        import json
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent.parent
        shipped = set(json.loads((root / "config/feature_flags.json").read_text()).keys())
        assert shipped, "feature_flags.json must not be empty"

        sources = "".join(
            p.read_text(encoding="utf-8", errors="ignore")
            for p in root.rglob("*.py")
            if "tests" not in p.parts and ".git" not in p.parts
        )
        unread = sorted(f for f in shipped
                        if not re.search(rf'is_enabled\(\s*["\']{re.escape(f)}["\']', sources))
        assert not unread, f"flags configured but never read by any code: {unread}"


    def test_feature_flags_file_based(self, tmp_path):
        from modules.cicd_improvements import FeatureFlagManager

        manager = FeatureFlagManager(config_file=str(tmp_path / "flags.json"))
        manager.set_flag("test_flag", enabled=True, description="temp flag")
        assert manager.is_enabled("test_flag") is True
        manager.set_flag("test_flag", enabled=False)
        assert manager.is_enabled("test_flag") is False

    # ---- Database improvements ----

    def test_db_health_check_handles_failure(self):
        from modules.database_improvements import DatabaseHealthCheck
        # MagicMock is imported at module level; re-importing it here was F811.

        broken_conn = MagicMock()
        broken_conn.cursor.side_effect = Exception("connection refused")

        check = DatabaseHealthCheck()
        result = check.run_health_check(broken_conn)
        assert result["status"] == "unhealthy"
        assert result["checks"]["connection"]["status"] == "unhealthy"

    def test_migration_manager_status(self, tmp_path):
        from modules.database_improvements import MigrationManager

        manager = MigrationManager(migrations_dir=str(tmp_path / "migrations"))
        status = manager.get_status()
        assert "applied" in status
        assert "pending" in status


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
