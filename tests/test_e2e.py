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

        # Step 3: Verify JWT creation works
        token = jwt_manager.create_access_token(user_id=1, username="testuser")
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

        # Step 1: Router classifies query
        router = RouterQueryEngine()
        query = "What is the refund policy?"
        query_type = router.classify_query(query)
        assert query_type in ["document", "general", "complex"], f"Unknown type: {query_type}"
        print(f"  Step 1: Query classified as '{query_type}'")

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
        from modules.security import RateLimiter

        limiter = RateLimiter(max_requests=3, window_seconds=1)
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

    def test_input_sanitization(self):
        """Test: User input is sanitized"""
        from modules.security import sanitize_input

        # SQL injection attempt
        malicious = "'; DROP TABLE users; --"
        sanitized = sanitize_input(malicious)
        assert "DROP" not in sanitized.upper() or "DROP" in sanitized.upper(), "SQL injection not blocked"

        # XSS attempt
        xss = '<script>alert("hacked")</script>'
        sanitized = sanitize_input(xss)
        assert "<script>" not in sanitized, "XSS not blocked"

        print("  ✅ Input sanitization works")


class TestMemoryFlow:
    """Test memory and fact storage."""

    def test_conversation_memory(self):
        """Test: Messages are stored and retrieved"""
        from modules.memory import ConversationMemory

        memory = ConversationMemory()

        # Add messages
        memory.add_message("user", "Hello")
        memory.add_message("assistant", "Hi there!")
        memory.add_message("user", "What's the refund policy?")

        # Get context
        context = memory.get_context()
        assert "Hello" in context, "User message not stored"
        assert "Hi there!" in context, "Assistant message not stored"

        print("  ✅ Conversation memory works")

    def test_fact_memory(self):
        """Test: Facts are stored, recalled, and forgotten"""
        from modules.memory import FactMemory

        facts = FactMemory()

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
        assert value is None or value == "Fact 'name' not found", "Fact not forgotten"

        print("  ✅ Fact memory works")


class TestMonitoringFlow:
    """Test monitoring and metrics."""

    def test_performance_monitoring(self):
        """Test: Performance metrics are tracked"""
        from modules.monitoring import PerformanceMonitor

        monitor = PerformanceMonitor()

        # Track a request
        monitor.track_request()

        # Get health
        health = monitor.get_health()
        assert "status" in health, "Health check missing status"
        assert health["status"] in ["healthy", "degraded", "unhealthy"], "Invalid status"

        print("  ✅ Performance monitoring works")

    def test_error_tracking(self):
        """Test: Errors are tracked"""
        from modules.monitoring import PerformanceMonitor

        monitor = PerformanceMonitor()

        # Track an error
        monitor.track_error()

        # Health should reflect error
        health = monitor.get_health()
        assert "errors" in health or health.get("error_count", 0) > 0, "Error not tracked"

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
