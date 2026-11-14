"""Password, token and account paths in modules/auth.py.

Two real bugs were found and fixed while writing these, both by running the
code rather than reading it:

  1. **bcrypt's 72-byte limit crashed auth.** `hashpw` raises ValueError beyond
     72 bytes, it does not truncate. `RegisterRequest` allows 128 characters,
     so any password of 73+ characters (or 19+ emoji) reached bcrypt and blew
     up. `/auth/register` caught the bare Exception and returned a generic
     **500**, and `User.verify_password` raised on login rather than rejecting
     the attempt.
  2. **A malformed stored hash crashed login.** `bcrypt.checkpw` raises
     ValueError("Invalid salt") when the stored hash is not a bcrypt hash — a
     corrupt row or a half-migrated column. An auth check that raises is a 500;
     an auth check that returns False is a rejected login.

The fix keeps the honest behaviour (reject with a reason) instead of truncating
to 72 bytes, which would silently make a long password and its prefix the same
password.

No live Postgres: `UserManager.db` is a session factory over SQLite in memory,
which is enough because the logic under test is the auth layer, not the driver.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from modules import auth as auth_mod
from modules.auth import (
    JWTManager,
    PasswordManager,
    UserManager,
    password_manager,
)
from modules.database import (
    BCRYPT_MAX_BYTES,
    ApiKey,
    Base,
    User,
    hash_password,
    password_bytes,
    verify_password,
)


# --------------------------------------------------------------------------
# A UserManager backed by SQLite in memory
# --------------------------------------------------------------------------

class _FakeDB:
    """The one method UserManager uses, with the real commit-on-exit semantics.

    DatabaseManager.session() is an async GENERATOR that yields the session
    and commits on clean exit -- not a context manager. A fake that just
    returned the session factory silently discarded every write, which made
    duplicate usernames look acceptable. Mirroring the real shape is what
    makes these tests test the real thing.
    """

    def __init__(self, session_factory):
        self._factory = session_factory

    @asynccontextmanager
    async def session(self):
        async with self._factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise


@pytest.fixture
def user_manager():
    async def build():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        manager = UserManager()
        manager.db = _FakeDB(factory)
        return manager, engine

    manager, engine = asyncio.run(build())
    yield manager
    asyncio.run(engine.dispose())


# --------------------------------------------------------------------------
# 1. Password limits — the bug that made 73 characters a 500
# --------------------------------------------------------------------------

def test_password_at_the_bcrypt_limit_is_accepted():
    pw = "a" * BCRYPT_MAX_BYTES
    hashed = hash_password(pw)
    assert verify_password(pw, hashed) is True


def test_password_one_byte_over_the_limit_is_rejected_with_a_message():
    pw = "a" * (BCRYPT_MAX_BYTES + 1)
    with pytest.raises(ValueError) as info:
        hash_password(pw)
    # The message has to be usable by a human: what is wrong and by how much.
    assert "72" in str(info.value)
    assert "1" in str(info.value)


def test_multibyte_password_is_measured_in_bytes_not_characters():
    """19 emoji is 76 bytes: allowed by max_length=128, rejected by bcrypt."""
    pw = "😀" * 19
    assert len(pw) < 72                 # comfortably under the character limit
    assert len(pw.encode("utf-8")) > 72  # but over the byte limit
    with pytest.raises(ValueError):
        password_bytes(pw)


def test_password_bytes_encodes_utf8():
    assert password_bytes("pässwörd") == "pässwörd".encode("utf-8")


def test_verify_returns_false_for_a_hash_that_is_not_bcrypt():
    """A corrupt row must reject the login, not raise a 500."""
    assert verify_password("anything", "not-a-bcrypt-hash") is False
    assert verify_password("anything", "") is False


def test_verify_returns_false_for_a_password_over_the_limit():
    """Not raising means the login is simply refused."""
    hashed = hash_password("correct")
    assert verify_password("a" * 200, hashed) is False


def test_password_manager_delegates_to_the_single_implementation():
    hashed = password_manager.hash("Str0ng!Password")
    assert password_manager.verify("Str0ng!Password", hashed) is True
    assert password_manager.verify("wrong", hashed) is False
    assert password_manager.verify("anything", "garbage") is False


def test_user_model_verification_never_raises():
    user = User(username="u", email="u@x.com", hashed_password="garbage")
    assert user.verify_password("anything") is False


# --------------------------------------------------------------------------
# 2. Strength checks
# --------------------------------------------------------------------------

@pytest.mark.parametrize("password,valid", [
    # --- length is a hard requirement (this is the fix) ---
    ("aA1!", False),                # 4 chars, otherwise 4/5 -> used to PASS
    ("aA1!bB", False),              # 6 chars, otherwise 4/5 -> used to PASS
    ("Sh0rt!", False),              # 6 chars
    ("abcdefgh", False),            # long enough, one class only
    ("password1", False),           # long enough, no uppercase/special
    ("Password", False),            # long enough, no digit/special
    # --- accepted: length + at least 3 more classes ---
    ("Password1", True),            # length+upper+lower+digit
    ("Str0ng!Password", True),      # all five
    ("NoSpecial1Here", True),       # length+upper+lower+digit = 4/5, by design
])
def test_password_strength(password, valid):
    assert password_manager.check_strength(password)["valid"] is valid


def test_length_cannot_be_traded_against_other_classes():
    """A password may fail at most one of the four character-class checks.

    The rule used to be "4 of 5 checks", which meant the length check was
    just one vote and could be the one that failed. These are the exact inputs
    that used to pass and now must not.
    """
    for pw in ("aA1!", "aA1!bB", "Ab1!"):
        result = password_manager.check_strength(pw)
        assert result["checks"]["length"] is False
        assert result["valid"] is False, f"{pw!r} must not be accepted"
        # The score is still reported, so a UI can explain what is missing.
        assert result["score"] >= 3


def test_strength_reports_which_checks_failed():
    result = password_manager.check_strength("abc")
    assert result["checks"]["length"] is False
    assert result["checks"]["uppercase"] is False
    assert result["score"] < 4


# --------------------------------------------------------------------------
# 3. JWT
# --------------------------------------------------------------------------

def test_jwt_roundtrip_carries_the_subject():
    mgr = JWTManager(secret_key="unit-test-secret", algorithm="HS256")
    token = mgr.create_access_token(data={"sub": "user-1"})
    payload = mgr.verify_token(token)
    assert payload["sub"] == "user-1"
    assert mgr.get_user_id_from_token(token) == "user-1"


def test_jwt_rejects_a_token_signed_with_another_key():
    issuer = JWTManager(secret_key="key-a", algorithm="HS256")
    checker = JWTManager(secret_key="key-b", algorithm="HS256")
    token = issuer.create_access_token(data={"sub": "user-1"})
    assert checker.verify_token(token) is None
    assert checker.get_user_id_from_token(token) is None


def test_jwt_rejects_garbage():
    mgr = JWTManager(secret_key="unit-test-secret", algorithm="HS256")
    assert mgr.verify_token("not.a.token") is None
    assert mgr.verify_token("") is None


def test_access_and_refresh_tokens_are_distinguishable():
    """refresh_token() must not accept an access token as a refresh token."""
    mgr = JWTManager(secret_key="unit-test-secret", algorithm="HS256")
    access = mgr.create_access_token(data={"sub": "user-1"})
    refresh = mgr.create_refresh_token(data={"sub": "user-1"})
    assert mgr.verify_token(access).get("type") != "refresh"
    assert mgr.verify_token(refresh).get("type") == "refresh"


def test_tokens_expire():
    mgr = JWTManager(secret_key="unit-test-secret", algorithm="HS256")
    # An already-expired token: build it with a negative lifetime.
    from datetime import datetime as _dt
    from datetime import timezone as _tz

    import jwt as _jwt

    expired = _jwt.encode(
        {"sub": "user-1", "exp": _dt.now(_tz.utc) - timedelta(seconds=5)},
        "unit-test-secret", algorithm="HS256",
    )
    assert mgr.verify_token(expired) is None


# --------------------------------------------------------------------------
# 4. Registration
# --------------------------------------------------------------------------

def test_register_returns_user_and_tokens(user_manager):
    result = asyncio.run(
        user_manager.register("alice", "alice@example.com", "Str0ng!Password")
    )
    assert result["user"]["username"] == "alice"
    assert result["user"]["email"] == "alice@example.com"
    assert result["access_token"] and result["refresh_token"]
    assert result["token_type"] == "bearer"
    # The stored value is a hash, not the password.
    assert result["user"] != "Str0ng!Password"


def test_register_rejects_a_weak_password(user_manager):
    with pytest.raises(HTTPException) as info:
        asyncio.run(user_manager.register("bob", "bob@example.com", "weak"))
    assert info.value.status_code == 400
    assert "too weak" in info.value.detail.lower()


def test_register_rejects_an_over_long_password_with_400_not_500(user_manager):
    """The regression: this used to be a ValueError on its way to a 500."""
    long_pw = "A1!" + "x" * 100
    with pytest.raises(HTTPException) as info:
        asyncio.run(user_manager.register("carol", "carol@example.com", long_pw))
    assert info.value.status_code == 400
    assert "too long" in info.value.detail.lower()


def test_register_rejects_a_duplicate_username(user_manager):
    asyncio.run(user_manager.register("dave", "dave@example.com", "Str0ng!Password"))
    with pytest.raises(HTTPException) as info:
        asyncio.run(user_manager.register("dave", "other@example.com", "Str0ng!Password"))
    assert info.value.status_code == 400
    assert "already registered" in info.value.detail.lower()


def test_register_rejects_a_duplicate_email(user_manager):
    asyncio.run(user_manager.register("erin", "erin@example.com", "Str0ng!Password"))
    with pytest.raises(HTTPException) as info:
        asyncio.run(user_manager.register("erin2", "erin@example.com", "Str0ng!Password"))
    assert info.value.status_code == 400


# --------------------------------------------------------------------------
# 5. Login
# --------------------------------------------------------------------------

def test_login_succeeds_with_the_right_password(user_manager):
    asyncio.run(user_manager.register("frank", "frank@example.com", "Str0ng!Password"))
    result = asyncio.run(user_manager.login("frank", "Str0ng!Password"))
    assert result["user"]["username"] == "frank"
    assert result["access_token"]


def test_login_accepts_the_email_as_the_identifier(user_manager):
    asyncio.run(user_manager.register("grace", "grace@example.com", "Str0ng!Password"))
    result = asyncio.run(user_manager.login("grace@example.com", "Str0ng!Password"))
    assert result["user"]["username"] == "grace"


def test_login_rejects_a_wrong_password(user_manager):
    asyncio.run(user_manager.register("heidi", "heidi@example.com", "Str0ng!Password"))
    with pytest.raises(HTTPException) as info:
        asyncio.run(user_manager.login("heidi", "WrongPassword1!"))
    assert info.value.status_code == 401


def test_login_rejects_an_unknown_user(user_manager):
    with pytest.raises(HTTPException) as info:
        asyncio.run(user_manager.login("nobody", "Str0ng!Password"))
    assert info.value.status_code == 401


def test_login_does_not_reveal_whether_the_user_exists(user_manager):
    """Same status and detail for 'no such user' and 'wrong password'."""
    asyncio.run(user_manager.register("ivan", "ivan@example.com", "Str0ng!Password"))
    with pytest.raises(HTTPException) as wrong_pw:
        asyncio.run(user_manager.login("ivan", "WrongPassword1!"))
    with pytest.raises(HTTPException) as no_user:
        asyncio.run(user_manager.login("ghost", "WrongPassword1!"))
    assert wrong_pw.value.status_code == no_user.value.status_code == 401
    assert wrong_pw.value.detail == no_user.value.detail


def test_login_rejects_a_disabled_account(user_manager):
    asyncio.run(user_manager.register("judy", "judy@example.com", "Str0ng!Password"))

    async def disable():
        async with user_manager.db.session() as session:
            from sqlalchemy import select
            row = (await session.execute(
                select(User).where(User.username == "judy")
            )).scalar_one()
            row.is_active = False
            await session.commit()

    asyncio.run(disable())
    with pytest.raises(HTTPException) as info:
        asyncio.run(user_manager.login("judy", "Str0ng!Password"))
    assert info.value.status_code == 403


# --------------------------------------------------------------------------
# 6. Refresh
# --------------------------------------------------------------------------

def test_refresh_issues_a_new_access_token(user_manager):
    result = asyncio.run(
        user_manager.register("ken", "ken@example.com", "Str0ng!Password")
    )
    refreshed = asyncio.run(user_manager.refresh_token(result["refresh_token"]))
    assert refreshed["access_token"]
    assert refreshed["token_type"] == "bearer"
    # The new access token is usable and names the same user.
    assert auth_mod.jwt_manager.get_user_id_from_token(
        refreshed["access_token"]
    ) is not None


def test_refresh_rejects_an_access_token():
    """An access token must not be usable as a refresh token."""
    access = auth_mod.jwt_manager.create_access_token(data={"sub": "user-1"})
    with pytest.raises(HTTPException) as info:
        asyncio.run(auth_mod.user_manager.refresh_token(access))
    assert info.value.status_code == 401


def test_refresh_rejects_a_garbage_token():
    with pytest.raises(HTTPException) as info:
        asyncio.run(auth_mod.user_manager.refresh_token("not.a.token"))
    assert info.value.status_code == 401


# --------------------------------------------------------------------------
# 7. Lookups
# --------------------------------------------------------------------------

def test_get_user_returns_the_profile(user_manager):
    created = asyncio.run(
        user_manager.register("laura", "laura@example.com", "Str0ng!Password")
    )
    user_id = created["user"]["id"]

    # get_user resolves the db through the module-level singleton; point that
    # at the same in-memory engine for this test.
    import modules.database as db_mod
    original = db_mod.get_db_manager
    db_mod.get_db_manager = lambda: user_manager.db
    try:
        profile = asyncio.run(user_manager.get_user(user_id))
    finally:
        db_mod.get_db_manager = original

    assert profile["username"] == "laura"
    assert profile["is_active"] is True


def test_get_user_returns_none_for_a_malformed_id(user_manager):
    """A malformed `sub` must be a 404, not a 500 from uuid.UUID()."""
    assert asyncio.run(user_manager.get_user("not-a-uuid")) is None
    assert asyncio.run(user_manager.get_user("")) is None
    assert asyncio.run(user_manager.get_user(None)) is None


# --------------------------------------------------------------------------
# 8. API keys
# --------------------------------------------------------------------------

def _with_db(manager):
    """Point modules.database.get_db_manager at this manager's fake DB."""
    import modules.database as db_mod
    original = db_mod.get_db_manager
    db_mod.get_db_manager = lambda: manager.db
    return original


def test_create_and_validate_an_api_key(user_manager):
    created = asyncio.run(
        user_manager.register("mallory", "mallory@example.com", "Str0ng!Password")
    )
    user_id = created["user"]["id"]

    original = _with_db(user_manager)
    try:
        key = asyncio.run(user_manager.create_api_key(user_id, "ci"))
        assert key["key"].startswith("sk-")
        info = asyncio.run(user_manager.validate_api_key(key["key"]))
    finally:
        import modules.database as db_mod
        db_mod.get_db_manager = original

    assert info["username"] == "mallory"
    assert info["api_key_name"] == "ci"
    assert info["user_id"] == user_id


def test_validate_api_key_rejects_an_unknown_key(user_manager):
    original = _with_db(user_manager)
    try:
        assert asyncio.run(user_manager.validate_api_key("sk-does-not-exist")) is None
    finally:
        import modules.database as db_mod
        db_mod.get_db_manager = original


def test_validate_api_key_rejects_an_expired_key(user_manager):
    created = asyncio.run(
        user_manager.register("nina", "nina@example.com", "Str0ng!Password")
    )
    user_id = created["user"]["id"]

    original = _with_db(user_manager)
    try:
        key = asyncio.run(user_manager.create_api_key(user_id, "old"))
        # Backdate the expiry: the key was valid, and has lapsed.
        async def expire():
            async with user_manager.db.session() as session:
                from sqlalchemy import select
                row = (await session.execute(
                    select(ApiKey).where(ApiKey.key == key["key"])
                )).scalar_one()
                row.expires_at = datetime.now(timezone.utc).replace(
                    tzinfo=None
                ) - timedelta(days=1)
                await session.commit()

        asyncio.run(expire())
        assert asyncio.run(user_manager.validate_api_key(key["key"])) is None
    finally:
        import modules.database as db_mod
        db_mod.get_db_manager = original


def test_validate_api_key_rejects_a_disabled_users_key(user_manager):
    created = asyncio.run(
        user_manager.register("oscar", "oscar@example.com", "Str0ng!Password")
    )
    user_id = created["user"]["id"]

    original = _with_db(user_manager)
    try:
        key = asyncio.run(user_manager.create_api_key(user_id, "k"))

        async def disable():
            async with user_manager.db.session() as session:
                from sqlalchemy import select
                row = (await session.execute(
                    select(User).where(User.id == uuid.UUID(user_id))
                )).scalar_one()
                row.is_active = False
                await session.commit()

        asyncio.run(disable())
        assert asyncio.run(user_manager.validate_api_key(key["key"])) is None
    finally:
        import modules.database as db_mod
        db_mod.get_db_manager = original


# --------------------------------------------------------------------------
# 9. The FastAPI dependencies — what every protected route actually calls
# --------------------------------------------------------------------------

def _creds(token):
    from fastapi.security import HTTPAuthorizationCredentials
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_get_current_user_returns_the_profile_for_a_valid_token():
    from modules import auth as a

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        original_db = a.user_manager.db
        a.user_manager.db = _FakeDB(factory)
        import modules.database as _dbm
        _orig_get = _dbm.get_db_manager
        _dbm.get_db_manager = lambda: a.user_manager.db
        try:
            created = await a.user_manager.register(
                "pat", "pat@example.com", "Str0ng!Password"
            )
            token = a.jwt_manager.create_access_token(
                data={"sub": created["user"]["id"]}
            )
            return await a.get_current_user(None, _creds(token))
        finally:
            a.user_manager.db = original_db
            _dbm.get_db_manager = _orig_get
            await engine.dispose()

    user = asyncio.run(scenario())
    assert user["username"] == "pat"


def test_get_current_user_rejects_a_bad_token():
    from modules import auth as a

    with pytest.raises(HTTPException) as info:
        asyncio.run(a.get_current_user(None, _creds("not.a.token")))
    assert info.value.status_code == 401


def test_get_current_user_rejects_missing_credentials():
    from modules import auth as a

    with pytest.raises(HTTPException) as info:
        asyncio.run(a.get_current_user(None, None))
    assert info.value.status_code == 401


def test_get_current_user_rejects_a_token_for_a_deleted_user():
    """Valid signature, no such user -> 401, not a 500."""
    from modules import auth as a

    token = a.jwt_manager.create_access_token(
        data={"sub": str(uuid.uuid4())}
    )

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        original_db = a.user_manager.db
        a.user_manager.db = _FakeDB(factory)
        import modules.database as _dbm
        _orig_get = _dbm.get_db_manager
        _dbm.get_db_manager = lambda: a.user_manager.db
        try:
            return await a.get_current_user(None, _creds(token))
        finally:
            a.user_manager.db = original_db
            _dbm.get_db_manager = _orig_get
            await engine.dispose()

    with pytest.raises(HTTPException) as info:
        asyncio.run(scenario())
    assert info.value.status_code == 401


def test_get_current_user_rejects_a_token_with_no_subject():
    from modules import auth as a

    token = a.jwt_manager.create_access_token(data={"not_sub": "x"})
    with pytest.raises(HTTPException) as info:
        asyncio.run(a.get_current_user(None, _creds(token)))
    assert info.value.status_code == 401


def test_require_admin_allows_an_admin():
    from modules import auth as a
    assert asyncio.run(a.require_admin({"username": "x", "is_admin": True}))["username"] == "x"


def test_require_admin_rejects_a_non_admin():
    from modules import auth as a
    with pytest.raises(HTTPException) as info:
        asyncio.run(a.require_admin({"username": "x", "is_admin": False}))
    assert info.value.status_code == 403


def test_require_admin_rejects_a_profile_without_the_flag():
    """A dict missing 'is_admin' must be refused, not KeyError."""
    from modules import auth as a
    with pytest.raises(HTTPException) as info:
        asyncio.run(a.require_admin({"username": "x"}))
    assert info.value.status_code == 403


def test_authenticate_user_prefers_a_valid_jwt():
    from modules import auth as a

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        original_db = a.user_manager.db
        a.user_manager.db = _FakeDB(factory)
        import modules.database as _dbm
        _orig_get = _dbm.get_db_manager
        _dbm.get_db_manager = lambda: a.user_manager.db
        try:
            created = await a.user_manager.register(
                "quinn", "quinn@example.com", "Str0ng!Password"
            )
            token = a.jwt_manager.create_access_token(
                data={"sub": created["user"]["id"]}
            )
            return await a.authenticate_user(_creds(token), None)
        finally:
            a.user_manager.db = original_db
            _dbm.get_db_manager = _orig_get
            await engine.dispose()

    assert asyncio.run(scenario())["username"] == "quinn"


def test_authenticate_user_rejects_when_both_methods_fail():
    from modules import auth as a
    with pytest.raises(HTTPException) as info:
        asyncio.run(a.authenticate_user(None, None))
    assert info.value.status_code == 401


def test_get_current_user_from_api_key_returns_none_without_a_key():
    from modules import auth as a
    assert asyncio.run(a.get_current_user_from_api_key(None)) is None


def test_get_current_user_from_api_key_returns_none_for_an_unknown_key():
    from modules import auth as a

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        original_db = a.user_manager.db
        a.user_manager.db = _FakeDB(factory)
        import modules.database as _dbm
        _orig_get = _dbm.get_db_manager
        _dbm.get_db_manager = lambda: a.user_manager.db
        try:
            return await a.get_current_user_from_api_key("sk-nope")
        finally:
            a.user_manager.db = original_db
            _dbm.get_db_manager = _orig_get
            await engine.dispose()

    assert asyncio.run(scenario()) is None


# --------------------------------------------------------------------------
# 10. Token type confusion — a refresh token must not authenticate a request
# --------------------------------------------------------------------------

def test_authenticate_user_refuses_a_refresh_token():
    """A 7-day refresh token must not work as a 30-minute access token.

    Regression: `authenticate_user` verified the signature and read `sub`
    without checking `type`, so a stolen refresh token bought 7 days of
    access. `get_current_user` had the check; this path did not.
    """
    from modules import auth as a

    refresh = a.jwt_manager.create_refresh_token(data={"sub": str(uuid.uuid4())})
    with pytest.raises(HTTPException) as info:
        asyncio.run(a.authenticate_user(_creds(refresh), None))
    assert info.value.status_code == 401


def test_get_current_user_refuses_a_refresh_token():
    from modules import auth as a

    refresh = a.jwt_manager.create_refresh_token(data={"sub": str(uuid.uuid4())})
    with pytest.raises(HTTPException) as info:
        asyncio.run(a.get_current_user(None, _creds(refresh)))
    assert info.value.status_code == 401


def test_an_access_token_still_authenticates():
    """The type check must not lock out the token it is meant to accept."""
    from modules import auth as a

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        original_db = a.user_manager.db
        a.user_manager.db = _FakeDB(factory)
        import modules.database as _dbm
        _orig_get = _dbm.get_db_manager
        _dbm.get_db_manager = lambda: a.user_manager.db
        try:
            created = await a.user_manager.register(
                "rita", "rita@example.com", "Str0ng!Password"
            )
            return await a.authenticate_user(
                _creds(created["access_token"]), None
            )
        finally:
            a.user_manager.db = original_db
            _dbm.get_db_manager = _orig_get
            await engine.dispose()

    assert asyncio.run(scenario())["username"] == "rita"


# --------------------------------------------------------------------------
# 11. The cookie path — browsers carry the token in an HttpOnly cookie
# --------------------------------------------------------------------------

class _FakeRequest:
    def __init__(self, cookies):
        self.cookies = cookies


def test_get_current_user_reads_the_httponly_cookie():
    """A browser sends no Authorization header; the token is in the cookie."""
    from modules import auth as a

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        original_db = a.user_manager.db
        a.user_manager.db = _FakeDB(factory)
        import modules.database as _dbm
        _orig_get = _dbm.get_db_manager
        _dbm.get_db_manager = lambda: a.user_manager.db
        try:
            created = await a.user_manager.register(
                "sam", "sam@example.com", "Str0ng!Password"
            )
            request = _FakeRequest({"access_token": created["access_token"]})
            return await a.get_current_user(request, None)
        finally:
            a.user_manager.db = original_db
            _dbm.get_db_manager = _orig_get
            await engine.dispose()

    assert asyncio.run(scenario())["username"] == "sam"


def test_get_current_user_rejects_a_refresh_token_in_the_cookie():
    """The cookie path must apply the same type check as the header path."""
    from modules import auth as a

    refresh = a.jwt_manager.create_refresh_token(data={"sub": str(uuid.uuid4())})
    with pytest.raises(HTTPException) as info:
        asyncio.run(a.get_current_user(_FakeRequest({"access_token": refresh}), None))
    assert info.value.status_code == 401
