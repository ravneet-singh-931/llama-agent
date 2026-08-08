#!/bin/bash
# ============================================
# Kubernetes Deployment Script
# ============================================
# Usage: ./k8s/deploy.sh [environment]
# Environments: dev, staging, production

set -e

ENVIRONMENT=${1:-dev}
NAMESPACE="llama-agent"

echo "🚀 Deploying Llama Agent to Kubernetes ($ENVIRONMENT)..."

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "❌ kubectl not found. Install it first."
    exit 1
fi

# Create namespace
echo "📦 Creating namespace..."
kubectl apply -f k8s/namespace.yaml

# Create secrets (if not exists)
echo "🔐 Setting up secrets..."
kubectl apply -f k8s/secrets.yaml || echo "⚠️  Secrets already exist (skipping)"

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

kubectl apply -f k8s/monitoring-ingress.yaml

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
