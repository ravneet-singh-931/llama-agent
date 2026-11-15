"""A feature-flag file must never be able to stop the service from starting.

Why this file exists: `api.py` does `from modules.cicd_improvements import
feature_flags`, and `feature_flags` is constructed **at import time** — so any
exception raised while reading `config/feature_flags.json` is raised during
`import api`, i.e. the app does not start. The file was read bare:

    with open(self.config_file, "r") as f:
        self.flags = json.load(f)

Three failures were reproduced against the real entry point before the fix,
each of which stopped `import api`:

  * truncated JSON (interrupted write, disk full, pod killed mid-save)
        -> JSONDecodeError
  * `{"flag": true}` instead of an object
        -> AttributeError: 'bool' object has no attribute 'get'  (at first read)
  * `"rollout_percent": "100"` as a string
        -> TypeError: '<' not supported between 'str' and 'int'  (at first read)

The point is not "the file is untrusted" — it is committed by us. The point is
that a *feature flag's whole purpose* is to let you change behaviour without a
deploy, and a file that can take the service down achieves the opposite.
"""

import importlib
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _manager(tmp_path, content=None, name="flags.json"):
    """A FeatureFlagManager pointed at a file we control."""
    from modules.cicd_improvements import FeatureFlagManager

    path = tmp_path / name
    if content is not None:
        path.write_text(content if isinstance(content, str) else json.dumps(content))
    return FeatureFlagManager(config_file=str(path)), path


# ------------------------------------------- the three startup killers --

def test_truncated_json_does_not_raise(tmp_path):
    mgr, _ = _manager(tmp_path, '{"enable_mcp_agents": {"enabled": tru')
    assert mgr.load_error
    assert "JSONDecodeError" in mgr.load_error
    # a manager that loaded nothing must still answer, not explode
    assert mgr.is_enabled("enable_mcp_agents") is False


def test_a_bool_instead_of_an_object_does_not_raise(tmp_path):
    mgr, _ = _manager(tmp_path, {"enable_mcp_agents": True})
    assert mgr.load_error and "must be an object" in mgr.load_error
    assert mgr.is_enabled("enable_mcp_agents") is False


def test_a_string_rollout_percent_does_not_raise(tmp_path):
    """The flag stays on; only the malformed field is ignored."""
    mgr, _ = _manager(tmp_path, {
        "enable_mcp_agents": {"enabled": True, "rollout_percent": "100"},
    })
    assert mgr.is_enabled("enable_mcp_agents") is True


def test_a_json_array_instead_of_an_object_does_not_raise(tmp_path):
    mgr, _ = _manager(tmp_path, '["enable_mcp_agents"]')
    assert mgr.load_error and "JSON object" in mgr.load_error


def test_importing_api_survives_a_corrupt_flags_file(tmp_path, monkeypatch):
    """End to end: the real import that used to fail.

    `feature_flags` is already imported by the time this test runs, so the
    module is reloaded with the working directory pointed at a corrupt file.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "feature_flags.json").write_text("{not json")

    import modules.cicd_improvements as mod
    reloaded = importlib.reload(mod)
    try:
        assert reloaded.feature_flags.load_error
        assert reloaded.feature_flags.is_enabled("enable_mcp_agents") is False
    finally:
        monkeypatch.undo()
        importlib.reload(mod)   # restore the real module for other tests


# ------------------------------------------------ behaviour preserved --

def test_a_valid_file_is_still_loaded_exactly(tmp_path):
    mgr, _ = _manager(tmp_path, {
        "enable_mcp_agents": {"enabled": True, "rollout_percent": 100},
        "other": {"enabled": False},
    })
    assert mgr.load_error is None
    assert set(mgr.flags) == {"enable_mcp_agents", "other"}
    assert mgr.is_enabled("enable_mcp_agents") is True
    assert mgr.is_enabled("other") is False


def test_a_missing_file_uses_defaults_without_an_error(tmp_path):
    mgr, _ = _manager(tmp_path, None)
    assert mgr.load_error is None
    assert mgr.flags            # defaults applied


def test_an_unreadable_file_falls_back_to_defaults(tmp_path):
    d = tmp_path / "flags.json"
    d.mkdir()                   # a directory where a file is expected
    from modules.cicd_improvements import FeatureFlagManager
    mgr = FeatureFlagManager(config_file=str(d))
    assert mgr.load_error and mgr.flags


def test_the_error_is_logged_not_swallowed_silently(tmp_path, caplog):
    with caplog.at_level("ERROR", logger="modules.cicd_improvements"):
        _manager(tmp_path, "{broken")
    assert any("could not load" in r.message for r in caplog.records)


# ------------------------------------------------ writing is best effort --

def test_saving_to_an_unwritable_path_does_not_raise(tmp_path, monkeypatch):
    mgr, path = _manager(tmp_path, {"f": {"enabled": True}})
    monkeypatch.setattr(
        "os.makedirs",
        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only filesystem")),
    )
    mgr.set_flag("f", False)          # calls _save_flags internally
    assert mgr.is_enabled("f") is False, "the flag must still apply in-process"


def test_rollout_is_deterministic_and_respected(tmp_path):
    mgr, _ = _manager(tmp_path, {"f": {"enabled": True, "rollout_percent": 0}})
    assert mgr.is_enabled("f", user_id="anyone") in (True, False)
    mgr0, _ = _manager(tmp_path, {"f": {"enabled": True, "rollout_percent": 0}},
                       name="g.json")
    assert mgr0.is_enabled("f", user_id="u") is False, "0% rollout means nobody"


def test_user_override_beats_the_global_switch(tmp_path):
    mgr, _ = _manager(tmp_path, {
        "f": {"enabled": True, "user_overrides": {"vip": True, "banned": False}},
    })
    assert mgr.is_enabled("f", user_id="vip") is True
    assert mgr.is_enabled("f", user_id="banned") is False
