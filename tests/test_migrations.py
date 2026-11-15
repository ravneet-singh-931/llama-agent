"""The database schema must be upgradable, not just creatable.

The bug this file exists for: `modules/database.py::init_db()` calls
`Base.metadata.create_all`, which **creates missing tables and does nothing
else**. It never adds a column to a table that already exists. Reproduced
directly:

    v1 columns: ['id', 'name']
    v2 columns: ['id', 'name']          <- the new column was added to the model
    create_all added the new column: False

So deploying a version that adds a field to `users` leaves the live table
without it. Every query touching that field then fails at *request* time, in
production, with no warning at startup — the startup log says
"✅ Database initialized" and means it.

Alembic was already a pinned dependency (`alembic==1.20.0`) and had no config,
no migrations directory, and no import anywhere in the codebase: installed on
every build, used by nothing.

These tests run the real Alembic command against a throwaway SQLite database.
They do not need Postgres.
"""

import os
import pathlib
import subprocess  # nosec B404 - spawns the current interpreter, fixed argv, no shell
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _alembic(args, db_url, check=True):
    env = dict(os.environ)
    env["ALEMBIC_DATABASE_URL"] = db_url
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(  # nosec B603 - sys.executable, fixed argv, no shell
        [sys.executable, "-m", "alembic"] + args,
        cwd=str(ROOT), env=env, capture_output=True, text=True,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}"
        )
    return result


@pytest.fixture
def fresh_db(tmp_path):
    return f"sqlite:///{tmp_path / 'mig.db'}"


# --------------------------------------------------- the structure --

def test_alembic_is_configured():
    """It was a dependency with no config for the life of the project."""
    assert (ROOT / "alembic.ini").is_file()
    assert (ROOT / "migrations" / "env.py").is_file()
    assert list((ROOT / "migrations" / "versions").glob("*.py")), "no migrations"


def test_the_ini_does_not_hardcode_a_database_url():
    """Two sources of truth for "which database" is how schemas and apps drift.

    `env.py` resolves the URL through `modules.secrets.resolve_database_url`,
    the same function the app uses.
    """
    text = (ROOT / "alembic.ini").read_text()
    url_line = [ln for ln in text.splitlines()
                if ln.strip().startswith("sqlalchemy.url")][0]
    assert url_line.split("=", 1)[1].strip() == "", (
        "sqlalchemy.url must stay empty; env.py resolves it from the app settings"
    )


def test_env_py_uses_the_application_resolver():
    env_py = (ROOT / "migrations" / "env.py").read_text()
    assert "resolve_database_url" in env_py, (
        "the migration tool must find the database the same way the app does"
    )


# --------------------------------------------------- it actually works --

def test_upgrade_head_creates_every_table(fresh_db):
    _alembic(["upgrade", "head"], fresh_db)

    import sqlite3
    con = sqlite3.connect(fresh_db.split("///", 1)[1])
    tables = {r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")}
    for expected in ("users", "api_keys", "conversations", "messages",
                     "facts", "documents"):
        assert expected in tables, f"{expected} missing after upgrade head"
    assert "alembic_version" in tables, "alembic must record what it applied"


def test_the_migrated_schema_matches_the_models(fresh_db):
    """`alembic check` fails if the database and the models disagree."""
    _alembic(["upgrade", "head"], fresh_db)
    result = _alembic(["check"], fresh_db, check=False)
    out = (result.stdout + result.stderr)
    # SQLite reflects `UUID` columns back as NUMERIC, so a naive check reports a
    # spurious type change *on SQLite only* (verified: the DDL it writes really
    # does say `UUID`). Those lines are excluded; everything else must be clean.
    unexplained = [
        ln for ln in out.splitlines()
        if "Detected" in ln and "NUMERIC() to UUID()" not in ln
    ]
    assert not unexplained, f"schema drift vs models:\n{out}"


def test_downgrade_removes_every_table(fresh_db):
    """A migration that cannot be undone is a migration you cannot back out of."""
    _alembic(["upgrade", "head"], fresh_db)
    _alembic(["downgrade", "base"], fresh_db)

    import sqlite3
    con = sqlite3.connect(fresh_db.split("///", 1)[1])
    tables = {r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")}
    for gone in ("users", "api_keys", "conversations", "messages",
                 "facts", "documents"):
        assert gone not in tables, f"{gone} survived downgrade base"


def test_the_single_head_rule(fresh_db):
    """Two heads means the history forked and `upgrade head` is ambiguous."""
    result = _alembic(["heads"], fresh_db)
    heads = [ln for ln in result.stdout.splitlines() if "(head)" in ln]
    assert len(heads) == 1, f"expected exactly one head, got: {heads}"


# ------------------------------------------- the bug this fixes --

def test_a_new_column_is_actually_applied(fresh_db):
    """The whole point: add a column to the model, migrate, and it exists.

    `create_all` cannot do this. That is the difference being tested.
    """
    _alembic(["upgrade", "head"], fresh_db)

    import sqlite3
    db_path = fresh_db.split("///", 1)[1]

    # Apply an ad-hoc migration the way a real one would run.
    from alembic import command
    from alembic.config import Config
    import importlib

    cfg = Config(str(ROOT / "alembic.ini"))
    os.environ["ALEMBIC_DATABASE_URL"] = fresh_db

    versions = sorted((ROOT / "migrations" / "versions").glob("*.py"))
    baseline = versions[0]
    mod = importlib.import_module(
        f"migrations.versions.{baseline.stem}")
    head_rev = mod.revision

    # Write a real revision file, then upgrade onto it.
    new_file = ROOT / "migrations" / "versions" / "_tmp_test_add_column.py"
    new_file.write_text(f'''"""temp: add a column"""
from alembic import op
import sqlalchemy as sa

revision = "tmpaddcol01"
down_revision = "{head_rev}"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("nickname", sa.String(50), nullable=True))


def downgrade():
    op.drop_column("users", "nickname")
''')
    try:
        _alembic(["upgrade", "head"], fresh_db)
        con = sqlite3.connect(db_path)
        cols = [r[1] for r in con.execute("pragma table_info(users)")]
        assert "nickname" in cols, (
            "the added column did not reach the database - this is the exact "
            "failure `create_all` has"
        )
        # and it is reversible
        _alembic(["downgrade", "-1"], fresh_db)
        cols = [r[1] for r in sqlite3.connect(db_path).execute("pragma table_info(users)")]
        assert "nickname" not in cols
    finally:
        new_file.unlink(missing_ok=True)


# ------------------------------------- how the application uses this --

def test_init_db_migrates_a_fresh_database(tmp_path):
    """The normal path: nothing there, so upgrade from scratch."""
    import asyncio
    from modules.database import DatabaseManager

    url = f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}"
    mgr = DatabaseManager(database_url=url)
    result = asyncio.run(mgr.init_db())
    assert result in ("migrated", "adopted")

    import sqlite3
    con = sqlite3.connect(str(tmp_path / "fresh.db"))
    tables = {r[0] for r in con.execute(
        "select name from sqlite_master where type='table'")}
    assert "users" in tables and "alembic_version" in tables


def test_init_db_adopts_an_existing_pre_migrations_database(tmp_path):
    """A deployment that predates Alembic must be ADOPTED, not clobbered.

    This is the migration path for the real world: there is already a database
    with users in it. `create_all` made it. Dropping and re-creating would
    destroy the data; running `upgrade` would fail because the tables exist but
    there is no version table. So it is stamped at the baseline.
    """
    import asyncio
    import sqlite3

    db = tmp_path / "existing.db"
    # Build a pre-migrations schema exactly the old way.
    from modules.database import Base
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")

    async def _old_init():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    asyncio.run(_old_init())
    asyncio.run(engine.dispose())

    # A row that must survive.
    con = sqlite3.connect(str(db))
    con.execute(
        "insert into users (id, username, email, hashed_password, is_active, "
        "is_admin, created_at, updated_at) values "
        "(?, 'keepme', 'k@e.com', 'x', 1, 0, '2026-01-01', '2026-01-01')",
        ("1111111111111111111111111111111111111111",),
    )
    con.commit()
    con.close()

    from modules.database import DatabaseManager
    mgr = DatabaseManager(database_url=f"sqlite+aiosqlite:///{db}")
    result = asyncio.run(mgr.init_db())

    assert result == "adopted", f"expected adoption of an existing schema, got {result}"
    con = sqlite3.connect(str(db))
    names = [r[0] for r in con.execute("select username from users")]
    assert "keepme" in names, "adopting an existing database destroyed its data"
    assert con.execute(
        "select count(*) from sqlite_master where name='alembic_version'"
    ).fetchone()[0] == 1


def test_init_db_is_idempotent(tmp_path):
    """Booting twice must not fail or duplicate anything."""
    import asyncio
    from modules.database import DatabaseManager

    url = f"sqlite+aiosqlite:///{tmp_path / 'twice.db'}"
    first = asyncio.run(DatabaseManager(database_url=url).init_db())
    second = asyncio.run(DatabaseManager(database_url=url).init_db())
    assert first in ("migrated", "adopted")
    assert second == "migrated", "a second boot should be a no-op upgrade"


def test_init_db_does_not_disable_the_application_loggers(tmp_path):
    """Running migrations in-process must not switch logging off.

    Alembic's `env.py` calls `logging.config.fileConfig`, which defaults to
    `disable_existing_loggers=True` — it sets `.disabled = True` on every logger
    that already exists. Because `init_db()` runs Alembic *inside* the live
    application, booting the app and then initialising the database silently
    killed all of its own logging for the rest of the process. Measured before
    the fix: `modules.api_utils` disabled=False before, disabled=True after.

    Logging that quietly stops is worse than logging that was never set up.
    """
    import asyncio
    import logging

    from modules.database import DatabaseManager

    sentinel = logging.getLogger("modules.api_utils")
    was_disabled = sentinel.disabled

    asyncio.run(DatabaseManager(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'log.db'}").init_db())

    assert sentinel.disabled == was_disabled, (
        "running migrations disabled an existing application logger"
    )


def test_init_db_does_not_leak_the_alembic_env_var(tmp_path):
    """The override is process-global, so it must be put back.

    A leaked ALEMBIC_DATABASE_URL makes a later migration silently target the
    wrong database — which is how this was first noticed: an unrelated test in
    another file started failing.
    """
    import asyncio

    from modules.database import DatabaseManager

    os.environ.pop("ALEMBIC_DATABASE_URL", None)
    asyncio.run(DatabaseManager(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'env.db'}").init_db())
    assert "ALEMBIC_DATABASE_URL" not in os.environ
