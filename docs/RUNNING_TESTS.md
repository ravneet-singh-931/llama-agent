# Running the tests (and why each flag is there)

The command CI runs, why each part of it exists, and the traps that cost real
time in this repo. Written from `.github/workflows/main.yml` (lines 43–70) and
from things that actually went wrong while doing this audit.

---

## The command

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install pytest pytest-cov pytest-asyncio

pytest tests/ -v -m "not mcp" --tb=short -p no:phoenix \
  --cov=. --cov-report=term --cov-fail-under=74
```

Expected, as of this audit: **1137 passed, 6 skipped, 1 xfailed**, `TOTAL` 87%.

---

## Why each flag

| flag | why it is not optional |
|---|---|
| `-m "not mcp"` | MCP tests spawn a **subprocess server**. They are marked `mcp` in `pyproject.toml`. Without the filter they either hang or fail for reasons unrelated to your change. |
| `-p no:phoenix` | Booting the app installs a **global OpenTelemetry TracerProvider**, and OTel allows that once per process. Without this, a later test that wants its own in-memory exporter silently gets the global one and collects zero spans. This is not theoretical — it broke `test_provider_client` in the full suite while it passed alone. |
| `--cov-fail-under=74` | A **ratchet**, not a target. It stops coverage sliding. Lower it only when a change legitimately deletes covered code, and say so in the commit. |
| `--cov=.` | The whole tree. `--cov=modules --cov=api` reads **70%**; `--cov=.` reads **87%**. They are not comparable — do not mix them. |

Roughly **4 minutes** for the full suite. That matters: see "loops" below.

---

## Running one file

```bash
pytest tests/test_auth_routes.py -q
```

Prefer this while iterating. The full suite is for the end.

---

## The traps, each one measured

**Tests need a fixture, not just a URL.** Nearly every integration test uses
`journey` (`tests/conftest.py` line 45), which boots the real app on a temp
directory with a fake LLM, in-memory SQLite and no network. A hand-written
probe that calls a DB-backed endpoint without it fails `503 Database
unavailable` — that is the missing fixture, not a product bug.

**The brute-force store is global *and* keyed by client IP.** One test that
trips the login lockout poisons every later test in the file
(`429 Try again in 899 seconds`). Snapshot and restore it around the test:

```python
@pytest.fixture
def fresh_user(journey):
    import api
    saved = dict(api.brute_force.attempts)
    yield journey
    api.brute_force.attempts.clear()
    api.brute_force.attempts.update(saved)
```

Its keys are `login:<identifier>`, not the bare username.

**The rate limiter is global too.** `rate_limiter._requests.clear()`.

**Registration sets cookies.** A test that then checks "the old cookie is gone"
must `cookies.clear()` first, or it is reading a leftover.

**`TestClient` re-raises server exceptions.** For a 500-path test, construct it
with `raise_server_exceptions=False`.

**A blocking read can hang the suite.** `ws.receive_text()` waits forever if the
server never speaks, and `asyncio.wait_for` does *not* interrupt it (starlette's
receive is blocking). Bound it in a thread and close the connection in `finally`.
See `MY_ERRORS.md` #16.

**Coverage misreports `raise` lines as unexecuted.** Seen three times. If a line
looks missing but you know the path ran, prove it by substituting a mutation, not
by trusting the report.

---

## The traps that are about *process*

**Do not loop on the full suite.** Four minutes × "let me just re-run it" is how
a session disappears. Run the one file, write results to a log, and run the full
suite once at the end.

**`/tmp` does not survive a reset.** A venv at `/tmp/vf` is convenient and
disposable. Check `git log --oneline -1` before assuming earlier context exists.

**Sabotage one thing per command.** Breaking code to check that a test notices is
the strongest verification available here, but a loop of four mutations in one
long command will get interrupted and can leave a **sabotaged file on disk**.
That happened four times in this audit. Diff against a backup after every one.

---

## Before you push

```bash
flake8 . --max-line-length=120
python -m pytest tests/ -q -m "not mcp" -p no:phoenix
```
plus the doc-link check below, because a broken relative link in a findings file
is easy to create and invisible until someone clicks it:

```bash
python - <<'PY'
import re, pathlib
bad = 0
for p in pathlib.Path("docs").glob("*.md"):
    for m in re.finditer(r"\]\(([^)#][^)]*)\)", p.read_text()):
        t = m.group(1)
        if t.startswith(("http", "mailto")): continue
        if not (p.parent / t.split("#")[0]).exists():
            print(f"BROKEN {p} -> {t}"); bad += 1
print(bad, "broken")
PY
```
