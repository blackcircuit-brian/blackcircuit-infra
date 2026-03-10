# WireGuard Operations for Private EKS Access

## 1. Purpose

This runbook defines how operators access a private-endpoint EKS control plane
from outside AWS using WireGuard.

The cluster API remains private-only:

- `bootstrap:clusterEndpointPrivateAccess=true`
- `bootstrap:clusterEndpointPublicAccess=false`

## 2. Do We Need Extra AWS Resources?

Yes. For private-only EKS access from laptops/workstations, you need a private
network entry point inside the VPC.

Minimum AWS additions:

- One WireGuard gateway host (EC2) in a public subnet
- Elastic IP on that host
- Security group allowing UDP/51820 only from trusted source CIDRs
- IAM role for SSM access (recommended for management access)

Optional hardening:

- One gateway per environment (dev/test/prod)
- One gateway per AZ for production resilience
- CloudWatch log shipping and alarm coverage

This repository can provision the minimum resources automatically through Pulumi
when WireGuard is enabled in stack config.

Pulumi config keys:

- `bootstrap:enableWireGuard`
- `bootstrap:wireGuardAllowedCidrs`
- `bootstrap:wireGuardInstanceType`
- `bootstrap:wireGuardAmiArch`
- `bootstrap:wireGuardAmiId` (optional override)
- `bootstrap:wireGuardSshKeyName` (optional; SSM access is preferred)
- `bootstrap:wireGuardAttachPrivateInterface` (default `true`)
- `bootstrap:wireGuardPrivateSubnetIndex` (default `0`)

## 3. Reference Topology

Client traffic path:

Client -> WireGuard tunnel -> EC2 WireGuard gateway -> VPC private EKS endpoint

Recommended WireGuard address plan (inside tunnel):

- `10.200.10.0/24` dev
- `10.200.20.0/24` test
- `10.200.30.0/24` prod

## 4. AWS Network Rules

Gateway security group:

- Inbound: UDP 51820 from known office/home CIDRs only
- Outbound: allow to VPC CIDR and internet egress for package updates

EKS API reachability:

- Keep EKS endpoint private-only
- Ensure gateway host can reach TCP/443 to the cluster private endpoint

## 5. Host-Side WireGuard Requirements

On the EC2 WireGuard gateway:

- Enable IP forwarding
- Install `wireguard-tools`
- Configure `wg0.conf`
- Apply NAT (MASQUERADE) from tunnel CIDR to egress interface(s)

NAT is recommended so VPC services see traffic as originating from the gateway
instance address.

If the gateway has multiple NICs (for example public + private), configure
masquerade on each egress interface used for routed traffic.

The gateway setup script also enforces Linux policy routing priority so
`lookup main` is set to a higher priority (`100` by default), which avoids
multi-NIC reply-path issues on EC2.

## 6. Client Profile Requirements

Each client profile should include:

- Unique keypair
- `AllowedIPs` including:
  - environment VPC CIDR (for private endpoint access)
  - WireGuard tunnel CIDR
- DNS resolver appropriate for internal domains (if needed)

## 7. Operational Lifecycle

Key rotation:

- Rotate peer keys on a fixed cadence (for example, every 90 days)
- Revoke compromised peers immediately
- Keep peer inventory with owner + device mapping

Access control:

- Separate peer lists by environment
- Do not share peer keys across users
- Remove stale peers during offboarding

## 8. Validation Checklist

From client:

1. Tunnel establishes (`wg show`)
2. EKS private endpoint resolves/reaches over tunnel
3. `kubectl get nodes` succeeds with expected cluster context

From gateway:

1. Handshakes observed for expected peers
2. Forwarded traffic visible to EKS endpoint
3. No broad inbound sources on UDP/51820

## 9. Failure Modes

Handshake failure:

- Check security group source CIDR and UDP/51820 path
- Check client/server key mismatch

Handshake works, API unreachable:

- Check `AllowedIPs` on client
- Check gateway IP forwarding and NAT rules
- Check route overlap with local LAN CIDRs

Intermittent behavior:

- Check MTU mismatch (adjust WG MTU)
- Check endpoint roaming and keepalive settings

## 10. Quick Procedure (Pi Client)

This is the simplest end-to-end flow to establish a tunnel from a Raspberry Pi.

1. Deploy or update the stack with temporary public API access and WireGuard enabled.

   Example stack config:

   - `bootstrap:clusterEndpointPublicAccess=true`
   - `bootstrap:clusterPublicAccessCidrs=["<your-public-ip>/32"]`
   - `bootstrap:enableWireGuard=true`
   - `bootstrap:wireGuardAllowedCidrs=["<your-public-ip>/32"]`

2. Get gateway details after `pulumi up`.

   - `wireGuardPublicIp` output from Pulumi
   - VPC CIDR for the environment (for `AllowedIPs`)

3. On the AWS gateway (SSM session), initialize WireGuard and get server public key.

   ```bash
   sudo WG_ACTION="init" \
        WG_SERVER_ADDRESS="10.200.10.1/24" \
        WG_MASQUERADE_IFACES="eth0,eth1" \
        ./scripts/wireguard/setup-gateway-wireguard.sh
   ```

   Save the `PublicKey` shown by the script as `<server-public-key>`.

4. On the Pi, generate keys and write client config.

   ```bash
   sudo WG_CLIENT_ADDRESS="10.200.10.2/24" \
        WG_SERVER_PUBLIC_KEY="<server-public-key>" \
        WG_SERVER_ENDPOINT="<wireGuardPublicIp>:51820" \
        WG_ALLOWED_IPS="<vpc-cidr>,10.200.10.0/24" \
        ./scripts/wireguard/setup-pi-wireguard.sh
   ```

   Save the `PublicKey` shown by the script as `<pi-public-key>`.

5. On the AWS gateway (SSM session), add the Pi peer.

   ```bash
   sudo WG_ACTION="add-peer" \
        WG_CLIENT_PUBLIC_KEY="<pi-public-key>" \
        WG_CLIENT_ADDRESS="10.200.10.2/32" \
        ./scripts/wireguard/setup-gateway-wireguard.sh
   ```

6. Validate tunnel.

   On Pi:

   - `sudo wg show wg0`
   - `kubectl get nodes`

   On gateway:

   - `sudo wg show wg0`

7. Configure CoreDNS to forward `int.blackcircuit.ca` to the Pi resolver over WireGuard.

   ```bash
   FORWARD_DNS="10.200.10.2" ./scripts/wireguard/configure-coredns-int-domain.sh
   ```

   Replace `10.200.10.2` with your Pi WireGuard IP for the environment.

8. Lock EKS API back to private-only once tunnel access is confirmed.

   ```bash
   pulumi config set bootstrap:clusterEndpointPublicAccess false
   pulumi config rm bootstrap:clusterPublicAccessCidrs
   pulumi up
   ```

Scripts referenced above:

- `scripts/wireguard/setup-pi-wireguard.sh`
- `scripts/wireguard/setup-gateway-wireguard.sh`
- `scripts/wireguard/configure-coredns-int-domain.sh`
- `scripts/wireguard/setup-bind-secondary-zone.sh`
- `scripts/wireguard/setup-gateway-bind-forwarder.sh`
- `scripts/wireguard/setup-gateway-bind-master-zone.sh`

## 11. DNS Host Secondary Setup (Optional)

If moving the authoritative `int.blackcircuit.ca` master to the WireGuard
gateway, run this on the existing DNS host to configure BIND as secondary:

```bash
sudo MASTER_IP="<gateway-master-ip>" \
     ./scripts/wireguard/setup-bind-secondary-zone.sh
```

If your master requires TSIG for transfers:

```bash
sudo MASTER_IP="<gateway-master-ip>" \
     MASTER_TSIG_NAME="rfc2136-tsig" \
     MASTER_TSIG_SECRET="<base64-secret>" \
     ./scripts/wireguard/setup-bind-secondary-zone.sh
```

## 12. Gateway BIND Forwarder Setup

If you want the WireGuard gateway to host BIND and forward `int.blackcircuit.ca`
to the current DNS server:

```bash
sudo FORWARD_DNS="10.200.10.2" \
     FORWARD_PORT="5335" \
     ./scripts/wireguard/setup-gateway-bind-forwarder.sh
```

Then point CoreDNS to the gateway resolver instead of the remote DNS host:

```bash
FORWARD_DNS="<gateway-private-ip>" ./scripts/wireguard/configure-coredns-int-domain.sh
```

## 13. Gateway BIND Master Setup (RFC2136 + TSIG)

If promoting the WireGuard gateway to authoritative master for
`int.blackcircuit.ca`, run:

```bash
sudo NS_A_RECORD_VALUE="<gateway-private-ip>" \
     ./scripts/wireguard/setup-gateway-bind-master-zone.sh
```

This script generates TSIG (unless provided) and prints a `kubectl` command
to update the `external-dns-internal/rfc2136-tsig` secret.

To reuse an existing TSIG key, pass either a key file or secret file:

```bash
sudo NS_A_RECORD_VALUE="<gateway-private-ip>" \
     TSIG_KEY_SOURCE_FILE="/etc/bind/keys/rfc2136-tsig.key" \
     ./scripts/wireguard/setup-gateway-bind-master-zone.sh
```
