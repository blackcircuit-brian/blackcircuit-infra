#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${ENVIRONMENT:-dev}"
PULUMI_STACK="${PULUMI_STACK:-${ENVIRONMENT}}"
KUSTOMIZE_OVERLAY="${KUSTOMIZE_OVERLAY:-clusters/single/${ENVIRONMENT}}"
PULUMI_DIR="${PULUMI_DIR:-scripts/pulumi}"
UPDATE_CABUNDLE="${UPDATE_CABUNDLE:-true}"
RESET_ACME_ACCOUNT="${RESET_ACME_ACCOUNT:-true}"

usage() {
  cat <<'EOF'
Single-command bootstrap + platform deployment.

Environment variables:
  ENVIRONMENT         Target environment name (default: dev)
  PULUMI_STACK        Pulumi stack name (default: ENVIRONMENT)
  KUSTOMIZE_OVERLAY   Cluster overlay path (default: clusters/single/$ENVIRONMENT)
  UPDATE_CABUNDLE     Update ClusterIssuer acme.caBundle from live step-ca cert (default: true)
  RESET_ACME_ACCOUNT  Delete stale ACME account key secret before reconcile (default: true)

Example:
  ENVIRONMENT=dev PULUMI_STACK=dev ./scripts/deploy-all.sh
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

required_cmds=(pulumi kubectl kustomize helm ksops)
for cmd in "${required_cmds[@]}"; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "Missing required command: ${cmd}" >&2
    exit 1
  fi
done

echo "==> pulumi up (${PULUMI_STACK})"
(
  cd "${PULUMI_DIR}"
  pulumi stack select "${PULUMI_STACK}"
  pulumi up -y
)

echo "==> bootstrap cert-manager CRDs"
kubectl apply -k platform/cert-manager/core
kubectl wait --for=condition=Established crd/clusterissuers.cert-manager.io --timeout=300s

echo "==> apply full overlay (${KUSTOMIZE_OVERLAY})"
kustomize build \
  --enable-helm \
  --enable-alpha-plugins \
  --enable-exec \
  "${KUSTOMIZE_OVERLAY}" | kubectl apply -f -

echo "==> wait for step-ca rollout"
kubectl -n step-ca rollout status deploy/step-ca --timeout=300s

if [[ "${UPDATE_CABUNDLE}" == "true" ]]; then
  echo "==> update step-ca caBundle in ClusterIssuer manifests"
  ./scripts/update-step-ca-cabundle.sh
  echo "==> apply updated cert-manager issuer manifests"
  kubectl apply -f platform/cert-manager/issuers/clusterissuer-step-ca-internal.yaml
fi

if [[ "${RESET_ACME_ACCOUNT}" == "true" ]]; then
  echo "==> reset ACME account key to force clean registration"
  kubectl -n cert-manager delete secret step-ca-int-acme-account-key --ignore-not-found
fi

echo "==> reconcile ACME ClusterIssuer"
kubectl annotate clusterissuer step-ca-int-acme reconcile.now="$(date +%s)" --overwrite

echo "==> wait for ClusterIssuer readiness"
if ! kubectl wait --for=condition=Ready clusterissuer/step-ca-int-acme --timeout=300s; then
  echo "ClusterIssuer failed to become Ready. Recent status:" >&2
  kubectl describe clusterissuer step-ca-int-acme >&2 || true
  exit 1
fi

echo "Deployment complete."
