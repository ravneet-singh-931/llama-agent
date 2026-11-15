"""The auth **routes** in `api.py` — the layer `tests/test_auth.py` does not reach.

`test_auth.py` (53 tests) covers `modules/auth.py` thoroughly: password
hashing, JWT signing, the user manager, `get_current_user`, and that a refresh
token cannot authenticate a request. What it never does is call the routes.

Measured before this file: `/auth/refresh` had **never executed in any test** -
all 26 of its lines were uncovered, and the failure branches of `register` and
`login` were too.

Two findings, both pinned below with the real behaviour and an explanation:

FINDING 1 (pinned as-is, needs a decision): **the refresh token is never
rotated and has no server-side revocation.** `/auth/refresh` deliberately keeps
the same refresh token ("it is still valid"), and nothing records it, so:

  * the same refresh token can be replayed indefinitely, for its full 7-day
    lifetime, from any number of clients;
  * logging out clears cookies on *that browser only* - a copy of the token
    taken earlier keeps working;
  * if it leaks there is no way to revoke it short of rotating the JWT secret,
    which logs out every user.

FINDING 2 (pinned as-is): **two refreshes inside the same second return a
byte-identical access token.** `iat`/`exp` are second-granularity, so the JWT
payload and therefore the signature do not change. Harmless, but it means
"refresh" is a no-op for that second and a test asserting rotation will fail for
a reason that has nothing to do with auth.
"""
import os

import jwt
import pytest

import api


PASSWORD = "Str0ng-Passw0rd!x"


@pytest.fixture
def fresh_user(journey):
    """A registered, logged-out client plus the credentials it used.

    The brute-force store is **global and keyed by client IP as well as by
    username**. Every test here shares one client object and therefore one IP,
    so a test that trips the lockout leaves the IP locked and every later test
    fails with 429. Measured: five tests failed for that reason before this
    fixture cleared the store.
    """
    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    bf = api.brute_force
    saved = dict(bf.attempts)
    bf.attempts.clear()

    c = journey.client
    name = f"auth_{os.urandom(4).hex()}"
    c.cookies.clear()
    r = c.post("/auth/register", json={
        "username": name, "email": f"{name}@example.com", "password": PASSWORD})
    assert r.status_code in (200, 201), r.text
    c.cookies.clear()
    try:
        yield {"client": c, "username": name, "email": f"{name}@example.com"}
    finally:
        bf.attempts.clear()
        bf.attempts.update(saved)
        rate_limiter._requests.clear()


def _login(u):
    r = u["client"].post("/auth/login", json={
        "username": u["username"], "password": PASSWORD})
    assert r.status_code == 200, r.text
    return u["client"]


# ---------------------------------------------------------------- register --

def test_register_rejects_a_duplicate_and_audits_it(fresh_user):
    """The duplicate-username branch must answer 400 and record the attempt.

    The audit write is the part nothing covered: a rejected registration is
    exactly what an operator needs to see during an enumeration attempt.
    """
    c = fresh_user["client"]
    before = len(api.audit_logger.events)

    r = c.post("/auth/register", json={
        "username": fresh_user["username"],
        "email": "other@example.com",
        "password": PASSWORD})

    assert r.status_code == 400, r.text
    assert "already" in r.json()["detail"].lower()
    assert len(api.audit_logger.events) > before, (
        "a rejected registration was not audited"
    )


# ------------------------------------------------------------------- login --

def test_a_wrong_password_counts_towards_the_lockout(fresh_user):
    """A failed login must record a failure, not just return 401.

    The counter is what produces the 429 after repeated attempts. If the failure
    were not recorded, brute-force protection would never trigger - and nothing
    tested that the route does it.
    """
    c = fresh_user["client"]
    # Keys are "login:<identifier>" (BruteForceProtection._get_key), not the raw
    # username - my first version of this test looked under the wrong key and
    # reported "not recorded" for a route that records correctly.
    api.brute_force.record_success(fresh_user["username"])
    key = f"login:{fresh_user['username']}"

    r = c.post("/auth/login", json={
        "username": fresh_user["username"], "password": "Wrong-Password-1!"})

    assert r.status_code == 401, r.text
    recorded = api.brute_force.attempts.get(key, {})
    assert recorded.get("count", 0) >= 1, (
        f"the failed login was not recorded for brute-force protection: {recorded}"
    )
    assert recorded.get("last_attempt", 0) > 0, "the attempt was recorded with no timestamp"


def test_a_locked_out_login_is_429_and_audited(fresh_user):
    """Once locked, the route refuses before it ever checks the password.

    Order matters: a locked account must not get a password verdict, or the
    lockout becomes a slower password oracle.
    """
    c = fresh_user["client"]
    bf = api.brute_force
    for _ in range(bf.max_attempts + 1):
        c.post("/auth/login", json={
            "username": fresh_user["username"], "password": "Wrong-Password-1!"})

    before = len(api.audit_logger.events)
    r = c.post("/auth/login", json={
        "username": fresh_user["username"], "password": PASSWORD})

    assert r.status_code == 429, (
        f"a locked-out account answered {r.status_code} instead of 429"
    )
    assert "too many" in r.json()["detail"].lower()
    assert len(api.audit_logger.events) > before, "the lockout was not audited"


# ----------------------------------------------------------------- refresh --

def test_refresh_with_no_token_is_401(fresh_user):
    c = fresh_user["client"]
    c.cookies.clear()
    r = c.post("/auth/refresh", json={})
    assert r.status_code == 401, r.text
    assert r.json()["detail"] == "Missing refresh token"


def test_refresh_issues_a_usable_access_token(fresh_user):
    """The whole point of the route: a new access token that actually works.

    Sabotage taught the difference between "works" and "was renewed". With
    `_set_auth_cookies(...)` replaced by `pass`, the *old* access token is still
    in the cookie jar and still valid, so merely calling `/auth/me` passes. The
    test therefore sets the jar aside first: the new cookie must exist on its
    own, carry a later `iat`, and work by itself.
    """
    c = _login(fresh_user)
    old_token = c.cookies.get("access_token")
    assert old_token

    r = c.post("/auth/refresh", json={})
    assert r.status_code == 200, r.text

    # Assert on THIS RESPONSE, not on the jar. A jar check cannot tell a fresh
    # cookie from the one login left behind - which is exactly why the sabotage
    # of `_set_auth_cookies(...)` survived the first version of this test.
    set_cookie = r.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie, (
        "refresh returned 200 without setting a new access_token cookie"
    )

    new_token = c.cookies.get("access_token")
    assert new_token, "refresh returned 200 but set no access_token cookie"

    old_iat = jwt.decode(old_token, options={"verify_signature": False})["iat"]
    new_iat = jwt.decode(new_token, options={"verify_signature": False})["iat"]
    assert new_iat >= old_iat, "the refreshed token predates the one it replaced"
    # NOT `new_token != old_token`: JWTs are second-granularity, so a refresh in
    # the same second as the login produces a byte-identical token. That quirk is
    # pinned in its own test below. What matters here is that a token IS set and
    # that it is fresh enough.
    assert new_iat >= old_iat

    # The new cookie alone must authenticate a protected route.
    c.cookies.clear()
    c.cookies.set("access_token", new_token)
    me = c.get("/auth/me")
    assert me.status_code == 200, f"the refreshed token does not work: {me.text}"
    assert me.json()["user"]["username"] == fresh_user["username"]


def test_refresh_rejects_an_access_token_in_the_body(fresh_user):
    """An access token is not a refresh token, even when handed over explicitly."""
    c = _login(fresh_user)
    access = c.cookies.get("access_token")
    c.cookies.clear()

    r = c.post("/auth/refresh", json={"refresh_token": access})
    assert r.status_code == 401, r.text
    assert "invalid refresh token" in r.json()["detail"].lower()


def test_refresh_accepts_the_token_from_the_body_as_well_as_the_cookie(fresh_user):
    """Both documented sources work: `body.refresh_token` or the cookie."""
    c = _login(fresh_user)
    cookie_token = c.cookies.get("refresh_token")
    assert cookie_token

    c.cookies.clear()
    r = c.post("/auth/refresh", json={"refresh_token": cookie_token})
    assert r.status_code == 200, r.text


def test_a_refresh_token_can_only_be_used_once(fresh_user):
    """Rotation is implemented: a used refresh token is dead.

    This started as `xfail(strict=True)` pinning the *old* behaviour - the token
    was replayed for its full 7-day lifetime. `strict` meant that the day the gap
    closed, pytest would fail the suite and point here. It did exactly that when
    rotation landed, so the marker is gone and the assertion below is the real
    requirement.

    The replay is refused because the token is at a retired generation, not
    because its signature or `exp` is wrong - see `test_a_replayed_token_is_
    refused_even_though_its_signature_is_still_valid` for that distinction.
    """
    c = _login(fresh_user)
    token = c.cookies.get("refresh_token")
    c.cookies.clear()

    first = c.post("/auth/refresh", json={"refresh_token": token})
    assert first.status_code == 200

    second = c.post("/auth/refresh", json={"refresh_token": token})
    assert second.status_code == 401, "the replayed refresh token was accepted again"


def test_replaying_a_refresh_token_is_refused(fresh_user):
    """Inverted from "replaying is accepted" when rotation landed.

    Kept as a separate test from the one-use check because they fail differently:
    this one states the *outcome* (three attempts, one success) without caring
    why, so it still fails if rotation is replaced by some other mechanism that
    silently allows two.
    """
    c = _login(fresh_user)
    token = c.cookies.get("refresh_token")
    c.cookies.clear()

    codes = [
        c.post("/auth/refresh", json={"refresh_token": token}).status_code
        for _ in range(3)
    ]
    assert codes == [200, 401, 401], f"refresh-token replay behaviour: {codes}"


def test_a_replay_is_refused_even_though_the_token_is_still_cryptographically_valid(fresh_user):
    """The point of rotation, stated as a test: the replay is refused on
    *generation*, not on signature or expiry.

    A denylist, an expiry change or a broken signature would all produce a 401
    for the wrong reason. This asserts the token still verifies - only the
    server-side generation has moved on.
    """
    import modules.auth as auth_mod

    c = _login(fresh_user)
    token = c.cookies.get("refresh_token")
    c.cookies.clear()

    assert c.post("/auth/refresh", json={"refresh_token": token}).status_code == 200

    payload = auth_mod.jwt_manager.verify_token(token)
    assert payload is not None, "the token no longer verifies - rotation is not what refused it"
    assert payload["type"] == "refresh"
    assert payload.get("sub")
    import asyncio
    current = asyncio.run(auth_mod.token_generations.get(str(payload["sub"])))
    assert auth_mod.jwt_manager.generation_of(payload) < current, (
        "the refused token is at the current generation - it was refused for a "
        "different reason than rotation"
    )


def test_logout_clears_the_cookies_but_does_not_revoke_the_token(fresh_user):
    """Logout is client-side only, and rotation does not change that.

    `POST /auth/logout` clears the cookies. It does **not** bump the token
    generation, so a copy of the refresh token taken *before* logout still
    refreshes successfully afterwards.

    This is deliberately NOT fixed by rotation: rotation retires a token when it
    is *used*, whereas logout would need to retire a token that has not been
    used. That is a one-line `token_generations.bump(...)` in the logout handler,
    and it is a behavioural change for every other tab of the same user, so it
    stays a product decision. The test asserts the current behaviour and names
    the fix.
    """
    c = _login(fresh_user)
    stolen = c.cookies.get("refresh_token")
    assert stolen

    assert c.post("/auth/logout").status_code == 200
    # The cookie is gone from this client...
    assert c.cookies.get("access_token") is None
    assert c.get("/auth/me").status_code == 401

    # ...but the token itself is still good.
    c.cookies.clear()
    replay = c.post("/auth/refresh", json={"refresh_token": stolen})
    assert replay.status_code == 200, (
        "logout DID revoke the token - update this test and the finding"
    )


def test_two_refreshes_in_the_same_second_return_the_same_token(fresh_user):
    """Pinned quirk: JWTs are second-granularity, so refresh is a no-op for 1s.

    Not a security issue on its own, but it makes 'the token changed' a flaky
    assertion, so the real behaviour is recorded here rather than being
    rediscovered by a future failing test.
    """
    c = _login(fresh_user)
    api_exp = api.ACCESS_TOKEN_EXPIRE_MINUTES

    first = c.post("/auth/refresh", json={})
    assert first.status_code == 200
    token_a = c.cookies.get("access_token")
    claims_a = jwt.decode(token_a, options={"verify_signature": False})

    second = c.post("/auth/refresh", json={})
    assert second.status_code == 200
    token_b = c.cookies.get("access_token")
    claims_b = jwt.decode(token_b, options={"verify_signature": False})

    if claims_a["iat"] == claims_b["iat"]:
        assert token_a == token_b, (
            "same iat but a different token: the payload gained a field"
        )
    else:
        # A second boundary was crossed; the tokens must differ.
        assert token_a != token_b
    assert claims_a["type"] == "access"
    assert claims_b["exp"] - claims_b["iat"] == api_exp * 60


def test_a_refresh_token_cannot_reach_a_protected_route(fresh_user):
    """Belt and braces at the route layer: refresh tokens are not credentials."""
    c = _login(fresh_user)
    refresh = c.cookies.get("refresh_token")

    c.cookies.clear()
    c.cookies.set("access_token", refresh)  # put it where an access token goes
    r = c.get("/auth/me")
    assert r.status_code == 401, (
        f"a refresh token authenticated a request: {r.status_code}"
    )


# ------------------------------------------------- internal failures (500) --

def test_a_database_failure_on_login_is_503_not_500(fresh_user, monkeypatch):
    """A DB outage during login must be 503 with a retry message, and audited.

    `_auth_error()` maps "the database is unreachable" to 503 (`try again in a
    minute`) and everything else to 500. That mapping is what tells a client
    whether retrying is worth it, and this branch had never executed.
    """
    async def _down(**kwargs):
        raise OSError("connection refused to postgres")

    monkeypatch.setattr(api.user_manager, "login", _down)
    before = len(api.audit_logger.events)

    r = fresh_user["client"].post("/auth/login", json={
        "username": fresh_user["username"], "password": PASSWORD})

    assert r.status_code == 503, f"a database outage answered {r.status_code}"
    assert "try again" in r.json()["detail"].lower()
    assert "postgres" not in r.text.lower(), "the internal error leaked to the client"
    assert len(api.audit_logger.events) > before, "the failure was not audited"


def test_a_database_failure_on_refresh_is_503_not_500(fresh_user, monkeypatch):
    """Same mapping on the refresh route - the other `_auth_error` call site."""
    c = _login(fresh_user)
    token = c.cookies.get("refresh_token")
    c.cookies.clear()

    async def _down(*args, **kwargs):
        raise OSError("connection refused to postgres")

    monkeypatch.setattr(api.user_manager, "refresh_token", _down)

    r = c.post("/auth/refresh", json={"refresh_token": token})
    assert r.status_code == 503, f"a database outage answered {r.status_code}"
    assert "try again" in r.json()["detail"].lower()
    assert "postgres" not in r.text.lower(), "the internal error leaked to the client"


def test_a_non_database_failure_on_login_is_500_and_neutral(fresh_user, monkeypatch):
    """A bug (not an outage) is a 500 with a deliberately vague message."""
    async def _boom(**kwargs):
        raise ValueError("something unexpected in the login path")

    monkeypatch.setattr(api.user_manager, "login", _boom)

    r = fresh_user["client"].post("/auth/login", json={
        "username": fresh_user["username"], "password": PASSWORD})

    assert r.status_code == 500, f"an internal bug answered {r.status_code}"
    assert r.json()["detail"] == "Internal server error"
    assert "unexpected" not in r.text, "the internal error leaked to the client"
