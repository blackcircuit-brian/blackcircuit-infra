# Black Circuit GitOps Bootstrap (v0.4.2)

This repository provides a deterministic, opinionated bootstrap workflow
for establishing a GitOps control plane in Kubernetes using Argo CD.

The goal is not flexibility — it is reproducibility.

Version 0.4 introduced a fully automated DNS control plane with strict
authority separation between internal and public domains.

Version 0.4.2 introduces an internal PKI service (step-ca) deployed under GitOps.
Issuer migration to ACME will follow in a subsequent point release.

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

    docs/release-notes/0.4.2.md

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

## DNS Architecture (v0.4)

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

Version 0.4.2 deploys step-ca as an internal ACME-capable PKI service.

Characteristics:

- Deployed via provider ApplicationSet under `gitops/manifests/step-ca`
- ClusterIP service (443 → 8443)
- Dynamic PVC via `StorageClass/gp3` (EBS CSI)
- Secrets are not GitOps-managed

Required Kubernetes secret (created manually or via automation):

    step-ca-secrets

Required keys:

- password
- provisioner_password

Data path in the container:

    /var/lib/step-ca

Prerequisites for dynamic volume provisioning:

- EKS add-on `aws-ebs-csi-driver` installed (Pulumi-managed)
- `StorageClass/gp3` applied in cluster bootstrap manifests

### Current Certificate Model

Internal ingress still uses:

    ClusterIssuer/int-ca

The bootstrap self-signed CA remains authoritative in v0.4.2.

step-ca is deployed and reachable, but cert-manager issuer cutover is deferred
to a later point release.

------------------------------------------------------------------------

## Prerequisites

- `pulumi` CLI
- AWS credentials/profile configured for your target account
- Python 3.11+ with `pip`

------------------------------------------------------------------------

## Quick Start

Initial bootstrap (avoid private-endpoint chicken-and-egg):

    cd scripts/pulumi
    pip install -r requirements.txt
    pulumi stack select dev
    pulumi config set bootstrap:clusterEndpointPublicAccess true
    pulumi config set --path 'bootstrap:clusterPublicAccessCidrs[0]' '<your-public-ip>/32'
    pulumi config set bootstrap:enableWireGuard true
    pulumi config set --path 'bootstrap:wireGuardAllowedCidrs[0]' '<your-public-ip>/32'
    pulumi up

After WireGuard tunnel is established and `kubectl` works through it:

    pulumi config set bootstrap:clusterEndpointPublicAccess false
    pulumi config rm bootstrap:clusterPublicAccessCidrs
    pulumi up

Fresh-cluster cert-manager CRD bootstrap (avoid first-run CRD race):

    kubectl apply -k platform/cert-manager/core
    kubectl wait --for=condition=Established crd/clusterissuers.cert-manager.io --timeout=300s

Then apply full environment bootstrap:

    kustomize build --enable-helm clusters/single/dev | kubectl apply -f -

------------------------------------------------------------------------

## Secrets Managed by Bootstrap

These secrets are intentionally not GitOps-managed:

- `argocd/repo-git-ssh`
- `cert-manager/cloudflare-api-token`
- `external-dns-internal/rfc2136-tsig`
- `step-ca/step-ca-secrets`

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
    pulumi destroy

A clean rebuild must succeed without manual intervention.

------------------------------------------------------------------------

## Out of Scope

- WireGuard host configuration and credential lifecycle
- Tunnel lifecycle automation
- Secret encryption (SOPS)
- cert-manager ACME cutover to step-ca (planned)

------------------------------------------------------------------------

## Version

Current release:

    0.4.2
