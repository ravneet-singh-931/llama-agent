#!/usr/bin/env bash
# =============================================================================
# apply-app.sh - apply the four app Deployments with images pinned to ONE tag
# =============================================================================
# The manifests carry a bootstrap tag (:v7). Applying them as-is and then
# running `kubectl set image ...:<sha>` changed every pod template TWICE per
# deploy: two rollouts, twice the surge pods on a CPU-tight node, and a
# brief run of whatever stale :v7 image the node had cached. This renders the
# tag into the manifests first, so each deploy is a single rollout straight
# to the immutable commit image (still revertible with `kubectl rollout undo`).
#
# Usage: bash k8s/apply-app.sh <image-tag> [namespace]
#   e.g. bash k8s/apply-app.sh "$GITHUB_SHA"
# =============================================================================
set -euo pipefail

TAG="${1:?usage: apply-app.sh <image-tag> [namespace]}"
NS="${2:-llama-agent}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! "$TAG" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "::error::invalid image tag: $TAG"
  exit 1
fi

for f in api mcp-server frontend; do
  # ravneet231/llama-agent[-mcp-server|-frontend]:<anything> -> :<TAG>
  sed -E "s#(image: ravneet231/llama-agent(-mcp-server|-frontend)?):[A-Za-z0-9._-]+#\1:${TAG}#" \
    "$DIR/$f.yaml" | kubectl apply -f -
done

echo "Pinned images:"
kubectl -n "$NS" get deploy llama-agent-api llama-agent-mcp-server llama-agent-frontend \
  -o custom-columns=DEPLOYMENT:.metadata.name,IMAGE:.spec.template.spec.containers[0].image
