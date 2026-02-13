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

- Kubernetes bootstrap with kubeadm:
  `docs/kubernetes-kubeadm-bootstrap.md`
- Architecture: `docs/architecture.md`
- Operations: `docs/operations.md`
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
- Static PV with `Retain` policy
- Secrets are not GitOps-managed

Required Kubernetes secret (created manually or via automation):

    step-ca-secrets

Required keys:

- password
- provisioner_password

Data path (default static PV):

    /var/lib/step-ca

### Current Certificate Model

Internal ingress still uses:

    ClusterIssuer/int-ca

The bootstrap self-signed CA remains authoritative in v0.4.2.

step-ca is deployed and reachable, but cert-manager issuer cutover is deferred
to a later point release.

------------------------------------------------------------------------

## Prerequisites

- Running Kubernetes cluster
- `kubectl`
- `helm`
- Cluster-admin privileges

Cluster provisioning is intentionally out of scope.

------------------------------------------------------------------------

## Quick Start

    ./bootstrap.py --env-file bootstrap/env/kubeadm.env

Optional flags:

    --ssh-key-file ~/.ssh/id_ed25519
    --cloudflare-token-file ~/.cloudflare-token
    --rfc2136-tsig-keyname external-dns-int
    --apply-rfc2136-tsig-secret
    --non-interactive

------------------------------------------------------------------------

## Secrets Managed by Bootstrap

These secrets are intentionally not GitOps-managed:

- `argocd/repo-git-ssh`
- `cert-manager/cloudflare-api-token`
- `external-dns-internal/rfc2136-tsig`
- `step-ca/step-ca-secrets`

Bootstrap inputs (`bootstrap/inputs/`) must be gitignored.

------------------------------------------------------------------------

## Accessing Argo CD

Without ingress:

    kubectl -n argocd port-forward svc/argocd-server 8080:443

With internal ingress:

    https://argocd.int.blackcircuit.ca

------------------------------------------------------------------------

## Clean Rebuild

Teardown:

    bootstrap/argocd/teardown.sh

A clean rebuild must succeed without manual intervention.

------------------------------------------------------------------------

## Out of Scope

- Cluster provisioning automation
- Tunnel lifecycle automation
- Secret encryption (SOPS)
- cert-manager ACME cutover to step-ca (planned)

------------------------------------------------------------------------

## Version

Current release:

    0.4.2

