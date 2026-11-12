"""Record/replay for the eval suite.

The point of these tests is not that recording works when everything is
correct. It is that the *failure* modes are loud:

* a replay must never call the provider, even once;
* a replay must never call a real tool, even once;
* a missing key must raise, not quietly go to the network;
* a recording made on one date must replay on another.

A replay that silently falls through to the provider is worse than no replay
at all: it spends quota and returns a different answer under the same name.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules import recording
from modules.recording import Recording, RecordingMiss


@pytest.fixture(autouse=True)
def _clean_session(tmp_path, monkeypatch):
    """Every test starts with no current session and an isolated directory."""
    Recording.stop()
    monkeypatch.setenv("EVAL_RECORD_MODE", "off")
    yield
    Recording.stop()


# ------------------------------------------------------------------ identity

def test_two_requests_with_different_messages_get_different_keys(tmp_path):
    """The key must cover the messages. If it does not, a replay answers one
    case with another case's recording - and reports a pass that never
    happened."""
    a = {"model": "m", "messages": [{"role": "user", "content": "one"}]}
    b = {"model": "m", "messages": [{"role": "user", "content": "two"}]}
    assert recording._key(a) != recording._key(b)


def test_two_requests_with_different_models_get_different_keys():
    a = {"model": "fast", "messages": [{"role": "user", "content": "x"}]}
    b = {"model": "strong", "messages": [{"role": "user", "content": "x"}]}
    assert recording._key(a) != recording._key(b)


def test_the_same_request_gets_the_same_key_regardless_of_dict_order():
    """json.dumps(sort_keys=True): a replay must not miss because a caller
    built the same request in a different order."""
    a = {"model": "m", "messages": [{"role": "user", "content": "x"}], "tools": None}
    b = {"tools": None, "messages": [{"role": "user", "content": "x"}], "model": "m"}
    assert recording._key(a) == recording._key(b)


def test_the_tool_key_covers_the_arguments():
    """Two searches with different queries must not share a recording."""
    assert (recording._key({"name": "web_search", "arguments": {"query": "a"}})
            != recording._key({"name": "web_search", "arguments": {"query": "b"}}))


# -------------------------------------------------------------------- record

def test_record_writes_a_reply_and_replay_serves_it(tmp_path):
    d = str(tmp_path / "rec")
    req = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}

    rec = Recording.begin(d, "record")
    calls = []

    def produce():
        calls.append(1)
        return {"choices": [{"message": {"content": "hello"}}]}

    assert rec.llm(req, produce) == {"choices": [{"message": {"content": "hello"}}]}
    assert len(calls) == 1
    Recording.stop()

    # Now replay: the provider must not be called at all.
    rec = Recording.begin(d, "replay")
    served = rec.llm(req, lambda: pytest.fail("replay called the provider"))
    assert served == {"choices": [{"message": {"content": "hello"}}]}
    assert rec.stats()["hits"] == 1


def test_replay_raises_on_a_miss_instead_of_calling_the_provider(tmp_path):
    """The important one. A miss must be an error, not a quiet network call."""
    d = str(tmp_path / "rec")
    Recording.begin(d, "record")
    Recording.stop()

    rec = Recording.begin(d, "replay")
    with pytest.raises(RecordingMiss):
        rec.llm({"model": "m", "messages": [{"role": "user", "content": "never recorded"}]},
                lambda: pytest.fail("replay fell through to the provider"))
    assert rec.stats()["misses"] == 1


def test_a_second_record_run_does_not_duplicate_a_key(tmp_path):
    """Recording the same request twice must not grow the file without bound;
    the second run re-reads what the first wrote."""
    d = str(tmp_path / "rec")
    req = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}

    Recording.begin(d, "record").llm(req, lambda: {"v": 1})
    Recording.stop()
    Recording.begin(d, "record").llm(req, lambda: {"v": 2})
    Recording.stop()

    rec = Recording.begin(d, "replay")
    assert rec.llm(req, lambda: pytest.fail("missed")) == {"v": 1}
    assert rec.stats()["hits"] == 1


# ---------------------------------------------------------------------- tools

def test_a_tool_result_is_replayed_without_running_the_tool(tmp_path):
    d = str(tmp_path / "rec")
    args = {"query": "formula 1"}

    Recording.begin(d, "record")
    recording.tool_call("web_search", args, lambda: "3 results")
    Recording.stop()

    Recording.begin(d, "replay")
    out = recording.tool_call(
        "web_search", args,
        lambda: pytest.fail("replay ran a real tool: this would go to the network"))
    assert out == "3 results"


def test_a_tool_miss_raises(tmp_path):
    d = str(tmp_path / "rec")
    Recording.begin(d, "record")
    Recording.stop()
    Recording.begin(d, "replay")
    with pytest.raises(RecordingMiss):
        recording.tool_call("web_search", {"query": "different"},
                            lambda: pytest.fail("replay ran a real tool"))


# ---------------------------------------------------------------------- clock

def test_replay_serves_the_recorded_date_not_todays(tmp_path, monkeypatch):
    """Every system prompt carries today's date. Without pinning it, a
    recording made on one day misses every key on the next."""
    d = str(tmp_path / "rec")
    Recording.begin(d, "record")
    rec = Recording.current()
    rec.run_date = "Friday, 23 October 2026 (Asia/Kolkata)"
    rec._save_manifest()
    Recording.stop()

    Recording.begin(d, "replay")
    from modules.prompt_rules import today_str
    assert today_str() == "Friday, 23 October 2026 (Asia/Kolkata)"


def test_off_mode_uses_the_real_clock(tmp_path, monkeypatch):
    """The pin must not leak into the nightly. `off` means the app behaves
    exactly as it did before this module existed."""
    monkeypatch.setenv("EVAL_RECORD_MODE", "off")
    Recording.stop()
    from modules.prompt_rules import today_str
    assert "Asia/Kolkata" in today_str()
    assert today_override_is_none()


def today_override_is_none():
    return recording.today_override() is None


# ------------------------------------------------------------------- no-op path

def test_everything_is_a_no_op_when_recording_is_off():
    """Off is the default and the nightly's behaviour: calls pass straight
    through, nothing is written, nothing is read."""
    Recording.stop()
    assert recording.mode() == "off"
    assert recording.llm_call({"x": 1}, lambda: "live") == "live"
    assert recording.tool_call("t", {}, lambda: "live") == "live"
    assert recording.today_override() is None


# --------------------------------------------------------------------- codec

class _Fn:
    def __init__(self, name, arguments):
        self.name, self.arguments = name, arguments


class _TC:
    def __init__(self, i, name, arguments):
        self.id, self.type, self.function = i, "function", _Fn(name, arguments)


class _Msg:
    def __init__(self, content="", tool_calls=None):
        self.role, self.content, self.tool_calls = "assistant", content, tool_calls or []


class _Choice:
    def __init__(self, msg):
        self.message = msg


class _Resp:
    def __init__(self, msg):
        self.choices, self.usage = [_Choice(msg)], None


def test_a_recorded_reply_keeps_its_tool_calls():
    """The tool loop reads `tc.function.name` by attribute. A reply that came
    back as a plain dict would raise AttributeError mid-loop."""
    resp = _Resp(_Msg("", [_TC("c1", "web_search", '{"query": "x"}')]))
    encoded = recording.encode_response(resp)

    assert encoded["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "web_search"

    decoded = recording.decode_response(encoded)
    tc = decoded.choices[0].message.tool_calls[0]
    assert tc.function.name == "web_search"
    assert json.loads(tc.function.arguments) == {"query": "x"}


def test_a_recorded_reply_keeps_its_content():
    decoded = recording.decode_response(recording.encode_response(_Resp(_Msg("the answer"))))
    assert decoded.choices[0].message.content == "the answer"


def test_a_recorded_stream_keeps_every_chunk_in_order():
    class Delta:
        def __init__(self, c):
            self.content, self.tool_calls = c, None

    class Chunk:
        def __init__(self, c):
            self.choices = [type("C", (), {"delta": Delta(c)})()]

    chunks = [Chunk("Hel"), Chunk("lo"), Chunk("")]
    encoded = recording.encode_stream(chunks)
    assert [e["content"] for e in encoded] == ["Hel", "lo", ""]

    served = [next_chunk.choices[0].delta.content
              for next_chunk in recording.decode_stream(encoded)]
    assert served == ["Hel", "lo", ""]


# ------------------------------------------------------------- no-key replay

def test_a_replay_can_start_without_an_api_key(tmp_path, monkeypatch):
    """A recording exists so the suite can be reproduced by someone with no
    Groq account. Refusing to import without a key would defeat that."""
    import subprocess, sys
    d = str(tmp_path / "rec")
    Recording.begin(d, "record")
    Recording.stop()

    env = {k: v for k, v in os.environ.items() if k != "GROQ_API_KEY"}
    env["EVAL_RECORD_MODE"] = "replay"
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import llama_agent1; "
        "print('imported with key:', llama_agent1.GROQ_API_KEY)"
    )
    proc = subprocess.run([sys.executable, "-c", code], env=env,
                          capture_output=True, text=True, cwd=env["PYTHONPATH"])
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "replay-no-key-needed" in proc.stdout


def test_a_normal_run_still_requires_a_key(tmp_path):
    """The exemption is scoped to replay. Everything else must still fail
    loudly rather than start with no credentials."""
    import subprocess, sys
    env = {k: v for k, v in os.environ.items() if k != "GROQ_API_KEY"}
    env.pop("EVAL_RECORD_MODE", None)
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run([sys.executable, "-c", "import llama_agent1"],
                          env=env, capture_output=True, text=True,
                          cwd=env["PYTHONPATH"])
    assert proc.returncode != 0
    assert "GROQ_API_KEY not set" in (proc.stderr + proc.stdout)
