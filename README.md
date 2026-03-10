# Black Circuit GitOps Bootstrap (v0.5)

This repository provides a deterministic, opinionated bootstrap workflow
for establishing a GitOps control plane in Kubernetes using Argo CD.

The goal is not flexibility — it is reproducibility.

Version 0.4 introduced a fully automated DNS control plane with strict
authority separation between internal and public domains.

Version 0.5 promotes step-ca to the active internal ACME issuer for internal
ingress and GitOps-managed platform workloads.

------------------------------------------------------------------------

## Documentation

- AWS EKS provisioning with Pulumi:
  `scripts/pulumi/`
- WireGuard private-access operations:
  `docs/wireguard-ops.md`
- Architecture: `docs/architecture.md`
- Operations: `docs/operations.md`
- GitHub + Argo CD bootstrap setup: `docs/github-argocd-bootstrap.md`
- GitOps structure: `gitops/README.md`

------------------------------------------------------------------------

## Release Notes

Release notes follow this structure:

    docs/release-notes/<tag>.md

Current release:

    docs/release-notes/v0.5.md

------------------------------------------------------------------------

## What This Bootstrap Installs

Bootstrap establishes the minimum control-plane foundation required for
GitOps reconciliation.

Bootstrap includes:

- Argo CD installation
- ApplicationSet-based provider deployment
- App-of-apps root wiring
- MetalLB controller + CRDs (runtime configuration managed via GitOps)
- ingress-nginx (public + private)
- cert-manager
- Internal CA for `*.int.blackcircuit.ca`
- step-ca (internal PKI service, GitOps-managed)
- Dual external-dns providers:
  - RFC2136 (internal)
  - Cloudflare (public)
- TSIG secret generation for internal DNS
- Cloudflare token duplication
- SSH repo secret handling for private Git

Bootstrap installs prerequisites. GitOps owns everything else.

------------------------------------------------------------------------

## Design Principles

- Deterministic cluster rebuild
- Clear separation of bootstrap vs GitOps ownership
- Explicit DNS authority boundaries
- Minimal manual intervention
- Declarative reconciliation (`policy=sync`)
- Clean teardown capability

------------------------------------------------------------------------

## DNS Architecture (v0.5)

DNS is part of the control plane.

### Internal DNS

Zone:

    int.blackcircuit.ca

Authority:

- BIND9 authoritative master
- Port 5335
- Dynamic updates via RFC2136
- TSIG-authenticated
- Managed by `external-dns-internal`
- Policy: `sync`
- TXT ownership: `internal-1`
- IngressClass: `nginx-private`

Traffic flow:

Client → Pi-hole (53) → BIND (5335)

------------------------------------------------------------------------

### Public DNS

Zone:

    blackcircuit.ca

Authority:

- Cloudflare

Managed by `external-dns-public`:

- Policy: `sync`
- TXT ownership: `public-1`
- IngressClass: `nginx-public`
- Annotation-gated publishing

Private IP A-record publication is prevented by design.

------------------------------------------------------------------------

## Internal PKI (step-ca)

Version 0.5 runs step-ca as the active internal ACME-capable PKI service.

Characteristics:

- Deployed via provider ApplicationSet under `gitops/manifests/step-ca`
- ClusterIP service (443 → 9000)
- Dynamic PVC via `StorageClass/gp3` (EBS CSI)
- Secrets are not GitOps-managed

Required Kubernetes secret (created manually or via automation):

    step-ca-secrets

Required keys:

- password
- provisioner_password

Data path in the container:

    /home/step

Prerequisites for dynamic volume provisioning:

- EKS add-on `aws-ebs-csi-driver` installed (Pulumi-managed)
- `StorageClass/gp3` applied in cluster bootstrap manifests

### Current Certificate Model

Internal ingress uses:

    ClusterIssuer/step-ca-int-acme

ACME directory:

    https://step-ca.step-ca.svc.cluster.local/acme/acme/directory

------------------------------------------------------------------------

## Prerequisites

- `pulumi` CLI
- AWS credentials/profile configured for your target account
- Python 3.11+ with `pip`
- `sops` + `ksops` for encrypted secret rendering in Kustomize

------------------------------------------------------------------------

## Quick Start

Bootstrap procedure (explicit two-phase endpoint flow):

    cd scripts/pulumi
    pip install -r requirements.txt
    pulumi stack select dev
Phase 1 (bring-up with temporary public API access):

    pulumi config set bootstrap:clusterEndpointPublicAccess true
    pulumi config set --path 'bootstrap:clusterPublicAccessCidrs[0]' '<your-public-ip>/32'
    # Keep private endpoint as desired for your environment.
    pulumi config set bootstrap:clusterEndpointPrivateAccess <true|false>
    pulumi config set bootstrap:enableWireGuard true
    pulumi config set --path 'bootstrap:wireGuardAllowedCidrs[0]' '<your-public-ip>/32'
    # Bootstrap secrets created by Pulumi from local files (recommended)
    pulumi config set bootstrap:argoRepoSshPrivateKeyFile ~/.ssh/argocd-repo
    pulumi config set bootstrap:sopsAgeKeyFile ~/.config/sops/age/keys.txt
    pulumi up -y

After WireGuard tunnel is established and `kubectl` works through it, lock API to private-only:

    pulumi config set bootstrap:clusterEndpointPublicAccess false
    pulumi config rm bootstrap:clusterPublicAccessCidrs
    pulumi up -y

Notes:

- Public API CIDRs use `bootstrap:clusterPublicAccessCidrs`.
- This stack does not define `bootstrap:clusterEndpointPrivateCidrs`; private endpoint reachability is controlled by VPC routing, security groups, and WireGuard.
- Inline secret config values are still supported (`bootstrap:argoRepoSshPrivateKey`, `bootstrap:argoRepoKnownHosts`, `bootstrap:sopsAgeKey`).
- `bootstrap:argoRepoKnownHostsAutoScan` defaults to `true` and will run `ssh-keyscan` from `bootstrap:argoRepoUrl` when known-hosts input is not provided.
- When the secret config values above are set, `pulumi up` creates:
  - `argocd-<env>/repo-git-ssh`
  - `argocd-<env>/sops-age`

Fresh-cluster cert-manager CRD bootstrap (avoid first-run CRD race):

    kubectl apply -k platform/cert-manager/core
    kubectl wait --for=condition=Established crd/clusterissuers.cert-manager.io --timeout=300s

Then apply full environment bootstrap:

    kustomize build --enable-helm --enable-alpha-plugins --enable-exec clusters/single/dev | kubectl apply -f -

Phased deployment with `deploy-all.sh`:

1.  Infrastructure phase:

        DEPLOY_PHASE=pulumi ENVIRONMENT=dev PULUMI_STACK=dev ./scripts/deploy-all.sh

2.  Configure and validate WireGuard access:

        kubectl get nodes

3.  Platform manifests and ACME reconcile:

        DEPLOY_PHASE=platform ENVIRONMENT=dev ./scripts/deploy-all.sh

Single-command deployment with pause for WireGuard:

    ENVIRONMENT=dev PULUMI_STACK=dev ./scripts/deploy-all.sh

------------------------------------------------------------------------

## Secrets Managed by Bootstrap

These secrets are intentionally not GitOps-managed:

- `argocd-<env>/repo-git-ssh`
- `argocd-<env>/sops-age`
- `cert-manager/cloudflare-api-token`
- `external-dns-internal/rfc2136-tsig`

Keep bootstrap inputs and cloud credentials out of Git.

------------------------------------------------------------------------

## Accessing Argo CD

Without ingress:

    kubectl -n argocd port-forward svc/argocd-server 8080:443

With internal ingress:

    https://argocd.int.blackcircuit.ca

------------------------------------------------------------------------

## Clean Rebuild

Teardown:

    cd scripts/pulumi
    pulumi destroy -y

Optional single-line rebuild:

    pulumi destroy -y && pulumi up -y

A clean rebuild must succeed without manual intervention.

------------------------------------------------------------------------

## Out of Scope

- WireGuard host configuration and credential lifecycle
- Tunnel lifecycle automation
- Secret encryption (SOPS)
- DNS authority design (internal BIND + public Cloudflare)

------------------------------------------------------------------------

## Version

Current release:

    0.5
