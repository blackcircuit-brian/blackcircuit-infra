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

overlays=(
  "clusters/single/dev"
  "clusters/single/test"
  "clusters/single/prod"
)

for overlay in "${overlays[@]}"; do
  echo "==> validating ${overlay}"
  "${KUSTOMIZE_BIN}" build \
    --enable-helm \
    --enable-alpha-plugins \
    --enable-exec \
    --helm-command "${HELM_BIN}" \
    "${overlay}" >/dev/null
  echo "ok: ${overlay}"
done

echo "all overlays validated"
