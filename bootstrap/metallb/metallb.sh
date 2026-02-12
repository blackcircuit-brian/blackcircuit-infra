#!/usr/bin/env bash
set -euo pipefail

# MetalLB bootstrap install (CRDs + controller)
# - Intentionally NOT GitOps-managed (CRD/API mutation)
# - Pinned version for reproducibility

: "${METALLB_NAMESPACE:=metallb-system}"
: "${METALLB_RELEASE:=metallb}"
: "${METALLB_CHART_REPO:=https://metallb.github.io/metallb}"
: "${METALLB_CHART:=metallb/metallb}"

# Pin this. Bump intentionally.
: "${METALLB_CHART_VERSION:=0.14.5}"

# Optional: kube-context override
: "${KUBE_CONTEXT:=}"

kubectl_ctx_args=()
if [[ -n "${KUBE_CONTEXT}" ]]; then
  kubectl_ctx_args+=(--context "${KUBE_CONTEXT}")
fi

echo "==> Verifying cluster access"
kubectl "${kubectl_ctx_args[@]}" cluster-info >/dev/null

echo "==> Ensuring namespace: ${METALLB_NAMESPACE}"
kubectl "${kubectl_ctx_args[@]}" get namespace "${METALLB_NAMESPACE}" >/dev/null 2>&1 \
  || kubectl "${kubectl_ctx_args[@]}" create namespace "${METALLB_NAMESPACE}"

echo "==> Ensuring Helm repo: metallb"
# Avoid failing if already added
helm repo add metallb "${METALLB_CHART_REPO}" >/dev/null 2>&1 || true
helm repo update >/dev/null

echo "==> Installing/upgrading MetalLB (${METALLB_CHART} @ ${METALLB_CHART_VERSION})"
helm upgrade --install "${METALLB_RELEASE}" "${METALLB_CHART}" \
  --namespace "${METALLB_NAMESPACE}" \
  --version "${METALLB_CHART_VERSION}" \
  --force-conflicts \
  --wait \
  --timeout 5m0s

echo "==> Waiting for MetalLB controller rollout"
kubectl "${kubectl_ctx_args[@]}" -n "${METALLB_NAMESPACE}" rollout status deployment/metallb-controller --timeout=5m

# MetalLB CRDs are cluster-scoped; controller readiness usually implies they exist,
# but we can be explicit and wait for them if present.
echo "==> Waiting for MetalLB CRDs to be Established (if present)"
crds=(
  "ipaddresspools.metallb.io"
  "bgppeers.metallb.io"
  "bgpadvertisements.metallb.io"
  "l2advertisements.metallb.io"
  "communities.metallb.io"
  "bfdprofiles.metallb.io"
)

for crd in "${crds[@]}"; do
  if kubectl "${kubectl_ctx_args[@]}" get crd "${crd}" >/dev/null 2>&1; then
    kubectl "${kubectl_ctx_args[@]}" wait --for=condition=Established "crd/${crd}" --timeout=2m
  fi
done

echo "✅ MetalLB installed: release=${METALLB_RELEASE} namespace=${METALLB_NAMESPACE} version=${METALLB_CHART_VERSION}"
echo "Next: manage IPAddressPool/L2Advertisement via GitOps-root."

