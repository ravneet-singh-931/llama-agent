"""Kubernetes ops fixes found by checking the live cluster (v3.5.4, docs/KUBERNETES_CHECKS.md)."""
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _docs(path):
    with open(ROOT / path) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def test_backup_waits_for_postgres_before_pg_dump():
    """A missed 02:00 run starts at boot, before Postgres accepts connections:
    the job must wait for pg_isready (bounded) instead of using up its retries."""
    cron = next(d for d in _docs("k8s/postgres-backup-cronjob.yaml") if d["kind"] == "CronJob")
    c = cron["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    script = "\n".join(c.get("command", []) + c.get("args", []))
    assert script.index("pg_isready") < script.index("pg_dump")
    assert "seq 1 60" in script and "exit 1" in script  # gives up after 5 min


def test_chroma_and_phoenix_are_pinned_by_digest():
    files = sorted((ROOT / "k8s").rglob("*.yaml")) + sorted(ROOT.glob("docker-compose*.y*ml"))
    found = []
    for f in files:
        for img in re.findall(r"^\s*-?\s*image:\s*[\"']?([^\s\"'#]+)", f.read_text(), re.M):
            if img.startswith(("chromadb/chroma", "arizephoenix/phoenix")):
                found.append(img)
                assert re.search(r"@sha256:[0-9a-f]{64}$", img), f"{f.name}: {img}"
    assert any("chroma" in i for i in found) and any("phoenix" in i for i in found), found


def test_metrics_server_is_shipped_for_the_hpas():
    """Without metrics-server both HPAs read cpu <unknown> and never scale."""
    docs = _docs("k8s/metrics-server.yaml")
    dep = next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "metrics-server")
    c = dep["spec"]["template"]["spec"]["containers"][0]
    assert re.search(r":v\d+\.\d+\.\d+$", c["image"]), c["image"]  # fixed release, not :latest
    assert "--kubelet-insecure-tls" in c["args"]
    assert "Remove this flag on a managed cluster" in (ROOT / "k8s/metrics-server.yaml").read_text()
    assert any(d["kind"] == "APIService" and d["metadata"]["name"] == "v1beta1.metrics.k8s.io" for d in docs)
    hpas = [d for d in _docs("k8s/api-hpa.yaml") if d["kind"] == "HorizontalPodAutoscaler"]
    assert hpas and all(any(m.get("resource", {}).get("name") == "cpu" for m in h["spec"]["metrics"]) for h in hpas)
    deploy = (ROOT / "k8s/deploy.sh").read_text()
    assert deploy.index("k8s/metrics-server.yaml") < deploy.index("k8s/api-hpa.yaml")
