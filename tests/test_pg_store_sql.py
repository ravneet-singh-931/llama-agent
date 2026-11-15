"""The SQL paths in `modules/pg_store.py`, not just the failure paths.

`tests/test_pg_store.py` (242 lines) is thorough about **degradation**: every
function is called against a pool that raises, and every one returns
`None`/`[]`/`False` as promised. That is valuable and it is the reason chat
survives a database outage.

But it means **no query in this module was ever executed**. 21 of its 22 tests
end in "the DB is down". The SQL itself - the owner checks, the conflict
handling, the ordering - had no test at all. Two things follow:

- a broken `WHERE c.user_id = $2` would still return `None` for an unreachable
  database, so every existing test would stay green
- the cross-user write path (`ON CONFLICT (id) DO UPDATE`) is invisible to a
  test whose pool raises before reaching it

This file drives the real functions against a recording fake. It does not prove
the SQL is valid PostgreSQL - only a real server can do that - and it says so
rather than implying otherwise.
"""
import asyncio
import uuid

import pytest

from modules import pg_store


# --------------------------------------------------------------- fake pool --

class _Row(dict):
    """asyncpg records support `row["col"]`; a dict does that already."""

    def __getitem__(self, key):
        return dict.__getitem__(self, key)


class _Conn:
    def __init__(self, log, rows=None, row=None, status="DELETE 1"):
        self.log = log
        self._rows = rows or []
        self._row = row
        self._status = status

    async def execute(self, sql, *args):
        self.log.append(("execute", " ".join(sql.split()), args))
        return self._status

    async def fetch(self, sql, *args):
        self.log.append(("fetch", " ".join(sql.split()), args))
        return self._rows

    async def fetchrow(self, sql, *args):
        self.log.append(("fetchrow", " ".join(sql.split()), args))
        return self._row


class _Pool:
    def __init__(self, log, **kw):
        self._conn = _Conn(log, **kw)

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._conn

            async def __aexit__(self, *a):
                return False
        return _Ctx()


@pytest.fixture
def sql_log(monkeypatch):
    """Capture every statement, then get out of the way."""
    log = []

    def _install(**kw):
        async def _pool():
            return _Pool(log, **kw)
        monkeypatch.setattr(pg_store, "get_pool", _pool)
        return log
    return _install


def _run(coro):
    return asyncio.run(coro)


USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


# --------------------------------------------------- the cross-user question --

def test_a_message_written_into_another_users_conversation_is_a_real_finding(
        sql_log):
    """MEASURED BEHAVIOUR: user B can write into user A's conversation.

    `add_message` runs `INSERT INTO conversations ... ON CONFLICT (id) DO UPDATE
    SET updated_at = NOW()` with **no owner predicate**. If B supplies A's
    conversation id, the conflict clause fires on A's row: `user_id` stays A's,
    and the following INSERT puts **B's content into A's conversation**.

    A's conversation id is not a secret: the default is
    `uuid5(DNS, f"llama-agent-default:{user_id}")`, computable by anyone who
    knows A's user id - which is in A's own JWT and in every URL the frontend
    builds.

    What is NOT broken, and was checked before claiming anything:
      B cannot READ A's history. `get_messages` joins `c.user_id = $2`, so B
      gets `[]`. The read path is correctly owner-checked.

    So this is a **write-only integrity defect**: B can inject into, and
    resurrect the `updated_at` of, a conversation B cannot read. It cannot
    exfiltrate data; it can corrupt it and move A's conversation to the top of
    A's sidebar.

    This test asserts the CURRENT behaviour so the gap cannot close silently.
    When an owner predicate is added, this test fails - and that failure is the
    fix arriving.
    """
    log = sql_log()

    result = _run(pg_store.add_message(
        USER_B, "anything", "user", "message injected by B"))

    assert result is not None, "the write did not reach the fake pool"
    inserts = [entry for entry in log if entry[0] == "execute"]
    conv_sql = inserts[0][1]

    assert "ON CONFLICT (id) DO UPDATE" in conv_sql
    assert "user_id" not in conv_sql.split("ON CONFLICT")[1], (
        "an owner predicate was added to the conflict clause - good. Invert "
        "this test and update SECURITY_FINDINGS.md; the gap it records is closed."
    )
    # The message row is bound to the requested conversation id, not to B's own.
    msg_args = inserts[1][2]
    assert msg_args[1] == pg_store.normalize_conversation_id("anything")


def test_a_message_row_gets_a_fresh_id_and_the_right_conversation(sql_log):
    """Bind order is (id, conversation_id, role, content).

    Added because a sabotage that swapped `$1`/`$2` in the VALUES list SURVIVED
    the first version of this file: I asserted the conversation argument but not
    the message id. Two messages sharing an id is a primary-key violation, and
    binding the wrong column would only show up against a real server.
    """
    log = sql_log()
    _run(pg_store.add_message(USER_A, "c", "user", "hello"))

    insert = [e for e in log if e[0] == "execute" and "INSERT INTO messages" in e[1]][0]
    sql, args = insert[1], insert[2]

    assert "VALUES ($1, $2, $3, $4, NOW())" in sql, f"bind order changed: {sql}"
    message_id, conversation_id, role, content = args
    assert str(uuid.UUID(message_id)) == message_id, "the message id is not a UUID"
    assert conversation_id == pg_store.normalize_conversation_id("c")
    assert role == "user" and content == "hello"

    # A second message must get a different id.
    _run(pg_store.add_message(USER_A, "c", "assistant", "hi"))
    second = [e for e in log if e[0] == "execute" and "INSERT INTO messages" in e[1]][1]
    assert second[2][0] != message_id, "two messages were written with the same id"


def test_reading_another_users_conversation_returns_nothing(sql_log):
    """The read path is owner-checked - this is the half that holds."""
    log = sql_log(rows=[])

    assert _run(pg_store.get_messages(USER_B, "anything")) == []

    sql, args = log[0][1], log[0][2]
    assert "c.user_id = $2" in sql, "the owner check disappeared from the read query"
    assert args[1] == pg_store.normalize_user_id(USER_B)


# ------------------------------------------------------- statements actually --

def test_an_unregistered_user_never_reaches_the_database(sql_log):
    """`is_registered_user` gates the write *before* the pool is touched.

    Derived ids have no `users` row, so the INSERT could never satisfy the FK.
    The gate is what turns a guaranteed failure into a no-op.
    """
    log = sql_log()
    assert _run(pg_store.add_message("guest", "c", "user", "hi")) is None
    assert log == [], f"the database was touched for an unregistered user: {log}"


def test_the_conversation_title_is_capped_at_80_characters(sql_log):
    log = sql_log()
    _run(pg_store.ensure_conversation(USER_A, "c", title="x" * 200))

    args = [e for e in log if e[0] == "execute"][0][2]
    assert len(args[2]) == 80, f"title was not capped: {len(args[2])} chars"


def test_the_first_user_message_auto_titles_the_conversation(sql_log):
    """Only an untitled conversation is renamed, and newlines are flattened."""
    log = sql_log()
    _run(pg_store.add_message(USER_A, "c", "user", "  hello\nworld  ", title=None))

    updates = [e for e in log if e[0] == "execute" and "UPDATE conversations SET title" in e[1]]
    assert len(updates) == 1, "the conversation was not auto-titled"
    new_title, _ = updates[0][2]
    assert new_title == "hello world", f"title not cleaned: {new_title!r}"
    assert "title IN ('New chat', 'New Chat')" in updates[0][1], (
        "the update is not restricted to untitled conversations - it would "
        "overwrite a title the user chose"
    )


def test_an_assistant_message_does_not_retitle(sql_log):
    log = sql_log()
    _run(pg_store.add_message(USER_A, "c", "assistant", "an answer", title=None))
    updates = [e for e in log if e[0] == "execute" and "UPDATE conversations SET title" in e[1]]
    assert updates == [], "an assistant reply renamed the conversation"


def test_delete_reports_whether_a_row_was_actually_removed(sql_log):
    """`DELETE 1` -> True, `DELETE 0` -> False. The count is parsed out of
    asyncpg's status string, so an unexpected shape must not raise."""
    sql_log(status="DELETE 1")
    assert _run(pg_store.delete_conversation(USER_A, "c")) is True

    sql_log(status="DELETE 0")
    assert _run(pg_store.delete_conversation(USER_A, "c")) is False

    sql_log(status="weird")
    assert _run(pg_store.delete_conversation(USER_A, "c")) is False


def test_delete_removes_messages_before_the_conversation(sql_log):
    """Order matters: the FK may be RESTRICT, and history must be fully gone
    even if the conversation row survives."""
    log = sql_log()
    _run(pg_store.delete_conversation(USER_A, "c"))

    deletes = [e[1] for e in log if e[0] == "execute"]
    assert deletes[0].startswith("DELETE FROM messages")
    assert deletes[1].startswith("DELETE FROM conversations")
    assert "user_id = $2" in deletes[1], "the owner check is missing from the delete"


def test_clear_user_deletes_messages_then_conversations(sql_log):
    log = sql_log()
    assert _run(pg_store.clear_user(USER_A)) is True

    deletes = [e[1] for e in log if e[0] == "execute"]
    assert deletes[0].startswith("DELETE FROM messages")
    assert "user_id = $1" in deletes[1]


# ------------------------------------------------------------- row shaping --

def test_conversation_summaries_are_json_safe(sql_log):
    """Dates become ISO strings, missing content becomes an empty string.

    A `datetime` reaching `json.dumps` raises `TypeError` inside FastAPI, which
    would surface as a 500 on `/conversations` rather than a bad payload.
    """
    import datetime
    now = datetime.datetime(2026, 11, 15, 10, 0, 0)
    sql_log(rows=[_Row(id=uuid.UUID(USER_A), title="t", created_at=now,
                       updated_at=now, message_count=3, last_message=None)])

    out = _run(pg_store.list_conversations(USER_A))

    assert out[0]["created_at"] == "2026-11-15T10:00:00"
    assert out[0]["last_message"] == "", "a NULL last_message must not stay None"
    assert out[0]["message_count"] == 3
    assert out[0]["user_id"] == pg_store.normalize_user_id(USER_A)


def test_messages_are_returned_in_the_database_order(sql_log):
    """Chronological, and the query says so - a chat rendered out of order is
    worse than no history."""
    import datetime
    now = datetime.datetime(2026, 11, 15, 10, 0, 0)
    log = sql_log(rows=[_Row(role="user", content="hi", created_at=now)])

    out = _run(pg_store.get_messages(USER_A, "c"))

    assert out[0] == {"role": "user", "content": "hi",
                      "timestamp": "2026-11-15T10:00:00"}
    assert "ORDER BY m.created_at ASC" in log[0][1]


def test_a_missing_conversation_summary_is_none_not_an_empty_dict(sql_log):
    sql_log(row=None)
    assert _run(pg_store.get_conversation(USER_A, "c")) is None
