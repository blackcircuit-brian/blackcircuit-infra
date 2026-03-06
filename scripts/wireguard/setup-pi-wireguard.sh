#!/usr/bin/env bash
set -euo pipefail

# Raspberry Pi WireGuard client bootstrap for private EKS access.
# This script writes /etc/wireguard/<iface>.conf and optionally brings it up.

WG_INTERFACE="${WG_INTERFACE:-wg0}"
WG_DIR="${WG_DIR:-/etc/wireguard}"
WG_CONF_PATH="${WG_CONF_PATH:-${WG_DIR}/${WG_INTERFACE}.conf}"

WG_PRIVATE_KEY_PATH="${WG_PRIVATE_KEY_PATH:-${WG_DIR}/${WG_INTERFACE}.key}"
WG_PUBLIC_KEY_PATH="${WG_PUBLIC_KEY_PATH:-${WG_DIR}/${WG_INTERFACE}.pub}"

WG_CLIENT_ADDRESS="${WG_CLIENT_ADDRESS:-}"
WG_DNS="${WG_DNS:-}"
WG_MTU="${WG_MTU:-1380}"

WG_SERVER_PUBLIC_KEY="${WG_SERVER_PUBLIC_KEY:-}"
WG_SERVER_ENDPOINT="${WG_SERVER_ENDPOINT:-}"
WG_ALLOWED_IPS="${WG_ALLOWED_IPS:-}"
WG_PERSISTENT_KEEPALIVE="${WG_PERSISTENT_KEEPALIVE:-25}"

WG_ENABLE_NOW="${WG_ENABLE_NOW:-true}"
WG_INSTALL_PACKAGES="${WG_INSTALL_PACKAGES:-true}"

usage() {
  cat <<'EOF'
Usage:
  sudo WG_CLIENT_ADDRESS="10.200.10.2/32" \
       WG_SERVER_PUBLIC_KEY="<server-public-key>" \
       WG_SERVER_ENDPOINT="<eip-or-dns>:51820" \
       WG_ALLOWED_IPS="10.42.0.0/16,10.200.10.0/24" \
       ./scripts/wireguard/setup-pi-wireguard.sh

Optional:
  WG_DNS="10.42.0.2"
  WG_INTERFACE="wg0"
  WG_ENABLE_NOW="true|false"
  WG_INSTALL_PACKAGES="true|false"

Notes:
  - Requires root.
  - Generates client keypair if not already present.
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run as root (use sudo)." >&2
    exit 1
  fi
}

check_required() {
  local missing=0
  for var_name in WG_CLIENT_ADDRESS WG_SERVER_PUBLIC_KEY WG_SERVER_ENDPOINT WG_ALLOWED_IPS; do
    if [[ -z "${!var_name}" ]]; then
      echo "Missing required env var: ${var_name}" >&2
      missing=1
    fi
  done
  if [[ "${missing}" -eq 1 ]]; then
    echo >&2
    usage
    exit 1
  fi
}

install_packages() {
  if [[ "${WG_INSTALL_PACKAGES}" != "true" ]]; then
    return
  fi

  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y wireguard wireguard-tools
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y wireguard-tools
  else
    echo "Unsupported package manager. Install wireguard-tools manually." >&2
    exit 1
  fi
}

ensure_keys() {
  mkdir -p "${WG_DIR}"
  chmod 700 "${WG_DIR}"

  if [[ ! -s "${WG_PRIVATE_KEY_PATH}" ]]; then
    umask 077
    wg genkey | tee "${WG_PRIVATE_KEY_PATH}" | wg pubkey >"${WG_PUBLIC_KEY_PATH}"
  elif [[ ! -s "${WG_PUBLIC_KEY_PATH}" ]]; then
    umask 077
    wg pubkey <"${WG_PRIVATE_KEY_PATH}" >"${WG_PUBLIC_KEY_PATH}"
  fi

  chmod 600 "${WG_PRIVATE_KEY_PATH}"
  chmod 644 "${WG_PUBLIC_KEY_PATH}"
}

write_config() {
  local private_key
  private_key="$(cat "${WG_PRIVATE_KEY_PATH}")"

  {
    echo "[Interface]"
    echo "PrivateKey = ${private_key}"
    echo "Address = ${WG_CLIENT_ADDRESS}"
    echo "MTU = ${WG_MTU}"
    if [[ -n "${WG_DNS}" ]]; then
      echo "DNS = ${WG_DNS}"
    fi
    echo
    echo "[Peer]"
    echo "PublicKey = ${WG_SERVER_PUBLIC_KEY}"
    echo "Endpoint = ${WG_SERVER_ENDPOINT}"
    echo "AllowedIPs = ${WG_ALLOWED_IPS}"
    echo "PersistentKeepalive = ${WG_PERSISTENT_KEEPALIVE}"
  } >"${WG_CONF_PATH}"

  chmod 600 "${WG_CONF_PATH}"
}

start_tunnel() {
  systemctl enable "wg-quick@${WG_INTERFACE}"
  if systemctl is-active --quiet "wg-quick@${WG_INTERFACE}"; then
    systemctl restart "wg-quick@${WG_INTERFACE}"
  else
    systemctl start "wg-quick@${WG_INTERFACE}"
  fi
}

print_summary() {
  echo "WireGuard client configured."
  echo "  Interface: ${WG_INTERFACE}"
  echo "  Config:    ${WG_CONF_PATH}"
  echo "  PublicKey: $(cat "${WG_PUBLIC_KEY_PATH}")"
  echo
  echo "Next:"
  echo "  1) Add this public key as a peer on the AWS WireGuard gateway."
  echo "  2) Verify with: wg show ${WG_INTERFACE}"
}

main() {
  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
  fi

  require_root
  check_required
  install_packages
  ensure_keys
  write_config

  if [[ "${WG_ENABLE_NOW}" == "true" ]]; then
    start_tunnel
  fi

  print_summary
}

main "$@"
