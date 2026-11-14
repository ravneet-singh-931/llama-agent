"""One user's conversations must be invisible to another user.

This is the highest-value gap in `api.py`'s coverage, and not because of the line
count. `get_conversation`, `delete_conversation`, `list_conversations`,
`create_conversation` and `clear_memory` were **entirely untested**. The bug they
can hide is the worst kind this codebase has: a request for someone else's
conversation that quietly succeeds.

It is easy to write that bug. Every one of those handlers looks like this:

    user_id = _request_uid(user)
    summary = await pg_store.get_conversation(user_id, conversation_id)

...and it is correct *only because* `user_id` is passed down. Drop that argument
for a friendlier signature, or fetch by id alone "since ids are UUIDs anyway",
and every test still passes while every user can read every other user's chats.
Nothing errors. The only way to catch it is to ask, as user B, for user A's
conversation and require a 404.

Same lesson as the document-scoping test: a green test is evidence of a test; a
failing sabotage is evidence of a check.
"""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

PASSWORD = "Str0ng-Passw0rd!x"


@pytest.fixture
def two_users(journey):
    """Two registered, logged-in users on the same booted app."""
    c = journey.client

    # The rate limiter is a process-wide singleton keyed on client IP, and every
    # request in this file shares the TestClient's address. These tests make far
    # more requests than a human would, so without resetting it the suite starts
    # failing with 429 partway through - which is the limiter working correctly,
    # just not on the thing being tested. Reset the counters, not the middleware:
    # disabling it would also disable the behaviour some other test checks.
    from modules.security import rate_limiter
    rate_limiter._requests.clear()

    def register(prefix):
        name = f"{prefix}_{uuid.uuid4().hex[:8]}"
        c.cookies.clear()
        r = c.post("/auth/register", json={
            "username": name, "email": f"{name}@example.com", "password": PASSWORD})
        assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
        # Registration also sets cookies, so log in explicitly and clear first -
        # otherwise a broken login is masked by registration's own session.
        c.cookies.clear()
        r = c.post("/auth/login", json={"username": name, "password": PASSWORD})
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
        r = c.get("/auth/me")
        assert r.status_code == 200, r.text
        return name, r.json().get("user", r.json())

    alice, alice_body = register("alice")
    yield {"client": c, "alice": alice, "alice_body": alice_body,
           "register": register, "journey": journey}


def _as(client, username, cookies):
    """Become another user without re-authenticating."""
    client.cookies.clear()
    for name, value in cookies.items():
        client.cookies.set(name, value)
    return username


def test_a_user_cannot_read_another_users_conversation(two_users):
    c = two_users["client"]

    r = c.post("/conversations", json={"title": "alice private notes"})
    assert r.status_code == 200, f"create failed: {r.status_code} {r.text}"
    conv_id = r.json()["id"]

    alice_cookies = dict(c.cookies)

    # Alice can read her own - this is the control, so a 404 below means
    # "denied", not "the endpoint is broken".
    r = c.get(f"/conversations/{conv_id}")
    assert r.status_code == 200, f"owner cannot read own conversation: {r.text}"

    # Bob registers (which switches the session), then reaches for Alice's id.
    two_users["register"]("bob")
    r = c.get(f"/conversations/{conv_id}")
    assert r.status_code == 404, (
        f"user B read user A's conversation (got {r.status_code}). "
        "Conversations are not scoped to their owner."
    )
    assert "alice private" not in r.text

    # And Alice still can, afterwards.
    _as(c, two_users["alice"], alice_cookies)
    assert c.get(f"/conversations/{conv_id}").status_code == 200


def test_a_user_cannot_delete_another_users_conversation(two_users):
    c = two_users["client"]

    conv_id = c.post("/conversations", json={"title": "alice keeps this"}).json()["id"]
    alice_cookies = dict(c.cookies)

    two_users["register"]("bob")

    r = c.delete(f"/conversations/{conv_id}")
    assert r.status_code == 404, (
        f"user B deleted user A's conversation (got {r.status_code})."
    )

    # The real assertion: it is still there.
    _as(c, two_users["alice"], alice_cookies)
    r = c.get(f"/conversations/{conv_id}")
    assert r.status_code == 200, "the conversation was deleted by another user"
    assert r.json().get("title") == "alice keeps this"


def test_one_users_conversations_do_not_appear_in_anothers_list(two_users):
    c = two_users["client"]

    c.post("/conversations", json={"title": "alice list marker"})
    alice_cookies = dict(c.cookies)

    two_users["register"]("bob")
    r = c.get("/conversations")
    assert r.status_code == 200, r.text
    titles = [x.get("title") for x in r.json()["conversations"]]
    assert "alice list marker" not in titles, (
        f"user B sees user A's conversations: {titles}"
    )
    assert r.json()["total"] == 0, r.json()

    _as(c, two_users["alice"], alice_cookies)
    r = c.get("/conversations")
    titles = [x.get("title") for x in r.json()["conversations"]]
    assert "alice list marker" in titles, titles


def test_clearing_memory_does_not_touch_another_users_conversations(two_users):
    c = two_users["client"]

    conv_id = c.post("/conversations", json={"title": "alice survives clear"}).json()["id"]
    alice_cookies = dict(c.cookies)

    two_users["register"]("bob")
    r = c.delete("/memory")
    assert r.status_code == 200, f"clear memory failed: {r.status_code} {r.text}"

    _as(c, two_users["alice"], alice_cookies)
    r = c.get(f"/conversations/{conv_id}")
    assert r.status_code == 200, (
        "user B's `clear memory` removed user A's conversation"
    )


def test_an_unknown_conversation_id_is_404_not_500(two_users):
    """A bad id must be a clean 404, not a server error.

    This is the error-branch half: `HTTPException` is re-raised rather than
    swallowed by the broad `except Exception` that returns 500. If that
    `except HTTPException: raise` were removed, every not-found would look like
    a server crash and page an operator.
    """
    c = two_users["client"]
    r = c.get(f"/conversations/{uuid.uuid4()}")
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"


def test_conversations_are_listed_with_paging(two_users):
    c = two_users["client"]
    for i in range(3):
        c.post("/conversations", json={"title": f"paged {i}"})

    r = c.get("/conversations?limit=2&offset=0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= 3, body
    assert len(body["conversations"]) <= 2, body
    assert body["limit"] == 2 and body["offset"] == 0

    # Limits are clamped, not trusted: a client cannot ask for everything.
    r = c.get("/conversations?limit=100000")
    assert r.json()["limit"] == 200, r.json()
    r = c.get("/conversations?offset=-5")
    assert r.json()["offset"] == 0, r.json()


# ------------------------------------------------ the rest of the CRUD surface --

def test_memory_is_scoped_to_the_caller(two_users):
    c = two_users["client"]
    conv_id = c.post("/conversations", json={"title": "alice memory"}).json()["id"]
    alice_cookies = dict(c.cookies)

    r = c.get("/memory")
    assert r.status_code == 200, r.text
    assert any(x.get("id") == conv_id for x in r.json()["conversations"]), "own conv missing"

    two_users["register"]("bob")
    r = c.get("/memory")
    assert r.status_code == 200, r.text
    assert not any(x.get("id") == conv_id for x in r.json()["conversations"]), (
        "user B's /memory lists user A's conversation"
    )

    _as(c, two_users["alice"], alice_cookies)
    r = c.get("/memory")
    assert any(x.get("id") == conv_id for x in r.json()["conversations"])


def test_clearing_memory_removes_only_the_callers_data(two_users):
    c = two_users["client"]
    conv_id = c.post("/conversations", json={"title": "alice gets cleared"}).json()["id"]
    alice_cookies = dict(c.cookies)

    two_users["register"]("bob")
    bob_conv = c.post("/conversations", json={"title": "bob keeps this"}).json()["id"]

    r = c.delete("/memory")
    assert r.status_code == 200, r.text
    # Bob's own data went away...
    assert c.get(f"/conversations/{bob_conv}").status_code == 404

    # ...but Alice's is untouched.
    _as(c, two_users["alice"], alice_cookies)
    assert c.get(f"/conversations/{conv_id}").status_code == 200, (
        "clearing one user's memory deleted another user's conversation"
    )


def test_facts_are_scoped_to_the_caller(two_users):
    """Facts are the "remember my name" store - the most personal data here."""
    c = two_users["client"]
    r = c.post("/chat", json={"message": "remember favorite color = blue"})
    assert r.status_code == 200, f"chat failed: {r.status_code} {r.text}"

    r = c.get("/facts")
    assert r.status_code == 200, r.text
    assert "favorite color" in str(r.json()), r.json()

    alice_facts = r.json()
    two_users["register"]("bob")
    r = c.get("/facts")
    assert r.status_code == 200, r.text
    assert "favorite color" not in str(r.json()), (
        f"user B sees user A's facts: {r.json()}"
    )
    assert r.json() != alice_facts or not alice_facts, "facts are not scoped"


def test_deleting_a_fact_only_touches_the_callers_store(two_users):
    c = two_users["client"]
    c.post("/chat", json={"message": "remember team = platform"})
    alice_cookies = dict(c.cookies)

    two_users["register"]("bob")
    # Bob tries to delete Alice's fact.
    r = c.delete("/facts/team")
    # Either way it must not remove Alice's.
    _as(c, two_users["alice"], alice_cookies)
    r = c.get("/facts")
    assert "team" in str(r.json()).lower(), (
        f"user B deleted user A's fact (delete returned {r.status_code})"
    )
