#!/bin/bash
# ============================================================================
# 📚 LEARNER GUIDE: Kubernetes Deployment Script — k8s/deploy.sh
# ============================================================================
# WHAT IS THIS FILE?
# ------------------
# This is a BASH SCRIPT that automates deploying the entire app to Kubernetes.
# Instead of running 30+ `kubectl apply -f` commands manually, you run ONE:
#   ./k8s/deploy.sh [environment]
#
# WHAT DOES IT DO?
# ----------------
# 1. Checks kubectl exists
# 2. Optionally installs cert-manager, ESO, oauth2-proxy, NetworkPolicies (via flags)
# 3. Creates namespace
# 4. Creates secrets (or skips if ESO)
# 5. Deploys infrastructure (Postgres, Redis, ChromaDB, Phoenix)
# 6. Deploys app (API, MCP, Web, Frontend)
# 7. Sets up Ingresses
# 8. Deploys monitoring (Prometheus, Loki, Promtail, Grafana, node-exporter)
# 9. Creates monitoring secrets (grafana-secret, monitoring-auth)
# 10. Deploys backups, PDB, HPA, ResourceQuota
# 11. Optionally enables SSO and NetworkPolicies
#
# USAGE:
# ------
#   ./k8s/deploy.sh              # Deploy to dev (default)
#   ./k8s/deploy.sh production   # Deploy to production
#   CERT_MANAGER=true ./k8s/deploy.sh production  # With TLS
#   CERT_MANAGER=true ESO_ENABLED=true OIDC_ENABLED=true NETWORK_POLICY=true ./k8s/deploy.sh production  # Full cloud
#
# FEATURE FLAGS (all OFF by default for local lab):
# --------------------------------------------------
#   CERT_MANAGER=true   → Install cert-manager + ClusterIssuers (for TLS)
#   ESO_ENABLED=true    → Install ESO + ExternalSecrets, skip static secrets.yaml
#   OIDC_ENABLED=true   → Deploy oauth2-proxy + external-auth for monitoring
#   NETWORK_POLICY=true → Apply zero-trust NetworkPolicies
#
# KUBERNETES CONCEPTS IN THIS SCRIPT:
# -----------------------------------
# - kubectl apply -f (create/update resources)
# - kubectl wait --for=condition=ready (wait for Pods to be ready)
# - kubectl create secret/configmap --dry-run=client -o yaml | kubectl apply -f - (create from file)
# - kubectl annotate, kubectl set env, kubectl patch (modify resources)
# - Helm (for cert-manager)
#
# FLOW (step-by-step, with learner comments below):
# --------------------------------------------------
# See inline comments for each command!
# ============================================================================

# Exit on error (if any command fails, script stops)
#   - Without this, script would continue even if kubectl fails (bad!)
set -e

# ENVIRONMENT: First argument or "dev" if not provided
#   - ${1:-dev} = Use $1 if set, else "dev"
#   - Example: ./k8s/deploy.sh production → ENVIRONMENT=production
ENVIRONMENT=${1:-dev}

# NAMESPACE: Fixed to "llama-agent" (must match namespace.yaml)
NAMESPACE="llama-agent"

# Feature flags (default off, can be overridden via env vars)
#   - ${VAR:-false} = Use VAR if set, else "false"
#   - Example: CERT_MANAGER=true ./k8s/deploy.sh → CERT_MANAGER=true
CERT_MANAGER=${CERT_MANAGER:-false}
ESO_ENABLED=${ESO_ENABLED:-false}
OIDC_ENABLED=${OIDC_ENABLED:-false}
NETWORK_POLICY=${NETWORK_POLICY:-false}

# Print deployment info (so user knows what's happening)
echo "🚀 Deploying Llama Agent to Kubernetes ($ENVIRONMENT)..."
echo "   CERT_MANAGER=$CERT_MANAGER ESO_ENABLED=$ESO_ENABLED OIDC_ENABLED=$OIDC_ENABLED NETWORK_POLICY=$NETWORK_POLICY"

# Check if kubectl is available (required for all k8s operations)
#   - command -v kubectl = check if kubectl exists in PATH
#   - &> /dev/null = hide output
#   - If not found, print error and exit 1 (failure)
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl not found. Install it first."
    exit 1
fi

# ---- Optional: cert-manager (Point 1: TLS) ----
# If CERT_MANAGER=true, install cert-manager via Helm and apply ClusterIssuers
if [ "$CERT_MANAGER" = "true" ]; then
    echo "🔐 Installing cert-manager..."
    # Check if cert-manager namespace exists, if not, install via Helm
    #   - kubectl get ns cert-manager → check if namespace exists
    #   - || → if fails (not exists), run Helm install
    #   - helm repo add jetstack → Add cert-manager Helm repo
    #   - helm install cert-manager → Install cert-manager with CRDs
    kubectl get ns cert-manager >/dev/null 2>&1 || \
      helm repo add jetstack https://charts.jetstack.io && \
      helm install cert-manager jetstack/cert-manager \
        --namespace cert-manager --create-namespace --set crds.enabled=true
    echo "📜 Applying ClusterIssuers..."
    # Apply ClusterIssuers (letsencrypt-prod, staging, selfsigned)
    kubectl apply -f k8s/cert-manager.yaml
fi

# ---- Optional: External Secrets Operator (Point 6: Secret Manager) ----
# If ESO_ENABLED=true, install ESO and apply ExternalSecrets
if [ "$ESO_ENABLED" = "true" ]; then
    echo "🔑 Installing External Secrets Operator..."
    # Install ESO from GitHub releases (latest)
    kubectl apply -f https://github.com/external-secrets/external-secrets/releases/latest/download/install.yaml
    echo "📜 Applying ExternalSecret resources..."
    # Apply ExternalSecrets (ClusterSecretStore + ExternalSecrets)
    kubectl apply -f k8s/external-secrets.yaml
fi

# Create namespace (always, even if exists — apply is idempotent)
echo "📦 Creating namespace..."
kubectl apply -f k8s/namespace.yaml

# Create secrets (if not using ESO)
#   - If ESO_ENABLED=true, skip static secrets.yaml (ESO owns Secrets)
#   - Else, apply secrets.yaml (with placeholders, for lab)
if [ "$ESO_ENABLED" = "true" ]; then
    echo "⚠️  ESO_ENABLED=true: skipping static k8s/secrets.yaml (ESO owns Secrets)"
else
    echo "🔐 Setting up secrets..."
    # Apply secrets.yaml, but don't fail if already exists (|| echo)
    kubectl apply -f k8s/secrets.yaml || echo "⚠️  Secrets already exist (skipping)"
fi

# Deploy infrastructure (Postgres, Redis)
echo "🗄️  Deploying PostgreSQL..."
kubectl apply -f k8s/postgres.yaml

echo "🔴 Deploying Redis..."
kubectl apply -f k8s/redis.yaml

# Wait for infrastructure to be ready (Pods must be Running and Ready)
#   - kubectl wait --for=condition=ready pod -l app=postgres → Wait for Pods with label app=postgres
#   - --timeout=120s → Fail if not ready in 120s
echo "⏳ Waiting for infrastructure..."
kubectl wait --for=condition=ready pod -l app=postgres -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l app=redis -n $NAMESPACE --timeout=120s

# Deploy ChromaDB (shared vector store for all API replicas)
echo "Deploying ChromaDB (shared vector store)..."
kubectl apply -f k8s/chromadb.yaml
kubectl wait --for=condition=ready pod -l app=chromadb -n $NAMESPACE --timeout=120s

# Deploy observability (Phoenix must exist before API/web/mcp so the
# "phoenix" Service is present; app pods rely on http://phoenix:6006)
#   - If Phoenix Service doesn't exist, API pods will have PHOENIX_PORT=tcp://... (wrong)
echo "🔭 Deploying Phoenix (observability)..."
kubectl apply -f k8s/phoenix.yaml

# Deploy application (API, MCP, Web, Frontend)
echo "🤖 Deploying Llama Agent API..."
kubectl apply -f k8s/api.yaml

echo "🔧 Deploying MCP Agent Server..."
kubectl apply -f k8s/mcp-server.yaml

echo "🌐 Deploying Streamlit Web UI..."
kubectl apply -f k8s/web.yaml

echo "🖥️  Deploying Next.js Frontend..."
kubectl apply -f k8s/frontend.yaml

# Wait for application pods to be ready
echo "⏳ Waiting for application pods..."
kubectl wait --for=condition=ready pod -l component=api -n $NAMESPACE --timeout=180s
kubectl wait --for=condition=ready pod -l component=mcp-server -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=web -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=frontend -n $NAMESPACE --timeout=120s

# Setup ingress (main + frontend)
echo "🌐 Setting up Ingress..."
kubectl apply -f k8s/ingress.yaml
kubectl apply -f k8s/frontend-ingress.yaml

# ============================================
# Monitoring stack (Prometheus, Loki, promtail, Grafana, node-exporter)
# ============================================
echo "📈 Deploying monitoring stack..."

# Grafana admin password Secret — created from gitignored file
#   - k8s/secrets.yaml only holds placeholders; real secrets are created via kubectl
#   - --from-file=admin-password=secrets/grafana_admin_password.txt → Read file, create Secret key
#   - --dry-run=client -o yaml | kubectl apply -f - → Create YAML and apply (idempotent)
kubectl create secret generic grafana-secret -n $NAMESPACE \
  --from-file=admin-password=secrets/grafana_admin_password.txt \
  --dry-run=client -o yaml | kubectl apply -f -

# Grafana dashboard ConfigMap — created from repo dashboard JSON
#   - Avoids YAML-escaping the ~8KB JSON inside a manifest
kubectl create configmap grafana-dashboard-llama-agent -n $NAMESPACE \
  --from-file=llama-agent.json=config/grafana/dashboards/llama-agent.json \
  --dry-run=client -o yaml | kubectl apply -f -

# Apply monitoring Deployments
kubectl apply -f k8s/prometheus.yaml
kubectl apply -f k8s/loki.yaml
kubectl apply -f k8s/promtail.yaml
kubectl apply -f k8s/node-exporter.yaml
kubectl apply -f k8s/grafana.yaml

# Wait for monitoring pods
echo "⏳ Waiting for monitoring pods..."
kubectl wait --for=condition=ready pod -l component=prometheus -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=loki -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=grafana -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=promtail -n $NAMESPACE --timeout=120s
kubectl wait --for=condition=ready pod -l component=node-exporter -n $NAMESPACE --timeout=120s

# Monitoring UIs — Grafana keeps its own login; Prometheus/Loki/Phoenix are
# protected by basic-auth using SAME credentials as Grafana
kubectl apply -f k8s/grafana-ingress.yaml
kubectl apply -f k8s/prometheus-ingress.yaml
kubectl apply -f k8s/loki-ingress.yaml
kubectl apply -f k8s/phoenix-ingress.yaml

# Monitoring basic-auth Secret — SAME credentials as Grafana
#   - Generated at deploy time from gitignored password file; never committed
#   - htpasswd format: admin:<hashed-password> (via openssl passwd -apr1)
if [ -f secrets/grafana_admin_password.txt ]; then
  GRAFANA_PW=$(cat secrets/grafana_admin_password.txt)  # Read password file
  HTPASSWD="admin:$(openssl passwd -apr1 "$GRAFANA_PW")"  # Hash it (apr1 = MD5)
  printf '%s' "$HTPASSWD" > /tmp/monitoring_htpasswd.txt  # Write to temp file
  kubectl create secret generic monitoring-auth -n $NAMESPACE \
    --from-file=auth=/tmp/monitoring_htpasswd.txt \
    --dry-run=client -o yaml | kubectl apply -f -
  rm -f /tmp/monitoring_htpasswd.txt  # Clean up temp file (security)
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
# If OIDC_ENABLED=true, deploy oauth2-proxy and switch monitoring ingresses to SSO
if [ "$OIDC_ENABLED" = "true" ]; then
    echo "🔐 Deploying oauth2-proxy SSO..."
    kubectl apply -f k8s/oauth2-proxy.yaml
    kubectl apply -f k8s/oauth2-proxy-ingress.yaml
    kubectl wait --for=condition=ready pod -l component=oauth2-proxy -n $NAMESPACE --timeout=120s
    # Replace basic-auth with external-auth on observability ingresses
    #   - auth-url: Where to check auth (oauth2-proxy)
    #   - auth-signin: Where to redirect if not authed (oauth2-proxy login)
    #   - Remove old basic-auth annotations (auth-type, auth-secret, auth-realm)
    for ing in grafana-ingress prometheus-ingress loki-ingress phoenix-ingress; do
        kubectl annotate ingress "$ing" -n $NAMESPACE --overwrite \
            nginx.ingress.kubernetes.io/auth-url="https://oauth.llama-agent.local/oauth2/auth" \
            nginx.ingress.kubernetes.io/auth-signin="https://oauth.llama-agent.local/oauth2/start?rd=\$escaped_request_uri"
        kubectl annotate ingress "$ing" -n $NAMESPACE \
            nginx.ingress.kubernetes.io/auth-type- \
            nginx.ingress.kubernetes.io/auth-secret- \
            nginx.ingress.kubernetes.io/auth-realm-
    done
    # Activate Grafana's native OAuth (env is optional:true, disabled by default)
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

# Docker Desktop (kind mode) does NOT forward hostPort 80/443 from node to host.
# Its built-in cloud-provider DOES publish LoadBalancer services on localhost,
# so expose ingress controller as LoadBalancer:
#   kubectl patch svc ingress-nginx-controller -n ingress-nginx \
#     --type=merge -p '{"spec":{"type":"LoadBalancer"}}'
# Then: add "127.0.0.1 llama-agent.local" to hosts file (admin rights).

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

# 💡 LEARNER TIP: What to do after deploy.sh?
#   kubectl get pods -n llama-agent  (check all Pods are Running)
#   kubectl get ingress -n llama-agent  (check Ingresses)
#   kubectl logs -f deployment/llama-agent-api -n llama-agent  (check API logs)
#   Add to hosts: echo "127.0.0.1 llama-agent.local grafana.llama-agent.local prometheus.llama-agent.local loki.llama-agent.local phoenix.llama-agent.local oauth.llama-agent.local" | sudo tee -a /etc/hosts
#   Open: http://llama-agent.local
