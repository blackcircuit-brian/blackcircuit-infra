#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-step-ca}"
POD="${POD:-$(kubectl -n "$NAMESPACE" get pod -l app.kubernetes.io/name=step-ca -o jsonpath='{.items[0].metadata.name}')}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="${BACKUP_DIR:-./backups/step-ca/${TIMESTAMP}}"

mkdir -p "$BACKUP_DIR"

kubectl -n "$NAMESPACE" cp "${POD}:/home/step/certs/root_ca.crt" "$BACKUP_DIR/root_ca.crt"
kubectl -n "$NAMESPACE" cp "${POD}:/home/step/secrets/root_ca_key" "$BACKUP_DIR/root_ca_key"
kubectl -n "$NAMESPACE" cp "${POD}:/home/step/config/ca.json" "$BACKUP_DIR/ca.json"

tar -C "$BACKUP_DIR" -czf "${BACKUP_DIR}.tar.gz" root_ca.crt root_ca_key ca.json

echo "Wrote step-ca root backup to ${BACKUP_DIR}.tar.gz"
echo "IMPORTANT: move this archive to offline/encrypted storage immediately."
