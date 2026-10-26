# Kubernetes checks (live cluster, 2026-10-26)

Checked on the running cluster (Docker Desktop, kind, 1 node, Kubernetes v1.36.1): what was
wrong, what was fixed, and the proof. The commands are safe to rerun.

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

## Still open

- Grafana, Loki and Prometheus set `runAsUser: 0`.
- One node. Postgres, Redis and Chroma are single-replica Deployments, and the backups are on
  the same disk as the database (losing the disk loses both).
- cert-manager, External Secrets and OIDC are not installed in this cluster (the manifests
  exist, but their `deploy.sh` flags default to off).
