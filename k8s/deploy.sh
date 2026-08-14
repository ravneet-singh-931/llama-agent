#!/bin/bash
# ============================================
# Kubernetes Deployment Script
# ============================================
# Usage: ./k8s/deploy.sh [environment]
# Environments: dev, staging, production
#
# Optional feature flags (set to "true" to activate — all OFF by default so
# the local lab keeps working without extra operators installed):
#   CERT_MANAGER=true   install cert-manager + apply k8s/cert-manager.yaml
#                       (required for the TLS blocks on the ingresses)
#   ESO_ENABLED=true    install External Secrets Operator + apply
#                       k8s/external-secrets.yaml, and SKIP the static
#                       k8s/secrets.yaml (ESO owns the Secrets)
#   OIDC_ENABLED=true   deploy oauth2-proxy SSO in front of the observability
#                       UIs (replaces basic-auth on those ingresses)
#   NETWORK_POLICY=true apply the zero-trust NetworkPolicies (k8s/networkpolicy.yaml)
#
# Example (production cloud):
#   CERT_MANAGER=true ESO_ENABLED=true OIDC_ENABLED=true NETWORK_POLICY=true \
#     ./k8s/deploy.sh production
# ============================================

set -e

ENVIRONMENT=${1:-dev}
NAMESPACE="llama-agent"

# Feature flags (default off)
CERT_MANAGER=${CERT_MANAGER:-false}
ESO_ENABLED=${ESO_ENABLED:-false}
OIDC_ENABLED=${OIDC_ENABLED:-false}
NETWORK_POLICY=${NETWORK_POLICY:-false}

echo "🚀 Deploying Llama Agent to Kubernetes ($ENVIRONMENT)..."
echo "   CERT_MANAGER=$CERT_MANAGER ESO_ENABLED=$ESO_ENABLED OIDC_ENABLED=$OIDC_ENABLED NETWORK_POLICY=$NETWORK_POLICY"

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl not found. Install it first."
    exit 1
fi

# ---- Optional: cert-manager (Point 1) ----
if [ "$CERT_MANAGER" = "true" ]; then
    echo "🔐 Installing cert-manager..."
    kubectl get ns cert-manager >/dev/null 2>&1 || \
      helm repo add jetstack https://charts.jetstack.io && \
      helm install cert-manager jetstack/cert-manager \
        --namespace cert-manager --create-namespace --set crds.enabled=true
    echo "📜 Applying ClusterIssuers..."
    kubectl apply -f k8s/cert-manager.yaml
fi

# ---- Optional: External Secrets Operator (Point 6) ----
if [ "$ESO_ENABLED" = "true" ]; then
    echo "🔑 Installing External Secrets Operator..."
    kubectl apply -f https://github.com/external-secrets/external-secrets/releases/latest/download/install.yaml
    echo "📜 Applying ExternalSecret resources..."
    kubectl apply -f k8s/external-secrets.yaml
fi

# Create namespace
echo "📦 Creating namespace..."
kubectl apply -f k8s/namespace.yaml

# Create secrets (if not using ESO)
if [ "$ESO_ENABLED" = "true" ]; then
    echo "⚠️  ESO_ENABLED=true: skipping static k8s/secrets.yaml (ESO owns Secrets)"
else
    echo "🔐 Setting up secrets..."
    kubectl apply -f k8s/secrets.yaml || echo "⚠️  Secrets already exist (skipping)"
fi

# Deploy infrastructure
echo "🗄️  Deploying PostgreSQL..."
kubectl apply -f k8s/postgres.yaml

echo "🔴 Deploying Redis..."
kubectl apply -f k8s/redis.yaml

# Wait for infrastructure
echo "⏳ Waiting for infrastructure..."
kubectl wait --for=condition=ready pod -l app=postgres -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l app=redis -n $NAMESPACE --timeout=120s

# Deploy observability (Phoenix must exist before API/web/mcp so the
# "phoenix" Service is present; app pods rely on http://phoenix:6006)
echo "🔭 Deploying Phoenix (observability)..."
kubectl apply -f k8s/phoenix.yaml

# Deploy application
echo "🤖 Deploying Llama Agent API..."
kubectl apply -f k8s/api.yaml

echo "🔧 Deploying MCP Agent Server..."
kubectl apply -f k8s/mcp-server.yaml

echo "🌐 Deploying Streamlit Web UI..."
kubectl apply -f k8s/web.yaml

echo "🖥️  Deploying Next.js Frontend..."
kubectl apply -f k8s/frontend.yaml

# Wait for application pods
echo "⏳ Waiting for application pods..."
kubectl wait --for=condition=ready pod -l component=api -n $NAMESPACE --timeout=180s
kubectl wait --for=condition=ready pod -l component=mcp-server -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=web -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=frontend -n $NAMESPACE --timeout=120s

# Setup ingress
echo "🌐 Setting up Ingress..."
kubectl apply -f k8s/ingress.yaml
kubectl apply -f k8s/frontend-ingress.yaml

# ============================================
# Monitoring stack (Prometheus, Loki, promtail, Grafana, node-exporter)
# ============================================
echo "📈 Deploying monitoring stack..."

# Grafana admin password Secret — created from the gitignored file.
# k8s/secrets.yaml only holds placeholders; real secrets are created
# directly with kubectl (never commit the real files).
kubectl create secret generic grafana-secret -n $NAMESPACE \
  --from-file=admin-password=secrets/grafana_admin_password.txt \
  --dry-run=client -o yaml | kubectl apply -f -

# Grafana dashboard ConfigMap — created from the repo dashboard JSON
# (avoids YAML-escaping the ~8KB JSON inside a manifest).
kubectl create configmap grafana-dashboard-llama-agent -n $NAMESPACE \
  --from-file=llama-agent.json=config/grafana/dashboards/llama-agent.json \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f k8s/prometheus.yaml
kubectl apply -f k8s/loki.yaml
kubectl apply -f k8s/promtail.yaml
kubectl apply -f k8s/node-exporter.yaml
kubectl apply -f k8s/grafana.yaml

echo "⏳ Waiting for monitoring pods..."
kubectl wait --for=condition=ready pod -l component=prometheus -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=loki -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=grafana -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=promtail -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=node-exporter -n $NAMESPACE --timeout=120s

# Monitoring UIs — Grafana keeps its own login; Prometheus/Loki/Phoenix are
# protected by basic-auth using the SAME credentials as Grafana.
kubectl apply -f k8s/grafana-ingress.yaml
kubectl apply -f k8s/prometheus-ingress.yaml
kubectl apply -f k8s/loki-ingress.yaml
kubectl apply -f k8s/phoenix-ingress.yaml

# Monitoring basic-auth Secret — SAME credentials as Grafana
# (admin / secrets/grafana_admin_password.txt). Generated at deploy time from
# the gitignored password file; never committed.
if [ -f secrets/grafana_admin_password.txt ]; then
  GRAFANA_PW=$(cat secrets/grafana_admin_password.txt)
  HTPASSWD="admin:$(openssl passwd -apr1 "$GRAFANA_PW")"
  printf '%s' "$HTPASSWD" > /tmp/monitoring_htpasswd.txt
  kubectl create secret generic monitoring-auth -n $NAMESPACE \
    --from-file=auth=/tmp/monitoring_htpasswd.txt \
    --dry-run=client -o yaml | kubectl apply -f -
  rm -f /tmp/monitoring_htpasswd.txt
fi

# ============================================
# Point 5: backups + Point 7: PDB / HPA / ResourceQuota
# ============================================
echo "🗄️  Deploying Postgres backup CronJob..."
kubectl apply -f k8s/postgres-backup-cronjob.yaml
echo "🛡️  Deploying PodDisruptionBudgets + HPA + ResourceQuota..."
kubectl apply -f k8s/poddisruptionbudgets.yaml
kubectl apply -f k8s/api-hpa.yaml
kubectl apply -f k8s/resourcequota.yaml

# ============================================
# Point 2 (optional): oauth2-proxy SSO for observability
# ============================================
if [ "$OIDC_ENABLED" = "true" ]; then
    echo "🔐 Deploying oauth2-proxy SSO..."
    kubectl apply -f k8s/oauth2-proxy.yaml
    kubectl apply -f k8s/oauth2-proxy-ingress.yaml
    kubectl wait --for=condition=ready pod -l component=oauth2-proxy -n $NAMESPACE --timeout=120s
    # Replace basic-auth with external-auth on the observability ingresses.
    for ing in grafana-ingress prometheus-ingress loki-ingress phoenix-ingress; do
        kubectl annotate ingress "$ing" -n $NAMESPACE --overwrite \
            nginx.ingress.kubernetes.io/auth-url="https://oauth.llama-agent.local/oauth2/auth" \
            nginx.ingress.kubernetes.io/auth-signin="https://oauth.llama-agent.local/oauth2/start?rd=\$escaped_request_uri"
        kubectl annotate ingress "$ing" -n $NAMESPACE \
            nginx.ingress.kubernetes.io/auth-type- \
            nginx.ingress.kubernetes.io/auth-secret- \
            nginx.ingress.kubernetes.io/auth-realm-
    done
    # Activate Grafana's native OAuth (env is optional:true, disabled by default).
    kubectl set env deployment/grafana -n $NAMESPACE GF_AUTH_GENERIC_OAUTH_ENABLED="true"
    echo "✅ SSO enabled. Ensure the 'oauth2-proxy-secret' exists with your IdP creds."
fi

# ============================================
# Point 7 (optional): zero-trust NetworkPolicies
# ============================================
if [ "$NETWORK_POLICY" = "true" ]; then
    echo "🕸️  Applying NetworkPolicies..."
    kubectl apply -f k8s/networkpolicy.yaml
fi

# Docker Desktop (kind mode) does NOT forward hostPort 80/443 from the node
# to the host. Its built-in cloud-provider DOES publish LoadBalancer services
# on localhost, so expose the ingress controller as a LoadBalancer:
#   kubectl patch svc ingress-nginx-controller -n ingress-nginx \
#     --type=merge -p '{"spec":{"type":"LoadBalancer"}}'
# Then: add "127.0.0.1 llama-agent.local" to the hosts file (admin rights).

echo "✅ Deployment complete!"
echo ""
echo "📊 Check status: kubectl get pods -n $NAMESPACE"
echo "📋 API logs:     kubectl logs -f deployment/llama-agent-api -n $NAMESPACE"
echo "📋 MCP logs:     kubectl logs -f deployment/llama-agent-mcp-server -n $NAMESPACE"
echo "📋 Web logs:     kubectl logs -f deployment/llama-agent-web -n $NAMESPACE"
echo "📋 Frontend logs: kubectl logs -f deployment/llama-agent-frontend -n $NAMESPACE"
echo "🔗 App URL:      http://llama-agent.local (add to /etc/hosts, then use the ingress)"
echo "🔭 Phoenix:      http://phoenix.llama-agent.local"
echo "📊 Grafana:      http://grafana.llama-agent.local (admin / secrets/grafana_admin_password.txt)"
echo "📈 Prometheus:   http://prometheus.llama-agent.local"
echo "🪵 Loki:         http://loki.llama-agent.local"
