#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-step-ca}"
DEPLOYMENT="${DEPLOYMENT:-step-ca}"
ROOT_CERT_PATH="${ROOT_CERT_PATH:-/home/step/certs/root_ca.crt}"

TARGET_FILES=(
  "platform/cert-manager/base/clusterissuer-step-ca-internal.yaml"
  "platform/cert-manager/issuers/clusterissuer-step-ca-internal.yaml"
)

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is required on PATH" >&2
  exit 1
fi

echo "Reading step-ca root certificate from ${NAMESPACE}/${DEPLOYMENT}:${ROOT_CERT_PATH}..."
CA_BUNDLE="$(kubectl -n "${NAMESPACE}" exec "deploy/${DEPLOYMENT}" -- cat "${ROOT_CERT_PATH}" | base64 -w0)"

if [[ -z "${CA_BUNDLE}" ]]; then
  echo "Failed to read a non-empty root certificate bundle" >&2
  exit 1
fi

for file in "${TARGET_FILES[@]}"; do
  if [[ ! -f "${file}" ]]; then
    echo "Missing target file: ${file}" >&2
    exit 1
  fi

  tmp="$(mktemp)"
  awk -v ca="${CA_BUNDLE}" '
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
  ' "${file}" > "${tmp}" || {
    code=$?
    rm -f "${tmp}"
    if [[ "${code}" -eq 2 ]]; then
      echo "Could not locate insertion point in ${file}" >&2
    fi
    exit "${code}"
  }
  mv "${tmp}" "${file}"
  echo "Updated ${file}"
done

echo "Done. Review diff, commit, and let Argo sync."
