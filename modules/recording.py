"""Record and replay the eval suite's non-deterministic inputs.

The problem this solves, stated as arithmetic: a full 60-case eval run needs
~314,330 input tokens and the Groq free tier allows 200,000 a day. A full run
therefore **cannot finish in one day**, which means nobody who clones this
repo can reproduce the numbers in the README. They exist in CI logs and
nowhere else.

This module makes the model's answers a file. Record a run once, then replay
it in seconds for zero tokens, on every pull request, on any machine.

What is recorded
----------------
Three things vary between runs. All three are captured:

  1. **Model replies** (`llm`) - keyed by a hash of model, messages,
     tools and sampling parameters. Both the non-streaming response and the
     streaming chunk list.
  2. **Tool results** (`tool`) - keyed by tool name plus arguments. Without
     this, replay calls Tavily for real and the answers drift for reasons
     that have nothing to do with the code under test.
  3. **The clock** (`clock`) - `modules.prompt_rules.today_str()` is injected
     into *every* system prompt, so a recording made on Tuesday replayed on
     Wednesday misses every single key. The date is stored in the manifest
     and served from there during replay.

What is *not* recorded, deliberately
------------------------------------
* Latency, timestamps, run ids - they differ every run and would make every
  diff meaningless.
* Token counts are recorded for reference but never used for grading.

The honest limit
----------------
A recording is a snapshot, not a live test. Replay proves the **grader** is
deterministic and that no numbers moved. It does not prove the **agent**
answers well today - that is what the nightly does. Both are needed: replay
is fast enough for every PR, the nightly is not.

Usage
-----
    # once, spending quota
    eval_record = Recording.begin(directory, mode="record")

    # later, free
    Recording.begin(directory, mode="replay")

Mode comes from `EVAL_RECORD_MODE` (off | record | replay) and
`EVAL_RECORD_DIR`; `off` makes every function here a no-op, so the normal
nightly path is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

_log = logging.getLogger("llama_agent.recording")

#: off - hit the provider, record nothing (the nightly's behaviour)
#: record - hit the provider and write what came back
#: replay - never hit the provider; serve from disk or fail loudly
MODES = ("off", "record", "replay")

MANIFEST_NAME = "manifest.json"


class RecordingMiss(RuntimeError):
    """A replay was asked for a value that was never recorded.

    Raised rather than silently falling through to the network. A replay that
    quietly hits the provider is not a replay: it spends quota, and it returns
    a different answer while the report still says "replayed". The whole point
    is that the run is reproducible, so a miss has to be an error.
    """


def _key(payload: Any) -> str:
    """Stable short hash of a JSON-serialisable payload."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class Recording:
    """Process-wide record/replay session. One at a time; use `begin()`."""

    _current: Optional["Recording"] = None
    _lock = threading.Lock()

    def __init__(self, directory: str, mode: str, run_date: str = ""):
        self.directory = directory
        self.mode = mode
        self.run_date = run_date or ""
        self._llm: Dict[str, Any] = {}
        self._tools: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._writes = 0
        #: set True by finish(); stays False if the run died part-way.
        self.complete = False
        os.makedirs(directory, exist_ok=True)

    # ---------------------------------------------------------------- setup

    @classmethod
    def begin(cls, directory: str, mode: str) -> "Recording":
        """Start a session and install it as the process-wide current one."""
        mode = (mode or "off").strip().lower()
        if mode not in MODES:
            raise ValueError(f"EVAL_RECORD_MODE must be one of {MODES}, got {mode!r}")
        with cls._lock:
            rec = cls(directory, mode)
            rec._load()
            cls._current = rec
            if mode != "off":
                _log.info("recording: mode=%s dir=%s date=%s llm=%d tools=%d",
                          mode, directory, rec.run_date or "(none)",
                          len(rec._llm), len(rec._tools))
            return rec

    @classmethod
    def current(cls) -> Optional["Recording"]:
        return cls._current

    @classmethod
    def stop(cls) -> None:
        with cls._lock:
            if cls._current is not None and cls._current.mode != "off":
                cls._current._save_manifest()
            cls._current = None

    @property
    def active(self) -> bool:
        return self.mode in ("record", "replay")

    def _path(self, kind: str) -> str:
        return os.path.join(self.directory, f"{kind}.jsonl")

    def _load(self) -> None:
        """Read whatever is already on disk (replay needs it; record appends)."""
        for kind, store in (("llm", self._llm), ("tool", self._tools)):
            path = self._path(kind)
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue          # a torn last line from a killed run
                    if "key" in row:
                        store[row["key"]] = row.get("value")
        manifest = os.path.join(self.directory, MANIFEST_NAME)
        if os.path.exists(manifest):
            try:
                with open(manifest, "r", encoding="utf-8") as fh:
                    self.run_date = self.run_date or json.load(fh).get("run_date", "")
            except (OSError, json.JSONDecodeError):
                pass

    def _append(self, kind: str, key: str, value: Any) -> None:
        with self._lock:
            path = self._path(kind)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"key": key, "value": value},
                                    ensure_ascii=False, default=str) + "\n")
            self._writes += 1

    def _save_manifest(self) -> None:
        path = os.path.join(self.directory, MANIFEST_NAME)
        data = {
            "run_date": self.run_date,
            "llm_calls": len(self._llm),
            "tool_calls": len(self._tools),
            # Complete is False when the process never reached the end of the
            # suite -- a quota cap, a crash, a killed job. A partial recording
            # is still worth keeping: every case it does contain replays for
            # free, which is the difference between a wasted night and a
            # usable artefact. It must not be presented as a full run, though,
            # so the flag is recorded rather than assumed.
            "complete": self.complete,
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except OSError as e:                      # a manifest is not worth a crash
            _log.warning("recording: could not write manifest: %s", e)

    def finish(self) -> None:
        """Mark the run complete and write the manifest.

        Called by the runner after the last case. Anything that ends the
        process earlier (an unhandled 429, the job timeout) leaves the flag
        False, and the manifest says so.
        """
        self.complete = True
        self._save_manifest()

    # ------------------------------------------------------------- model path

    def llm(self, request: Dict, produce) -> Any:
        """Serve (replay) or capture (record) one model call.

        `request` is the identity of the call: model, messages, tools and the
        sampling parameters that change the answer. `produce` is a zero-arg
        callable that performs the real request; it is only invoked in `record`
        mode, and only on a miss.
        """
        key = _key(request)
        if self.mode == "replay":
            with self._lock:
                if key in self._llm:
                    self._hits += 1
                    return self._llm[key]
                self._misses += 1
            raise RecordingMiss(
                f"no recorded model reply for {request.get('model')} "
                f"(key {key}). The recording at {self.directory} was made with "
                f"different prompts, tools or sampling settings - re-record it, "
                f"or run with EVAL_RECORD_MODE=off."
            )
        value = produce()
        if self.mode == "record":
            # First write wins. Appending a second value for the same key would
            # make the *last* recording the one replayed, so re-running a
            # record would silently change what a committed recording means -
            # and the report would still call it the same recording.
            with self._lock:
                known = key in self._llm
            if not known:
                self._llm[key] = value
                self._append("llm", key, value)
            # Hand back the FIRST recording, not what the provider just said.
            # Otherwise the run that records and the run that replays can
            # disagree, and the committed artefact is the one that was never
            # used.
            return self._llm[key]
        return value

    # -------------------------------------------------------------- tool path

    def tool(self, name: str, arguments: Dict, produce) -> str:
        """Serve (replay) or capture (record) one tool result."""
        key = _key({"name": name, "arguments": arguments})
        if self.mode == "replay":
            with self._lock:
                if key in self._tools:
                    self._hits += 1
                    return self._tools[key]
                self._misses += 1
            raise RecordingMiss(
                f"no recorded result for tool {name!r} with these arguments "
                f"(key {key}). Replay refuses to call a real tool: that would "
                f"spend quota and return a different answer."
            )
        value = produce()
        if self.mode == "record":
            with self._lock:
                known = key in self._tools
            if not known:
                self._tools[key] = value
                self._append("tool", key, value)
        return value

    # ------------------------------------------------------------------ clock

    def today(self) -> Optional[str]:
        """The recorded date, or None when not replaying.

        Returns None in record mode so the caller uses the real clock and the
        recording captures whatever that clock said.
        """
        if self.mode == "replay":
            return self.run_date or None
        return None

    def stamp_date(self) -> None:
        """Remember today's date so a replay tomorrow finds the same key.

        The manifest is written immediately, not only at the end of the run.
        A run that dies at case 55 (a quota cap, a killed job) never reaches
        finish(), and without a manifest the recorded date is lost - which
        makes every reply in it unreplayable, because the date is part of
        every prompt. A partial recording with a date is worth keeping; a
        partial recording without one is scrap.
        """
        if self.mode == "record" and not self.run_date:
            from modules.prompt_rules import today_str
            self.run_date = today_str()
            self._save_manifest()

    # ----------------------------------------------------------------- stats

    def stats(self) -> Dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "writes": self._writes}


# --------------------------------------------------------------- convenience

def mode() -> str:
    """The active mode.

    An explicitly started session wins over the environment variable. Reading
    only the env var made `Recording.begin(dir, "record")` start a session that
    the call sites then ignored - the session existed, the flag said otherwise,
    and every call went to the provider while the recorder wrote nothing.
    """
    rec = Recording.current()
    if rec is not None:
        return rec.mode
    return (os.getenv("EVAL_RECORD_MODE", "off") or "off").strip().lower()


def directory() -> str:
    return os.getenv("EVAL_RECORD_DIR", "evals/recordings") or "evals/recordings"


def replaying() -> bool:
    rec = Recording.current()
    return bool(rec and rec.mode == "replay")


def llm_call(request: Dict, produce):
    """Record/replay a model call. A no-op when recording is off."""
    rec = Recording.current()
    if rec is None or not rec.active:
        return produce()
    return rec.llm(request, produce)


def tool_call(name: str, arguments: Dict, produce):
    """Record/replay a tool call. A no-op when recording is off."""
    rec = Recording.current()
    if rec is None or not rec.active:
        return produce()
    return rec.tool(name, arguments, produce)


def stamp_date() -> None:
    """Remember today's date on the active recording (record mode only)."""
    rec = Recording.current()
    if rec is not None and rec.mode == "record":
        rec.stamp_date()


def today_override() -> Optional[str]:
    """The recorded date during replay, else None."""
    rec = Recording.current()
    return rec.today() if rec else None


# --------------------------------------------------------------- serialisation
#
# A recorded reply has to come back as something the callers can use with
# attribute access: `resp.choices[0].message.content`, `tc.function.name`,
# `chunk.choices[0].delta.content`. Returning plain dicts would turn every
# one of those into an AttributeError, so the shapes are rebuilt as real
# pydantic models from the Groq SDK when it is importable, and as duck-typed
# stand-ins when it is not (the eval's offline gate never installs groq).
#
# Only the fields the app actually reads are serialised. Recording a whole
# response verbatim would pin provider internals that change between versions
# and bloat the file for data nothing consumes.


def encode_response(resp: Any) -> Dict:
    """Reduce a non-streaming completion to the fields the app reads."""
    if isinstance(resp, dict):
        return resp
    out: Dict[str, Any] = {"choices": []}
    for ch in getattr(resp, "choices", None) or []:
        msg = getattr(ch, "message", None)
        entry: Dict[str, Any] = {"message": {
            "role": getattr(msg, "role", "assistant"),
            "content": getattr(msg, "content", "") or "",
        }}
        calls = getattr(msg, "tool_calls", None) or []
        if calls:
            entry["message"]["tool_calls"] = [
                {
                    "id": getattr(tc, "id", f"call_{i}"),
                    "type": getattr(tc, "type", "function"),
                    "function": {
                        "name": getattr(getattr(tc, "function", None), "name", ""),
                        "arguments": getattr(getattr(tc, "function", None),
                                            "arguments", "") or "",
                    },
                }
                for i, tc in enumerate(calls)
            ]
        out["choices"].append(entry)
    usage = getattr(resp, "usage", None)
    if usage is not None:
        out["usage"] = {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
        }
    return out


def encode_chunk(chunk: Any) -> Dict:
    """Reduce one streaming chunk to {content, tool_calls} - all that is read."""
    if isinstance(chunk, dict):
        return chunk
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        return {"content": ""}
    delta = getattr(choices[0], "delta", None)
    entry: Dict[str, Any] = {"content": getattr(delta, "content", None) or ""}
    calls = getattr(delta, "tool_calls", None) or []
    if calls:
        entry["tool_calls"] = [
            {
                "index": getattr(tc, "index", 0),
                "id": getattr(tc, "id", None),
                "function": {
                    "name": getattr(getattr(tc, "function", None), "name", None),
                    "arguments": getattr(getattr(tc, "function", None),
                                        "arguments", None),
                },
            }
            for tc in calls
        ]
    return entry


class _Obj:
    """Attribute access over a dict, for when the SDK is unavailable."""

    def __init__(self, data: Dict):
        self._data = data or {}
        for k, v in self._data.items():
            setattr(self, k, _wrap(v))

    def __getattr__(self, name):          # only reached for missing attributes
        return None


def _wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return _Obj(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def decode_response(encoded: Dict) -> Any:
    """Rebuild a completion the callers can attribute-access."""
    try:
        from groq.types.chat import ChatCompletion
        return ChatCompletion.model_validate(encoded)
    except Exception:
        return _Obj(encoded)


def encode_stream(chunks: List[Any]) -> List[Dict]:
    return [encode_chunk(c) for c in chunks]


def decode_stream(encoded: List[Dict]):
    """Yield stand-ins with `choices[0].delta.content`."""
    for entry in encoded or []:
        yield _Obj({"choices": [{"delta": {
            "content": entry.get("content", ""),
            "tool_calls": entry.get("tool_calls"),
        }}]})
