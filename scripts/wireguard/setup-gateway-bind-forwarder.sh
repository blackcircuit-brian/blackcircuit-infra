#!/usr/bin/env bash
set -euo pipefail

# Configure BIND on the WireGuard gateway as a forwarding resolver
# for an internal zone (for example int.blackcircuit.ca) toward an
# upstream DNS server (for example the current DNS host across WireGuard).

ZONE_DOMAIN="${ZONE_DOMAIN:-int.blackcircuit.ca}"
FORWARD_DNS="${FORWARD_DNS:-}"
FORWARD_PORT="${FORWARD_PORT:-5335}"
ALLOW_QUERY="${ALLOW_QUERY:-any}"
LISTEN_ON="${LISTEN_ON:-any}"
LISTEN_ON_V6="${LISTEN_ON_V6:-any}"
BIND_SERVICE="${BIND_SERVICE:-}"
BIND_OPTIONS_CONFIG="${BIND_OPTIONS_CONFIG:-}"
BIND_ZONE_CONFIG="${BIND_ZONE_CONFIG:-}"

usage() {
  cat <<'EOF'
Usage:
  sudo FORWARD_DNS="10.200.10.2" \
       ./scripts/wireguard/setup-gateway-bind-forwarder.sh

Optional:
  ZONE_DOMAIN="int.blackcircuit.ca"
  FORWARD_PORT="5335"
  ALLOW_QUERY="any"                 # e.g. "10.42.0.0/16; 10.200.10.0/24;"
  LISTEN_ON="any"                   # e.g. "127.0.0.1; 10.42.128.10;"
  LISTEN_ON_V6="any"
  BIND_SERVICE="bind9|named"        # auto-detected if omitted
  BIND_OPTIONS_CONFIG="/etc/bind/named.conf.options|/etc/named.conf"
  BIND_ZONE_CONFIG="/etc/bind/named.conf.blackcircuit-forwarder.conf|/etc/named/blackcircuit-forwarder.conf"
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run as root (use sudo)." >&2
    exit 1
  fi
}

detect_layout() {
  if [[ -f /etc/bind/named.conf.options ]]; then
    : "${BIND_OPTIONS_CONFIG:=/etc/bind/named.conf.options}"
    : "${BIND_ZONE_CONFIG:=/etc/bind/named.conf.blackcircuit-forwarder.conf}"
    : "${BIND_SERVICE:=bind9}"
    return
  fi

  if [[ -f /etc/named.conf ]]; then
    : "${BIND_OPTIONS_CONFIG:=/etc/named.conf}"
    : "${BIND_ZONE_CONFIG:=/etc/named/blackcircuit-forwarder.conf}"
    : "${BIND_SERVICE:=named}"
    return
  fi

  echo "Could not auto-detect BIND config layout." >&2
  echo "Install bind9/named first or set BIND_OPTIONS_CONFIG/BIND_ZONE_CONFIG explicitly." >&2
  exit 1
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

check_required() {
  if [[ -z "${FORWARD_DNS}" ]]; then
    echo "FORWARD_DNS is required." >&2
    usage
    exit 1
  fi
}

configure_options_listeners() {
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

configure_zone_snippet() {
  local zone_dir
  zone_dir="$(dirname "${BIND_ZONE_CONFIG}")"
  mkdir -p "${zone_dir}"

  cat > "${BIND_ZONE_CONFIG}" <<EOF
// Managed by setup-gateway-bind-forwarder.sh
zone "${ZONE_DOMAIN}" {
    type forward;
    forward only;
    forwarders { ${FORWARD_DNS} port ${FORWARD_PORT}; };
};
EOF
}

ensure_include() {
  local include_line
  include_line="include \"${BIND_ZONE_CONFIG}\";"

  if ! grep -Fq "${include_line}" "${BIND_OPTIONS_CONFIG}"; then
    printf "\n%s\n" "${include_line}" >> "${BIND_OPTIONS_CONFIG}"
  fi
}

ensure_allow_query() {
  local tmp
  tmp="$(mktemp)"

  awk -v allow_query="${ALLOW_QUERY}" '
    BEGIN { replaced = 0 }
    {
      if (!replaced && $0 ~ /^[[:space:]]*allow-query[[:space:]]*\{/) {
        print "    allow-query { " allow_query "; };"
        replaced = 1
        next
      }
      print
    }
    END {
      if (!replaced) {
        # No direct insertion point found; keep file unchanged.
      }
    }
  ' "${BIND_OPTIONS_CONFIG}" > "${tmp}"

  mv "${tmp}" "${BIND_OPTIONS_CONFIG}"
}

validate_and_reload() {
  if command -v named-checkconf >/dev/null 2>&1; then
    named-checkconf
  fi

  systemctl enable "${BIND_SERVICE}" >/dev/null 2>&1 || true
  systemctl restart "${BIND_SERVICE}"
}

print_summary() {
  echo "Configured gateway BIND forwarder."
  echo "  Zone:            ${ZONE_DOMAIN}"
  echo "  Forward target:  ${FORWARD_DNS}:${FORWARD_PORT}"
  echo "  Service:         ${BIND_SERVICE}"
  echo "  Options config:  ${BIND_OPTIONS_CONFIG}"
  echo "  Zone snippet:    ${BIND_ZONE_CONFIG}"
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
  install_bind
  detect_layout
  check_required

  configure_options_listeners
  ensure_allow_query

  configure_zone_snippet
  ensure_include
  validate_and_reload
  print_summary
}

main "$@"
