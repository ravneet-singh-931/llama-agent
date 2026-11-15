#!/bin/bash
# ============================================================================
# k8s/deploy.sh - deploy the whole Llama Agent stack to Kubernetes
# ============================================================================
# USAGE (run from the repo root):
#   ./k8s/deploy.sh                 # dev (default)
#   ./k8s/deploy.sh production
#   CERT_MANAGER=true ESO_ENABLED=true OIDC_ENABLED=true NETWORK_POLICY=true \
#     ./k8s/deploy.sh production    # full cloud setup
#
# FEATURE FLAGS (all off by default for a local lab):
#   CERT_MANAGER=true   -> install cert-manager + ClusterIssuers (TLS)
#   ESO_ENABLED=true    -> install External Secrets Operator; Secrets come from
#                          AWS Secrets Manager (k8s/external-secrets.yaml)
#   OIDC_ENABLED=true   -> oauth2-proxy SSO in front of the monitoring UIs
#   NETWORK_POLICY=true -> zero-trust NetworkPolicies
#   ESO_VERSION=v0.x.y  -> pin the ESO release (default: latest)
#
# SECRETS (when ESO_ENABLED=false) are created from local files, never from
# the placeholder template k8s/secrets.yaml:
#   secrets/postgres_password.txt     (required)
#   secrets/groq_api_key.txt          (required)
#   secrets/jwt_secret_key.txt        (required)
#   secrets/grafana_admin_password.txt(required for monitoring)
#   secrets/tavily_api_key.txt        (optional - web search)
# The script FAILS LOUDLY if a required file is missing, empty, or still a
# placeholder - it never silently deploys with default credentials.
#
# ORDER MATTERS:
#   1. namespace (everything else lives in it)
#   2. operators (cert-manager / ESO) + wait for their CRDs to be Established
#   3. secrets
#   4. infra -> app -> ingress -> monitoring -> policies
# ============================================================================

set -euo pipefail

ENVIRONMENT=${1:-dev}
NAMESPACE="llama-agent"
CERT_MANAGER=${CERT_MANAGER:-false}
ESO_ENABLED=${ESO_ENABLED:-false}
OIDC_ENABLED=${OIDC_ENABLED:-false}
NETWORK_POLICY=${NETWORK_POLICY:-false}
ESO_VERSION=${ESO_VERSION:-latest}
SECRETS_DIR=${SECRETS_DIR:-secrets}

cd "$(dirname "$0")/.."

echo "🚀 Deploying Llama Agent to Kubernetes ($ENVIRONMENT)..."
echo "   CERT_MANAGER=$CERT_MANAGER ESO_ENABLED=$ESO_ENABLED OIDC_ENABLED=$OIDC_ENABLED NETWORK_POLICY=$NETWORK_POLICY"

command -v kubectl >/dev/null 2>&1 || { echo "❌ kubectl not found. Install it first."; exit 1; }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# read_secret <file> <required:true|false> -> prints the value; exits on
# missing/empty/placeholder values when required.
read_secret() {
    local file="$SECRETS_DIR/$1" required="$2" value
    if [ ! -f "$file" ]; then
        if [ "$required" = "true" ]; then
            echo "❌ Missing required secret file: $file" >&2
            exit 1
        fi
        return 0
    fi
    value="$(tr -d '\r' < "$file" | sed -e 's/[[:space:]]*$//')"
    if [ -z "$value" ]; then
        if [ "$required" = "true" ]; then
            echo "❌ Secret file is empty: $file" >&2
            exit 1
        fi
        return 0
    fi
    case "$value" in
        *"<"*">"*|*YOUR_*|*CHANGE_ME*|*change_me*|*changeme*|*REPLACE*|postgres|password|secret)
            echo "❌ $file still contains a placeholder/default value - set a real secret." >&2
            exit 1
            ;;
    esac
    printf '%s' "$value"
}

wait_ready() {  # wait_ready <label selector> <timeout>
    kubectl wait --for=condition=ready pod -l "$1" -n "$NAMESPACE" --timeout="$2"
}

# ---------------------------------------------------------------------------
# 1. Namespace FIRST (ExternalSecrets, Secrets, everything is namespaced)
# ---------------------------------------------------------------------------
echo "📦 Creating namespace..."
kubectl apply -f k8s/namespace.yaml

# ---------------------------------------------------------------------------
# 2. Operators
# ---------------------------------------------------------------------------
if [ "$CERT_MANAGER" = "true" ]; then
    echo "🔐 Installing cert-manager..."
    command -v helm >/dev/null 2>&1 || { echo "❌ helm is required for CERT_MANAGER=true"; exit 1; }
    if ! kubectl get ns cert-manager >/dev/null 2>&1; then
        helm repo add jetstack https://charts.jetstack.io >/dev/null 2>&1 || true
        helm repo update jetstack
        helm install cert-manager jetstack/cert-manager \
            --namespace cert-manager --create-namespace --set crds.enabled=true --wait
    fi
    kubectl wait --for=condition=Established --timeout=120s \
        crd/certificates.cert-manager.io crd/clusterissuers.cert-manager.io
    kubectl -n cert-manager rollout status deploy/cert-manager-webhook --timeout=180s
    echo "📜 Applying ClusterIssuers..."
    kubectl apply -f k8s/cert-manager.yaml
fi

if [ "$ESO_ENABLED" = "true" ]; then
    echo "🔑 Installing External Secrets Operator ($ESO_VERSION)..."
    if [ "$ESO_VERSION" = "latest" ]; then
        ESO_URL="https://github.com/external-secrets/external-secrets/releases/latest/download/install.yaml"
    else
        ESO_URL="https://github.com/external-secrets/external-secrets/releases/download/${ESO_VERSION}/install.yaml"
    fi
    kubectl apply --server-side -f "$ESO_URL"
    echo "⏳ Waiting for ESO CRDs + webhook..."
    kubectl wait --for=condition=Established --timeout=180s \
        crd/externalsecrets.external-secrets.io \
        crd/clustersecretstores.external-secrets.io
    kubectl -n external-secrets wait --for=condition=Available deployment --all --timeout=300s  # gitleaks:allow (kubectl flag, not a secret)
    kubectl get secret aws-creds -n external-secrets >/dev/null 2>&1 || \
      kubectl get secret aws-creds -n "$NAMESPACE" >/dev/null 2>&1 || \
      echo "⚠️  Secret 'aws-creds' not found - the ClusterSecretStore cannot authenticate until you create it."
    echo "📜 Applying ExternalSecret resources..."
    kubectl apply -f k8s/external-secrets.yaml
    echo "⏳ Waiting for ExternalSecrets to sync..."
    kubectl wait --for=condition=Ready externalsecret --all -n "$NAMESPACE" --timeout=180s
fi

# ---------------------------------------------------------------------------
# 3. Secrets (from local files - fail loudly on placeholders)
# ---------------------------------------------------------------------------
if [ "$ESO_ENABLED" = "true" ]; then
    echo "🔐 ESO owns app-secrets/db-secrets (static secrets skipped)"
else
    echo "🔐 Creating secrets from $SECRETS_DIR/ ..."
    PG_PASSWORD="$(read_secret postgres_password.txt true)"
    GROQ_KEY="$(read_secret groq_api_key.txt true)"
    JWT_KEY="$(read_secret jwt_secret_key.txt true)"
    TAVILY_KEY="$(read_secret tavily_api_key.txt false)"

    kubectl -n "$NAMESPACE" create secret generic db-secrets \
        --from-literal=username=postgres \
        --from-literal=password="$PG_PASSWORD" \
        --dry-run=client -o yaml | kubectl apply -f -
    # DATABASE_URL carries no password: pods get POSTGRES_PASSWORD from
    # db-secrets and the app injects it (modules/secrets.resolve_database_url).
    kubectl -n "$NAMESPACE" create secret generic app-secrets \
        --from-literal=database-url="postgresql+asyncpg://postgres@postgres-service:5432/llama_agent" \
        --from-literal=redis-url="redis://redis-service:6379" \
        --from-literal=groq-api-key="$GROQ_KEY" \
        --from-literal=jwt-secret-key="$JWT_KEY" \
        --from-literal=tavily-api-key="$TAVILY_KEY" \
        --dry-run=client -o yaml | kubectl apply -f -
    unset PG_PASSWORD GROQ_KEY JWT_KEY TAVILY_KEY
fi

GRAFANA_PW="$(read_secret grafana_admin_password.txt true)"

# ---------------------------------------------------------------------------
# 4. Infrastructure
# ---------------------------------------------------------------------------
echo "🗄️  Deploying PostgreSQL + Redis..."
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/redis.yaml
echo "⏳ Waiting for infrastructure..."
wait_ready app=postgres 180s
wait_ready app=redis 120s

echo "🧠 Deploying ChromaDB (shared vector store)..."
kubectl apply -f k8s/chromadb.yaml
wait_ready app=chromadb 180s

echo "🔭 Deploying Phoenix (observability)..."
kubectl apply -f k8s/phoenix.yaml

# ---------------------------------------------------------------------------
# 5. Application
# ---------------------------------------------------------------------------
echo "🤖 Deploying API, MCP server, Next.js frontend..."
# IMAGE_TAG=<git sha> pins every app image in one apply (one rollout).
bash k8s/apply-app.sh "${IMAGE_TAG:-v7}" "$NAMESPACE"

echo "⏳ Waiting for application rollouts..."
kubectl -n "$NAMESPACE" rollout status deployment/llama-agent-api --timeout=1200s
kubectl -n "$NAMESPACE" rollout status deployment/llama-agent-mcp-server --timeout=180s
kubectl -n "$NAMESPACE" rollout status deployment/llama-agent-frontend --timeout=180s

if kubectl get pvc uploads-pvc -n "$NAMESPACE" >/dev/null 2>&1; then
    echo "ℹ️  Legacy PVC 'uploads-pvc' is no longer used (documents live in ChromaDB)."
    echo "   Remove it when convenient: kubectl delete pvc uploads-pvc -n $NAMESPACE"
fi

# ---------------------------------------------------------------------------
# 6. Ingress (API/frontend + passthrough ingress for /ws)
# ---------------------------------------------------------------------------
echo "🌐 Setting up Ingress..."
kubectl apply -f k8s/ingress.yaml
kubectl apply -f k8s/frontend-ingress.yaml

# ---------------------------------------------------------------------------
# 7. Monitoring
# ---------------------------------------------------------------------------
echo "📈 Deploying monitoring stack..."
kubectl -n "$NAMESPACE" create secret generic grafana-secret \
    --from-literal=admin-password="$GRAFANA_PW" \
    --dry-run=client -o yaml | kubectl apply -f -
HTPASSWD="admin:$(openssl passwd -apr1 "$GRAFANA_PW")"
kubectl -n "$NAMESPACE" create secret generic monitoring-auth \
    --from-literal=auth="$HTPASSWD" \
    --dry-run=client -o yaml | kubectl apply -f -
unset GRAFANA_PW HTPASSWD
kubectl -n "$NAMESPACE" create configmap grafana-dashboard-llama-agent \
    --from-file=llama-agent.json=config/grafana/dashboards/llama-agent.json \
    --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f k8s/prometheus.yaml
kubectl apply -f k8s/loki.yaml
kubectl apply -f k8s/promtail.yaml
kubectl apply -f k8s/node-exporter.yaml
kubectl apply -f k8s/grafana.yaml

echo "⏳ Waiting for monitoring pods..."
wait_ready component=prometheus 120s
wait_ready component=loki 120s
wait_ready component=grafana 120s
wait_ready component=promtail 120s
wait_ready component=node-exporter 120s

kubectl apply -f k8s/grafana-ingress.yaml
kubectl apply -f k8s/prometheus-ingress.yaml
kubectl apply -f k8s/loki-ingress.yaml
kubectl apply -f k8s/phoenix-ingress.yaml

# ---------------------------------------------------------------------------
# 8. Backups, availability, quotas
# ---------------------------------------------------------------------------
echo "🗄️  Deploying Postgres backup CronJob..."
kubectl apply -f k8s/postgres-backup-cronjob.yaml

echo "🛡️  Deploying PodDisruptionBudgets + HPA + ResourceQuota..."
kubectl apply -f k8s/poddisruptionbudgets.yaml
# The HPAs need metrics-server (kind / Docker Desktop don't ship it).
kubectl apply -f k8s/metrics-server.yaml
kubectl apply -f k8s/api-hpa.yaml
kubectl apply -f k8s/resourcequota.yaml

# ---------------------------------------------------------------------------
# 9. Optional: SSO for monitoring, NetworkPolicies
# ---------------------------------------------------------------------------
if [ "$OIDC_ENABLED" = "true" ]; then
    echo "🔐 Deploying oauth2-proxy SSO..."
    kubectl get secret oauth2-proxy-secret -n "$NAMESPACE" >/dev/null 2>&1 || {
        echo "❌ Secret 'oauth2-proxy-secret' is missing (IdP client id/secret)."; exit 1; }
    kubectl apply -f k8s/oauth2-proxy.yaml
    kubectl apply -f k8s/oauth2-proxy-ingress.yaml
    wait_ready component=oauth2-proxy 120s
    for ing in grafana-ingress prometheus-ingress loki-ingress phoenix-ingress; do
        kubectl annotate ingress "$ing" -n "$NAMESPACE" --overwrite \
            nginx.ingress.kubernetes.io/auth-url="https://oauth.llama-agent.local/oauth2/auth" \
            nginx.ingress.kubernetes.io/auth-signin="https://oauth.llama-agent.local/oauth2/start?rd=\$escaped_request_uri"
        kubectl annotate ingress "$ing" -n "$NAMESPACE" \
            nginx.ingress.kubernetes.io/auth-type- \
            nginx.ingress.kubernetes.io/auth-secret- \
            nginx.ingress.kubernetes.io/auth-realm- || true
    done
    kubectl set env deployment/grafana -n "$NAMESPACE" GF_AUTH_GENERIC_OAUTH_ENABLED="true"
    echo "✅ SSO enabled."
fi

if [ "$NETWORK_POLICY" = "true" ]; then
    echo "🕸️  Applying NetworkPolicies..."
    kubectl apply -f k8s/networkpolicy.yaml
fi

echo "✅ Deployment complete!"
echo ""
echo "📊 Check status:  kubectl get pods -n $NAMESPACE"
echo "📋 API logs:      kubectl logs -f deployment/llama-agent-api -n $NAMESPACE"
echo "📋 MCP logs:      kubectl logs -f deployment/llama-agent-mcp-server -n $NAMESPACE"
echo "📋 Frontend logs: kubectl logs -f deployment/llama-agent-frontend -n $NAMESPACE"
echo "🔗 App URL:       https://llama-agent.local"
echo "🔭 Phoenix:       https://phoenix.llama-agent.local"
echo "📊 Grafana:       https://grafana.llama-agent.local (admin / secrets/grafana_admin_password.txt)"
echo "📈 Prometheus:    https://prometheus.llama-agent.local"
echo "🪵 Loki:          https://loki.llama-agent.local"
