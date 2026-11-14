# Kubernetes checks (live cluster, 2026-10-26; audited 2026-11-13)

Checked on the running cluster (Docker Desktop, kind, 1 node, Kubernetes v1.36.1): what was
wrong, what was fixed, and the proof. The commands are safe to rerun.

**Status, stated up front.** The seven checks below were run against a live cluster on
2026-10-26. On **2026-11-13** every claim that can be verified from the manifests alone was
re-verified by parsing all 31 files (89 resources) — and **one claim in this document was
wrong**. Check 8 is newer still (2026-11-13) and did not come from the cluster at all. See *"What the audit found"* at the end. The four checks that need a running
cluster (`kubectl`) could not be re-executed here and are marked as such; they are not
being re-asserted as freshly true.

| Document | Surface | Findings |
|---|---|---|
| [`EVAL_FINDINGS.md`](EVAL_FINDINGS.md) | the measuring stick | 19 |
| [`AGENT_FINDINGS.md`](AGENT_FINDINGS.md) | the agents | 5 fixed, 4 open |
| [`OBSERVABILITY_FINDINGS.md`](OBSERVABILITY_FINDINGS.md) | Prometheus / Grafana / Phoenix | 4 |
| [`RAG_FINDINGS.md`](RAG_FINDINGS.md) | retrieval, ranking, the gate | 6 |
| [`SECURITY_FINDINGS.md`](SECURITY_FINDINGS.md) | the browser boundary | 4 |
| [`DARK_MODULES.md`](DARK_MODULES.md) | 11 modules, 2,928 lines | 23 dead names |
| **this file** | **the cluster itself** | **8 checks** |

**The through-line here is different again.** The eval findings were guards attached one
level too low; the agent findings were agents answering without the input they needed.
Every item below is a case where **the cluster was working exactly as configured and the
configuration was wrong** — a CronJob scheduled at an hour the machine is off, autoscalers
with nothing to scale on, an image tag that moves. Nothing was broken. Nothing was right.

**The one worth reading first:** the nightly backup failed three nights running. It was not
broken either. It was scheduled for 02:00 UTC, the PC is off at that hour, so Kubernetes ran
the missed job at **boot** — where it started 13 seconds after Postgres and failed before
Postgres accepted connections. The job was fine; the schedule and the boot order were not.

| Check | Before | After | Proof |
|---|---|---|---|
| Nightly Postgres backup | Last 3 jobs failed | Waits for Postgres, then dumps | Postgres scaled to 0: job waited, finished when Postgres was back |
| Restore from backup | Never tested | Restore drill passes | Restored copy has the same row counts as the live DB (6 tables) |
| Autoscaling (HPA) | `cpu: <unknown>`, could not scale | Reads CPU (`3%/70%`) | `kubectl get hpa` |
| NetworkPolicy | Not known if enforced | Enforced (no change needed) | Pod in another namespace blocked, pod in `llama-agent` allowed |
| Image versions | Chroma and Phoenix on `:latest` | Pinned by digest | `k8s/chromadb.yaml`, `k8s/phoenix.yaml` |

## 1. Backup waits for Postgres

The CronJob runs at 02:00 UTC. The PC is usually off then, so Kubernetes runs the missed job
at boot. The failed jobs started at 12:25, 07:13 and 06:28 UTC (boot times). The last one
started 13 s after the Postgres container, before Postgres accepted connections, and used up
its 2 retries within seconds. Run by hand with Postgres up, the same job worked.

Fix: `pg_isready` every 5 s, up to 5 minutes, before `pg_dump`.

Proof (the app has no database for about 30 s during this test):
```bash
kubectl -n llama-agent scale deploy/postgres --replicas=0
kubectl -n llama-agent create job backup-wait-test --from=cronjob/postgres-backup
sleep 20; kubectl -n llama-agent logs job/backup-wait-test --tail=4   # Waiting for Postgres (1/60)
kubectl -n llama-agent scale deploy/postgres --replicas=1
kubectl -n llama-agent wait --for=condition=complete job/backup-wait-test --timeout=300s
kubectl -n llama-agent delete job backup-wait-test
```
Result: "Waiting for Postgres (1/60)", "(2/60)", then complete.

## 2. Restore drill

The newest dump is restored into a throwaway Postgres pod (read-only access to the backup
volume), then row counts are compared with the live database.
```bash
kubectl -n llama-agent run restore-drill --image=postgres:15-alpine --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"restore-drill","image":"postgres:15-alpine","env":[{"name":"POSTGRES_PASSWORD","value":"drill-only"}],"volumeMounts":[{"name":"b","mountPath":"/backup","readOnly":true}]}],"volumes":[{"name":"b","persistentVolumeClaim":{"claimName":"postgres-backup-pvc","readOnly":true}}]}}'
kubectl -n llama-agent wait --for=condition=Ready pod/restore-drill --timeout=120s
kubectl -n llama-agent exec restore-drill -- sh -c 'until pg_isready -h 127.0.0.1 -U postgres; do sleep 2; done'
kubectl -n llama-agent exec restore-drill -- sh -c 'F=$(ls -1t /backup/llama_agent_*.sql.gz | head -1); gunzip -c "$F" | psql -h 127.0.0.1 -U postgres -q'
printf '%s\n' "select format('select %L as t, count(*) from %I', tablename, tablename) from pg_tables where schemaname='public' order by 1 \gexec" > /tmp/count.sql
kubectl -n llama-agent exec -i restore-drill -- psql -h 127.0.0.1 -U postgres -d llama_agent -At < /tmp/count.sql
kubectl -n llama-agent exec -i deploy/postgres -- sh -c 'psql -U "$POSTGRES_USER" -d llama_agent -At' < /tmp/count.sql
kubectl -n llama-agent delete pod restore-drill
```

| Table | Restored | Live |
|---|---|---|
| api_keys | 0 | 0 |
| conversations | 3 | 3 |
| documents | 0 | 0 |
| facts | 0 | 0 |
| messages | 160 | 160 |
| users | 1 | 1 |

## 3. Autoscaling

Both HPAs showed `cpu: <unknown>` because the cluster had no metrics-server. It is now vendored
in `k8s/metrics-server.yaml` (v0.9.0) and applied with
`kubectl apply -f k8s/metrics-server.yaml`. The HPAs now read `cpu: 3%/70%`
(`kubectl -n llama-agent get hpa`). `--kubelet-insecure-tls` is set because local kind
kubelets use self-signed certificates. Remove it on a managed cluster (EKS/GKE/AKS).
Scaling up under load has not been demonstrated yet.

## 4. NetworkPolicy is enforced

kindnet (`kindest/kindnetd:v20260528`) enforces NetworkPolicy.
```bash
# from another namespace: blocked (times out, exit 1)
kubectl run np-test -n default --rm -i --restart=Never --image=busybox:1.36 -- wget -qO- -T 5 http://llama-agent-api-service.llama-agent:8000/health
# from inside llama-agent: allowed ({"status":"healthy",...}, exit 0)
kubectl -n llama-agent run np-test-in --rm -i --restart=Never --image=busybox:1.36 -- wget -qO- -T 5 http://llama-agent-api-service:8000/health
```

## 5. Pinned images

`chromadb/chroma` and `arizephoenix/phoenix` were `:latest`, so a restart could silently pull
a new version. They are pinned to the digests that were running (Chroma server 1.0.0,
Phoenix 19.4.0). To upgrade, change the digest on purpose and test.

## 6. Containers don't run as root (v3.5.5)

```bash
kubectl -n llama-agent exec deploy/llama-agent-api -- id                  # uid=1000(app)
kubectl -n llama-agent exec deploy/llama-agent-frontend -- id             # uid=1001(nextjs)
kubectl -n llama-agent exec deploy/llama-agent-api -- touch /app/api.py   # Permission denied
kubectl label --dry-run=server --overwrite ns llama-agent pod-security.kubernetes.io/enforce=restricted
```

The dry run lists the pods the `restricted` level would reject: chromadb, phoenix, postgres,
redis, grafana, loki, prometheus, node-exporter and promtail. The 4 app Deployments (api,
mcp-server, web, frontend) are not in the list.

## 7. Upload 502 was an OOMKilled API pod (v3.5.5)

| | Before (reranker batch 16) | After (batch 4) |
|---|---|---|
| API pod after document questions | 1208 - 1288 MB, never released | 481 / 627 MB max |
| Question + 65 KB .docx upload | ~1630 MB > 1536 MiB limit: OOMKilled, HTTP 502 | works, 0 restarts |
| Reranker alone, 8 questions in a row (local, offline) | 1019 MB | 354 MB |
| RAG retrieval (hit@1 / hit@3 / hit@6 / MRR) | 0.816 / 0.895 / 0.921 / 0.862 | same |

Memory per pod: `process_resident_memory_bytes{job="llama-agent-api"}` in Prometheus.

## 8. The app could not start without a key (2026-11-13)

Not found on the cluster. Found by trying to do what a stranger does: clone the
repository and start it.

**What was wrong.** `llama_agent1.py` ended its key loading with

```python
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    ...
    raise SystemExit("GROQ_API_KEY not set. Add it to your environment or a .env file.")
```

That is at **import time**, and `api.py`'s lifespan imports the module. So
uvicorn imported the app, the import raised `SystemExit`, and the process died
**before a socket was opened**. The consequence is not "chat is unavailable" —
it is that nothing is available:

```
$ curl -s localhost:8000/health
curl: (7) Failed to connect
```

`/health`, `/docs`, `/openapi.json`: all of it. What the operator sees is a
container that exits, and `docker compose up` reports three of them, because
the API, the web UI and the MCP server all mount `groq_api_key`.

**Why it is the first thing a new clone hits.** The key lives in `secrets/`,
which is gitignored, so a fresh clone has no `secrets/` at all. The README tells
you to create the file, and the common way to do that is `touch` / `echo` — which
leaves a file that exists and is empty. Checked directly:

```python
>>> get_secret("GROQ_API_KEY")   # file exists, contains b""
''
>>> bool(get_secret("GROQ_API_KEY"))
False
```

So "I created the secret file" and "there is no key" were the same state to the
program, and both crashed it.

**Why 828 tests did not catch it.** Every test that imports `api` sets
`GROQ_API_KEY` first — CI exports `test_key_for_ci`, and the local suite does the
same. The one configuration a new user actually has was the one configuration
never exercised. It is the same shape as the rest of this project: the check and
the thing being checked were configured to agree.

**The fix.** Start anyway. The Groq client accepts an empty string (verified:
`Groq(api_key="")` and `build_client("groq", "")` both construct), so nothing
below the boot path needed a key to exist. `llama_agent1` now records
`GROQ_API_KEY_CONFIGURED`, logs a warning, and returns; `api.py` gained
`require_api_key()`, called by `/chat`, `/chat/stream` and `/documents/ask`, which
raises **503** with a message naming the variable and the file. `/health` gained
an `llm_key` field so a probe can tell *up and ready* from *up and unconfigured*.

**Proof**, with no key in the environment:

| Request | Before | After |
|---|---|---|
| the process | exits before binding a port | binds and serves |
| `GET /health` | connection refused | **200**, `llm_key: missing` |
| `GET /docs` | connection refused | **200** |
| `GET /tools` | connection refused | **200** |
| `POST /chat` | connection refused | **503** + what to set and where |
| `POST /chat/stream` | connection refused | **503** |
| `POST /documents/ask` | connection refused | **503** |

`tests/test_boot_without_key.py` (5 tests) boots the app in a **subprocess with
the key removed**, because the parent process has already imported the module
and Python will not re-run a top-level block. Restoring the `SystemExit` fails
**4 of the 5**; the fifth reads the flag and is unaffected by design.

**The lesson, and it is the same one as every other check in this file:** the
test suite was green and the deployment worked, because the person running both
had already done the thing the new user has not. The gap was not in the code's
behaviour — the code behaved exactly as written. It was between the documented
first step and what the program accepted as a first step.

---

## What the audit found (2026-11-13)

All 31 manifests were parsed and every claim above that can be checked statically was
checked. Result: **six of seven checks stand, one sentence did not.**

### Verified against the manifests

| Check | Claim | Verified how | Result |
|---|---|---|---|
| 1 | CronJob waits up to 5 min for Postgres | `k8s/postgres-backup-cronjob.yaml:107` — `for i in $(seq 1 60)` with `pg_isready`, `sleep 5` | ✅ present |
| 3 | HPA on the api | `k8s/api-hpa.yaml` | ✅ `llama-agent-api-hpa`, min 2 / max 10, CPU 70%, scale-down stabilised 300 s |
| 3 | HPA on the mcp server | same file | ✅ `llama-agent-mcp-hpa`, min 1 / max 4, CPU 70% |
| 4 | NetworkPolicy includes a default deny | `k8s/networkpolicy.yaml` | ✅ `default-deny-ingress` **and** `default-deny-egress`, both `podSelector: {}` |
| 4 | Only specific pods accept ingress | same | ✅ `allow-from-ingress-nginx` scoped to 7 components |
| 5 | Chroma and Phoenix pinned by digest | all 13 Deployments parsed | ✅ both `@sha256:`; the other 11 use **tags**, not digests (see below) |
| 6 | The 4 app Deployments do not run as root | all 16 workloads parsed | ✅ api/web/mcp `1000`, frontend `1001` |
| 6 | Grafana, Loki, Prometheus run as root | same | ✅ all three `container.securityContext.runAsUser: 0` |

### A number the manifest pass disagrees with, in the flattering direction

Check 6 says *"the 4 app Deployments (api, mcp-server, web, frontend) are not in the
[restricted-violation] list"* — true, and it implies everything else fails. The parse says
it is better than that: `metrics-server` runs as `1000` and `node-exporter` as `65534`, so
**six** workloads are non-root, not four. Nine are unset (image default) or root.

That is a small correction in the project's favour, recorded because the paragraph that
says "nine pods would be rejected" is doing work in the argument and should be right.

### The sentence that was wrong

`## Still open` used to say:

> *"the backups are on the same disk as the database (losing the disk loses both)."*

**That is false as a statement about storage layout, and it contradicted the manifest it
was describing.** `k8s/postgres-backup-cronjob.yaml:157` says the opposite, in a comment
written at the time:

> `# Separate from postgres-pvc so a disk failure on data volume doesn't also destroy backups.`

Two PVCs, `postgres-pvc` (5Gi) and `postgres-backup-pvc` (10Gi), both `ReadWriteOnce`, both
on the cluster default StorageClass.

**What is actually true**, and it is a narrower and worse-shaped thing:

> There is one node. All seven PVCs are `ReadWriteOnce`, and with no StorageClass manifest
> in this repo they bind to whatever the cluster default provides — on a single-node Docker
> Desktop cluster that is local storage on that node. So a *volume* failure loses only the
> backups or only the data, as designed. **Losing the node's disk loses both**, and
> `ReadWriteOnce` means neither volume can move to another node anyway.

The original sentence had the right conclusion (this is not durable) and the wrong reason
(layout), and the wrong reason is the one a reader would check and find false. That is the
same failure this project has spent six documents on: **a true worry attached to the wrong
evidence.** Corrected above with the reason that holds.

### What could not be re-verified

Checks 1 (result), 2, 3 (the `cpu: <unknown>` → `3%/70%` reading), 4 (cross-namespace
blocked / in-namespace allowed) and 7 (the memory numbers) need a live cluster. **No
`kubectl` is available in this environment**, so they are unchanged from 2026-10-26 and are
not being re-asserted. The commands to reproduce them are in each section and are still
accurate. Saying so is the point: re-reading a YAML file is not re-running a cluster.

## Still open

- Grafana, Loki and Prometheus set `runAsUser: 0`.
- **One node, all `ReadWriteOnce`, no StorageClass in the repo** — losing the node's disk
  loses data and backups together, and neither volume can move to another node. (Corrected
  2026-11-13; this bullet used to give the wrong reason.)
- Only the two app Deployments have an HPA. Postgres, Redis and Chroma are single-replica.
- The other eleven images are pinned by **tag**, not digest (`v7`, `15-alpine`,
  `7-alpine`, `10.4.0`, `2.9.6`, `v2.51.0`, `v0.9.0`, `v7.6.0`). A tag can be re-pushed;
  only Chroma and Phoenix are digest-pinned. Worth knowing before claiming "pinned images."
- cert-manager, External Secrets and OIDC are not installed in this cluster (the manifests
  exist, but their `deploy.sh` flags default to off).

---

## Check 9 — the integration job did not install what the image installs

*(Added 2026-11-13. This one is about the CI pipeline rather than the cluster, but it is
the same defect: a green tick that never touched the artifact.)*

`.github/workflows/main.yml` had two jobs installing dependencies two different ways.

| Job | Installed | Could it verify the shipped image? |
|---|---|---|
| `test` (L38) | `pip install -r requirements.txt` — pinned `mcp==1.30.0` | yes |
| `integration` (L275) | five hand-written `pip install` lines, **unpinned** | **no** |

The `test` job even carries the comment *"Pinned: install exactly what the Docker image
ships (requirements.txt)"*. The `integration` job — whose entire purpose is "every module
imports" — built its own dependency set:

```yaml
pip install groq llama-index-core llama-index-llms-groq
pip install chromadb pydantic fastapi uvicorn PyJWT bcrypt
pip install python-dotenv pypdf python-docx pandas
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
pip install pytest sqlalchemy psycopg2-binary asyncpg redis "mcp<2" prometheus-client "fastmcp<4"
```

`"mcp<2"` resolves to the newest `mcp` under 2 — not the 1.30.0 the image pins. The job
could stay green while the image broke, and the one thing it was written to prove was
that the image's imports work.

### The second half: it installed a package the project explicitly excludes

The last line installed `"fastmcp<4"`. Nothing imports `fastmcp`. The MCP server uses
`from mcp.server.fastmcp import FastMCP` — a submodule of `mcp` itself, not the separate
`fastmcp` distribution. `tests/test_version_and_deps.py`
still asserts the package is absent from `requirements.txt`:

```python
def test_unused_fastmcp_is_not_in_requirements():
    assert not re.search(r"(?im)^fastmcp\b", _read("requirements.txt"))
```

So the CI job and the test suite disagreed about the dependency set, and only the suite
was right.

### The fix

```yaml
- name: "📦 Install dependencies"
  run: |
    # Pinned: install exactly what the Docker image ships (requirements.txt).
    # This job used to hand-list unpinned packages, so it could go green while
    # testing a newer `mcp` than the image actually installs. It also installed
    # the standalone `fastmcp` package, which nothing imports — the MCP server
    # uses `mcp.server.fastmcp`, which ships inside `mcp` itself.
    pip install -r requirements.txt
```

Verified that `mcp==1.30.0` alone provides `mcp.server.fastmcp`:

```
$ python -c "from mcp.server.fastmcp import FastMCP; print('ok')"
ok
```

### Why this is check 9 and not a footnote

Checks 1–8 are about a cluster that was configured correctly and described incorrectly.
This one is the inverse: a CI job that described itself as verifying the artifact and
verified a different one. **A green check that does not test the thing it is named after
is worse than a missing check**, because a missing check is visible in the list.
