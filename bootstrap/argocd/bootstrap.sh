#!/usr/bin/env bash
set -euo pipefail

# This script is now a wrapper for the ingress phase of the bootstrap.
# Most core bootstrap logic (secrets, Argo CD, cert-manager CRDs) 
# has been moved to bootstrap.py.

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

PHASE="${PHASE:-gitops}"
INGRESS_INSTALL_SCRIPT="${INGRESS_INSTALL_SCRIPT:-bootstrap/ingress/install.sh}"

if [[ "${PHASE}" == "ingress" || "${PHASE}" == "all" ]]; then
  echo ">>> Ingress phase starting"

  if [[ -f "${INGRESS_INSTALL_SCRIPT}" ]]; then
    echo ">>> Running ingress install: ${INGRESS_INSTALL_SCRIPT}"
    bash "${INGRESS_INSTALL_SCRIPT}"
  else
    echo ">>> No ingress install script found, skipping"
  fi

  echo ">>> Ingress phase complete"
fi

echo ">>> Done."

