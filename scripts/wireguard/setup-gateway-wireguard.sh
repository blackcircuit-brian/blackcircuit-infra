#!/usr/bin/env bash
set -euo pipefail

# AWS WireGuard gateway bootstrap.
# Run on the EC2 gateway host (for example via SSM session).

WG_INTERFACE="${WG_INTERFACE:-wg0}"
WG_DIR="${WG_DIR:-/etc/wireguard}"
WG_CONF_PATH="${WG_CONF_PATH:-${WG_DIR}/${WG_INTERFACE}.conf}"

WG_PRIVATE_KEY_PATH="${WG_PRIVATE_KEY_PATH:-${WG_DIR}/${WG_INTERFACE}.key}"
WG_PUBLIC_KEY_PATH="${WG_PUBLIC_KEY_PATH:-${WG_DIR}/${WG_INTERFACE}.pub}"

WG_SERVER_ADDRESS="${WG_SERVER_ADDRESS:-}"
WG_LISTEN_PORT="${WG_LISTEN_PORT:-51820}"
WG_PUBLIC_IFACE="${WG_PUBLIC_IFACE:-}"
WG_MASQUERADE_IFACES="${WG_MASQUERADE_IFACES:-}"
WG_ACTION="${WG_ACTION:-init}"

WG_CLIENT_PUBLIC_KEY="${WG_CLIENT_PUBLIC_KEY:-}"
WG_CLIENT_ADDRESS="${WG_CLIENT_ADDRESS:-}"

WG_ENABLE_NOW="${WG_ENABLE_NOW:-true}"
WG_INSTALL_PACKAGES="${WG_INSTALL_PACKAGES:-true}"
WG_OVERWRITE="${WG_OVERWRITE:-false}"

usage() {
  cat <<'EOF'
Usage:
  # Initialize gateway (generate keypair, write wg0.conf, start wg0)
  sudo WG_ACTION="init" \
       WG_SERVER_ADDRESS="10.200.10.1/24" \
       ./scripts/wireguard/setup-gateway-wireguard.sh

  # Add a Pi peer to an existing gateway interface
  sudo WG_ACTION="add-peer" \
       WG_CLIENT_PUBLIC_KEY="<pi-public-key>" \
       WG_CLIENT_ADDRESS="10.200.10.2/32" \
       ./scripts/wireguard/setup-gateway-wireguard.sh

  # One-shot init + first peer
  sudo WG_SERVER_ADDRESS="10.200.10.1/24" \
       WG_CLIENT_PUBLIC_KEY="<pi-public-key>" \
       WG_CLIENT_ADDRESS="10.200.10.2/32" \
       ./scripts/wireguard/setup-gateway-wireguard.sh

Optional:
  WG_ACTION="init|add-peer"
  WG_LISTEN_PORT="51820"
  WG_PUBLIC_IFACE="eth0"   # auto-detected if omitted
  WG_MASQUERADE_IFACES="eth0,eth1"  # defaults to WG_PUBLIC_IFACE
  WG_OVERWRITE="false|true"

Notes:
  - Requires root.
  - Generates gateway keypair if missing.
  - Writes /etc/wireguard/wg0.conf with NAT + forwarding rules.
EOF
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run as root (use sudo)." >&2
    exit 1
  fi
}

check_required() {
  if [[ "${WG_ACTION}" != "init" && "${WG_ACTION}" != "add-peer" ]]; then
    echo "WG_ACTION must be 'init' or 'add-peer'." >&2
    exit 1
  fi

  local missing=0
  if [[ "${WG_ACTION}" == "init" ]]; then
    for var_name in WG_SERVER_ADDRESS; do
      if [[ -z "${!var_name}" ]]; then
        echo "Missing required env var: ${var_name}" >&2
        missing=1
      fi
    done
  fi
  if [[ "${WG_ACTION}" == "add-peer" ]]; then
    for var_name in WG_CLIENT_PUBLIC_KEY WG_CLIENT_ADDRESS; do
      if [[ -z "${!var_name}" ]]; then
        echo "Missing required env var: ${var_name}" >&2
        missing=1
      fi
    done
  fi
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

  if command -v dnf >/dev/null 2>&1; then
    dnf install -y wireguard-tools iptables
  elif command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y wireguard wireguard-tools iptables
  else
    echo "Unsupported package manager. Install wireguard-tools manually." >&2
    exit 1
  fi
}

detect_public_iface() {
  if [[ -n "${WG_PUBLIC_IFACE}" ]]; then
    return
  fi

  WG_PUBLIC_IFACE="$(ip route get 1.1.1.1 | awk '/dev/ {for (i=1;i<=NF;i++) if ($i=="dev") {print $(i+1); exit}}')"
  if [[ -z "${WG_PUBLIC_IFACE}" ]]; then
    echo "Unable to auto-detect public interface. Set WG_PUBLIC_IFACE explicitly." >&2
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
  if [[ -f "${WG_CONF_PATH}" && "${WG_OVERWRITE}" != "true" ]]; then
    echo "${WG_CONF_PATH} already exists. Set WG_OVERWRITE=true to replace it." >&2
    exit 1
  fi

  local private_key
  private_key="$(cat "${WG_PRIVATE_KEY_PATH}")"
  local masq_ifaces_raw
  masq_ifaces_raw="${WG_MASQUERADE_IFACES}"
  if [[ -z "${masq_ifaces_raw}" ]]; then
    masq_ifaces_raw="${WG_PUBLIC_IFACE}"
  fi

  local -a masq_ifaces=()
  local -A seen_ifaces=()
  local raw_iface iface_trimmed
  IFS=',' read -r -a raw_items <<<"${masq_ifaces_raw}"
  for raw_iface in "${raw_items[@]}"; do
    iface_trimmed="$(echo "${raw_iface}" | xargs)"
    if [[ -n "${iface_trimmed}" && -z "${seen_ifaces[${iface_trimmed}]:-}" ]]; then
      masq_ifaces+=("${iface_trimmed}")
      seen_ifaces["${iface_trimmed}"]=1
    fi
  done
  if [[ "${#masq_ifaces[@]}" -eq 0 ]]; then
    echo "No masquerade interfaces resolved. Set WG_MASQUERADE_IFACES or WG_PUBLIC_IFACE." >&2
    exit 1
  fi

  local post_up post_down out_iface
  post_up="iptables -A FORWARD -i ${WG_INTERFACE} -j ACCEPT; iptables -A FORWARD -o ${WG_INTERFACE} -j ACCEPT"
  post_down="iptables -D FORWARD -i ${WG_INTERFACE} -j ACCEPT; iptables -D FORWARD -o ${WG_INTERFACE} -j ACCEPT"
  for out_iface in "${masq_ifaces[@]}"; do
    post_up="${post_up}; iptables -t nat -A POSTROUTING -o ${out_iface} -j MASQUERADE"
    post_down="${post_down}; iptables -t nat -D POSTROUTING -o ${out_iface} -j MASQUERADE"
  done

  {
    echo "[Interface]"
    echo "PrivateKey = ${private_key}"
    echo "Address = ${WG_SERVER_ADDRESS}"
    echo "ListenPort = ${WG_LISTEN_PORT}"
    echo "PostUp = ${post_up}"
    echo "PostDown = ${post_down}"
    if [[ -n "${WG_CLIENT_PUBLIC_KEY}" && -n "${WG_CLIENT_ADDRESS}" ]]; then
      echo
      echo "[Peer]"
      echo "PublicKey = ${WG_CLIENT_PUBLIC_KEY}"
      echo "AllowedIPs = ${WG_CLIENT_ADDRESS}"
    fi
  } >"${WG_CONF_PATH}"

  chmod 600 "${WG_CONF_PATH}"
}

enable_forwarding() {
  cat >/etc/sysctl.d/99-wireguard-forwarding.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=1
EOF
  sysctl --system >/dev/null
}

start_tunnel() {
  systemctl enable "wg-quick@${WG_INTERFACE}"
  if systemctl is-active --quiet "wg-quick@${WG_INTERFACE}"; then
    systemctl restart "wg-quick@${WG_INTERFACE}"
  else
    systemctl start "wg-quick@${WG_INTERFACE}"
  fi
}

add_peer() {
  if [[ ! -f "${WG_CONF_PATH}" ]]; then
    echo "${WG_CONF_PATH} not found. Run WG_ACTION=init first." >&2
    exit 1
  fi

  wg set "${WG_INTERFACE}" peer "${WG_CLIENT_PUBLIC_KEY}" allowed-ips "${WG_CLIENT_ADDRESS}"

  if ! grep -q "PublicKey = ${WG_CLIENT_PUBLIC_KEY}" "${WG_CONF_PATH}"; then
    {
      echo
      echo "[Peer]"
      echo "PublicKey = ${WG_CLIENT_PUBLIC_KEY}"
      echo "AllowedIPs = ${WG_CLIENT_ADDRESS}"
    } >>"${WG_CONF_PATH}"
  fi
}

print_summary() {
  echo "WireGuard gateway configured."
  echo "  Interface: ${WG_INTERFACE}"
  echo "  Config:    ${WG_CONF_PATH}"
  echo "  PublicKey: $(cat "${WG_PUBLIC_KEY_PATH}")"
  echo
  echo "Next:"
  echo "  1) Use this public key in the Pi client config."
  echo "  2) Verify handshakes with: wg show ${WG_INTERFACE}"
}

main() {
  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
  fi

  require_root
  check_required
  install_packages
  detect_public_iface
  ensure_keys

  if [[ "${WG_ACTION}" == "init" ]]; then
    write_config
    enable_forwarding

    if [[ "${WG_ENABLE_NOW}" == "true" ]]; then
      start_tunnel
    fi
  else
    add_peer
  fi

  print_summary
}

main "$@"
