#!/usr/bin/env bash
set -euo pipefail

# Bootstrap Argo CD and optionally apply a root "app-of-apps".
#
# Configuration is via env vars (can be loaded from a .env file).
#
# Required tools: kubectl, helm
#
# Typical usage:
#   ORG_SLUG=aethericforge ENV=test-k3d ./bootstrap/argocd/bootstrap.sh
#
# Or:
#   ./bootstrap/argocd/bootstrap.sh --env-file bootstrap/env/test-k3d.env

ENV_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"; shift 2 ;;
    *)
      echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ -n "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

# ---- Customizable inputs (with sensible defaults) ---------------------------
ORG_SLUG="${ORG_SLUG:-aethericforge}"
ENV="${ENV:-test-k3d}"

ARGO_NAMESPACE="${ARGO_NAMESPACE:-argocd}"

# Where the GitOps root app lives
ROOT_APP_PATH="${ROOT_APP_PATH:-gitops/clusters/${ENV}/root-app.yaml}"

# Helm chart version (pin for reproducibility)
ARGO_HELM_CHART_VERSION="${ARGO_HELM_CHART_VERSION:-7.7.12}"

# Values layering (base + optional overrides)
VALUES_BASE="${VALUES_BASE:-bootstrap/argocd/values.yaml}"
VALUES_ORG="${VALUES_ORG:-bootstrap/argocd/values.${ORG_SLUG}.yaml}"
VALUES_ENV="${VALUES_ENV:-bootstrap/argocd/values.${ENV}.yaml}"

# Whether to apply the root app-of-apps after install
APPLY_ROOT_APP="${APPLY_ROOT_APP:-true}"

# ---- Helpers ----------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }

need kubectl
need helm

echo ">>> ORG_SLUG=${ORG_SLUG}"
echo ">>> ENV=${ENV}"
echo ">>> ARGO_NAMESPACE=${ARGO_NAMESPACE}"
echo ">>> ROOT_APP_PATH=${ROOT_APP_PATH}"

echo ">>> Adding Argo Helm repo"
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update >/dev/null

echo ">>> Ensuring namespace exists: ${ARGO_NAMESPACE}"
kubectl create namespace "${ARGO_NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Build Helm -f args list (base + optional overrides)
VALUES_ARGS=()
if [[ -f "${VALUES_BASE}" ]]; then VALUES_ARGS+=(-f "${VALUES_BASE}"); else
  echo "ERROR: base values file not found: ${VALUES_BASE}" >&2; exit 1
fi
if [[ -f "${VALUES_ORG}" ]]; then VALUES_ARGS+=(-f "${VALUES_ORG}"); fi
if [[ -f "${VALUES_ENV}" ]]; then VALUES_ARGS+=(-f "${VALUES_ENV}"); fi

echo ">>> Installing Argo CD via Helm (chart ${ARGO_HELM_CHART_VERSION})"
helm upgrade --install argocd argo/argo-cd \
  --namespace "${ARGO_NAMESPACE}" \
  --create-namespace \
  --version "${ARGO_HELM_CHART_VERSION}" \
  "${VALUES_ARGS[@]}" \
  --set dex.enabled=false \
  --wait

echo ">>> Argo CD install complete"

if [[ "${APPLY_ROOT_APP}" == "true" ]]; then
  if [[ -f "${ROOT_APP_PATH}" ]]; then
    echo ">>> Applying root app: ${ROOT_APP_PATH}"
    
    if grep -qE '^\s*kind:\s*\S+' "${ROOT_APP_PATH}"; then
      kubectl apply -f "${ROOT_APP_PATH}"
    else
      echo ">>> Root app is empty; skipping apply."
    fi
    echo ">>> Root app applied."
  else
    echo "ERROR: root app not found at ${ROOT_APP_PATH}" >&2
    echo "       Set ROOT_APP_PATH or create the file." >&2
    exit 1
  fi
else
  echo ">>> Skipping root app apply (APPLY_ROOT_APP=false)"
fi

echo ">>> Done."

