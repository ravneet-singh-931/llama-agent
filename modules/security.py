"""
Security Module - Input Validation & File Upload Protection

Features:
- File upload validation (size, type, filename sanitization)
- Input length limits to prevent abuse
- Rate limiting helper
- Path traversal prevention
"""

import ast
import math
import operator
import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple, List


# ============ FILE UPLOAD SECURITY ============

# Maximum file size: 10 MB
MAX_FILE_SIZE = 10 * 1024 * 1024

# Allowed file extensions (lowercase, with dot)
ALLOWED_EXTENSIONS = {
    ".txt", ".md", ".py", ".json", ".csv", ".pdf", ".docx"
}

# Dangerous extensions that should NEVER be uploaded
BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".scr", ".pif",
    ".sh", ".bash", ".ps1", ".vbs", ".js", ".jar",
    ".php", ".asp", ".aspx", ".jsp", ".cgi",
    ".dll", ".so", ".dylib",
}


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent path traversal attacks.
    
    Attacks like: ../../../etc/passwd or ..\\windows\\system32\\config
    This function strips directory components and dangerous characters.
    """
    # Remove any directory components (path traversal)
    filename = os.path.basename(filename)
    
    # Remove null bytes
    filename = filename.replace("\x00", "")
    
    # Replace dangerous characters with underscores
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', filename)
    
    # Remove any remaining path traversal sequences (..)
    filename = filename.replace('..', '')
    
    # Collapse multiple underscores
    filename = re.sub(r'_+', '_', filename)
    
    # Strip leading/trailing dots and spaces
    filename = filename.strip('. ')
    
    # If empty after sanitization, use a default
    if not filename:
        filename = "uploaded_file"
    
    # Limit length
    if len(filename) > 255:
        name, ext = os.path.splitext(filename)
        filename = name[:255 - len(ext)] + ext
    
    return filename


def validate_file_upload(
    filename: str,
    file_size: int,
    content_type: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Validate a file upload for security.
    
    Args:
        filename: Original filename from the upload
        file_size: Size of the file in bytes
        content_type: MIME type (optional, for additional validation)
    
    Returns:
        (is_valid, error_message)
        If valid: (True, "")
        If invalid: (False, "reason for rejection")
    """
    # 1. Check file size
    if file_size > MAX_FILE_SIZE:
        size_mb = file_size / (1024 * 1024)
        max_mb = MAX_FILE_SIZE / (1024 * 1024)
        return False, f"File too large ({size_mb:.1f} MB). Maximum: {max_mb} MB"
    
    if file_size == 0:
        return False, "File is empty"
    
    # 2. Check file extension
    ext = Path(filename).suffix.lower()
    
    if not ext:
        return False, "File has no extension"
    
    if ext in BLOCKED_EXTENSIONS:
        return False, f"File type '{ext}' is not allowed for security reasons"
    
    if ext not in ALLOWED_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_EXTENSIONS))
        return False, f"File type '{ext}' not supported. Allowed: {allowed}"
    
    # 3. Check filename for suspicious patterns
    suspicious_patterns = [
        r'\.\.',           # Path traversal
        r'[/\\]',          # Directory separators (should be stripped by basename, but check)
        r'^\.',            # Hidden files
        r'[\x00-\x1f]',   # Control characters
    ]
    
    for pattern in suspicious_patterns:
        if re.search(pattern, filename):
            return False, f"Filename contains suspicious characters"
    
    return True, ""


def get_upload_path(upload_dir: str, filename: str) -> str:
    """
    Get a safe upload path with sanitized filename.
    Prevents path traversal in the upload directory.
    """
    safe_name = sanitize_filename(filename)
    full_path = os.path.join(upload_dir, safe_name)
    
    # Ensure the resolved path is still within the upload directory
    upload_dir_resolved = os.path.realpath(upload_dir)
    full_path_resolved = os.path.realpath(full_path)
    
    if not full_path_resolved.startswith(upload_dir_resolved):
        raise ValueError(f"Path traversal detected: {filename}")
    
    return full_path


def is_path_within_roots(file_path: str, allowed_roots: List[str]) -> bool:
    """
    Check that a user-supplied file path resolves inside one of the allowed
    root directories.

    This is the authoritative guard against Local-File-Inclusion style
    attacks (e.g. /documents/read?file_path=../../etc/passwd or symlinks
    pointing outside the data roots). Both '..' components and symlinks are
    neutralized by os.path.realpath before comparison.
    """
    if not file_path or not isinstance(file_path, str):
        return False

    # Normalize separators for cross-platform consistency, then resolve
    # symlinks/.. so the containment check cannot be bypassed.
    try:
        resolved = os.path.realpath(os.path.normpath(file_path))
    except (OSError, ValueError):
        return False

    for root in allowed_roots:
        root_resolved = os.path.realpath(os.path.normpath(root))
        if resolved == root_resolved or resolved.startswith(root_resolved + os.sep):
            return True

    return False


# ============ INPUT VALIDATION ============

# Maximum lengths for various inputs
MAX_USERNAME_LENGTH = 50
MAX_EMAIL_LENGTH = 255
MAX_PASSWORD_LENGTH = 128
MAX_MESSAGE_LENGTH = 10000  # 10K chars for chat messages
MAX_FACT_KEY_LENGTH = 100
MAX_FACT_VALUE_LENGTH = 1000
MAX_SEARCH_QUERY_LENGTH = 500


def validate_username(username: str) -> Tuple[bool, str]:
    """Validate a username"""
    if not username:
        return False, "Username is required"
    if len(username) > MAX_USERNAME_LENGTH:
        return False, f"Username too long (max {MAX_USERNAME_LENGTH} characters)"
    if not re.match(r'^[a-zA-Z0-9_-]+$', username):
        return False, "Username can only contain letters, numbers, underscores, and hyphens"
    if username.startswith('-') or username.startswith('_'):
        return False, "Username cannot start with a hyphen or underscore"
    return True, ""


def validate_email(email: str) -> Tuple[bool, str]:
    """Validate an email address"""
    if not email:
        return False, "Email is required"
    if len(email) > MAX_EMAIL_LENGTH:
        return False, f"Email too long (max {MAX_EMAIL_LENGTH} characters)"
    # Basic email pattern check
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, "Invalid email format"
    return True, ""


def validate_password(password: str) -> Tuple[bool, str]:
    """Validate a password"""
    if not password:
        return False, "Password is required"
    if len(password) < 4:
        return False, "Password must be at least 4 characters"
    if len(password) > MAX_PASSWORD_LENGTH:
        return False, f"Password too long (max {MAX_PASSWORD_LENGTH} characters)"
    return True, ""


def validate_message(message: str) -> Tuple[bool, str]:
    """Validate a chat message"""
    if not message or not message.strip():
        return False, "Message cannot be empty"
    if len(message) > MAX_MESSAGE_LENGTH:
        return False, f"Message too long (max {MAX_MESSAGE_LENGTH} characters)"
    return True, ""


def validate_search_query(query: str) -> Tuple[bool, str]:
    """Validate a search query"""
    if not query or not query.strip():
        return False, "Search query cannot be empty"
    if len(query) > MAX_SEARCH_QUERY_LENGTH:
        return False, f"Query too long (max {MAX_SEARCH_QUERY_LENGTH} characters)"
    return True, ""


def validate_fact_key(key: str) -> Tuple[bool, str]:
    """Validate a fact key"""
    if not key or not key.strip():
        return False, "Fact key cannot be empty"
    if len(key) > MAX_FACT_KEY_LENGTH:
        return False, f"Fact key too long (max {MAX_FACT_KEY_LENGTH} characters)"
    return True, ""


def validate_fact_value(value: str) -> Tuple[bool, str]:
    """Validate a fact value"""
    if not value or not value.strip():
        return False, "Fact value cannot be empty"
    if len(value) > MAX_FACT_VALUE_LENGTH:
        return False, f"Fact value too long (max {MAX_FACT_VALUE_LENGTH} characters)"
    return True, ""


# ============ RATE LIMITING (Simple In-Memory) ============

from collections import defaultdict
from datetime import datetime, timedelta
import threading


class SimpleRateLimiter:
    """
    Rate limiter with an in-memory fallback and optional Redis backing.

    When REDIS_URL is configured AND reachable, request counts live in Redis so
    the limit is enforced across all API replicas (a single in-process limiter
    can be bypassed by hitting a different pod). Falls back to the in-memory
    store when Redis is unavailable (degraded single-process mode, identical to
    the original behavior).
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        requests_per_hour: int = 1000,
    ):
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self._requests = defaultdict(list)
        self._lock = threading.Lock()
        self._redis = None
        self._redis_checked = False

    def _get_redis(self):
        """Lazily connect to Redis (best-effort). Returns client or None."""
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return None
        try:
            import redis
            client = redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
            client.ping()
            self._redis = client
        except Exception:
            self._redis = None
        return self._redis

    def _cleanup(self, key: str):
        """Remove old entries (in-memory path)"""
        now = datetime.now()
        cutoff_minute = now - timedelta(minutes=1)
        cutoff_hour = now - timedelta(hours=1)

        self._requests[key] = [
            ts for ts in self._requests[key]
            if ts > cutoff_hour
        ]

    def is_rate_limited(self, key: str) -> Tuple[bool, str]:
        """
        Check if a key (e.g., IP address, user ID) is rate limited.

        Returns:
            (is_limited, message)
        """
        r = self._get_redis()
        if r is not None:
            try:
                return self._is_rate_limited_redis(r, key)
            except Exception:
                # Redis error -> fall back to in-memory so the service keeps
                # working (degraded, per-process limiting only).
                pass
        return self._is_rate_limited_memory(key)

    def _is_rate_limited_redis(self, r, key: str) -> Tuple[bool, str]:
        now = time.time()
        zkey = f"ratelimit:{key}"
        member = f"{now}:{uuid.uuid4().hex}"
        pipe = r.pipeline()
        pipe.zremrangebyscore(zkey, 0, now - 3600)  # drop entries older than 1h
        pipe.zadd(zkey, {member: now})
        pipe.expire(zkey, 3600)
        pipe.zcard(zkey)                  # total requests in the last hour
        pipe.zcount(zkey, now - 60, now)  # requests in the last minute
        _, _, _, hour_count, minute_count = pipe.execute()
        if minute_count > self.requests_per_minute:
            return True, f"Rate limit: {self.requests_per_minute} requests per minute"
        if hour_count > self.requests_per_hour:
            return True, f"Rate limit: {self.requests_per_hour} requests per hour"
        return False, ""

    def _is_rate_limited_memory(self, key: str) -> Tuple[bool, str]:
        with self._lock:
            self._cleanup(key)
            now = datetime.now()
            cutoff_minute = now - timedelta(minutes=1)
            cutoff_hour = now - timedelta(hours=1)

            # Count requests in last minute
            recent_minute = sum(1 for ts in self._requests[key] if ts > cutoff_minute)
            if recent_minute >= self.requests_per_minute:
                return True, f"Rate limit: {self.requests_per_minute} requests per minute"

            # Count requests in last hour
            recent_hour = len(self._requests[key])
            if recent_hour >= self.requests_per_hour:
                return True, f"Rate limit: {self.requests_per_hour} requests per hour"

            # Record this request
            self._requests[key].append(now)
            return False, ""


# Global rate limiter instance
rate_limiter = SimpleRateLimiter()


# ============ SAFE MATH EVALUATION ============

# Whitelisted binary/unary operators for safe_math_eval
_MATH_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_MATH_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# Whitelisted functions/constants (subset of the math module)
_MATH_FUNCS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "exp": math.exp,
    "floor": math.floor, "ceil": math.ceil, "degrees": math.degrees,
    "radians": math.radians, "abs": abs,
    "pi": math.pi, "e": math.e, "tau": math.tau,
}


def safe_math_eval(expression: str) -> float:
    """Evaluate a mathematical expression WITHOUT arbitrary code execution.

    Replaces the previous ``eval(expression, {"__builtins__": {}}, ...)``
    approach, which is a classic sandbox-escape vector. Only whitelisted
    arithmetic operators, numeric constants, and math functions are allowed.

    Args:
        expression: A math expression, e.g. "2 + 3 * sqrt(16)".

    Returns:
        The numeric result.

    Raises:
        ValueError / TypeError: if the expression contains anything unsafe.
    """
    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _MATH_BIN_OPS:
            return _MATH_BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _MATH_UNARY_OPS:
            return _MATH_UNARY_OPS[type(node.op)](_eval(node.operand))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _MATH_FUNCS):
            return _MATH_FUNCS[node.func.id](*[_eval(a) for a in node.args])
        if isinstance(node, ast.Name) and node.id in _MATH_FUNCS:
            return _MATH_FUNCS[node.id]
        raise ValueError(f"Unsupported expression element: {type(node).__name__}")

    tree = ast.parse(expression, mode="eval")
    return _eval(tree.body)
