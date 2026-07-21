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

# Deploy application
echo "🤖 Deploying Llama Agent API..."
kubectl apply -f k8s/api.yaml

# Setup ingress
echo "🌐 Setting up Ingress..."
kubectl apply -f k8s/ingress.yaml

echo "✅ Deployment complete!"
echo ""
echo "📊 Check status: kubectl get pods -n $NAMESPACE"
echo "📋 View logs:    kubectl logs -f deployment/llama-agent-api -n $NAMESPACE"
echo "🔗 API URL:      http://llama-agent.local (or configure your ingress)"
