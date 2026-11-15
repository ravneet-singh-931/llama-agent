"""Refresh-token rotation: the mechanism, not just the endpoint.

`tests/test_auth_routes.py` covers what a client sees (a replayed token gets
401). This file covers the parts that decide *whether* that is true, and the two
places it can silently stop being true:

1. **The generation store.** Rotation is a counter, not a denylist. If the
   counter is not read on one path, that path becomes a bypass - and a bypass on
   the middleware is invisible to any test that only calls `/auth/refresh`.
   Measured before the fix: a request carrying only a refresh cookie got a 200
   and a fresh access cookie from the middleware.
2. **The multi-replica case.** With Redis configured, a rotation on pod A must
   retire the token on pod B, or "single use" holds only until the load balancer
   sends the replay elsewhere.

Legacy tokens (issued before rotation existed, no `gen` claim) are generation 0,
so the first rotation retires them and nobody is signed out by deploying this.
"""
import asyncio

import pytest

from modules import auth as auth_mod
from modules.auth import TokenGenerationStore


@pytest.fixture(autouse=True)
def clean_generations():
    """The store is process-global; a leaked generation poisons later tests."""
    auth_mod.token_generations.reset()
    yield
    auth_mod.token_generations.reset()


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- the store --

def test_an_unknown_user_is_generation_zero():
    store = TokenGenerationStore()
    assert _run(store.get("never-seen")) == 0


def test_bumping_is_monotonic_and_per_user():
    store = TokenGenerationStore()
    assert _run(store.bump("alice")) == 1
    assert _run(store.bump("alice")) == 2
    assert _run(store.bump("bob")) == 1, "one user's rotation bumped another's"
    assert _run(store.get("alice")) == 2
    assert _run(store.get("bob")) == 1


def test_the_store_is_shared_through_redis_across_replicas(monkeypatch):
    """Two instances, one Redis: pod B must see pod A's rotation.

    Without this, "single use" holds only until the replay lands on another pod.
    """
    from modules import redis_store
    shared = {}

    async def _get(key):
        return shared.get(key)

    async def _set(key, value, ttl=0):
        shared[key] = value
        return True

    monkeypatch.setattr(redis_store, "is_configured", lambda: True)
    monkeypatch.setattr(redis_store, "get_json", _get)
    monkeypatch.setattr(redis_store, "set_json", _set)

    pod_a, pod_b = TokenGenerationStore(), TokenGenerationStore()   # separate mirrors
    assert _run(pod_a.bump("alice")) == 1

    assert _run(pod_b.get("alice")) == 1, "pod B did not see pod A's rotation"


def test_a_redis_failure_degrades_instead_of_locking_the_user_out(monkeypatch):
    """A read failure must return the local value, never raise.

    An exception here would surface as a 500 on every authenticated request -
    an availability outage caused by a cache.
    """
    from modules import redis_store

    async def _boom(*a, **k):
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis_store, "is_configured", lambda: True)
    monkeypatch.setattr(redis_store, "get_json", _boom)

    store = TokenGenerationStore()
    store._generations["alice"] = 3
    assert _run(store.get("alice")) == 3


# --------------------------------------------------- the staleness predicate --

def test_a_token_at_the_current_generation_is_not_stale():
    payload = {"type": "refresh", "sub": "alice", "gen": 0}
    assert _run(auth_mod.jwt_manager.is_stale_refresh(payload)) is False


def test_a_token_below_the_current_generation_is_stale():
    _run(auth_mod.token_generations.bump("alice"))          # now generation 1
    stale = {"type": "refresh", "sub": "alice", "gen": 0}
    assert _run(auth_mod.jwt_manager.is_stale_refresh(stale)) is True


def test_a_legacy_token_without_a_gen_claim_is_generation_zero():
    """Tokens minted before rotation existed must not break.

    Treating them as 0 means they work until the user first rotates - not that
    they work forever, and not that deploying this signs everyone out.
    """
    assert auth_mod.jwt_manager.generation_of({}) == 0
    assert auth_mod.jwt_manager.generation_of({"sub": "alice"}) == 0
    assert auth_mod.jwt_manager.generation_of(None) == 0


def test_a_malformed_gen_claim_is_treated_as_zero_not_raising():
    """A hand-edited or hostile token must not raise inside a request."""
    assert auth_mod.jwt_manager.generation_of({"gen": "not-a-number"}) == 0
    assert auth_mod.jwt_manager.generation_of({"gen": None}) == 0


def test_anything_that_is_not_a_refresh_token_is_stale():
    """An access token must never be accepted where a refresh token is required."""
    assert _run(auth_mod.jwt_manager.is_stale_refresh({"type": "access", "sub": "a"})) is True
    assert _run(auth_mod.jwt_manager.is_stale_refresh({})) is True
    assert _run(auth_mod.jwt_manager.is_stale_refresh(None)) is True


def test_a_refresh_token_with_no_subject_is_stale():
    assert _run(auth_mod.jwt_manager.is_stale_refresh({"type": "refresh"})) is True


# ------------------------------------------------- the middleware must obey --

def test_the_middleware_will_not_mint_an_access_token_from_a_dead_refresh(journey):
    """The bypass, closed.

    Before rotation existed the middleware refreshed on *any* valid refresh
    cookie. Once `/auth/refresh` started rotating, checking only that endpoint
    would have left this path minting access tokens from retired tokens -
    rotation would have been theatre: measured, `/auth/me` with a revoked cookie
    returned 200 and set a new access cookie.
    """
    import os
    c = journey.client
    name = "mw_" + os.urandom(3).hex()
    c.cookies.clear()
    c.post("/auth/register", json={
        "username": name, "email": f"{name}@e.com", "password": "Str0ng-Passw0rd!x"})
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": "Str0ng-Passw0rd!x"}).status_code == 200

    dead = c.cookies.get("refresh_token")
    c.cookies.clear()
    assert c.post("/auth/refresh", json={"refresh_token": dead}).status_code == 200

    c.cookies.clear()
    c.cookies.set("refresh_token", dead)
    response = c.get("/auth/me")

    assert response.status_code == 401, (
        "the middleware authenticated a request carrying a rotated-away token"
    )
    assert "access_token" not in response.cookies, (
        "the middleware minted a fresh access cookie from a revoked refresh token"
    )


def test_the_middleware_still_works_for_a_live_refresh_token(journey):
    """The negative test above must not have broken the feature it guards.

    A user whose access cookie has expired but whose refresh token is current
    should still be silently re-authenticated - that is the whole point of the
    middleware.
    """
    import os
    c = journey.client
    name = "mwl_" + os.urandom(3).hex()
    c.cookies.clear()
    c.post("/auth/register", json={
        "username": name, "email": f"{name}@e.com", "password": "Str0ng-Passw0rd!x"})
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": "Str0ng-Passw0rd!x"}).status_code == 200

    live = c.cookies.get("refresh_token")
    c.cookies.clear()
    c.cookies.set("refresh_token", live)          # access cookie gone, refresh current

    response = c.get("/auth/me")

    assert response.status_code == 200, "a legitimate silent refresh was refused"
    assert "access_token" in response.cookies, "no access cookie was re-minted"


# -------------------------------------------------------- rotation end to end --

def test_rotation_chains(journey):
    """Three refreshes in sequence each hand back a working token, and every
    earlier one is dead - the property a denylist would have to maintain."""
    import os
    c = journey.client
    name = "chain_" + os.urandom(3).hex()
    c.cookies.clear()
    c.post("/auth/register", json={
        "username": name, "email": f"{name}@e.com", "password": "Str0ng-Passw0rd!x"})
    c.cookies.clear()
    assert c.post("/auth/login", json={
        "username": name, "password": "Str0ng-Passw0rd!x"}).status_code == 200

    tokens = [c.cookies.get("refresh_token")]
    for _ in range(3):
        c.cookies.clear()
        r = c.post("/auth/refresh", json={"refresh_token": tokens[-1]})
        assert r.status_code == 200
        raw = r.headers.get("set-cookie", "")
        new = None
        for part in raw.split(","):
            if "refresh_token=" in part:
                new = part.split("refresh_token=")[1].split(";")[0]
        assert new and new != tokens[-1], "the refresh token was not rotated"
        tokens.append(new)

    # Every retired token is refused; only the newest works.
    for old in tokens[:-1]:
        c.cookies.clear()
        assert c.post("/auth/refresh", json={"refresh_token": old}).status_code == 401
    c.cookies.clear()
    assert c.post("/auth/refresh", json={"refresh_token": tokens[-1]}).status_code == 200


def test_two_users_rotate_independently(journey):
    """A generation store keyed on anything but the user would retire the wrong
    sessions - the highest-consequence way to get this wrong.

    `journey.client` is a SINGLE TestClient, so two users need two cookie jars:
    the first version of this test logged in as alice, then logged in as bob
    *with the same client*, which just replaced alice's cookies. It failed for
    that reason, not because rotation was broken.
    """
    import os
    from fastapi.testclient import TestClient

    def fresh_session(label):
        name = f"ind_{label}_" + os.urandom(3).hex()
        session = TestClient(journey.api.app, raise_server_exceptions=False)
        session.cookies.clear()
        session.post("/auth/register", json={
            "username": name, "email": f"{name}@e.com",
            "password": "Str0ng-Passw0rd!x"})
        session.cookies.clear()
        assert session.post("/auth/login", json={
            "username": name, "password": "Str0ng-Passw0rd!x"}).status_code == 200
        return session

    alice, bob = fresh_session("a"), fresh_session("b")
    alice_token = alice.cookies.get("refresh_token")
    bob_token = bob.cookies.get("refresh_token")
    assert alice_token and bob_token and alice_token != bob_token

    # alice rotates. Nothing about bob's session should move.
    alice.cookies.clear()
    assert alice.post("/auth/refresh", json={
        "refresh_token": alice_token}).status_code == 200

    bob.cookies.clear()
    assert bob.post("/auth/refresh", json={
        "refresh_token": bob_token}).status_code == 200, (
        "another user's rotation retired this user's token"
    )
