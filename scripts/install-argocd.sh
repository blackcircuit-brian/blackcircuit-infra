#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="blackcircuit-system"

# Pin the HELM CHART version, not the app version
ARGO_HELM_CHART_VERSION="7.7.12"

echo ">>> Adding Argo Helm repo"
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update

echo ">>> Ensuring namespace exists"
kubectl create namespace ${NAMESPACE} --dry-run=client -o yaml | kubectl apply -f -

echo ">>> Installing ArgoCD via Helm (chart ${ARGO_HELM_CHART_VERSION})"

helm upgrade --install argocd argo/argo-cd \
  --namespace ${NAMESPACE} \
  --create-namespace \
  --version ${ARGO_HELM_CHART_VERSION} \
  --set dex.enabled=false \
  --wait

echo ">>> ArgoCD install complete"
