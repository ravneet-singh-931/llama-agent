"""The security primitives in `modules/security_hardening.py`.

`api.py` coverage sat at 74% and was declared done. This file exists because the
module that *makes the security decisions* was at **67%**, which is a worse place
to stop: `api.py` was mostly print statements, this one is mostly defences.

Three things are checked here that nothing checked before:

1. **Path traversal.** `sanitize_filename` is the single function standing
   between a user-supplied upload name and the filesystem. It is reached on every
   upload (`api.py` 2465), and it had no test of its own.
2. **Filename length.** It has no length bound - measured, not assumed: a
   300-character name uploads successfully. That is correct on ext4 and wrong on
   a 255-**byte** filesystem, and the failure would arrive as a 500 during an
   upload rather than as a rejection.
3. **`CSRFProtection` is dead code.** Not imported by `api.py`, not called
   anywhere, not tested. Cookie CSRF is actually handled by `samesite="lax"`
   (`api.py` 823, 967-984). The class is tested here so that it cannot quietly
   rot into something someone later wires up *believing it is already proven*.

The brute-force and audit-log paths are exercised against a fake Redis, because
that is the branch a multi-replica deployment uses and the one no test reached.
"""
import time

import pytest

from modules import security_hardening as sh


# --------------------------------------------------------------- filenames --

@pytest.mark.parametrize("hostile", [
    "../../etc/passwd",
    "..\\..\\windows\\system32\\cmd.exe",
    "....//....//etc/passwd",
    "..%2f..%2fetc/passwd",
    "..\\..\\..\\..\\etc\\shadow",
    "/etc/passwd",
    ".htaccess",
    "..",
    ".",
])
def test_path_traversal_never_survives_sanitisation(hostile):
    """No separator, no `..`, no leading dot may come out the other side.

    Checked on the *output* rather than by matching a known string, because the
    interesting question is what reaches the filesystem, not what the input
    looked like.
    """
    out = sh.sanitize_filename(hostile)
    assert "/" not in out, f"{hostile!r} kept a forward slash: {out!r}"
    assert "\\" not in out, f"{hostile!r} kept a backslash: {out!r}"
    assert ".." not in out, f"{hostile!r} kept dot-dot: {out!r}"
    assert not out.startswith("."), f"{hostile!r} kept a leading dot: {out!r}"
    assert out, "sanitisation produced an empty name"


def test_hidden_files_cannot_be_written():
    """`.htaccess` must not survive as a dotfile.

    Found by sabotage: six of this function's seven defensive lines can be
    removed without breaking any test, because they overlap. `lstrip('.')` is
    the one line that is load-bearing for this case - remove it and a hidden
    file becomes writable - so it gets its own test.
    """
    out = sh.sanitize_filename(".htaccess")
    assert out == "htaccess", f"a hidden filename survived: {out!r}"


def test_an_empty_name_gets_a_placeholder():
    assert sh.sanitize_filename("") == "unnamed_file"
    assert sh.sanitize_filename(None) == "unnamed_file"


def test_reserved_windows_names_are_prefixed():
    """`CON` and `NUL` are unusable as filenames on Windows."""
    assert sh.sanitize_filename("CON") == "file_CON"
    assert sh.sanitize_filename("nul.txt") == "file_nul.txt"


def test_a_double_extension_is_preserved_not_stripped():
    """Documenting a deliberate limit: `shell.php.jpg` stays `shell.php.jpg`.

    Stripping it here would be the wrong place to fight double extensions - the
    decision that matters is the extension allowlist in
    `modules/security.py::validate_file_upload`. Recorded so nobody adds a
    "fix" here that gives a false sense of safety.
    """
    assert sh.sanitize_filename("shell.php.jpg") == "shell.php.jpg"


def test_long_filenames_are_not_truncated():
    """KNOWN GAP, measured. A 300-character name passes through untouched.

    Most filesystems cap a single name at 255 **bytes**, and a multibyte
    character costs more than one byte, so a name well under 300 characters can
    still exceed the limit. Today the upload succeeds on ext4 (measured: 200,
    "Added 1 chunks") and would raise `OSError: File name too long` - a 500
    during an upload - on a stricter filesystem.

    This test asserts the CURRENT behaviour so the gap cannot change silently.
    When a length bound is added, this test fails and should be inverted: that
    failure is the fix arriving.
    """
    long_name = "a" * 300 + ".txt"
    out = sh.sanitize_filename(long_name)
    assert len(out) > 255, (
        "filenames are now length-bounded - good. Invert this test and update "
        "SECURITY_FINDINGS.md, the gap it records is closed."
    )


# ------------------------------------------------------------ brute force --

class _FakeRedis:
    """Just the hash operations `BruteForceProtection` uses."""

    def __init__(self):
        self.hash = {}
        self.fail_writes = False

    def hget(self, name, key):
        return self.hash.get(key)

    def hset(self, name, key, value):
        if self.fail_writes:
            raise ConnectionError("write failed")
        self.hash[key] = value
        return 1


def test_lockout_escalates_geometrically(monkeypatch):
    """Each failure past the limit doubles the lockout.

    `increment_multiplier=2` with `max_attempts=3`: the third failure locks for
    `lockout_duration`, the fourth for twice that, the fifth for four times.
    A flat lockout would let an attacker pace attempts forever.
    """
    bf = sh.BruteForceProtection(max_attempts=3, lockout_duration=100)
    monkeypatch.setattr(bf, "_store", lambda k, d: bf.attempts.__setitem__(k, d))

    bf.record_failure("attacker")
    bf.record_failure("attacker")
    assert not bf.is_locked("attacker")[0], "locked before the limit was reached"

    bf.record_failure("attacker")
    locked, first = bf.is_locked("attacker")
    assert locked and 95 <= first <= 100

    bf.record_failure("attacker")
    _, second = bf.is_locked("attacker")
    assert second > first, f"lockout did not escalate: {first} -> {second}"


def test_a_successful_login_resets_the_counter():
    bf = sh.BruteForceProtection(max_attempts=3)
    for _ in range(2):
        bf.record_failure("user")
    assert bf.get_remaining_attempts("user") == 1

    bf.record_success("user")
    assert bf.get_remaining_attempts("user") == 3
    assert not bf.is_locked("user")[0]


def test_remaining_attempts_never_goes_negative():
    bf = sh.BruteForceProtection(max_attempts=2)
    for _ in range(5):
        bf.record_failure("user")
    assert bf.get_remaining_attempts("user") == 0


def test_lockout_is_shared_between_replicas(monkeypatch):
    """The whole point of the Redis branch: two pods, one counter.

    Without this, an attacker simply retries against a different pod and the
    per-process counter never reaches the limit.
    """
    fake = _FakeRedis()
    monkeypatch.setattr(sh, "_get_redis", lambda: fake)

    pod_a = sh.BruteForceProtection(max_attempts=3)
    for _ in range(3):
        pod_a.record_failure("victim")

    pod_b = sh.BruteForceProtection(max_attempts=3)   # empty local mirror
    locked, seconds = pod_b.is_locked("victim")
    assert locked, "a second replica did not see the lockout - protection bypassed"
    assert seconds and seconds > 0


def test_a_failed_redis_read_falls_back_to_the_local_mirror(monkeypatch):
    """Redis errors must degrade, never propagate into a login request."""
    class Broken:
        def hget(self, *a):
            raise ConnectionError("redis read down")

        def hset(self, *a):
            raise ConnectionError("redis write down")

    monkeypatch.setattr(sh, "_get_redis", lambda: Broken())
    bf = sh.BruteForceProtection(max_attempts=2)
    for _ in range(2):
        bf.record_failure("user")          # must not raise
    assert bf.is_locked("user")[0], "lockout lost when Redis failed"


def test_redis_url_set_but_unreachable_still_locks_out(monkeypatch):
    """The realistic degraded deployment, not a synthetic exception."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")  # nothing there
    monkeypatch.setattr(sh, "_REDIS_CLIENT", None)
    bf = sh.BruteForceProtection(max_attempts=3)
    for _ in range(3):
        bf.record_failure("victim")
    assert bf.is_locked("victim")[0]


# --------------------------------------------------------------- audit log --

def test_audit_events_can_be_queried_by_window():
    """`get_recent_events(hours=N)` must exclude anything older than the window.

    Uses the real logger API (`log_login_attempt`), not a name I guessed - the
    first version of this test called `log_event` and `_trim`, neither of which
    exists.
    """
    log = sh.AuditLogger()
    log._write_log = lambda event: log.events.append(event)   # no disk, no Redis

    log.log_login_attempt("alice", "1.2.3.4", success=False, reason="wrong password")
    log.log_login_attempt("alice", "1.2.3.4", success=True)

    assert len(log.get_recent_events(hours=1)) == 2
    assert log.events[0]["event_type"] == "LOGIN_FAILURE"
    assert log.events[1]["event_type"] == "LOGIN_SUCCESS"

    log.events[0]["timestamp"] = "2020-01-01T00:00:00+00:00"
    assert len(log.get_recent_events(hours=1)) == 1, (
        "an event from 2020 appeared inside a one-hour window"
    )


def test_the_audit_log_is_bounded(monkeypatch, tmp_path):
    """A bounded log is the difference between a security trail and a leak.

    Runs the REAL `_write_log`, stubbing only its two side effects (the file and
    Redis). The first version of this test replaced `_write_log` itself, so it
    asserted that my stub trimmed a list - it could never have failed for the
    real code.
    """
    monkeypatch.setattr(sh, "_get_redis", lambda: None)
    log = sh.AuditLogger(log_file=str(tmp_path / "audit.log"))

    for i in range(sh.AUDIT_MAX_EVENTS + 10):
        log.log_login_attempt(f"u{i}", "1.2.3.4", success=False)

    assert len(log.events) == sh.AUDIT_MAX_EVENTS, (
        f"in-memory audit mirror grew to {len(log.events)}"
    )
    # The oldest ten were dropped, the newest kept.
    assert log.events[-1]["identifier"] == f"u{sh.AUDIT_MAX_EVENTS + 9}"


def test_legally_sensitive_identifiers_are_redacted_out_of_the_audit_trail(
        monkeypatch, tmp_path):
    """SSNs and card numbers must not outlive the request in a log line.

    Deliberately does NOT assert on email addresses. `modules/pii.py` states the
    scope: emails, names, addresses and phone numbers are LEFT ALONE on purpose,
    because redacting them destroys the usefulness of a log and a regex cannot
    tell a name from a noun. My first version of this test asserted that an email
    was gone and failed - the code was right and the test was wrong. Recorded
    here so the next person does not "fix" the redactor.
    """
    monkeypatch.setattr(sh, "_get_redis", lambda: None)
    log = sh.AuditLogger(log_file=str(tmp_path / "audit.log"))

    log.log_login_attempt(
        "alice", "1.2.3.4", success=False,
        reason="ssn 123-45-6789 card 4111 1111 1111 1111 for bob@example.com",
    )

    stored = log.events[-1]["details"]
    assert "123-45-6789" not in stored, f"an SSN reached the audit trail: {stored!r}"
    assert "4111 1111 1111 1111" not in stored, (
        f"a card number reached the audit trail: {stored!r}"
    )
    assert "[REDACTED]" in stored


def test_an_ordinary_long_number_is_not_mangled(monkeypatch, tmp_path):
    """The Luhn check is what separates a card from any 16-digit id."""
    monkeypatch.setattr(sh, "_get_redis", lambda: None)
    log = sh.AuditLogger(log_file=str(tmp_path / "audit.log"))

    log.log_login_attempt("alice", "1.2.3.4", success=False,
                          reason="order 1234567890123456 shipped")

    stored = log.events[-1]["details"]
    assert "1234567890123456" in stored, (
        "a non-Luhn number was redacted; ordinary IDs will be mangled"
    )


# -------------------------------------------------------------------- CSRF --

def test_csrf_tokens_are_single_use():
    csrf = sh.CSRFProtection()
    token = csrf.generate_token("session-1")
    assert csrf.validate_token("session-1", token) is True
    assert csrf.validate_token("session-1", token) is False, "token was replayable"


def test_a_csrf_token_is_bound_to_its_session():
    csrf = sh.CSRFProtection()
    token = csrf.generate_token("session-1")
    assert csrf.validate_token("session-2", token) is False, (
        "a token from one session was accepted for another"
    )


def test_expired_csrf_tokens_are_refused_and_cleaned():
    csrf = sh.CSRFProtection()
    csrf.token_expiry = 0
    token = csrf.generate_token("session-1")
    time.sleep(0.01)
    assert csrf.validate_token("session-1", token) is False
    assert not csrf.tokens, "the expired token was left in the store"


def test_csrf_cleanup_reports_what_it_removed():
    csrf = sh.CSRFProtection()
    csrf.token_expiry = 0
    for i in range(3):
        csrf.generate_token(f"s{i}")
    time.sleep(0.01)
    assert csrf.cleanup_expired() == 3
    assert csrf.cleanup_expired() == 0


def test_csrf_is_not_wired_into_the_app():
    """A guard on a real product decision, recorded rather than assumed.

    `CSRFProtection` is unused. Cookie CSRF is handled by `samesite="lax"` on
    every auth cookie (`api.py` 823, 967-984), which is the standard defence for
    a same-origin app. If someone wires this class in later, this test fails and
    that is the signal to give it real coverage against the request path.
    """
    import pathlib
    api_src = pathlib.Path("api.py").read_text()
    assert "csrf_protection" not in api_src and "CSRFProtection" not in api_src, (
        "CSRFProtection is now used by api.py - replace this test with real "
        "coverage of the request path"
    )
