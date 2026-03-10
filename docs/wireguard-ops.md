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
