# Pi Edge Routing, TLS, and DDNS

This document captures the Pi deployment model used by this repository.

## 1. Edge Routing (No MetalLB)

Traffic is steered by interface and port on the Pi host:

- LAN (`pi.int.blackcircuit.ca`) to private ingress controller nodeports
- WAN (`pi.blackcircuit.ca`) to public ingress controller nodeports

Ingress nodeports used by platform manifests:

- Private: `30080` (HTTP), `30443` (HTTPS)
- Public: `31080` (HTTP), `31443` (HTTPS)

Apply host iptables rules:

```bash
sudo WAN_IFACE=eth0 LAN_IFACE=eth1 ./scripts/configure-pi-edge-routing.sh
```

Notes:

- iptables does interface/port steering.
- Hostname-level routing is handled by ingress rules and ingress class (`nginx-private` vs `nginx-public`).

## 2. TLS via step-ca + cert-manager

`cert-manager` and `step-ca` are included in cluster overlays.

- `step-ca` runs in namespace `step-ca` with PVC `step-ca-data`.
- `cert-manager` creates `ClusterIssuer/step-ca-int-acme` against:
  `https://step-ca.step-ca.svc.cluster.local/acme/k8s-int/directory`

Required secret before deploying step-ca:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: step-ca-secrets
  namespace: step-ca
type: Opaque
stringData:
  password: <step-ca-password>
  provisioner_password: <step-ca-provisioner-password>
```

## 3. Root Certificate Backup Strategy

Export and archive root materials from the step-ca pod:

```bash
./scripts/backup-step-ca-root.sh
```

This produces `./backups/step-ca/<timestamp>.tar.gz`.

Store backups offline and encrypted.

## 4. Cloudflare DDNS

Prod overlay includes `platform/cloudflare-ddns/base` CronJob in namespace `cert-manager`.

Create/update secret values in:

- `platform/cloudflare-ddns/base/secrets.enc.yaml`

Edit with SOPS:

```bash
sops platform/cloudflare-ddns/base/secrets.enc.yaml
```

Required values:

- `api_token`
- `zone_id`
- `record_name` (example: `pi.blackcircuit.ca`)

The secret name is `cloudflare-api-token` so the same token can be reused for cert-manager DNS01 and DDNS.
The CronJob runs every 10 minutes and updates/creates the A record when the public IP changes.
