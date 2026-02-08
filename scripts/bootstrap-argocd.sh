#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./scripts/bootstrap-argocd.sh [environment]
#
# Defaults to 'test-k3s'. The script installs Argo CD via Helm and then applies the
# environment root app so GitOps can take over.

ENVIRONMENT="${1:-test-k3s}"
NAMESPACE="blackcircuit-system"
ROOT_APP_PATH="clusters/${ENVIRONMENT}/root-app.yaml"

# Pin the HELM chart version (not the app version)
ARGO_HELM_CHART_VERSION="7.7.12"

echo ">>> Target environment: ${ENVIRONMENT}"
echo ">>> Adding Argo Helm repo"
helm repo add argo https://argoproj.github.io/argo-helm
helm repo update

echo ">>> Ensuring namespace exists: ${NAMESPACE}"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo ">>> Installing ArgoCD via Helm (chart ${ARGO_HELM_CHART_VERSION})"
helm upgrade --install argocd argo/argo-cd \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --version "${ARGO_HELM_CHART_VERSION}" \
  -f platform/argocd/values.yaml \
  --set dex.enabled=false \
  --wait

echo ">>> ArgoCD install complete"

if [[ -f "${ROOT_APP_PATH}" ]]; then
  echo ">>> Applying root app: ${ROOT_APP_PATH}"
  kubectl apply -f "${ROOT_APP_PATH}"
  echo ">>> Root app applied. ArgoCD should begin syncing shortly."
else
  echo "!!! Root app not found at ${ROOT_APP_PATH}"
  echo "    Create it (or pass the correct environment) and apply it manually with:"
  echo "    kubectl apply -f ${ROOT_APP_PATH}"
  exit 1
fi
