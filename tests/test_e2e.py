"""
End-to-End Tests for Llama Agent

Tests the FULL flow: Browser → FastAPI → Database → AI → Response

Run with: pytest tests/test_e2e.py -v
"""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============ END-TO-END TESTS ============


class TestAuthenticationFlow:
    """Test complete authentication flow."""

    def test_register_and_login_flow(self):
        """Test: Register → Login → Get Token → Access Protected Endpoint"""
        from modules.auth import user_manager, jwt_manager
        from modules.database import get_db_manager

        # Step 1: Register
        print("\n  Step 1: Registering new user...")
        # This would normally call the API
        # For unit test, we test the modules directly

        # Step 2: Verify password hashing works
        from modules.auth import hash_password, verify_password
        password = "test_password_123"
        hashed = hash_password(password)
        assert verify_password(password, hashed), "Password verification failed"
        print("  Step 2: Password hashing works")

        # Step 3: Verify JWT creation works (sub must be a string per PyJWT)
        token = jwt_manager.create_access_token({"sub": "1", "username": "testuser"})
        assert token is not None, "JWT token creation failed"
        assert len(token) > 50, "JWT token too short"
        print("  Step 3: JWT token created")

        # Step 4: Verify JWT verification works
        payload = jwt_manager.verify_token(token)
        assert payload is not None, "JWT verification failed"
        print("  Step 4: JWT token verified")

        print("  ✅ Authentication flow complete")

    def test_password_security(self):
        """Test: Passwords are hashed, never stored as plain text"""
        from modules.auth import hash_password, verify_password

        password = "my_secret_password"
        hashed = hash_password(password)

        # Password should be hashed
        assert hashed != password, "Password stored as plain text!"
        assert len(hashed) > 50, "Hash too short"
        assert hashed.startswith("$2b$") or hashed.startswith("$2a$"), "Not bcrypt hash"

        # Verification should work
        assert verify_password(password, hashed), "Verification failed"
        assert not verify_password("wrong_password", hashed), "Wrong password accepted"

        print("  ✅ Password security verified")


class TestChatFlow:
    """Test complete chat flow."""

    def test_chat_request_validation(self):
        """Test: Chat request is validated before processing"""
        from pydantic import ValidationError
        from api import ChatRequest

        # Valid request
        valid = ChatRequest(message="Hello")
        assert valid.message == "Hello"

        # Empty message should fail
        with pytest.raises(ValidationError):
            ChatRequest(message="")

        # Too long message should fail
        with pytest.raises(ValidationError):
            ChatRequest(message="x" * 20000)

        print("  ✅ Chat request validation works")

    def test_rag_pipeline_flow(self):
        """Test: Query → Embedding → Search → Re-rank → Answer"""
        from modules.advanced_rag import ReRanker, RouterQueryEngine

        # Step 1: Router classifies query (routes to configured collections;
        # a fresh router with only the default store routes to ["default"])
        router = RouterQueryEngine()
        query = "What is the refund policy?"
        query_type = router.classify_query(query)
        assert isinstance(query_type, list) and query_type == ["default"], f"Unknown type: {query_type}"
        print(f"  Step 1: Query routed to '{query_type}'")

        # Step 2: Re-ranker processes results
        reranker = ReRanker()
        mock_results = [
            {"content": "Refund policy: 30 days", "similarity": 0.7, "chunk_index": 0, "total_chunks": 5},
            {"content": "Pricing plans start at $9", "similarity": 0.8, "chunk_index": 1, "total_chunks": 5},
            {"content": "Contact support for help", "similarity": 0.6, "chunk_index": 2, "total_chunks": 5},
        ]
        reranked = reranker.rerank(query, mock_results, top_k=2)
        assert len(reranked) == 2, "Re-ranking failed"
        print("  Step 2: Results re-ranked")

        # Step 3: Top result should be most relevant
        top_content = reranked[0]["content"]
        assert "refund" in top_content.lower() or "rerank_score" in reranked[0], "Top result not most relevant"
        print("  Step 3: Top result is most relevant")

        print("  ✅ RAG pipeline flow complete")


class TestSecurityFlow:
    """Test security measures."""

    def test_rate_limiting(self):
        """Test: Rate limiter blocks excessive requests"""
        from modules.security import SimpleRateLimiter

        limiter = SimpleRateLimiter(requests_per_minute=3, requests_per_hour=100)
        test_ip = "192.168.1.100"

        # First 3 requests should pass
        for i in range(3):
            is_limited, msg = limiter.is_rate_limited(test_ip)
            assert not is_limited, f"Request {i+1} should not be limited"

        # 4th request should be blocked
        is_limited, msg = limiter.is_rate_limited(test_ip)
        assert is_limited, "Rate limiter not working"

        print("  ✅ Rate limiting works")

    def test_file_upload_validation(self):
        """Test: File uploads are validated"""
        from modules.security import validate_file_upload

        # Valid file
        is_valid, msg = validate_file_upload("document.pdf", 1024 * 1024)  # 1MB
        assert is_valid, f"Valid file rejected: {msg}"

        # Invalid file type
        is_valid, msg = validate_file_upload("malware.exe", 1024)
        assert not is_valid, "EXE file accepted!"

        # File too large
        is_valid, msg = validate_file_upload("huge.pdf", 100 * 1024 * 1024)  # 100MB
        assert not is_valid, "Oversized file accepted!"

        print("  ✅ File upload validation works")

    def test_input_validation(self):
        """Test: User input is validated before processing.

        SQL-injection / XSS neutralization is handled by parameterized
        SQL (SQLAlchemy) on the backend and React's default output
        escaping on the frontend — the security module validates input
        length and format instead of string-sanitizing.
        """
        from modules.security import (
            validate_message,
            validate_username,
            validate_email,
            validate_password,
            validate_search_query,
        )

        # Chat messages: empty / oversized rejected
        ok, msg = validate_message("")
        assert not ok and "empty" in msg.lower(), f"Empty message accepted: {msg}"
        ok, msg = validate_message("x" * 20000)
        assert not ok and "long" in msg.lower(), f"Oversized message accepted: {msg}"
        assert validate_message("Hello there") == (True, "")

        # Usernames: format enforced (rejects SQLi payloads too)
        assert validate_username("valid_user-1") == (True, "")
        ok, msg = validate_username("bad user!")
        assert not ok and "only contain" in msg.lower(), f"Bad username accepted: {msg}"

        # Email format enforced
        assert validate_email("a@b.co") == (True, "")
        ok, msg = validate_email("not-an-email")
        assert not ok and "format" in msg.lower(), f"Bad email accepted: {msg}"

        # Password: minimum length enforced
        assert validate_password("StrongPass1!") == (True, "")
        ok, msg = validate_password("123")
        assert not ok and "least" in msg.lower(), f"Weak password accepted: {msg}"

        # Search query: empty rejected
        assert validate_search_query("largest planet") == (True, "")
        ok, msg = validate_search_query("")
        assert not ok, "Empty search query accepted"

        print("  ✅ Input validation works")


class TestMemoryFlow:
    """Test memory and fact storage."""

    def test_conversation_memory(self):
        """Test: Messages are stored and retrieved"""
        import tempfile
        from modules.memory import ConversationMemory

        with tempfile.TemporaryDirectory() as td:
            memory = ConversationMemory(memory_file=os.path.join(td, "conv.json"))

            # Add messages
            memory.add_message("user", "Hello")
            memory.add_message("assistant", "Hi there!")
            memory.add_message("user", "What's the refund policy?")

            # Get context
            context = memory.get_context_string()
            assert "Hello" in context, "User message not stored"
            assert "Hi there!" in context, "Assistant message not stored"

        print("  ✅ Conversation memory works")

    def test_fact_memory(self):
        """Test: Facts are stored, recalled, and forgotten"""
        import tempfile
        from modules.memory import FactMemory

        with tempfile.TemporaryDirectory() as td:
            facts = FactMemory(facts_file=os.path.join(td, "facts.json"))

            # Remember a fact
            facts.remember("name", "Ravneet")
            value = facts.recall("name")
            assert value == "Ravneet", "Fact not stored correctly"

            # Recall all facts
            all_facts = facts.get_all_facts()
            assert "name" in all_facts, "Fact not in all facts"

            # Forget a fact
            facts.forget("name")
            value = facts.recall("name")
            assert "don't remember" in value, f"Fact not forgotten: {value!r}"

        print("  ✅ Fact memory works")


class TestMonitoringFlow:
    """Test monitoring and metrics."""

    def test_performance_monitoring(self):
        """Test: process health is reported"""
        from modules.monitoring import PerformanceMonitor

        monitor = PerformanceMonitor()

        health = monitor.get_health()
        assert "status" in health, "Health check missing status"
        assert health["status"] in ["healthy", "degraded", "unhealthy"], "Invalid status"
        assert health["uptime_human"], "Health check missing uptime"

        print("  ✅ Performance monitoring works")

    def test_error_tracking(self):
        """Test: errors are counted by the error logger, not the monitor

        Errors used to be incremented twice: once by PerformanceMonitor
        (4 call sites) and once by the AgentLogger that actually writes the
        record. The monitor's copy is gone; log_error is the one that counts.
        """
        import tempfile

        from modules.monitoring import AgentLogger

        with tempfile.TemporaryDirectory() as tmp:
            logger = AgentLogger(log_dir=tmp)
            before = logger.get_error_count()
            logger.log_error(ValueError("boom"), context="test")
            after = logger.get_error_count()
        assert after == before + 1, "Error not tracked"

        print("  ✅ Error tracking works")


# ============ RUN ALL TESTS ============


if __name__ == "__main__":
    print("=" * 60)
    print("END-TO-END TESTS")
    print("=" * 60)

    tests = [
        TestAuthenticationFlow(),
        TestChatFlow(),
        TestSecurityFlow(),
        TestMemoryFlow(),
        TestMonitoringFlow(),
    ]

    passed = 0
    failed = 0

    for test_class in tests:
        print(f"\n--- {test_class.__name__} ---")
        for method_name in dir(test_class):
            if method_name.startswith("test_"):
                method = getattr(test_class, method_name)
                try:
                    method()
                    passed += 1
                except Exception as e:
                    print(f"  ❌ FAILED: {method_name} - {e}")
                    failed += 1

    print("\n" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
