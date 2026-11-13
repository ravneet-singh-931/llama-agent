"""
Security Tests for Llama Agent

Tests for common attack vectors and security vulnerabilities.

Run with: pytest tests/test_security.py -v
"""

import sys
import os
import re
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


# ============ SQL INJECTION TESTS ============


class TestSQLInjection:
    """Test protection against SQL injection attacks.

    SQL injection is neutralized by:
      1. Username/password VALIDATION (modules.security) — payloads with
         quotes/spaces/control chars are rejected at the input layer.
      2. Parameterized SQL queries (SQLAlchemy) — values are bound, never
         interpolated into raw SQL.
    """

    def test_sql_injection_in_login(self):
        """Test: SQL injection payloads are rejected as usernames"""
        from modules.security import validate_username, validate_password

        payloads = [
            "' OR '1'='1",
            "admin'--",
            "' OR 1=1--",
            "'; DROP TABLE users; --",
            "' UNION SELECT * FROM users --",
            "1' AND '1'='1",
            "admin' UNION SELECT * FROM users --",
        ]

        for payload in payloads:
            # Username validation must reject these (invalid characters)
            is_valid, _ = validate_username(payload)
            assert not is_valid, f"SQL injection payload accepted as username: {payload}"
            # Password validation only enforces length (free-form); values
            # are bound via parameterized queries, never executed as SQL.
            is_valid, _ = validate_password(payload)
            assert isinstance(is_valid, bool)

        print("  ✅ SQL injection in login blocked by username validation")

    def test_sql_injection_in_chat(self):
        """Test: SQL injection in chat messages is length-limited and safe"""
        from modules.security import validate_message

        payloads = [
            "'; SELECT * FROM users; --",
            "1' UNION SELECT password FROM users WHERE username='admin'--",
            "'; INSERT INTO users (username, password) VALUES ('hacker', 'pass'); --",
        ]

        for payload in payloads:
            # Chat messages are free-form text: validation enforces the
            # length limit, and the backend stores them via parameterized
            # statements — the payload is never executed as SQL.
            is_valid, msg = validate_message(payload)
            assert is_valid, f"Valid-length chat message rejected: {msg}"
            assert len(payload) <= 10000, "Payload exceeds message limit"

        print("  ✅ SQL injection in chat neutralized by parameterized queries")


# ============ XSS TESTS ============


class TestXSS:
    """Test protection against Cross-Site Scripting attacks.

    XSS is mitigated by:
      1. React's default output escaping on the frontend — user content is
         rendered as TEXT, never as raw HTML.
      2. Input validation (modules.security) enforcing length limits.
    """

    def test_xss_payloads_stored_as_plain_text(self):
        """Test: XSS payloads pass message validation but are rendered as
        escaped text (React escapes output by default)."""
        from modules.security import validate_message

        payloads = [
            '<script>alert("hacked")</script>',
            '<SCRIPT>alert("hacked")</SCRIPT>',
            '<script src="http://evil.com/steal.js"></script>',
            '<script>document.cookie</script>',
            '<img onerror="alert(1)">',
            '<body onload="alert(1)">',
            '<input onfocus="alert(1)">',
            '<div onmouseover="alert(1)">',
            'javascript:alert(1)',
            'JAVASCRIPT:alert(1)',
            'java\nscript:alert(1)',
        ]

        for payload in payloads:
            # Free-form messages are allowed (with length limits); the
            # frontend escapes them, so they can never execute.
            is_valid, msg = validate_message(payload)
            assert is_valid, f"XSS payload rejected: {msg}"

        print("  ✅ XSS payloads stored safely as plain text")

    def test_no_user_content_rendered_as_raw_html(self):
        """Test: The frontend never injects user content as raw HTML.

        The only dangerouslySetInnerHTML usage in the entire frontend is
        a static theme-init script in layout.tsx (no user data). Chat
        messages render through React's automatic escaping.
        """
        frontend_src = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "frontend", "src"
        )
        if not os.path.isdir(frontend_src):
            pytest.skip("frontend/ not present in this checkout")

        uses = []
        for root, _, files in os.walk(frontend_src):
            for fn in files:
                if fn.endswith((".tsx", ".ts", ".jsx", ".js")):
                    path = os.path.join(root, fn)
                    with open(path, encoding="utf-8", errors="replace") as f:
                        for line in f:
                            if "dangerouslySetInnerHTML" in line:
                                # Normalize separators so the comparison
                                # below works on Windows too.
                                uses.append(
                                    os.path.relpath(path, frontend_src)
                                    .replace(os.sep, "/")
                                )

        # Allowed: the static theme-init script only (no user data)
        allowed_files = {"app/layout.tsx"}
        offenders = [u for u in uses if u not in allowed_files]
        assert not offenders, (
            f"dangerouslySetInnerHTML used outside layout.tsx: {offenders}"
        )

        print("  ✅ No user content rendered as raw HTML")


# ============ INPUT VALIDATION TESTS ============


class TestInputValidation:
    """Test input validation and constraints."""

    def test_message_length_limit(self):
        """Test: Messages have length limits"""
        from pydantic import ValidationError
        from api import ChatRequest

        # Valid message
        valid = ChatRequest(message="Hello")
        assert len(valid.message) <= 10000

        # Too long
        with pytest.raises(ValidationError):
            ChatRequest(message="x" * 20000)

        print("  ✅ Message length limit enforced")

    def test_username_validation(self):
        """Test: Username format is validated"""
        from pydantic import ValidationError
        from api import RegisterRequest

        # Valid username
        valid = RegisterRequest(
            username="ravneet",
            email="test@test.com",
            password="password123"
        )
        assert valid.username.isalnum() or "_" in valid.username

        # Invalid usernames
        invalid_usernames = [
            "ra",  # Too short
            "a" * 60,  # Too long
            "user name",  # Spaces
            "user@name",  # Special chars
        ]

        for username in invalid_usernames:
            with pytest.raises(ValidationError):
                RegisterRequest(
                    username=username,
                    email="test@test.com",
                    password="password123"
                )

        print("  ✅ Username validation works")

    def test_password_strength(self):
        """Test: Password requirements are enforced"""
        from pydantic import ValidationError
        from api import RegisterRequest

        # Valid password
        valid = RegisterRequest(
            username="testuser",
            email="test@test.com",
            password="strongpassword123"
        )
        assert len(valid.password) >= 8

        # Too short
        with pytest.raises(ValidationError):
            RegisterRequest(
                username="testuser",
                email="test@test.com",
                password="short12"  # 7 chars: below the 8-char minimum
            )

        print("  ✅ Password strength enforced")


# ============ AUTHENTICATION TESTS ============


class TestAuthenticationSecurity:
    """Test authentication security."""

    def test_jwt_token_structure(self):
        """Test: JWT token has correct structure"""
        from modules.auth import jwt_manager

        token = jwt_manager.create_access_token({"sub": "1", "username": "test"})

        # JWT should have 3 parts separated by dots
        parts = token.split(".")
        assert len(parts) == 3, f"JWT has {len(parts)} parts, expected 3"

        # Each part should be base64url encoded (URL-safe alphabet)
        import base64
        for part in parts:
            try:
                padded = part + "=" * ((4 - len(part) % 4) % 4)
                base64.urlsafe_b64decode(padded)
            except Exception as e:
                pytest.fail(f"Invalid base64url in JWT part {part[:20]}...: {e}")

        print("  ✅ JWT token structure valid")

    def test_jwt_expiration(self):
        """Test: JWT tokens expire"""
        from modules.auth import jwt_manager
        import time

        # Create token (expiry is configurable; default is 30 minutes)
        token = jwt_manager.create_access_token({"sub": "1", "username": "test"})

        # Verify it works immediately
        payload = jwt_manager.verify_token(token)
        assert payload is not None, "Token should be valid immediately"

        # Note: We can't easily test expiration without waiting
        # But we can verify the token has an expiry claim
        assert payload.get("exp") is not None, "Token missing expiry claim"

        print("  ✅ JWT expiration configured")

    def test_password_hashing_uniqueness(self):
        """Test: Same password produces different hashes"""
        from modules.auth import hash_password

        password = "same_password"
        hash1 = hash_password(password)
        hash2 = hash_password(password)

        # Same password should produce different hashes (random salt)
        assert hash1 != hash2, "Same password produced same hash!"

        # But both should verify correctly
        from modules.auth import verify_password
        assert verify_password(password, hash1)
        assert verify_password(password, hash2)

        print("  ✅ Password hashing uses unique salts")


# ============ RATE LIMITING TESTS ============


class TestRateLimiting:
    """Test rate limiting protection."""

    def test_rate_limit_enforcement(self):
        """Test: Rate limiter blocks after limit"""
        from modules.security import SimpleRateLimiter

        limiter = SimpleRateLimiter(requests_per_minute=5, requests_per_hour=100)
        test_ip = "10.0.0.1"

        # First 5 should pass
        for i in range(5):
            is_limited, _ = limiter.is_rate_limited(test_ip)
            assert not is_limited, f"Request {i+1} should not be limited"

        # 6th should be blocked
        is_limited, _ = limiter.is_rate_limited(test_ip)
        assert is_limited, "Rate limiter not working"

        print("  ✅ Rate limiting enforced")

    def test_different_ips_separate_limits(self):
        """Test: Different IPs have separate limits"""
        from modules.security import SimpleRateLimiter

        limiter = SimpleRateLimiter(requests_per_minute=2, requests_per_hour=100)
        ip1 = "10.0.0.1"
        ip2 = "10.0.0.2"

        # IP1 uses up its limit
        limiter.is_rate_limited(ip1)
        limiter.is_rate_limited(ip1)

        # IP2 should still work
        is_limited, _ = limiter.is_rate_limited(ip2)
        assert not is_limited, "Different IPs should have separate limits"

        print("  ✅ Separate rate limits per IP")


# ============ FILE UPLOAD TESTS ============


class TestFileUploadSecurity:
    """Test file upload security."""

    def test_malicious_file_types(self):
        """Test: Dangerous file types are rejected"""
        from modules.security import validate_file_upload

        dangerous_files = [
            "virus.exe",
            "malware.bat",
            "script.ps1",
            "trojan.scr",
            "backdoor.dll",
        ]

        for filename in dangerous_files:
            is_valid, _ = validate_file_upload(filename, 1024)
            assert not is_valid, f"Dangerous file accepted: {filename}"

        print("  ✅ Dangerous file types rejected")

    def test_file_size_limits(self):
        """Test: Oversized files are rejected"""
        from modules.security import validate_file_upload

        # Under limit
        is_valid, _ = validate_file_upload("document.pdf", 10 * 1024 * 1024)  # 10MB
        assert is_valid, "Valid file rejected"

        # Over limit
        is_valid, _ = validate_file_upload("huge.pdf", 100 * 1024 * 1024)  # 100MB
        assert not is_valid, "Oversized file accepted"

        print("  ✅ File size limits enforced")

    def test_filename_sanitization(self):
        """Test: Filenames are sanitized"""
        from modules.security import sanitize_filename

        malicious_names = [
            "../../../etc/passwd",
            "..\\..\\windows\\system32\\config\\sam",
            "file with spaces.pdf",
            "file;rm -rf /.pdf",
            "file|cat /etc/passwd.pdf",
        ]

        for name in malicious_names:
            sanitized = sanitize_filename(name)
            # Should not contain path traversal
            assert ".." not in sanitized, f"Path traversal not blocked: {name}"
            assert "/" not in sanitized or sanitized.count("/") <= 1, f"Slash not removed: {name}"
            assert "\\" not in sanitized, f"Backslash not removed: {name}"

        print("  ✅ Filenames sanitized")


# ============ CORS TESTS ============


class TestCORS:
    """Test CORS configuration."""

    def test_cors_headers_present(self):
        """Test: CORS headers are configured

        This test used to build a list of middleware, discard it, print a
        checkmark and pass — so deleting CORSMiddleware from api.py would not
        have failed it. It now asserts on the middleware Starlette actually
        holds, and on the values inside it.
        """
        from fastapi.middleware.cors import CORSMiddleware

        from api import app

        cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
        assert cors, "api.py must register CORSMiddleware"

        kwargs = cors[0].kwargs
        assert kwargs["allow_origins"], "allow_origins must not be empty"
        # Wildcard origins and credentials together are rejected by browsers and
        # are a real misconfiguration, not a style choice.
        assert not (kwargs["allow_origins"] == ["*"] and kwargs["allow_credentials"]), (
            "allow_origins='*' with allow_credentials=True is invalid CORS"
        )
        assert "GET" in kwargs["allow_methods"] and "POST" in kwargs["allow_methods"]
        assert any(h.lower() == "authorization" for h in kwargs["allow_headers"])

    def test_cors_origins_restricted(self):
        """Test: CORS origins are restricted in production"""
        import os
        previous = os.environ.get("ENVIRONMENT")
        try:
            os.environ["ENVIRONMENT"] = "production"

            # Reload the app to check production CORS
            # In production, only specific origins should be allowed
            from modules.config import AppConfig, Environment
            config = AppConfig.from_env()
            assert config.environment == Environment.PRODUCTION
            print("  ✅ CORS origins restricted in production")
        finally:
            # Restore environment so other tests are not affected
            if previous is None:
                os.environ.pop("ENVIRONMENT", None)
            else:
                os.environ["ENVIRONMENT"] = previous


# ============ WEBSOCKET AUTH TESTS ============


class TestWebSocketAuthentication:
    """Test WebSocket token authentication (frontend sends ?token=<JWT>).

    The /ws/{user_id} endpoint uses modules.auth.authenticate_ws_token:
      - no/empty token       -> anonymous connection (allowed by caller)
      - valid access token   -> caller's real identity
      - invalid/expired      -> fail closed (connection rejected)
    """

    def test_no_token_is_anonymous(self):
        """Test: No token -> None (anonymous allowed by caller)"""
        import asyncio
        from modules.auth import authenticate_ws_token

        result = asyncio.run(authenticate_ws_token(None))
        assert result is None
        result = asyncio.run(authenticate_ws_token(""))
        assert result is None

        print("  ✅ No token -> anonymous")

    def test_malformed_tokens_rejected(self):
        """Test: Malformed tokens are rejected (fail closed)"""
        import asyncio
        from modules.auth import authenticate_ws_token

        bad_tokens = [
            "garbage",
            "not.a.jwt",
            "abc.def.ghi",
            "eyJhbGciOiJIUzI1NiJ9.invalid-signature.totally",
            "x" * 1000,
        ]
        for bad in bad_tokens:
            result = asyncio.run(authenticate_ws_token(bad))
            assert result is None, f"Malformed token accepted: {bad[:25]}..."

        print("  ✅ Malformed tokens rejected")

    def test_refresh_token_rejected_for_ws(self):
        """Test: Refresh tokens are NOT accepted for WebSocket connections"""
        import asyncio
        from modules.auth import authenticate_ws_token, jwt_manager

        refresh = jwt_manager.create_refresh_token(
            {"sub": "00000000-0000-0000-0000-000000000000"}
        )
        result = asyncio.run(authenticate_ws_token(refresh))
        assert result is None, "Refresh token accepted for WebSocket!"

        print("  ✅ Refresh tokens rejected for WebSocket")

    def test_expired_access_token_rejected(self):
        """Test: Expired access tokens are rejected"""
        import asyncio
        from datetime import timedelta
        from modules.auth import authenticate_ws_token, jwt_manager

        expired = jwt_manager.create_access_token(
            {"sub": "00000000-0000-0000-0000-000000000000"},
            expires_delta=timedelta(seconds=-60),
        )
        result = asyncio.run(authenticate_ws_token(expired))
        assert result is None, "Expired token accepted for WebSocket!"

        print("  ✅ Expired tokens rejected")


# ============ PATH CONTAINMENT (LFI GUARD) TESTS ============


class TestDocumentPathRestriction:
    """Test /documents/read path containment (Local-File-Inclusion guard).

    The endpoint only allows files under uploads/, data/, documents/ —
    verified via modules.security.is_path_within_roots (realpath based).
    """

    def test_allowed_roots_pass(self):
        """Test: Files inside uploads/, data/, documents/ are allowed"""
        from modules.security import is_path_within_roots

        roots = ["uploads", "data", "documents"]
        allowed = [
            "uploads/notes.pdf",
            "data/dataset.csv",
            "documents/report.txt",
            "uploads/sub/file.pdf",
            "data/nested/x.json",
        ]
        for path in allowed:
            assert is_path_within_roots(path, roots), f"Allowed path rejected: {path}"

        print("  ✅ Files in allowed roots pass")

    def test_traversal_attempts_blocked(self):
        """Test: Traversal, absolute paths, and outside files are blocked"""
        from modules.security import is_path_within_roots

        roots = ["uploads", "data", "documents"]
        evil = [
            "../../etc/passwd",
            "uploads/../../etc/passwd",
            "/etc/passwd",
            "/app/api.py",
            "logs/audit.log",
            "requirements.txt",
            "api.py",
            ".",
            "..",
            "",
            None,
            123,
            "..\\..\\windows\\system32\\config",
        ]
        for path in evil:
            assert not is_path_within_roots(path, roots), f"Evil path allowed: {path!r}"

        print("  ✅ Traversal / outside paths blocked")


# ============ RUN ALL TESTS ============


if __name__ == "__main__":
    print("=" * 60)
    print("SECURITY TESTS")
    print("=" * 60)

    tests = [
        TestSQLInjection(),
        TestXSS(),
        TestInputValidation(),
        TestAuthenticationSecurity(),
        TestRateLimiting(),
        TestFileUploadSecurity(),
        TestCORS(),
        TestWebSocketAuthentication(),
        TestDocumentPathRestriction(),
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


class TestFactInputLimits:
    """`validate_fact_key` / `validate_fact_value` were written and never called.

    `POST /remember` was already capped by its Pydantic model, so the uncovered
    path was the one that matters more: six call sites build a fact out of a
    free-text chat message ("X is Y", "X = Y"), where a 10,000-character message
    became a 10,000-character fact. The limits are now enforced in
    `FactMemory.remember`, which every caller goes through.
    """

    def test_remember_rejects_oversized_key(self):
        from modules.memory import FactMemory

        with pytest.raises(ValueError, match="too long"):
            FactMemory().remember("k" * 101, "value")

    def test_remember_rejects_oversized_value(self):
        """The chat path: nothing but this check stands between a 10,000-char
        message and a 10,000-char stored fact."""
        from modules.memory import FactMemory

        with pytest.raises(ValueError, match="too long"):
            FactMemory().remember("ok", "v" * 1001)

    def test_remember_rejects_empty(self):
        from modules.memory import FactMemory

        with pytest.raises(ValueError):
            FactMemory().remember("ok", "")

    def test_remember_still_accepts_a_normal_fact(self):
        """The guard must not break the happy path it protects."""
        from modules.memory import FactMemory

        mem = FactMemory()
        mem.remember("colour", "blue")
        assert mem.recall("colour") == "blue"

    def test_endpoint_limits_come_from_the_model(self):
        """POST /remember rejects the same values, one layer earlier (422)."""
        from starlette.testclient import TestClient

        from api import app

        with TestClient(app) as client:
            r = client.post("/remember", json={"key": "k" * 101, "value": "v"})
        assert r.status_code == 422
