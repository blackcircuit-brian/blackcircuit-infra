# Black Circuit GitOps Bootstrap (v0.4.0)

This repository provides a deterministic, opinionated bootstrap workflow
for establishing a GitOps control plane in Kubernetes using Argo CD.

The goal is not flexibility --- it is reproducibility.

Version 0.4.0 introduces a fully automated DNS control plane with strict
authority separation between internal and public domains.

------------------------------------------------------------------------

## Documentation

-   Kubernetes bootstrap with kubeadm:
    `docs/kubernetes-kubeadm-bootstrap.md`
-   Architecture: `docs/architecture.md`
-   Operations: `docs/operations.md`
-   GitOps structure: `gitops/README.md`

------------------------------------------------------------------------

## Release Notes

Release notes follow this structure:

    docs/release-notes/<tag>.md

Current release:

    docs/release-notes/v0.4.0.md

------------------------------------------------------------------------

## What This Bootstrap Installs

Bootstrap establishes the minimum control-plane foundation required for
GitOps reconciliation.

Bootstrap includes:

-   Argo CD installation
-   ApplicationSet-based provider deployment
-   App-of-apps root wiring
-   MetalLB controller (pools managed via GitOps)
-   ingress-nginx (public + private)
-   cert-manager
-   Internal CA for `*.int.blackcircuit.ca`
-   Dual external-dns providers:
    -   RFC2136 (internal)
    -   Cloudflare (public)
-   TSIG secret generation for internal DNS
-   Cloudflare token duplication
-   SSH repo secret handling for private Git

Bootstrap installs prerequisites. GitOps owns everything else.

------------------------------------------------------------------------

## Design Principles

-   Deterministic cluster rebuild
-   Clear separation of bootstrap vs GitOps ownership
-   Explicit DNS authority boundaries
-   Minimal manual intervention
-   Declarative reconciliation (`policy=sync`)
-   Clean teardown capability

------------------------------------------------------------------------

## DNS Architecture (v0.4)

DNS is no longer optional. It is part of the control plane.

### Internal DNS

Zone:

    int.blackcircuit.ca

Authority:

-   BIND9 authoritative master
-   Port 5335
-   Dynamic updates via RFC2136
-   TSIG-authenticated
-   Managed by `external-dns-internal`
-   Policy: `sync`
-   TXT ownership: `internal-1`
-   IngressClass: `nginx-private`

Traffic flow:

Client → Pi-hole (53) → BIND (5335)

Authoritative testing:

    dig @pi.int.blackcircuit.ca -p 5335 host.int.blackcircuit.ca

------------------------------------------------------------------------

### Public DNS

Zone:

    blackcircuit.ca

Authority:

-   Cloudflare

Managed by `external-dns-public`:

-   Policy: `sync`
-   TXT ownership: `public-1`
-   IngressClass: `nginx-public`
-   Annotation-gated publishing

Public records require explicit opt-in:

    external-dns.alpha.kubernetes.io/target=<tunnel-host>

Example:

    2ce35617-07ec-48c7-a184-0c45e645417a.cfargotunnel.com

Publishing model:

Client → Cloudflare Edge → Tunnel → nginx-public

Private IP A-record publication is prevented by design.

------------------------------------------------------------------------

## Bootstrap Phases

Bootstrap is driven by `bootstrap.py`.

### Phase: `gitops` (default)

-   Installs Argo CD via Helm
-   Applies root app-of-apps
-   Installs MetalLB controller
-   Applies provider ApplicationSets
-   Hands reconciliation to Argo CD

### Phase: `ingress`

-   Applies ingress-related applications
-   Requires Argo CD to exist

### Phase: `all`

-   Runs `gitops`
-   Then runs `ingress`

------------------------------------------------------------------------

## Prerequisites

-   Running Kubernetes cluster
-   `kubectl`
-   `helm`
-   Cluster-admin privileges

Cluster provisioning is intentionally out of scope.

------------------------------------------------------------------------

## Quick Start

    ./bootstrap.py --env-file bootstrap/env/kubeadm.env

Optional flags:

    --ssh-key-file ~/.ssh/id_ed25519
    --cloudflare-token-file ~/.cloudflare-token
    --rfc2136-tsig-keyname external-dns-int
    --apply-rfc2136-tsig-secret
    --phase gitops|ingress|all
    --non-interactive

Bootstrap will:

-   Install Argo CD
-   Configure Git access
-   Generate TSIG secret for internal DNS
-   Install MetalLB
-   Deploy provider ApplicationSets
-   Allow Argo CD to reconcile the platform

------------------------------------------------------------------------

## Secrets Managed by Bootstrap

These secrets are intentionally not GitOps-managed:

-   `argocd/repo-git-ssh`
-   `cert-manager/cloudflare-api-token`
-   `external-dns-internal/rfc2136-tsig`

Bootstrap inputs (`bootstrap/inputs/`) must be gitignored.

------------------------------------------------------------------------

## Certificates

Internal domain:

    *.int.blackcircuit.ca

Internal ingress uses:

    ClusterIssuer/int-ca

Public ingress may use DNS01 via Cloudflare.

Future evolution:

-   step-ca will replace the internal bootstrap CA.

------------------------------------------------------------------------

## Accessing Argo CD

Without ingress:

    kubectl -n argocd port-forward svc/argocd-server 8080:443

With internal ingress:

    https://argocd.int.blackcircuit.ca

(Requires trusting the internal CA root.)

------------------------------------------------------------------------

## Clean Rebuild

Teardown:

    bootstrap/argocd/teardown.sh

A clean rebuild must succeed without manual intervention.

------------------------------------------------------------------------

## Out of Scope

-   Cluster provisioning automation
-   step-ca (planned)
-   Tunnel lifecycle automation
-   Secret encryption (SOPS)

------------------------------------------------------------------------

## Version

Current release:

    v0.4.0
