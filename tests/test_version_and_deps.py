"""v3.5.6 audit fixes: the app reports its real version, and the fixes stay fixed."""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def _latest_release():
    for line in _read("CHANGELOG.md").splitlines():
        m = re.match(r"^## \[(\d+\.\d+\.\d+)\]", line)
        if m:
            return m.group(1)
    raise AssertionError("no released version in CHANGELOG.md")


def test_app_reports_the_newest_changelog_version():
    v = '"' + _latest_release() + '"'
    api = _read("api.py")
    cfg = _read("modules/config.py")
    assert api.count(v) >= 3, "FastAPI(version=...), /health and the OpenAPI schema must use " + v
    assert v in cfg
    assert '"3.0.0"' not in api and '"3.0.0"' not in cfg


def test_unused_fastmcp_is_not_in_requirements():
    assert not re.search(r"(?im)^fastmcp\b", _read("requirements.txt"))
    # the MCP server uses the official mcp SDK's FastMCP, not the separate fastmcp package
    assert "from mcp.server.fastmcp import FastMCP" in _read("mcp_agents_server.py")


def test_image_upgrades_pip_before_dropping_root():
    lines = _read("Dockerfile").splitlines()
    pip = [i for i, l in enumerate(lines) if l.startswith("RUN") and "--upgrade pip==" in l]
    user = [i for i, l in enumerate(lines) if l.startswith("USER ")]
    assert pip and user and pip[-1] < user[-1]
    major, minor = map(int, re.search(r"pip==(\d+)\.(\d+)", lines[pip[-1]]).groups())
    assert (major, minor) >= (26, 2)
