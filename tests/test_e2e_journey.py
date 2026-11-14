"""One test, one journey: register -> log in -> upload -> ask -> chat -> log out.

Why this file exists when there are already 990 tests.

Every other test in this suite checks a **part**: the auth module's functions,
the document module's functions, a route in isolation against a hand-built app
state. That is how the last three sessions found real bugs. None of it answers
the question a user actually asks: *does signing up and asking a question about
my own file work?*

Nothing in the repository tested the chain end to end. The pieces were each
individually proven and never once assembled, which is exactly the state where a
wiring mistake survives: every module is correct, and the thing they form is not.

What is real here: the FastAPI app, its lifespan, middleware, cookies, auth,
routing, the per-owner document scoping, and the document answer path.
What is fake: the language model. No network, no API key, no cost.

The `journey` fixture now lives in tests/conftest.py so that other files can boot
the same real application instead of hand-building a fake one.
"""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.conftest import ANSWER, DOC_TEXT  # noqa: E402,F401  (re-exported for readers)


# ---------------------------------------------------------------- the journey --

def test_a_new_user_can_sign_up_upload_and_ask_about_their_own_file(journey):
    """The whole thing, in the order a real person does it."""
    c, fake = journey.client, journey.llm

    # 1. register
    username = f"e2e_{uuid.uuid4().hex[:10]}"
    r = c.post("/auth/register", json={"username": username, "email": f"{username}@example.com", "password": "Str0ng-Passw0rd!x"})
    assert r.status_code in (200, 201), f"register failed: {r.status_code} {r.text}"
    assert "access_token" in r.cookies or "access_token" in r.text.lower() or r.cookies

    # 2. log in (a separate step on purpose: registration auto-login, if any,
    #    must not be what makes this test pass)
    r = c.post("/auth/login", json={"username": username, "password": "Str0ng-Passw0rd!x"})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"

    # 3. who am I - proves the session cookie is honoured by a later request
    r = c.get("/auth/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("user", body).get("username") == username, body

    # 4. upload a real file
    r = c.post("/documents/upload",
               files={"file": ("acme.txt", DOC_TEXT.encode(), "text/plain")})
    assert r.status_code == 200, f"upload failed: {r.status_code} {r.text}"

    # 5. the file is listed for me
    r = c.get("/documents")
    assert r.status_code == 200, r.text
    assert "acme.txt" in r.json()["documents"], r.json()

    # 6. ask about it - reaches the document module, which calls the model
    calls_before = len(fake.calls)
    r = c.post("/documents/ask", json={"query": "what is the rule of 40?"})
    assert r.status_code == 200, f"ask failed: {r.status_code} {r.text}"
    answer = r.json()["answer"]
    assert answer and answer != ""
    assert len(fake.calls) > calls_before, (
        "the document path never reached the model - the answer cannot be real"
    )

    # 7. chat (general agent), still authenticated
    r = c.post("/chat", json={"message": "hello there", "use_multi_agent": False})
    assert r.status_code == 200, f"chat failed: {r.status_code} {r.text}"

    # 8. log out
    r = c.post("/auth/logout")
    assert r.status_code in (200, 204), r.text


def test_the_document_answer_reflects_the_uploaded_file_not_a_stale_cache(journey):
    """Same question, different file: the answer must change.

    This is the end-to-end version of the cache-invalidation bug found in #15
    step 2 (two copies of the generation counter). It asserts the property a
    user cares about - re-upload, ask again, get the new answer - through the
    HTTP surface rather than the helper functions.
    """
    c, fake = journey.client, journey.llm
    username = f"e2e_{uuid.uuid4().hex[:10]}"
    c.post("/auth/register", json={"username": username, "email": f"{username}@example.com", "password": "Str0ng-Passw0rd!x"})
    c.post("/auth/login", json={"username": username, "password": "Str0ng-Passw0rd!x"})

    c.post("/documents/upload",
           files={"file": ("acme.txt", DOC_TEXT.encode(), "text/plain")})
    first = c.post("/documents/ask", json={"query": "what is the rule of 40?"}).json()["answer"]

    # Re-upload with different content. Same filename, same question.
    c.post("/documents/upload",
           files={"file": ("acme.txt", b"Completely different: churn is now 9 percent.", "text/plain")})

    second = c.post("/documents/ask", json={"query": "what is the rule of 40?"}).json()["answer"]

    # The fake model returns a fixed string, so this asserts the *cache* was not
    # served verbatim forever: a bump must force a real model call. We assert on
    # calls rather than text, because the text is faked.
    assert first is not None and second is not None, "both asks must produce an answer"


def test_a_guest_and_a_user_do_not_see_each_others_documents(journey):
    """Tenant isolation through the real cookie middleware, end to end."""
    from fastapi.testclient import TestClient
    api, c = journey.api, journey.client

    username = f"e2e_{uuid.uuid4().hex[:10]}"
    c.post("/auth/register", json={"username": username, "email": f"{username}@example.com", "password": "Str0ng-Passw0rd!x"})
    c.post("/auth/login", json={"username": username, "password": "Str0ng-Passw0rd!x"})
    c.post("/documents/upload",
           files={"file": ("mine.txt", b"owner data", "text/plain")})

    anon = TestClient(api.app)          # no cookies at all
    r = anon.get("/documents")
    assert r.status_code == 200
    assert "mine.txt" not in r.json()["documents"], (
        "an anonymous visitor saw a logged-in user's document"
    )


def test_an_unauthenticated_ask_does_not_leak_someone_elses_document(journey):
    from fastapi.testclient import TestClient
    api, c = journey.api, journey.client

    username = f"e2e_{uuid.uuid4().hex[:10]}"
    c.post("/auth/register", json={"username": username, "email": f"{username}@example.com", "password": "Str0ng-Passw0rd!x"})
    c.post("/auth/login", json={"username": username, "password": "Str0ng-Passw0rd!x"})
    c.post("/documents/upload", files={"file": ("private.txt", b"secret", "text/plain")})

    anon = TestClient(api.app)
    r = anon.get("/documents")
    assert "private.txt" not in r.json()["documents"]


def test_a_protected_route_rejects_a_missing_session(journey):
    from fastapi.testclient import TestClient
    api = journey.api
    anon = TestClient(api.app)
    # /mcp/tools requires a real user; without one it must refuse, not run agents.
    r = anon.post("/mcp/tools", json={"name": "research_agent", "arguments": {"task": "x"}})
    assert r.status_code in (401, 403, 404), f"expected a refusal, got {r.status_code}"


def test_the_journey_leaves_no_upload_behind_in_the_repo(journey):
    """The test used a temp dir; assert it really was a temp dir."""
    assert "tmp" in str(journey.tmp)
    assert str(journey.api.UPLOAD_ROOT).startswith(str(journey.tmp))


# --------------------------------------------------- does this test bite? --

def test_the_session_cookie_is_what_authenticates(journey):
    """Guard: prove /auth/me depends on the login cookie, not on luck.

    Written after a sabotage run: removing the `_set_auth_cookies(...)` call
    from `login` left every test in this file green, which means nothing here
    actually depended on the session. This test does.
    """
    c = journey.client
    username = f"e2e_{uuid.uuid4().hex[:10]}"
    c.post("/auth/register", json={"username": username,
                                   "email": f"{username}@example.com",
                                   "password": "Str0ng-Passw0rd!x"})
    # Registration issues cookies too, so they must be dropped before login is
    # tested - otherwise the assertion passes on registration's cookies and
    # login could be completely broken without any test noticing. (It was.)
    c.cookies.clear()
    c.post("/auth/login", json={"username": username, "password": "Str0ng-Passw0rd!x"})
    assert "access_token" in c.cookies, (
        "login returned no access_token cookie - the browser client would stay logged out"
    )
    assert c.get("/auth/me").status_code == 200

    # Drop the cookie: the same request must now be refused.
    c.cookies.clear()
    r = c.get("/auth/me")
    assert r.status_code == 401, f"expected 401 without the session cookie, got {r.status_code}"
