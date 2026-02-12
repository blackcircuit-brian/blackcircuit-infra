#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   YES=true ./bootstrap/argocd/teardown.sh
# Optional:
#   ARGO_NAMESPACE=argocd ROOT_APP_NAME=root REMOVE_CERT_MANAGER_CRDS=false YES=true ./bootstrap/argocd/teardown.sh

YES="${YES:-false}"
ARGO_NAMESPACE="${ARGO_NAMESPACE:-argocd}"
ROOT_APP_NAME="${ROOT_APP_NAME:-root}"
REMOVE_CERT_MANAGER_CRDS="${REMOVE_CERT_MANAGER_CRDS:-false}"

log() { printf "\n==> %s\n" "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }

need kubectl
need jq

if [[ "$YES" != "true" ]]; then
  echo "Refusing to run without YES=true" >&2
  exit 1
fi

log "Context"
kubectl config current-context
kubectl cluster-info >/dev/null || true

log "Delete root app (app-of-apps) if present"
kubectl -n "${ARGO_NAMESPACE}" delete application "${ROOT_APP_NAME}" --ignore-not-found=true || true

log "Delete remaining Argo Applications and AppProjects (best-effort)"
kubectl -n "${ARGO_NAMESPACE}" delete applications.argoproj.io --all --ignore-not-found=true || true
kubectl -n "${ARGO_NAMESPACE}" delete appprojects.argoproj.io --all --ignore-not-found=true || true

log "Clear finalizers on Applications (both namespaced and cluster-wide listing)"
# Namespaced
for a in $(kubectl -n "${ARGO_NAMESPACE}" get applications.argoproj.io -o name 2>/dev/null || true); do
  kubectl -n "${ARGO_NAMESPACE}" patch "$a" --type=merge -p '{"metadata":{"finalizers":[]}}' >/dev/null || true
done
# Cluster-wide (if any exist outside argocd ns)
for a in $(kubectl get applications.argoproj.io -A -o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{"\n"}{end}' 2>/dev/null || true); do
  ns="$(awk '{print $1}' <<<"$a")"
  name="$(awk '{print $2}' <<<"$a")"
  kubectl -n "$ns" patch "application/$name" --type=merge -p '{"metadata":{"finalizers":[]}}' >/dev/null || true
done

log "Clear finalizers on AppProjects"
for p in $(kubectl -n "${ARGO_NAMESPACE}" get appprojects.argoproj.io -o name 2>/dev/null || true); do
  kubectl -n "${ARGO_NAMESPACE}" patch "$p" --type=merge -p '{"metadata":{"finalizers":[]}}' >/dev/null || true
done

log "Delete cert-manager namespace (controller + webhooks live there)"
kubectl delete ns cert-manager --ignore-not-found=true || true

if [[ "${REMOVE_CERT_MANAGER_CRDS}" == "true" ]]; then
  log "Removing cert-manager CRDs (destructive)"
  kubectl get crd -o name | grep -E '\.cert-manager\.io$' | xargs -r kubectl delete || true
fi

log "Delete ArgoCD namespace"
kubectl delete ns "${ARGO_NAMESPACE}" --ignore-not-found=true || true

force_finalize_namespace() {
  local ns="$1"
  if ! kubectl get ns "$ns" >/dev/null 2>&1; then return 0; fi

  local phase
  phase="$(kubectl get ns "$ns" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  if [[ "$phase" != "Terminating" ]]; then return 0; fi

  log "Namespace $ns is Terminating: removing namespace finalizers via /finalize"
  kubectl get ns "$ns" -o json \
    | jq 'del(.spec.finalizers)' \
    | kubectl replace --raw "/api/v1/namespaces/${ns}/finalize" -f - >/dev/null || true
}

log "Force-finalize stuck namespaces (if any)"
force_finalize_namespace "${ARGO_NAMESPACE}"
force_finalize_namespace "cert-manager"

log "Done"

