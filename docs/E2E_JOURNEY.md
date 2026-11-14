# The end-to-end journey, and why it took until now to have one

`tests/test_e2e_journey.py` — 7 tests, ~4 seconds. It registers a user, logs
them in, uploads a real file, asks a question about it, chats, and logs out,
against the **real FastAPI app**: its lifespan, middleware, cookies, auth,
per-owner document scoping and the document answering path. Only the language
model is faked, at its narrowest point.

Nothing in the repository tested that. Every part was proven and the assembly
never was — which is the state where a wiring mistake survives indefinitely,
because each module stays correct while the thing they form is not.

## The gap it filled

Before this file, coverage of `api.py` was **46%** and 13 of its 35 routes were
under 50%. The routes that require a *session* — `/auth/login`, `/auth/me`,
`/remember`, `/facts`, `/conversations`, `/documents/ask` — were the least
covered, because testing them means building an authenticated client, and no
test did.

## Four things this test found while being written

Each of these was a wrong assumption of mine, discovered by running the code:

1. **`/auth/register` requires `email`.** I assumed username + password. It is
   `username`, `email`, `password`. A 422, not a crash.
2. **`/auth/me` nests the user under `"user"`.** My assertion read the top level.
3. **Patching `modules.database.get_db_manager` is not enough.** Both `api.py`
   and `modules/auth.py` did `from modules.database import get_db_manager`, so
   each holds its *own* binding, and the routes use a shared `user_manager`
   object that captured a DB at import. Three things had to point at the
   temporary engine before registration stopped dialling Postgres.
4. **`register` sets the session cookies too.** So a test that registers and
   then logs in was passing on registration's cookies — **login could have been
   completely broken and every test would still have gone green.** The guard
   test now clears cookies between the two steps. Verified: sabotaging `login`
   to skip `_set_auth_cookies` fails it.

## It also broke the suite, and that is the most useful finding

Booting the real lifespan runs `api.py`'s `phoenix.otel.register(...)`, which
installs a **global OpenTelemetry TracerProvider**. OTel permits that once per
process: later attempts log `Overriding of current TracerProvider is not allowed`
and are ignored.

Consequence: `test_provider_client::test_swapping_the_vendor_keeps_the_llm_span`
— which installs its own in-memory exporter to assert that LLM spans are
produced — collected **zero spans** and failed. It passed in isolation and only
failed in the full suite, because my new file had already claimed the provider.

That is a test-pollution bug I introduced, and it is the kind that would have
looked like "flaky CI" and been re-run until it went away. Fixed by stubbing
`phoenix.otel.register` in the fixture, with the reason written down.

> A test that boots the real application can change process-wide state that
> every later test depends on. Isolation is not automatic just because the test
> asserts nothing global.

## What it does not cover

* **No real LLM.** Correct for a test that must run free in CI; it means model
  *quality* is out of scope, and that is what `evals/` is for.
* **No real Postgres or Redis.** Auth runs on in-memory SQLite; the Redis-backed
  paths are exercised elsewhere with fakes. The one test that boots the true
  `lifespan` still cannot prove a real database connection.
* **No browser.** The frontend has 0 tests of its own; this proves the HTTP
  contract, not that the React components call it correctly.

## Offline

The fake LLM keeps this free and hermetic, but note that
`test_provider_client`'s tracer test is process-global and order-sensitive; if
another test ever boots the lifespan without stubbing `register`, that failure
returns. The stub is in this file's fixture for exactly that reason.
