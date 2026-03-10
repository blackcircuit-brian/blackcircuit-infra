#!/usr/bin/env bash
set -euo pipefail

# Configure CoreDNS to forward a private DNS zone to a resolver reachable over WireGuard.

DOMAIN="${DOMAIN:-int.blackcircuit.ca}"
FORWARD_DNS="${FORWARD_DNS:-}"
NAMESPACE="${NAMESPACE:-kube-system}"
CONFIGMAP_NAME="${CONFIGMAP_NAME:-coredns}"
TMP_COREFILE=""

cleanup() {
  if [[ -n "${TMP_COREFILE:-}" ]]; then
    rm -f "${TMP_COREFILE}"
  fi
}

usage() {
  cat <<'EOF'
Usage:
  FORWARD_DNS=10.200.10.2 ./scripts/wireguard/configure-coredns-int-domain.sh

Optional:
  DOMAIN=int.blackcircuit.ca
  NAMESPACE=kube-system
  CONFIGMAP_NAME=coredns
EOF
}

check_required() {
  if [[ -z "${FORWARD_DNS}" ]]; then
    echo "FORWARD_DNS is required (for example 10.200.10.2)." >&2
    usage
    exit 1
  fi
}

build_zone_block() {
  cat <<EOF
${DOMAIN}:53 {
    errors
    cache 30
    forward . ${FORWARD_DNS}
}
EOF
}

replace_or_append_block() {
  local current_corefile="$1"
  local zone_start_regex
  zone_start_regex="^${DOMAIN}:53[[:space:]]*\\{[[:space:]]*$"

  if grep -Eq "${zone_start_regex}" <<<"${current_corefile}"; then
    awk -v start_re="${zone_start_regex}" -v dns="${FORWARD_DNS}" -v domain="${DOMAIN}" '
      BEGIN {
        in_block = 0
      }
      $0 ~ start_re {
        in_block = 1
        print domain ":53 {"
        print "    errors"
        print "    cache 30"
        print "    forward . " dns
        print "}"
        next
      }
      in_block == 1 {
        if ($0 ~ /^[[:space:]]*}[[:space:]]*$/) {
          in_block = 0
        }
        next
      }
      {
        print
      }
    ' <<<"${current_corefile}"
  else
    printf "%s\n\n%s\n" "${current_corefile}" "$(build_zone_block)"
  fi
}

main() {
  trap cleanup EXIT

  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  check_required

  local current_corefile
  current_corefile="$(kubectl -n "${NAMESPACE}" get configmap "${CONFIGMAP_NAME}" -o jsonpath='{.data.Corefile}')"
  local updated_corefile
  updated_corefile="$(replace_or_append_block "${current_corefile}")"

  TMP_COREFILE="$(mktemp)"
  printf "%s\n" "${updated_corefile}" >"${TMP_COREFILE}"

  kubectl -n "${NAMESPACE}" create configmap "${CONFIGMAP_NAME}" \
    --from-file=Corefile="${TMP_COREFILE}" \
    --dry-run=client -o yaml \
    | kubectl apply -f -

  kubectl -n "${NAMESPACE}" rollout restart deployment coredns

  echo "CoreDNS updated for ${DOMAIN} -> ${FORWARD_DNS}"
}

main "$@"
