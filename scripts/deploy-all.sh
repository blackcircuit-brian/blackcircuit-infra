#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${ENVIRONMENT:-dev}"
PULUMI_STACK="${PULUMI_STACK:-${ENVIRONMENT}}"
KUSTOMIZE_OVERLAY="${KUSTOMIZE_OVERLAY:-clusters/single/${ENVIRONMENT}}"
PULUMI_DIR="${PULUMI_DIR:-scripts/pulumi}"
UPDATE_CABUNDLE="${UPDATE_CABUNDLE:-true}"
RESET_ACME_ACCOUNT="${RESET_ACME_ACCOUNT:-true}"
DEPLOY_PHASE="${DEPLOY_PHASE:-all}"
WAIT_FOR_WIREGUARD="${WAIT_FOR_WIREGUARD:-true}"

CLUSTER_ISSUER_NAME="${CLUSTER_ISSUER_NAME:-step-ca-int-acme}"
CLUSTER_ISSUER_MANIFEST="${CLUSTER_ISSUER_MANIFEST:-platform/cert-manager/issuers/clusterissuer-step-ca-internal.yaml}"

usage() {
  cat <<'EOF'
Single-command bootstrap + platform deployment.

Environment variables:
  ENVIRONMENT         Target environment name (default: dev)
  PULUMI_STACK        Pulumi stack name (default: ENVIRONMENT)
  KUSTOMIZE_OVERLAY   Cluster overlay path (default: clusters/single/$ENVIRONMENT)
  DEPLOY_PHASE        all | pulumi | platform (default: all)
  WAIT_FOR_WIREGUARD  Pause between Pulumi and platform phase in all mode (default: true)
  UPDATE_CABUNDLE     Update ClusterIssuer acme.caBundle from live step-ca cert (default: true)
  RESET_ACME_ACCOUNT  Delete stale ACME account key secret before reconcile (default: true)
  CLUSTER_ISSUER_NAME ClusterIssuer name (default: step-ca-int-acme)
  CLUSTER_ISSUER_MANIFEST Path to ClusterIssuer manifest
                      (default: platform/cert-manager/issuers/clusterissuer-step-ca-internal.yaml)

Example:
  ENVIRONMENT=dev PULUMI_STACK=dev ./scripts/deploy-all.sh
  DEPLOY_PHASE=pulumi ENVIRONMENT=dev ./scripts/deploy-all.sh
  DEPLOY_PHASE=platform ENVIRONMENT=dev ./scripts/deploy-all.sh
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

delete_acme_account_secrets() {
  echo "==> reset ACME account key to force clean registration"

  local current_ref
  current_ref="$(kubectl get clusterissuer "${CLUSTER_ISSUER_NAME}" -o jsonpath='{.spec.acme.privateKeySecretRef.name}' 2>/dev/null || true)"

  declare -A seen
  local candidates=(
    "${current_ref}"
    "step-ca-int-acme-account-key"
    "step-ca-int-acme-account-key-v2"
    "step-ca-int-acme-account-key-v3"
  )
  local secret_name
  for secret_name in "${candidates[@]}"; do
    if [[ -n "${secret_name}" && -z "${seen[${secret_name}]:-}" ]]; then
      seen["${secret_name}"]=1
      kubectl -n cert-manager delete secret "${secret_name}" --ignore-not-found
    fi
  done
}

run_pulumi_phase() {
  echo "==> pulumi up (${PULUMI_STACK})"
  (
    cd "${PULUMI_DIR}"
    pulumi stack select "${PULUMI_STACK}"
    pulumi up -y
  )
}

run_platform_phase() {
  echo "==> bootstrap cert-manager CRDs"
  kustomize build --enable-helm platform/cert-manager/core | kubectl apply -f -
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
    echo "==> recreate ClusterIssuer with live step-ca caBundle"
    CA_BUNDLE_B64="$(
      kubectl -n step-ca exec deploy/step-ca -- sh -c '
        set -eu
        if [ -f /home/step/certs/intermediate_ca.crt ]; then
          cat /home/step/certs/intermediate_ca.crt
        fi
        cat /home/step/certs/root_ca.crt
      ' | base64 -w0
    )"
    if [[ -z "${CA_BUNDLE_B64}" ]]; then
      echo "Failed to read non-empty step-ca CA bundle" >&2
      exit 1
    fi
    kubectl delete clusterissuer "${CLUSTER_ISSUER_NAME}" --ignore-not-found
    awk -v ca="${CA_BUNDLE_B64}" '
      {
        if ($1 == "caBundle:") {
          if (!updated) {
            print "    caBundle: " ca
            updated = 1
          }
          next
        }
        print
        if (!updated && $1 == "server:") {
          print "    caBundle: " ca
          updated = 1
        }
      }
      END {
        if (!updated) {
          exit 2
        }
      }
    ' "${CLUSTER_ISSUER_MANIFEST}" | kubectl apply -f -
  fi

  echo "==> restart cert-manager controller"
  kubectl -n cert-manager rollout restart deploy/cert-manager
  kubectl wait -n cert-manager --for=condition=Available deploy/cert-manager --timeout=180s

  if [[ "${RESET_ACME_ACCOUNT}" == "true" ]]; then
    delete_acme_account_secrets
  fi

  echo "==> reconcile ACME ClusterIssuer"
  kubectl annotate clusterissuer "${CLUSTER_ISSUER_NAME}" reconcile.now="$(date +%s)" --overwrite

  echo "==> wait for ClusterIssuer readiness"
  if ! kubectl wait --for=condition=Ready "clusterissuer/${CLUSTER_ISSUER_NAME}" --timeout=300s; then
    echo "ClusterIssuer failed to become Ready. Recent status:" >&2
    kubectl describe clusterissuer "${CLUSTER_ISSUER_NAME}" >&2 || true
    exit 1
  fi
}

case "${DEPLOY_PHASE}" in
  pulumi)
    run_pulumi_phase
    ;;
  platform)
    run_platform_phase
    ;;
  all)
    run_pulumi_phase
    if [[ "${WAIT_FOR_WIREGUARD}" == "true" ]]; then
      cat <<'EOF'
==> pause for WireGuard configuration
Complete WireGuard setup and verify cluster access before continuing:
  kubectl get nodes
Press Enter to continue with platform deployment.
EOF
      read -r _
    fi
    run_platform_phase
    ;;
  *)
    echo "Invalid DEPLOY_PHASE: ${DEPLOY_PHASE}. Expected one of: all, pulumi, platform." >&2
    exit 1
    ;;
esac

echo "Deployment complete."
