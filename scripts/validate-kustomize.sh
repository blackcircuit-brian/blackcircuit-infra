#!/usr/bin/env bash
set -euo pipefail

# Some environments (non-login shells, services) omit /snap/bin even when
# kustomize is installed via snap.
if [[ -d /snap/bin ]] && [[ ":${PATH}:" != *":/snap/bin:"* ]]; then
  export PATH="${PATH}:/snap/bin"
fi

KUSTOMIZE_BIN="${KUSTOMIZE_BIN:-kustomize}"
HELM_BIN="${HELM_BIN:-helm}"

if ! command -v "${KUSTOMIZE_BIN}" >/dev/null 2>&1; then
  echo "kustomize is required on PATH (current PATH: ${PATH})" >&2
  echo "hint: if installed via snap, ensure /snap/bin is included" >&2
  exit 1
fi

if ! command -v "${HELM_BIN}" >/dev/null 2>&1; then
  echo "helm is required on PATH for kustomize --enable-helm (current PATH: ${PATH})" >&2
  echo "hint: install helm or run with HELM_BIN=/path/to/helm" >&2
  exit 1
fi

if [[ "${KUSTOMIZE_BIN}" == /snap/bin/* ]] || [[ "${HELM_BIN}" == /snap/bin/* ]]; then
  echo "warning: snap-based kustomize/helm may fail with permission errors under --enable-helm" >&2
  echo "warning: prefer non-snap binaries (for example in ~/.local/bin)" >&2
fi

if ! command -v ksops >/dev/null 2>&1; then
  echo "ksops is required on PATH for SOPS-encrypted secrets" >&2
  exit 1
fi

KUSTOMIZE_PLUGIN_HOME="${KUSTOMIZE_PLUGIN_HOME:-${HOME}/.config/kustomize/plugin}"
KSOPS_PLUGIN_BIN="${KUSTOMIZE_PLUGIN_HOME}/viaduct.ai/v1/ksops/ksops"
if [[ ! -x "${KSOPS_PLUGIN_BIN}" ]]; then
  mkdir -p "$(dirname "${KSOPS_PLUGIN_BIN}")"
  cp "$(command -v ksops)" "${KSOPS_PLUGIN_BIN}"
  chmod 0755 "${KSOPS_PLUGIN_BIN}"
fi

overlays=(
  "clusters/single/dev"
  "clusters/single/test"
  "clusters/single/prod"
)

RETRY_COUNT="${RETRY_COUNT:-3}"
RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-5}"

validate_overlay() {
  local overlay="$1"
  local attempt=1
  local delay="${RETRY_DELAY_SECONDS}"

  while true; do
    if KUSTOMIZE_PLUGIN_HOME="${KUSTOMIZE_PLUGIN_HOME}" \
      "${KUSTOMIZE_BIN}" build \
        --enable-helm \
        --enable-alpha-plugins \
        --enable-exec \
        --helm-command "${HELM_BIN}" \
        "${overlay}" >/dev/null; then
      return 0
    fi

    if (( attempt >= RETRY_COUNT )); then
      return 1
    fi

    echo "retry ${attempt}/${RETRY_COUNT} failed for ${overlay}; retrying in ${delay}s..." >&2
    sleep "${delay}"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

for overlay in "${overlays[@]}"; do
  echo "==> validating ${overlay}"
  validate_overlay "${overlay}"
  echo "ok: ${overlay}"
done

echo "all overlays validated"
