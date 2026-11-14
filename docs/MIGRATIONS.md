# Database migrations

`alembic` is the schema tool. This document explains what was wrong before it
was configured, how to use it, and what is still unverified.

## What was wrong

`modules/database.py::init_db()` called `Base.metadata.create_all`. That creates
**missing tables and does nothing else** — it never adds a column to a table that
already exists. Reproduced directly:

```
v1 columns: ['id', 'name']
v2 columns: ['id', 'name']          <- the model has a new field
create_all added the new column: False
```

Consequence: a release that adds a field to `users` starts cleanly, logs
`✅ Database initialized`, and then fails **every query that reads that field**,
at request time, in production. Nothing warns you at startup, and the failure
looks like a code bug rather than a schema problem.

The dependency was already there — `alembic==1.20.0` appears in
`requirements.txt` — with no `alembic.ini`, no `migrations/` directory, and no
import anywhere in the codebase. Installed on every build, used by nothing.

## How it works now

Three rules, each of which exists because of a specific failure.

**1. One source of truth for the database URL.**

`alembic.ini` leaves `sqlalchemy.url` empty. `migrations/env.py` resolves the URL
by calling `modules.secrets.resolve_database_url()` — the same function the
application uses at startup. A migration tool pointed at a different database
than the app is the worst version of duplicated configuration: the schema is
correct and the application is still broken.

**2. A pre-migrations deployment is adopted, not clobbered.**

`init_db()` checks for an `alembic_version` table:

| database state | action |
|---|---|
| empty | `alembic upgrade head` |
| has our tables, no `alembic_version` | **stamp at baseline** (adopt) |
| has `alembic_version` | `alembic upgrade head` |
| alembic unavailable | fall back to `create_all`, say so loudly |

The second row is the real-world one: someone is already running with data made
by `create_all`. Dropping and re-creating would destroy it; running `upgrade`
would fail because the tables exist but the version table does not. A test
inserts a row, adopts the database, and asserts the row survives.

**3. Running migrations must not disturb the running app.**

`env.py` calls `logging.config.fileConfig` with
**`disable_existing_loggers=False`**. The default is `True`, which sets
`.disabled = True` on every logger that already exists. Because `init_db()` runs
Alembic *in-process*, booting the app and then initialising the database
silently killed all of the application's own logging for the rest of the
process:

```
before init_db: disabled = False
after  init_db: disabled = True     <-- logging gone, silently
```

That was caught by an unrelated test going red in the full suite. Logging that
quietly stops is worse than logging that was never configured, because nobody
goes looking for it.

The same class of bug: `init_db()` sets `ALEMBIC_DATABASE_URL` to hand the
target to `env.py`, and **restores it afterwards**. A leaked value makes a later
migration silently target the wrong database.

## Using it

```bash
alembic current                 # what revision is the database on?
alembic upgrade head            # apply everything pending
alembic downgrade -1            # back out one revision
alembic history                 # the full list
alembic revision --autogenerate -m "add users.nickname"
```

`--autogenerate` compares the models to the database and writes the operations.
**Read what it wrote** before committing: it cannot see renames (it will drop and
create), and it does not know about data migrations.

Point a migration at a scratch database with
`ALEMBIC_DATABASE_URL=sqlite:///scratch.db` — that variable is read *only* by
`migrations/env.py`, never by the application.

## What is verified, and what is not

13 tests in `tests/test_migrations.py` run the real Alembic command. Verified:

* `upgrade head` from empty creates all six tables and `alembic_version`
* `downgrade base` removes them again
* exactly one head (a forked history makes `upgrade head` ambiguous)
* the migrated schema matches the models
* adding a column, migrating, and reading it back — **the exact thing
  `create_all` cannot do** — and the downgrade removes it
* adopting an existing `create_all` database preserves its data
* booting twice is a no-op
* logging survives, and the env override is not leaked

**Not verified: Postgres.** The tests run against SQLite because that is what
this environment has. Two consequences:

1. SQLite reflects `UUID` columns back as `NUMERIC`, so `alembic check` reports a
   spurious type change *there only*. The migration writes `UUID` (confirmed in
   the generated DDL); on Postgres the comparison is clean. The test excludes
   exactly those lines and fails on anything else.
2. PostgreSQL-specific behaviour — transactional DDL, locks taken by
   `ALTER TABLE`, how long a migration holds a write lock on a large table — is
   untested here. Before the first real production migration, run
   `alembic upgrade head` against a copy of the production database.

**Not done: CI enforcement.** Nothing currently fails a pull request that changes
a model without adding a migration. The check would be
`alembic upgrade head && alembic check` against a scratch Postgres; it belongs
with the Postgres service in CI, which does not exist yet.
