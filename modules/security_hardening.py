"""
Security Hardening Module for Llama Agent

Features:
- Input sanitization (SQL injection, XSS, path traversal)
- Brute force protection
- Security headers
- CSRF protection
- Audit logging
"""

import os
import re
import time
import hashlib
import secrets
from typing import Tuple, Optional, Dict, List
from collections import defaultdict
from datetime import datetime, timedelta


# ============ INPUT SANITIZATION ============


def sanitize_input(text: str) -> str:
    """
    Sanitize user input to prevent injection attacks.
    
    Protects against:
    - SQL injection
    - XSS (Cross-Site Scripting)
    - Command injection
    """
    if not text:
        return text
    
    # Remove null bytes
    text = text.replace('\x00', '')
    
    # Remove script tags (case-insensitive)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<script[^>]*/?>', '', text, flags=re.IGNORECASE)
    
    # Remove event handlers
    text = re.sub(r'\bon\w+\s*=\s*["\'][^"\']*["\']', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\bon\w+\s*=\s*\S+', '', text, flags=re.IGNORECASE)
    
    # Remove javascript: protocol
    text = re.sub(r'javascript\s*:', '', text, flags=re.IGNORECASE)
    
    # Remove data: protocol (can execute code)
    text = re.sub(r'data\s*:', '', text, flags=re.IGNORECASE)
    
    # Remove vbscript: protocol
    text = re.sub(r'vbscript\s*:', '', text, flags=re.IGNORECASE)
    
    # Remove HTML tags (optional - depends on if you want to allow HTML)
    # Uncomment if you want to strip ALL HTML:
    # text = re.sub(r'<[^>]+>', '', text)
    
    # Escape HTML entities (if keeping some HTML)
    text = text.replace('&', '&amp;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    text = text.replace('"', '&quot;')
    text = text.replace("'", '&#x27;')
    
    # Remove SQL injection patterns
    sql_patterns = [
        r"(\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|EXEC|EXECUTE)\b)",
        r"(--|#|/\*|\*/)",
        r"(\b(OR|AND)\b\s+\d+\s*=\s*\d+)",
        r"('\s*(OR|AND)\s+')",
        r"(;\s*(DROP|DELETE|INSERT|UPDATE))",
    ]
    
    for pattern in sql_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)
    
    # Remove command injection patterns
    cmd_patterns = [
        r"[;&|`$]",
        r"\$\(",
        r"\{.*\}",
    ]
    
    for pattern in cmd_patterns:
        text = re.sub(pattern, '', text)
    
    return text.strip()


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent path traversal and injection.
    
    Protects against:
    - Path traversal (../)
    - Special characters
    - Reserved names
    """
    if not filename:
        return "unnamed_file"
    
    # Remove path components
    filename = os.path.basename(filename)
    
    # Remove path traversal
    filename = filename.replace('..', '')
    filename = filename.replace('/', '')
    filename = filename.replace('\\', '')
    
    # Remove special characters (keep letters, numbers, dots, hyphens, underscores)
    filename = re.sub(r'[^\w\-.]', '_', filename)
    
    # Remove multiple dots (prevent double extension attacks)
    filename = re.sub(r'\.{2,}', '.', filename)
    
    # Remove leading dots (hidden files)
    filename = filename.lstrip('.')
    
    # Reserved Windows names
    reserved = ['CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4',
                'COM5', 'COM6', 'COM7', 'COM8', 'COM9', 'LPT1', 'LPT2', 'LPT3',
                'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9']
    
    name_without_ext = filename.split('.')[0].upper()
    if name_without_ext in reserved:
        filename = f"file_{filename}"
    
    # Ensure filename is not empty
    if not filename or filename == '.':
        filename = f"file_{secrets.token_hex(4)}"
    
    return filename


# ============ BRUTE FORCE PROTECTION ============


class BruteForceProtection:
    """
    Protects against brute force attacks.
    
    Features:
    - Tracks failed login attempts per IP/username
    - Locks accounts after too many failures
    - Gradual lockout (longer lockout with more failures)
    """
    
    def __init__(
        self,
        max_attempts: int = 5,
        lockout_duration: int = 900,  # 15 minutes
        increment_multiplier: int = 2,
    ):
        self.max_attempts = max_attempts
        self.lockout_duration = lockout_duration
        self.increment_multiplier = increment_multiplier
        
        # Track attempts: {key: {"count": int, "last_attempt": float, "locked_until": float}}
        self.attempts: Dict[str, Dict] = defaultdict(lambda: {
            "count": 0,
            "last_attempt": 0,
            "locked_until": 0,
        })
    
    def _get_key(self, identifier: str, attempt_type: str = "login") -> str:
        """Get storage key for identifier."""
        return f"{attempt_type}:{identifier}"
    
    def record_failure(self, identifier: str, attempt_type: str = "login") -> None:
        """Record a failed attempt."""
        key = self._get_key(identifier, attempt_type)
        now = time.time()
        
        self.attempts[key]["count"] += 1
        self.attempts[key]["last_attempt"] = now
        
        # Calculate lockout duration (increases with more failures)
        failures = self.attempts[key]["count"]
        if failures >= self.max_attempts:
            lockout_seconds = self.lockout_duration * (
                self.increment_multiplier ** (failures - self.max_attempts)
            )
            self.attempts[key]["locked_until"] = now + lockout_seconds
    
    def record_success(self, identifier: str, attempt_type: str = "login") -> None:
        """Record a successful attempt (resets counter)."""
        key = self._get_key(identifier, attempt_type)
        self.attempts[key]["count"] = 0
        self.attempts[key]["locked_until"] = 0
    
    def is_locked(self, identifier: str, attempt_type: str = "login") -> Tuple[bool, Optional[int]]:
        """
        Check if identifier is locked.
        
        Returns:
            (is_locked, seconds_until_unlock)
        """
        key = self._get_key(identifier, attempt_type)
        now = time.time()
        
        locked_until = self.attempts[key]["locked_until"]
        if locked_until > now:
            seconds_left = int(locked_until - now)
            return True, seconds_left
        
        return False, None
    
    def get_remaining_attempts(self, identifier: str, attempt_type: str = "login") -> int:
        """Get remaining attempts before lockout."""
        key = self._get_key(identifier, attempt_type)
        return max(0, self.max_attempts - self.attempts[key]["count"])


# ============ SECURITY HEADERS ============


SECURITY_HEADERS = {
    # Prevent XSS
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "1; mode=block",
    
    # Prevent MIME sniffing
    "X-Download-Options": "noopen",
    
    # HSTS (HTTP Strict Transport Security)
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    
    # Content Security Policy
    "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' data:;",
    
    # Referrer Policy
    "Referrer-Policy": "strict-origin-when-cross-origin",
    
    # Permissions Policy
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    
    # Cache control for sensitive endpoints
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
}


def get_security_headers() -> Dict[str, str]:
    """Get all security headers."""
    return SECURITY_HEADERS.copy()


# ============ AUDIT LOGGING ============


class AuditLogger:
    """
    Logs security-relevant events for auditing.
    
    Events logged:
    - Login attempts (success/failure)
    - Registration attempts
    - Password changes
    - File uploads
    - Rate limit violations
    - Suspicious activity
    """
    
    def __init__(self, log_file: str = "logs/audit.log"):
        self.log_file = log_file
        self.events: List[Dict] = []
        
        # Ensure log directory exists
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    def _write_log(self, event: Dict) -> None:
        """Write event to log file."""
        self.events.append(event)
        
        try:
            with open(self.log_file, "a") as f:
                f.write(f"{event['timestamp']} | {event['event_type']} | "
                       f"{event.get('identifier', 'N/A')} | "
                       f"{event.get('details', '')}\n")
        except Exception:
            pass  # Don't fail if logging fails
    
    def log_login_attempt(
        self,
        username: str,
        ip_address: str,
        success: bool,
        reason: str = "",
    ) -> None:
        """Log a login attempt."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "LOGIN_SUCCESS" if success else "LOGIN_FAILURE",
            "identifier": username,
            "ip_address": ip_address,
            "details": reason,
        }
        self._write_log(event)
    
    def log_registration(
        self,
        username: str,
        ip_address: str,
        success: bool,
        reason: str = "",
    ) -> None:
        """Log a registration attempt."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "REGISTRATION_SUCCESS" if success else "REGISTRATION_FAILURE",
            "identifier": username,
            "ip_address": ip_address,
            "details": reason,
        }
        self._write_log(event)
    
    def log_file_upload(
        self,
        filename: str,
        user_id: str,
        success: bool,
        reason: str = "",
    ) -> None:
        """Log a file upload attempt."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "FILE_UPLOAD_SUCCESS" if success else "FILE_UPLOAD_FAILURE",
            "identifier": user_id,
            "details": f"filename={filename}, reason={reason}",
        }
        self._write_log(event)
    
    def log_rate_limit_violation(
        self,
        ip_address: str,
        endpoint: str,
    ) -> None:
        """Log a rate limit violation."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "RATE_LIMIT_VIOLATION",
            "identifier": ip_address,
            "details": f"endpoint={endpoint}",
        }
        self._write_log(event)
    
    def log_suspicious_activity(
        self,
        activity_type: str,
        ip_address: str,
        details: str,
    ) -> None:
        """Log suspicious activity."""
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": "SUSPICIOUS_ACTIVITY",
            "identifier": ip_address,
            "details": f"type={activity_type}, details={details}",
        }
        self._write_log(event)
    
    def get_recent_events(self, hours: int = 24) -> List[Dict]:
        """Get events from the last N hours."""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return [
            e for e in self.events
            if datetime.fromisoformat(e["timestamp"]) > cutoff
        ]


# ============ CSRF PROTECTION ============


class CSRFProtection:
    """
    CSRF (Cross-Site Request Forgery) protection.
    
    Generates and validates tokens to ensure requests
    come from your own site, not malicious sites.
    """
    
    def __init__(self):
        self.tokens: Dict[str, float] = {}
        self.token_expiry = 3600  # 1 hour
    
    def generate_token(self, session_id: str) -> str:
        """Generate a CSRF token for a session."""
        token = secrets.token_hex(32)
        self.tokens[f"{session_id}:{token}"] = time.time()
        return token
    
    def validate_token(self, session_id: str, token: str) -> bool:
        """Validate a CSRF token."""
        key = f"{session_id}:{token}"
        
        if key not in self.tokens:
            return False
        
        # Check expiry
        created_at = self.tokens[key]
        if time.time() - created_at > self.token_expiry:
            del self.tokens[key]
            return False
        
        # Token is valid, remove it (one-time use)
        del self.tokens[key]
        return True
    
    def cleanup_expired(self) -> int:
        """Remove expired tokens. Returns number removed."""
        now = time.time()
        expired = [
            key for key, created_at in self.tokens.items()
            if now - created_at > self.token_expiry
        ]
        for key in expired:
            del self.tokens[key]
        return len(expired)


# ============ SINGLETON INSTANCES ============


# Global instances
brute_force = BruteForceProtection()
audit_logger = AuditLogger()
csrf_protection = CSRFProtection()


# ============ HELPER FUNCTIONS ============


def get_client_ip(request) -> str:
    """Extract client IP from request."""
    # Check for forwarded IP (behind proxy)
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    
    # Check for real IP
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    # Direct connection
    return request.client.host if request.client else "unknown"


def validate_request_size(content_length: int, max_size: int = 10 * 1024 * 1024) -> bool:
    """Validate request body size (default 10MB)."""
    return content_length <= max_size
