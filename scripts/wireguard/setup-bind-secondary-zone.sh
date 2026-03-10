#!/usr/bin/env bash
set -euo pipefail

# Configure a BIND host as secondary (slave) for an internal zone.
# Intended for the current DNS host when promoting the WireGuard gateway to master.

ZONE_DOMAIN="${ZONE_DOMAIN:-int.blackcircuit.ca}"
MASTER_IP="${MASTER_IP:-}"
MASTER_PORT="${MASTER_PORT:-53}"
ALLOW_QUERY="${ALLOW_QUERY:-any}"

MASTER_TSIG_NAME="${MASTER_TSIG_NAME:-}"
MASTER_TSIG_SECRET="${MASTER_TSIG_SECRET:-}"
MASTER_TSIG_ALGORITHM="${MASTER_TSIG_ALGORITHM:-hmac-sha256}"

BIND_SERVICE="${BIND_SERVICE:-}"
BIND_MAIN_CONFIG="${BIND_MAIN_CONFIG:-}"
SECONDARY_SNIPPET="${SECONDARY_SNIPPET:-}"
ZONE_FILE="${ZONE_FILE:-}"

usage() {
  cat <<'EOF'
Usage:
  sudo MASTER_IP="10.42.128.10" \
       ./scripts/wireguard/setup-bind-secondary-zone.sh

Optional:
  ZONE_DOMAIN="int.blackcircuit.ca"
  MASTER_PORT="53"
  ALLOW_QUERY="any"                 # e.g. "10.42.0.0/16; 10.200.10.0/24;"
  BIND_SERVICE="bind9|named"        # auto-detected if omitted
  BIND_MAIN_CONFIG="/etc/bind/named.conf.local|/etc/named.conf"
  SECONDARY_SNIPPET="/etc/bind/named.conf.blackcircuit-secondary.conf|/etc/named/blackcircuit-secondary.conf"
  ZONE_FILE="/var/cache/bind/db.int.blackcircuit.ca.slave|/var/named/slaves/db.int.blackcircuit.ca.slave"

Optional TSIG for zone transfers:
  MASTER_TSIG_NAME="rfc2136-tsig"
  MASTER_TSIG_SECRET="<base64-secret>"
  MASTER_TSIG_ALGORITHM="hmac-sha256"
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run as root (use sudo)." >&2
    exit 1
  fi
}

detect_layout() {
  if [[ -z "${BIND_MAIN_CONFIG}" ]]; then
    if [[ -f /etc/bind/named.conf.local ]]; then
      BIND_MAIN_CONFIG="/etc/bind/named.conf.local"
    elif [[ -f /etc/named.conf ]]; then
      BIND_MAIN_CONFIG="/etc/named.conf"
    else
      echo "Could not auto-detect BIND config file. Set BIND_MAIN_CONFIG explicitly." >&2
      exit 1
    fi
  fi

  local conf_dir
  conf_dir="$(dirname "${BIND_MAIN_CONFIG}")"

  if [[ -z "${SECONDARY_SNIPPET}" ]]; then
    if [[ "${BIND_MAIN_CONFIG}" == /etc/bind/* ]]; then
      SECONDARY_SNIPPET="/etc/bind/named.conf.blackcircuit-secondary.conf"
    else
      SECONDARY_SNIPPET="${conf_dir}/blackcircuit-secondary.conf"
    fi
  fi

  if [[ -z "${ZONE_FILE}" ]]; then
    if [[ "${BIND_MAIN_CONFIG}" == /etc/bind/* ]]; then
      ZONE_FILE="/var/cache/bind/db.${ZONE_DOMAIN}.slave"
    else
      ZONE_FILE="/var/named/slaves/db.${ZONE_DOMAIN}.slave"
    fi
  fi

  if [[ -z "${BIND_SERVICE}" ]]; then
    if systemctl list-unit-files | grep -q '^bind9\.service'; then
      BIND_SERVICE="bind9"
    else
      BIND_SERVICE="named"
    fi
  fi
}

check_required() {
  if [[ -z "${MASTER_IP}" ]]; then
    echo "MASTER_IP is required." >&2
    usage
    exit 1
  fi

  if [[ -n "${MASTER_TSIG_NAME}" || -n "${MASTER_TSIG_SECRET}" ]]; then
    if [[ -z "${MASTER_TSIG_NAME}" || -z "${MASTER_TSIG_SECRET}" ]]; then
      echo "Both MASTER_TSIG_NAME and MASTER_TSIG_SECRET are required when TSIG is enabled." >&2
      exit 1
    fi
  fi
}

write_snippet() {
  local zone_dir
  zone_dir="$(dirname "${ZONE_FILE}")"
  mkdir -p "${zone_dir}"

  {
    echo "// Managed by setup-bind-secondary-zone.sh"
    if [[ -n "${MASTER_TSIG_NAME}" ]]; then
      echo "key \"${MASTER_TSIG_NAME}\" {"
      echo "    algorithm ${MASTER_TSIG_ALGORITHM};"
      echo "    secret \"${MASTER_TSIG_SECRET}\";"
      echo "};"
      echo
    fi
    echo "zone \"${ZONE_DOMAIN}\" {"
    echo "    type slave;"
    if [[ -n "${MASTER_TSIG_NAME}" ]]; then
      echo "    masters port ${MASTER_PORT} { ${MASTER_IP} key \"${MASTER_TSIG_NAME}\"; };"
    else
      echo "    masters port ${MASTER_PORT} { ${MASTER_IP}; };"
    fi
    echo "    file \"${ZONE_FILE}\";"
    echo "    allow-query { ${ALLOW_QUERY}; };"
    echo "};"
  } > "${SECONDARY_SNIPPET}"
}

ensure_include() {
  local include_line
  include_line="include \"${SECONDARY_SNIPPET}\";"
  if ! grep -Fq "${include_line}" "${BIND_MAIN_CONFIG}"; then
    printf "\n%s\n" "${include_line}" >> "${BIND_MAIN_CONFIG}"
  fi
}

validate_and_reload() {
  if command -v named-checkconf >/dev/null 2>&1; then
    named-checkconf
  fi

  if systemctl is-enabled --quiet "${BIND_SERVICE}" 2>/dev/null || systemctl is-active --quiet "${BIND_SERVICE}" 2>/dev/null; then
    systemctl reload "${BIND_SERVICE}" || systemctl restart "${BIND_SERVICE}"
  else
    systemctl restart "${BIND_SERVICE}"
  fi
}

print_summary() {
  echo "Configured BIND secondary zone."
  echo "  Zone:            ${ZONE_DOMAIN}"
  echo "  Master:          ${MASTER_IP}:${MASTER_PORT}"
  echo "  Main config:     ${BIND_MAIN_CONFIG}"
  echo "  Snippet:         ${SECONDARY_SNIPPET}"
  echo "  Zone file path:  ${ZONE_FILE}"
  echo
  echo "Validation commands:"
  echo "  dig @127.0.0.1 ${ZONE_DOMAIN} SOA"
  echo "  dig @127.0.0.1 host.${ZONE_DOMAIN}"
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi

  require_root
  detect_layout
  check_required
  write_snippet
  ensure_include
  validate_and_reload
  print_summary
}

main "$@"
