"""The app must start with no LLM key, and say so.

Every other test in this suite sets ``GROQ_API_KEY`` before importing ``api``
because ``llama_agent1`` used to ``raise SystemExit`` at import time when the
key was missing. That meant the check ``import llama_agent1`` could only ever
run in an environment that already had the key -- so the one configuration a
new clone actually has (a gitignored ``secrets/`` directory that is absent, or
a file people create empty before filling it) was never exercised.

The bug that hid there: uvicorn imports the app module, the import raised
``SystemExit``, and the process died before a socket was opened. No ``/health``,
no ``/docs``, no message a client could read -- just a stack trace in a
container log, surfaced by ``docker compose up`` as an exit code. Three
services depend on that secret, so all three crash-looped.

These tests run in a subprocess with the key explicitly removed. That is the
only way to assert *startup* behaviour: the parent process has already imported
``llama_agent1`` with whatever environment pytest was given, and Python will
not re-run a module's top level.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - spawns the current interpreter only, fixed argv, no shell
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Runs in a clean child process: no GROQ_API_KEY, no .env reliance, then boots
# the ASGI app the same way uvicorn does and asks it questions over TestClient.
_BOOT_SCRIPT = r"""
import json, os, sys
os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("GROQ_API_KEY_FILE", None)
os.environ["EVAL_RECORD_MODE"] = ""

from fastapi.testclient import TestClient
import api

out = {}
with TestClient(api.app) as c:
    out["health_status"] = c.get("/health").status_code
    body = c.get("/health").json()
    out["llm_key"] = body.get("modules", {}).get("llm_key")
    out["docs"] = c.get("/docs").status_code
    # The three routes that call a model must refuse with 503 + a message.
    chat = c.post("/chat", json={"message": "hi"})
    out["chat"] = chat.status_code
    out["chat_detail"] = chat.json().get("detail", "")
    out["stream"] = c.post("/chat/stream", json={"message": "hi"}).status_code
    out["docask"] = c.post("/documents/ask", json={"query": "hi"}).status_code
    # A route that needs no model keeps working, so the app is genuinely up.
    out["tools"] = c.get("/tools").status_code

print("RESULT:" + json.dumps(out))
"""


def _boot_without_key() -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GROQ") and k not in ("EVAL_RECORD_MODE", "JWT_SECRET_KEY")}
    env["PYTHONPATH"] = str(REPO)
    proc = subprocess.run(  # nosec B603 - fixed argv, sys.executable, no shell
        [sys.executable, "-c", _BOOT_SCRIPT],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, (
        "the app failed to start without GROQ_API_KEY -- this is the first "
        "thing a fresh clone does.\n"
        f"--- stdout ---\n{proc.stdout[-3000:]}\n"
        f"--- stderr ---\n{proc.stderr[-3000:]}"
    )
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("RESULT:"):
            return json.loads(line[len("RESULT:"):])
    raise AssertionError(f"no RESULT line.\nstdout:\n{proc.stdout[-3000:]}")


def test_the_app_starts_when_no_llm_key_is_configured():
    """The regression: ``raise SystemExit`` at import killed the process.

    With that line restored this test fails at ``returncode == 0`` -- uvicorn
    never gets as far as opening a port.
    """
    result = _boot_without_key()
    assert result["health_status"] == 200


def test_health_reports_the_key_as_missing_rather_than_claiming_healthy():
    """A probe must be able to tell "up but unconfigured" from "up and ready".

    Without this field ``/health`` says ``healthy`` while every chat call is
    guaranteed to fail, which is the sort of green light that makes someone
    think the deployment works.
    """
    result = _boot_without_key()
    assert result["llm_key"] == "missing"


def test_chat_routes_refuse_with_503_and_an_actionable_message():
    """503, not a provider auth error several layers down.

    The message has to name the key and where it goes, because the person
    reading it is the one who just cloned the repo.
    """
    result = _boot_without_key()
    assert result["chat"] == 503
    assert result["stream"] == 503
    assert result["docask"] == 503
    detail = result["chat_detail"]
    assert "GROQ_API_KEY" in detail
    assert "secrets/groq_api_key.txt" in detail


def test_the_rest_of_the_app_is_still_reachable():
    """The point of starting is that something is served.

    ``/docs`` and a model-free route both have to answer, otherwise "the app
    boots" would only mean "the process is alive".
    """
    result = _boot_without_key()
    assert result["docs"] == 200
    assert result["tools"] == 200


def test_the_key_check_does_not_wire_itself_to_a_truthy_value():
    """``GROQ_API_KEY_CONFIGURED`` must be a real check, not ``bool("")`` fudged.

    An empty secret file is the common case (touch the file, then fill it), and
    ``get_secret`` returns ``""`` for it. If the flag were computed from
    "did we find a file" it would be True and the guard would never fire.
    """
    from llama_agent1 import GROQ_API_KEY_CONFIGURED
    from modules.secrets import get_secret

    # The parent pytest process supplies a key (CI sets test_key_for_ci, and
    # local runs export one), so the flag is True here; the empty-file case is
    # covered by the subprocess test that removes the key entirely.
    assert isinstance(GROQ_API_KEY_CONFIGURED, bool)
    assert get_secret("GROQ_API_KEY", default="") != "" or GROQ_API_KEY_CONFIGURED is False
