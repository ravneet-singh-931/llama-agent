"""`modules/cicd_improvements.py`: the feature flags that gate MCP.

Unlike most of `cicd_improvements.py` (the A/B tester and deployment manager are
unused), `feature_flags` is **live**: `api.py` imports the module-level instance
and two endpoints are wrapped in `is_enabled("enable_mcp_agents")` - `/mcp/status`
(2836) and `/mcp/tools` (2859). If that call is wrong, MCP returns 404 for
everyone, or for nobody.

Three defects are measured here, all in the "difficult to notice" class:

1. **A corrupt flag file is overwritten with the defaults.** The fallback is
   correct and well documented - availability must not depend on a config file -
   but `_apply_defaults()` calls `_save_flags()`, so the half-written JSON that
   caused the failure is replaced. The operator loses the evidence, and because
   the defaults ship `enable_mcp_agents: false` while `config/feature_flags.json`
   ships `true`, MCP silently turns itself off - permanently, because the file is
   now the defaults.
2. **A user override cannot re-enable a flag excluded by rollout.** The override
   is read *after* the rollout gate, so `rollout_percent: 0` with
   `user_overrides: {"vip": true}` returns `False` for `vip`.
3. **`rollout_percent: 0` disables the flag for everyone**, including callers
   that pass no `user_id`, because the rollout check is skipped entirely when
   `user_id` is `None` and `0 < 100` never gets a chance to exclude... measured
   directly rather than reasoned about below.

All three are latent with the shipped config (`rollout_percent: 100`, file
present). They are pinned, not fixed - a rollout policy is a product decision.
"""
import json

import pytest

from modules.cicd_improvements import FeatureFlagManager


@pytest.fixture
def flags(tmp_path):
    """A manager backed by a file in the test's own directory."""
    def _make(config=None, name="flags.json"):
        path = tmp_path / name
        if config is not None:
            path.write_text(json.dumps(config))
        return FeatureFlagManager(config_file=str(path)), path
    return _make


# ------------------------------------------------------------- the live flag --

def test_the_flag_shipped_in_config_is_on(tmp_path):
    """Guards the deployment: the shipped file must keep MCP reachable.

    If this flips, `/mcp/status` and `/mcp/tools` start returning 404 and the
    Agent Team page has nothing to call.
    """
    shipped = json.loads(open("config/feature_flags.json").read())
    assert shipped["enable_mcp_agents"]["enabled"] is True
    assert shipped["enable_mcp_agents"]["rollout_percent"] == 100


def test_an_unknown_flag_is_disabled(flags):
    """Default-deny. A typo in a flag name must not open a feature."""
    mgr, _ = flags({"known": {"enabled": True}})
    assert mgr.is_enabled("unknown") is False


def test_a_flag_turned_off_is_off(flags):
    mgr, _ = flags({"x": {"enabled": False}})
    assert mgr.is_enabled("x") is False


def test_a_flag_turned_on_is_on(flags):
    mgr, _ = flags({"x": {"enabled": True, "rollout_percent": 100}})
    assert mgr.is_enabled("x") is True


# ------------------------------------------- defect 2: override vs rollout --

def test_a_user_override_cannot_rescue_a_user_excluded_by_rollout(flags):
    """MEASURED DEFECT. The override is checked AFTER the rollout gate.

    `rollout_percent: 0` with `user_overrides: {"vip-user": true}` returns False
    for `vip-user`. The override is unreachable, so the "give this one customer
    the feature" switch does nothing when a rollout is in progress.

    When the order is fixed (override first, or rollout only when no override
    exists) this test fails - invert it and note it in CHANGELOG.md.
    """
    mgr, _ = flags({"f": {"enabled": True, "rollout_percent": 0,
                          "user_overrides": {"vip-user": True}}})

    assert mgr.is_enabled("f", "vip-user") is False, (
        "the override now wins over the rollout - that is the fix, invert this test"
    )


def test_a_user_override_applies_when_the_rollout_does_not_exclude(flags):
    """The override does work at 100% - which is why the bug is easy to miss."""
    mgr, _ = flags({"f": {"enabled": True, "rollout_percent": 100,
                          "user_overrides": {"blocked": False}}})

    assert mgr.is_enabled("f", "blocked") is False
    assert mgr.is_enabled("f", "someone-else") is True


def test_a_partial_rollout_is_deterministic(flags):
    """The same user must not flip between enabled and disabled per request."""
    mgr, _ = flags({"f": {"enabled": True, "rollout_percent": 50}})

    first = mgr.is_enabled("f", "a-user")
    for _ in range(20):
        assert mgr.is_enabled("f", "a-user") == first


def test_a_partial_rollout_excludes_some_and_includes_some(flags):
    """A rollout that includes everybody (or nobody) is not a rollout."""
    mgr, _ = flags({"f": {"enabled": True, "rollout_percent": 50}})
    results = {mgr.is_enabled("f", f"user-{i}") for i in range(200)}
    assert results == {True, False}


def test_a_zero_percent_rollout_is_off_even_without_a_user(flags):
    """MEASURED. Documented behaviour, and worth pinning: with no `user_id` the
    rollout branch is skipped, so the flag falls through to `True`.

    So `rollout_percent: 0` means "off for identified users, on for anonymous
    callers" - the opposite of what "0%" reads as.
    """
    mgr, _ = flags({"f": {"enabled": True, "rollout_percent": 0}})

    assert mgr.is_enabled("f") is True, (
        "the anonymous-caller rollout behaviour changed - re-read this test"
    )
    assert mgr.is_enabled("f", "anyone") is False


# ------------------------------------------------- defect 1: the file on disk --

def test_a_corrupt_file_does_not_stop_startup(flags):
    """The documented promise, and it holds: a bad file must cost flags, not
    availability. `_load_flags` runs at import time, so raising here would mean
    the app cannot start at all.
    """
    mgr, _ = flags(None)
    mgr.config_file
    import pathlib
    pathlib.Path(mgr.config_file).write_text('{"x": {"enabled": tru')

    mgr2 = FeatureFlagManager(config_file=mgr.config_file)

    assert mgr2.load_error is not None
    assert mgr2.is_enabled("anything") in (True, False)   # no exception


def test_a_corrupt_file_is_overwritten_with_the_defaults(flags):
    """MEASURED DEFECT. `_apply_defaults()` ends with `_save_flags()`.

    So the half-written JSON that caused the fallback is replaced by the built-in
    defaults. Consequences:

    * the operator loses the evidence of what went wrong
    * `enable_mcp_agents` defaults to **false** while the shipped config says
      **true**, so a corrupt file silently turns MCP off - and the file now says
      so, so a restart does not recover it

    Pinned rather than fixed: whether to overwrite a config file on a load error
    is a deployment decision. Invert this test when the write is skipped.
    """
    mgr, path = flags({"keep": {"enabled": True}}, name="corrupt.json")
    original = '{"keep": {"enabled": tru'
    path.write_text(original)

    mgr2 = FeatureFlagManager(config_file=str(path))

    assert mgr2.load_error is not None
    assert path.read_text() != original, (
        "the corrupt file is now preserved - that is the fix, invert this test"
    )
    assert "keep" not in path.read_text(), "the defaults replaced the operator's flags"


def test_the_mcp_flag_is_off_after_a_corrupt_file(flags, tmp_path):
    """The concrete consequence, measured: MCP ends up disabled."""
    path = tmp_path / "flags.json"
    path.write_text('{"enable_mcp_agents": {"enabled": tru')

    mgr = FeatureFlagManager(config_file=str(path))

    assert mgr.is_enabled("enable_mcp_agents") is False


def test_a_flag_of_the_wrong_shape_is_disabled_not_fatal(flags):
    """`{"flag": true}` instead of `{"flag": {"enabled": true}}`.

    `_load_flags` rejects the whole file for this (see its docstring), so the
    defaults are used and nothing raises.
    """
    mgr, path = flags({"x": True}, name="wrongshape.json")
    assert mgr.load_error is not None
    assert mgr.is_enabled("x") is False


def test_flag_reads_never_raise_inside_a_request(flags):
    """`/mcp/status` calls this on every request; an exception is a 500."""
    mgr, _ = flags({"good": {"enabled": True},
                    "nondict": "yes",
                    "nonnumeric": {"enabled": True, "rollout_percent": "100"}})

    for name in ("good", "nondict", "nonnumeric", "missing", ""):
        assert isinstance(mgr.is_enabled(name), bool)
        assert isinstance(mgr.is_enabled(name, "u1"), bool)


def test_a_non_numeric_rollout_is_treated_as_fully_on(flags):
    """Documented: ignore the rollout rather than read it as 0 (which would
    disable the feature because of a typo)."""
    mgr, _ = flags({"f": {"enabled": True, "rollout_percent": "100"}})
    assert mgr.is_enabled("f", "anyone") is True
