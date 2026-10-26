"""v3.5.5: the API, MCP and Streamlit containers never run as root."""
import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")
ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_dockerfile_final_user_is_non_root():
    users = re.findall(r"^USER\s+(\S+)", (ROOT / "Dockerfile").read_text(), re.M)
    assert users and users[-1] == "1000:1000"


def test_dockerfile_gives_runtime_write_dirs_to_app_user():
    d = (ROOT / "Dockerfile").read_text()
    for name in ("data", "memory", "backups", "migrations", "uploads", "logs"):
        assert re.search(r"chown -R 1000:1000[^\n]*\b%s\b" % name, d), name
    assert "ENV CHROMA_CACHE_DIR=/home/app/.cache/chroma" in d


@pytest.mark.parametrize("fname,name", [
    ("api.yaml", "llama-agent-api"),
    ("mcp-server.yaml", "llama-agent-mcp-server"),
    ("web.yaml", "llama-agent-web"),
])
def test_deployment_security_context(fname, name):
    docs = [x for x in yaml.safe_load_all((ROOT / "k8s" / fname).read_text()) if x]
    dep = next(x for x in docs
               if x["kind"] == "Deployment" and x["metadata"]["name"] == name)
    pod = dep["spec"]["template"]["spec"]
    psc = pod["securityContext"]
    assert psc["runAsNonRoot"] is True
    assert psc["runAsUser"] == 1000 and psc["fsGroup"] == 1000
    assert psc["seccompProfile"]["type"] == "RuntimeDefault"
    for c in pod["containers"]:
        csc = c["securityContext"]
        assert csc["allowPrivilegeEscalation"] is False
        assert csc["capabilities"]["drop"] == ["ALL"]
