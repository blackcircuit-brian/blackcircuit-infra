#!/usr/bin/env bash
set -euo pipefail

WAN_IFACE="${WAN_IFACE:-eth0}"
LAN_IFACE="${LAN_IFACE:-eth1}"
LAN_HTTP_NODEPORT="${LAN_HTTP_NODEPORT:-30080}"
LAN_HTTPS_NODEPORT="${LAN_HTTPS_NODEPORT:-30443}"
WAN_HTTP_NODEPORT="${WAN_HTTP_NODEPORT:-31080}"
WAN_HTTPS_NODEPORT="${WAN_HTTPS_NODEPORT:-31443}"

ensure_rule() {
  local table="$1"
  shift
  if ! iptables -t "$table" -C "$@" 2>/dev/null; then
    iptables -t "$table" -A "$@"
  fi
}

# LAN edge -> private ingress controller nodeports.
ensure_rule nat PREROUTING -i "$LAN_IFACE" -p tcp --dport 80 -j REDIRECT --to-ports "$LAN_HTTP_NODEPORT"
ensure_rule nat PREROUTING -i "$LAN_IFACE" -p tcp --dport 443 -j REDIRECT --to-ports "$LAN_HTTPS_NODEPORT"

# WAN edge -> public ingress controller nodeports.
ensure_rule nat PREROUTING -i "$WAN_IFACE" -p tcp --dport 80 -j REDIRECT --to-ports "$WAN_HTTP_NODEPORT"
ensure_rule nat PREROUTING -i "$WAN_IFACE" -p tcp --dport 443 -j REDIRECT --to-ports "$WAN_HTTPS_NODEPORT"

# Ensure redirected traffic is accepted.
ensure_rule filter INPUT -i "$LAN_IFACE" -p tcp -m multiport --dports "$LAN_HTTP_NODEPORT,$LAN_HTTPS_NODEPORT" -j ACCEPT
ensure_rule filter INPUT -i "$WAN_IFACE" -p tcp -m multiport --dports "$WAN_HTTP_NODEPORT,$WAN_HTTPS_NODEPORT" -j ACCEPT

echo "Configured iptables edge routing:"
echo "  LAN ${LAN_IFACE}:80,443 -> ${LAN_HTTP_NODEPORT},${LAN_HTTPS_NODEPORT}"
echo "  WAN ${WAN_IFACE}:80,443 -> ${WAN_HTTP_NODEPORT},${WAN_HTTPS_NODEPORT}"
