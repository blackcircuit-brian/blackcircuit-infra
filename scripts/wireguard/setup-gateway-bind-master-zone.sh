#!/usr/bin/env bash
set -euo pipefail

# Configure BIND on the WireGuard gateway as authoritative master for an internal zone,
# including TSIG generation for RFC2136 updates.

ZONE_DOMAIN="${ZONE_DOMAIN:-int.blackcircuit.ca}"
ZONE_FILE="${ZONE_FILE:-}"
ZONE_SERIAL="${ZONE_SERIAL:-$(date +%Y%m%d%H)}"
NS_HOST="${NS_HOST:-ns1.int.blackcircuit.ca.}"
NS_A_RECORD_NAME="${NS_A_RECORD_NAME:-ns1.int.blackcircuit.ca.}"
NS_A_RECORD_VALUE="${NS_A_RECORD_VALUE:-}"
LISTEN_ON="${LISTEN_ON:-any}"
LISTEN_ON_V6="${LISTEN_ON_V6:-any}"

TSIG_KEY_NAME="${TSIG_KEY_NAME:-rfc2136-tsig}"
TSIG_ALGORITHM="${TSIG_ALGORITHM:-hmac-sha256}"
TSIG_SECRET="${TSIG_SECRET:-}"
TSIG_SECRET_FILE="${TSIG_SECRET_FILE:-}"
TSIG_KEY_SOURCE_FILE="${TSIG_KEY_SOURCE_FILE:-}"
TSIG_KEY_FILE="${TSIG_KEY_FILE:-}"

ALLOW_QUERY="${ALLOW_QUERY:-any}"
ALLOW_TRANSFER="${ALLOW_TRANSFER:-none}" # e.g. "10.200.10.2;"

BIND_SERVICE="${BIND_SERVICE:-}"
BIND_MAIN_CONFIG="${BIND_MAIN_CONFIG:-}"
BIND_OPTIONS_CONFIG="${BIND_OPTIONS_CONFIG:-}"
MASTER_SNIPPET="${MASTER_SNIPPET:-}"

usage() {
  cat <<'EOF'
Usage:
  sudo NS_A_RECORD_VALUE="<gateway-private-ip>" \
       ./scripts/wireguard/setup-gateway-bind-master-zone.sh

Optional:
  ZONE_DOMAIN="int.blackcircuit.ca"
  NS_HOST="ns1.int.blackcircuit.ca."
  NS_A_RECORD_NAME="ns1.int.blackcircuit.ca."
  NS_A_RECORD_VALUE="<gateway-private-ip>"
  ALLOW_QUERY="any"                       # e.g. "10.42.0.0/16; 10.200.10.0/24;"
  ALLOW_TRANSFER="none"                   # e.g. "10.200.10.2;"
  LISTEN_ON="any"                         # e.g. "127.0.0.1; 10.42.128.10;"
  LISTEN_ON_V6="any"
  TSIG_KEY_NAME="rfc2136-tsig"
  TSIG_ALGORITHM="hmac-sha256"
  TSIG_SECRET="<base64-secret>"           # if omitted, generated with tsig-keygen
  TSIG_SECRET_FILE="/path/to/secret.txt"  # optional, one-line secret value
  TSIG_KEY_SOURCE_FILE="/path/to/keyfile" # optional, parse key+algo+secret from existing key file
  BIND_SERVICE="bind9|named"              # auto-detected if omitted

After run:
  - Uses generated/provided TSIG key in update-policy for RFC2136.
  - Prints kubectl command to update external-dns TSIG secret.
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run as root (use sudo)." >&2
    exit 1
  fi
}

install_bind() {
  if command -v named >/dev/null 2>&1; then
    return
  fi
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y bind9 dnsutils
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y bind bind-utils
  else
    echo "Unsupported package manager. Install BIND manually." >&2
    exit 1
  fi
}

detect_layout() {
  if [[ -f /etc/bind/named.conf.local ]]; then
    : "${BIND_MAIN_CONFIG:=/etc/bind/named.conf.local}"
    : "${BIND_OPTIONS_CONFIG:=/etc/bind/named.conf.options}"
    : "${MASTER_SNIPPET:=/etc/bind/named.conf.blackcircuit-master.conf}"
    : "${TSIG_KEY_FILE:=/etc/bind/keys/${TSIG_KEY_NAME}.key}"
    : "${ZONE_FILE:=/var/cache/bind/db.${ZONE_DOMAIN}}"
    : "${BIND_SERVICE:=bind9}"
    return
  fi
  if [[ -f /etc/named.conf ]]; then
    : "${BIND_MAIN_CONFIG:=/etc/named.conf}"
    : "${BIND_OPTIONS_CONFIG:=/etc/named.conf}"
    : "${MASTER_SNIPPET:=/etc/named/blackcircuit-master.conf}"
    : "${TSIG_KEY_FILE:=/etc/named/keys/${TSIG_KEY_NAME}.key}"
    : "${ZONE_FILE:=/var/named/${ZONE_DOMAIN}.zone}"
    : "${BIND_SERVICE:=named}"
    return
  fi
  echo "Could not auto-detect BIND config layout." >&2
  exit 1
}

ensure_tsig_secret() {
  if [[ -n "${TSIG_KEY_SOURCE_FILE}" ]]; then
    if [[ ! -f "${TSIG_KEY_SOURCE_FILE}" ]]; then
      echo "TSIG_KEY_SOURCE_FILE does not exist: ${TSIG_KEY_SOURCE_FILE}" >&2
      exit 1
    fi

    local parsed_name parsed_algo parsed_secret
    parsed_name="$(awk -F'"' '/^key / {print $2; exit}' "${TSIG_KEY_SOURCE_FILE}")"
    parsed_algo="$(awk '/algorithm/ {gsub(";",""); print tolower($2); exit}' "${TSIG_KEY_SOURCE_FILE}")"
    parsed_secret="$(awk -F'"' '/secret/ {print $2; exit}' "${TSIG_KEY_SOURCE_FILE}")"

    if [[ -z "${parsed_secret}" ]]; then
      echo "Failed to parse TSIG secret from TSIG_KEY_SOURCE_FILE." >&2
      exit 1
    fi

    if [[ -z "${TSIG_KEY_NAME}" || "${TSIG_KEY_NAME}" == "rfc2136-tsig" ]]; then
      TSIG_KEY_NAME="${parsed_name:-${TSIG_KEY_NAME}}"
    fi
    if [[ -z "${TSIG_ALGORITHM}" || "${TSIG_ALGORITHM}" == "hmac-sha256" ]]; then
      TSIG_ALGORITHM="${parsed_algo:-${TSIG_ALGORITHM}}"
    fi
    TSIG_SECRET="${parsed_secret}"
    return
  fi

  if [[ -n "${TSIG_SECRET_FILE}" ]]; then
    if [[ ! -f "${TSIG_SECRET_FILE}" ]]; then
      echo "TSIG_SECRET_FILE does not exist: ${TSIG_SECRET_FILE}" >&2
      exit 1
    fi
    TSIG_SECRET="$(tr -d '\r\n' < "${TSIG_SECRET_FILE}")"
    if [[ -z "${TSIG_SECRET}" ]]; then
      echo "TSIG_SECRET_FILE is empty: ${TSIG_SECRET_FILE}" >&2
      exit 1
    fi
    return
  fi

  if [[ -n "${TSIG_SECRET}" ]]; then
    return
  fi
  if ! command -v tsig-keygen >/dev/null 2>&1; then
    echo "tsig-keygen not found. Install BIND utilities or set TSIG_SECRET explicitly." >&2
    exit 1
  fi
  TSIG_SECRET="$(tsig-keygen -a "${TSIG_ALGORITHM^^}" "${TSIG_KEY_NAME}" | awk -F'"' '/secret/ {print $2; exit}')"
  if [[ -z "${TSIG_SECRET}" ]]; then
    echo "Failed to generate TSIG secret." >&2
    exit 1
  fi
}

write_tsig_key_file() {
  local key_dir
  key_dir="$(dirname "${TSIG_KEY_FILE}")"
  mkdir -p "${key_dir}"
  cat > "${TSIG_KEY_FILE}" <<EOF
key "${TSIG_KEY_NAME}" {
    algorithm ${TSIG_ALGORITHM};
    secret "${TSIG_SECRET}";
};
EOF
  chmod 600 "${TSIG_KEY_FILE}"
}

write_zone_file() {
  local zone_dir
  zone_dir="$(dirname "${ZONE_FILE}")"
  mkdir -p "${zone_dir}"

  if [[ ! -f "${ZONE_FILE}" ]]; then
    cat > "${ZONE_FILE}" <<EOF
\$TTL 300
@   IN  SOA ${NS_HOST} hostmaster.${ZONE_DOMAIN}. (
        ${ZONE_SERIAL} ; serial
        300            ; refresh
        120            ; retry
        604800         ; expire
        300            ; minimum
)
    IN  NS  ${NS_HOST}
EOF
    if [[ -n "${NS_A_RECORD_VALUE}" ]]; then
      cat >> "${ZONE_FILE}" <<EOF
${NS_A_RECORD_NAME} IN A ${NS_A_RECORD_VALUE}
EOF
    fi
  fi
}

write_master_snippet() {
  cat > "${MASTER_SNIPPET}" <<EOF
// Managed by setup-gateway-bind-master-zone.sh
include "${TSIG_KEY_FILE}";

zone "${ZONE_DOMAIN}" {
    type master;
    file "${ZONE_FILE}";
    allow-query { ${ALLOW_QUERY}; };
    allow-transfer { ${ALLOW_TRANSFER}; };
    update-policy { grant "${TSIG_KEY_NAME}" zonesub ANY; };
};
EOF
}

ensure_include() {
  local include_line
  include_line="include \"${MASTER_SNIPPET}\";"
  if ! grep -Fq "${include_line}" "${BIND_MAIN_CONFIG}"; then
    printf "\n%s\n" "${include_line}" >> "${BIND_MAIN_CONFIG}"
  fi
}

configure_listeners() {
  local tmp
  tmp="$(mktemp)"

  awk -v listen_on="${LISTEN_ON}" -v listen_on_v6="${LISTEN_ON_V6}" '
    BEGIN { in_options=0; inserted4=0; inserted6=0 }
    /^[[:space:]]*options[[:space:]]*\{/ { in_options=1; print; next }
    in_options && $0 ~ /^[[:space:]]*listen-on[[:space:]]+port[[:space:]]+53[[:space:]]*\{/ {
      print "    listen-on port 53 { " listen_on "; };"
      inserted4=1
      next
    }
    in_options && $0 ~ /^[[:space:]]*listen-on-v6[[:space:]]*\{/ {
      print "    listen-on-v6 { " listen_on_v6 "; };"
      inserted6=1
      next
    }
    in_options && $0 ~ /^[[:space:]]*\}[[:space:]]*;[[:space:]]*$/ {
      if (!inserted4) print "    listen-on port 53 { " listen_on "; };"
      if (!inserted6) print "    listen-on-v6 { " listen_on_v6 "; };"
      in_options=0
      print
      next
    }
    { print }
  ' "${BIND_OPTIONS_CONFIG}" > "${tmp}"

  mv "${tmp}" "${BIND_OPTIONS_CONFIG}"
}

validate_and_reload() {
  if command -v named-checkconf >/dev/null 2>&1; then
    named-checkconf
  fi
  if command -v named-checkzone >/dev/null 2>&1; then
    named-checkzone "${ZONE_DOMAIN}" "${ZONE_FILE}"
  fi

  systemctl enable "${BIND_SERVICE}" >/dev/null 2>&1 || true
  systemctl restart "${BIND_SERVICE}"
}

print_summary() {
  echo "Configured gateway BIND master zone."
  echo "  Zone:            ${ZONE_DOMAIN}"
  echo "  Zone file:       ${ZONE_FILE}"
  echo "  Main config:     ${BIND_MAIN_CONFIG}"
  echo "  Snippet:         ${MASTER_SNIPPET}"
  echo "  TSIG key name:   ${TSIG_KEY_NAME}"
  echo "  TSIG algorithm:  ${TSIG_ALGORITHM}"
  echo
  echo "Use this to update external-dns TSIG secret:"
  echo "  kubectl -n external-dns-internal create secret generic rfc2136-tsig \\"
  echo "    --from-literal=rfc2136_tsig_keyname='${TSIG_KEY_NAME}' \\"
  echo "    --from-literal=rfc2136_tsig_secret='${TSIG_SECRET}' \\"
  echo "    --from-literal=rfc2136_tsig_algorithm='${TSIG_ALGORITHM}' \\"
  echo "    --dry-run=client -o yaml | kubectl apply -f -"
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
  fi
  require_root
  install_bind
  detect_layout
  configure_listeners
  ensure_tsig_secret
  write_tsig_key_file
  write_zone_file
  write_master_snippet
  ensure_include
  validate_and_reload
  print_summary
}

main "$@"
