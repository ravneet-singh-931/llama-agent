"""Request identity: who is a request running on behalf of?

Extracted from api.py in #15 step 3. The functions moved unchanged, so these
tests are less about "does it work" and more about the one thing a move can
break: **one piece of shared state now lives in a different module.**

`_guest_id_ctx` is a context variable that `identity_middleware` (in api.py)
*writes* and `_request_uid` (in modules/doc_identity.py) *reads*. If the two
modules ever hold separate context variables, every anonymous visitor silently
becomes a new guest on every request — all their documents, facts and history
would seem to disappear. Nothing about the code would look wrong: both halves
would be valid and each would be used by one side.

So the first test here compares identity, not behaviour.
"""

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def ident():
    from modules import doc_identity
    return doc_identity


# ------------------------------------------- the shared-object guard --

def test_api_and_module_share_the_same_context_variable():
    """The whole point of the move: one object, two modules.

    If this is ever a copy, the middleware below sets a variable nobody reads.
    """
    import api
    from modules import doc_identity

    assert api._guest_id_ctx is doc_identity._guest_id_ctx


def test_api_and_module_share_the_same_regex_and_cookie_name():
    import api
    from modules import doc_identity

    assert api.GUEST_COOKIE is doc_identity.GUEST_COOKIE == "guest_id"
    assert api._GUEST_ID_RE is doc_identity._GUEST_ID_RE


def test_what_the_middleware_sets_is_what_request_uid_reads():
    """Set via api's name, read via the module's function."""
    import api
    from modules import doc_identity

    gid = uuid.uuid4().hex
    token = api._guest_id_ctx.set(gid)          # identity_middleware does this
    try:
        assert doc_identity._request_uid(None) == f"guest-{gid}"
    finally:
        api._guest_id_ctx.reset(token)


# ------------------------------------------------- identity rules --

def test_a_valid_guest_cookie_is_honoured(ident):
    gid = uuid.uuid4().hex
    assert ident._guest_uid(gid) == f"guest-{gid}"


@pytest.mark.parametrize("forged", [
    "admin",             # not 32 hex
    "A" * 32,            # uppercase is rejected (regex is lowercase-only)
    "a" * 31,            # one short
    "g" * 32,            # non-hex
    "",
    None,
])
def test_a_forged_or_absent_cookie_gets_a_fresh_identity(ident, forged):
    """Never echo an attacker-chosen id back as their identity."""
    made = ident._guest_uid(forged)
    assert made.startswith("guest-")
    assert made not in (f"guest-{forged}", "guest-None")


def test_forged_cookies_do_not_collide(ident):
    """Two requests with the same bad cookie must not share an identity."""
    assert ident._guest_uid("bad") != ident._guest_uid("bad")


def test_a_registered_user_beats_the_guest_cookie(ident):
    gid = uuid.uuid4().hex
    token = ident._guest_id_ctx.set(gid)
    try:
        assert ident._request_uid({"id": "user-uuid-1234"}) == "user-uuid-1234"
    finally:
        ident._guest_id_ctx.reset(token)


def test_a_user_without_an_id_falls_back_to_guest(ident):
    assert ident._request_uid({"id": None}).startswith("guest-")
    assert ident._request_uid({}).startswith("guest-")


def test_two_anonymous_visitors_never_share_an_identity(ident):
    assert ident._request_uid(None) != ident._request_uid(None)


def test_request_uid_binds_the_owner_for_downstream_code(ident):
    assert ident._request_uid({"id": "bound-user"}) == "bound-user"


# ------------------------------------------------ no import cycle --

def test_doc_identity_does_not_import_api():
    """A cycle would be invisible at runtime but fatal on a cold import."""
    import ast
    import pathlib

    src = pathlib.Path(
        os.path.join(os.path.dirname(__file__), "..", "modules", "doc_identity.py")
    ).read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            assert not any(a.name == "api" for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert node.module != "api"


def test_user_rag_stayed_in_api_because_it_needs_app():
    """Documenting the boundary: _user_rag reads app.state.rag, so it cannot move."""
    import api
    assert hasattr(api, "_user_rag")
    import modules.doc_identity as d
    assert not hasattr(d, "_user_rag")
