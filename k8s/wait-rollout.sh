#!/usr/bin/env bash
# Wait for the app Deployments to finish rolling out (used by the CD deploy jobs).
#
# Why the long API timeout: the API runs 2 replicas with the default rolling
# update (maxSurge 1 / maxUnavailable 0), so new Pods start ONE AFTER THE OTHER,
# and each has a startupProbe budget of 10 min (first start pulls a multi-GB
# image and may download models). A 300s wait timed out mid-rollout even though
# the rollout itself was healthy.
#
# On failure it prints Pod status / describe / events / logs AND emits them as
# GitHub annotations (::error::), which stay readable even when job logs aren't.
#
# Usage: k8s/wait-rollout.sh [namespace]
set -uo pipefail

NS="${1:-llama-agent}"
# deployment:timeout_seconds
TARGETS=(
  "llama-agent-api:900"
  "llama-agent-mcp-server:600"
  "llama-agent-web:600"
  "llama-agent-frontend:600"
)

# GitHub annotation messages must escape %, CR and LF.
annotate() {  # annotate <level> <title> <message>
  local msg="$3"
  msg="${msg//'%'/'%25'}"
  msg="${msg//$'\r'/'%0D'}"
  msg="${msg//$'\n'/'%0A'}"
  echo "::$1 title=$2::$msg"
}

diagnose() {  # diagnose <deployment>
  local dep="$1" component pods
  component="$(kubectl -n "$NS" get deploy "$dep" -o jsonpath='{.spec.selector.matchLabels.component}' 2>/dev/null)"
  local sel="component=${component:-$dep}"

  echo "::group::Diagnostics for $dep ($sel)"
  kubectl -n "$NS" get deploy "$dep" -o wide || true
  kubectl -n "$NS" get rs,pods -l "$sel" -o wide || true
  pods="$(kubectl -n "$NS" get pods -l "$sel" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null)"
  for p in $pods; do
    echo "----- describe $p -----"
    kubectl -n "$NS" describe pod "$p" | tail -n 40 || true
    echo "----- logs $p (last 60) -----"
    kubectl -n "$NS" logs "$p" --all-containers --tail=60 || true
    kubectl -n "$NS" logs "$p" --all-containers --tail=30 --previous 2>/dev/null || true
  done
  echo "----- recent events -----"
  kubectl -n "$NS" get events --sort-by=.lastTimestamp | tail -n 25 || true
  echo "::endgroup::"

  # Compact versions as annotations (visible on the run summary page)
  annotate error "$dep pods" "$(kubectl -n "$NS" get pods -l "$sel" -o wide 2>&1 | head -n 15)"
  annotate error "$dep events" "$(kubectl -n "$NS" get events --sort-by=.lastTimestamp 2>&1 | grep -E "Warning|$dep" | tail -n 15)"
  for p in $pods; do
    local ready
    ready="$(kubectl -n "$NS" get pod "$p" -o jsonpath='{.status.containerStatuses[*].ready}' 2>/dev/null)"
    if [[ "$ready" != *true* ]]; then
      annotate error "$p (not ready) logs" "$(kubectl -n "$NS" logs "$p" --all-containers --tail=25 2>&1)"
    fi
  done
}

failed=0
for t in "${TARGETS[@]}"; do
  dep="${t%%:*}"; timeout="${t##*:}"
  echo "⏳ Waiting for $dep (timeout ${timeout}s)"
  if ! kubectl -n "$NS" rollout status "deployment/$dep" --timeout="${timeout}s"; then
    annotate error "Rollout failed: $dep" "deployment/$dep did not become ready within ${timeout}s - see diagnostics below."
    diagnose "$dep"
    failed=1
  fi
done

# Redis reachability from the API (REDIS_URL comes from app-secrets). A wrong
# host doesn't crash anything - every Pod silently falls back to its own memory
# - so surface it here. Warning only: the deploy itself succeeded.
if [[ $failed -eq 0 ]]; then
  if kubectl -n "$NS" exec deploy/llama-agent-api -- python -c \
      "import os, redis; redis.from_url(os.environ['REDIS_URL'], socket_connect_timeout=3).ping(); print('Redis OK:', os.environ['REDIS_URL'].split('@')[-1])"; then
    :
  else
    annotate warning "Redis unreachable from API" "The API Pods cannot reach REDIS_URL, so each Pod uses its own memory (WebSocket logins can fail with 2+ replicas). Check the REDIS_URL secret - in k8s use redis://redis-service:6379 (or redis://redis:6379)."
  fi
fi

exit $failed
