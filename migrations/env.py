"""Alembic environment.

Two things here are deliberate and worth reading before changing them.

**1. The database URL is not written down twice.**

`alembic.ini` leaves `sqlalchemy.url` empty on purpose. The URL is resolved here
by calling `modules.secrets.resolve_database_url()` — the same function the
application uses at startup. This repository has a history of the same value
living in two places and drifting (two feature-flag systems, two counters of the
same cache generation), and a migration tool pointed at a different database
than the app is the worst version of that mistake: the schema would be correct
and the application would still be broken.

**2. Async URLs are converted, not required to be different.**

The app uses `postgresql+asyncpg://`. Alembic runs migrations synchronously, so
the driver name is rewritten to a synchronous one here. The URL itself — host,
port, database, credentials — is untouched.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the repository importable when alembic is run from anywhere.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.database import Base          # noqa: E402
from modules.secrets import resolve_database_url   # noqa: E402

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False is REQUIRED, not tidiness.
    #
    # This file is also run *in-process* by `DatabaseManager.init_db()` via
    # alembic.command, from inside a live application. `fileConfig` defaults to
    # disable_existing_loggers=True, which sets `.disabled = True` on every
    # logger that already exists — so booting the app and then initialising the
    # database silently switched off the application's own logging for the rest
    # of the process. Measured: `modules.api_utils` disabled=False before
    # init_db, disabled=True after.
    #
    # Logging that quietly stops working is worse than logging that was never
    # configured, because nobody goes looking for it.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _sync_url(url: str) -> str:
    """Return a URL Alembic can drive synchronously.

    Only the driver is swapped; every other part of the URL is preserved so the
    migration always targets the database the application will use.
    """
    replacements = {
        "postgresql+asyncpg://": "postgresql+psycopg2://",
        "postgresql+psycopg://": "postgresql+psycopg2://",
        "sqlite+aiosqlite://": "sqlite://",
    }
    for async_driver, sync_driver in replacements.items():
        if url.startswith(async_driver):
            return sync_driver + url[len(async_driver):]
    return url


def get_url() -> str:
    """The one database this project talks to."""
    # An explicit override exists for tests and for `alembic` against a scratch
    # database; it is deliberately not read by the application.
    override = os.getenv("ALEMBIC_DATABASE_URL")
    return _sync_url(override or resolve_database_url())


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (`alembic upgrade --sql`)."""
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,          # detect column type changes
            compare_server_default=True,  # detect default changes
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
