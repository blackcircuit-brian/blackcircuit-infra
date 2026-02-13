# Black Circuit Kubernetes Architecture

## v0.4.0

------------------------------------------------------------------------

## 1. Overview

This document describes the reference Kubernetes architecture used for
Black Circuit platform deployments.

Version 0.4.0 introduces a fully automated dual-provider DNS control
plane, TSIG-authenticated RFC2136 updates for internal DNS,
Cloudflare-managed public DNS with tunnel-based publishing, and
ApplicationSet-driven provider deployment.

The system is designed around:

-   Deterministic bootstrap
-   GitOps reconciliation (Argo CD)
-   Strict authority boundaries
-   Operational clarity
-   Explicit ownership of infrastructure state

------------------------------------------------------------------------

## 2. Core Control Plane Model

Argo CD is the reconciliation engine for all in-cluster workloads.

### Deployment Model

-   Root application: `gitops-root`
-   Providers deployed via ApplicationSet
-   Branch-aware targetRevision control
-   No manual per-application Git reference changes

This ensures reproducible cluster rebuilds and controlled environment
promotion.

------------------------------------------------------------------------

## 3. DNS Control Plane

v0.4 introduces a strict separation between internal and public DNS
authority.

Two independent external-dns instances manage records declaratively.

------------------------------------------------------------------------

### 3.1 Internal DNS

Zone:

    int.blackcircuit.ca

Authority:

-   Authoritative server: BIND9
-   Port: 5335
-   Dynamic updates via RFC2136
-   TSIG authentication (hmac-sha256)

Managed by:

-   Deployment: `external-dns-internal`
-   Policy: `sync`
-   Registry: `txt`
-   Owner ID: `internal-1`
-   IngressClass: `nginx-private`

Traffic flow:

Client → Pi-hole (53) → BIND authoritative (5335)

Operational notes:

-   BIND journal files must be writable

-   Firewall must allow cluster IPv4 and IPv6 to port 5335

-   Negative caching behavior must be understood when troubleshooting

-   Authoritative queries can be tested directly:

    dig @pi.int.blackcircuit.ca -p 5335 host.int.blackcircuit.ca

Internal DNS does not depend on public infrastructure.

------------------------------------------------------------------------

### 3.2 Public DNS

Zone:

    blackcircuit.ca

Authority:

-   Cloudflare

Managed by:

-   Deployment: `external-dns-public`
-   Policy: `sync`
-   Registry: `txt`
-   Owner ID: `public-1`
-   IngressClass: `nginx-public`
-   Annotation-gated publishing

Publishing model:

Public records require explicit opt-in via ingress annotation:

    external-dns.alpha.kubernetes.io/target=<tunnel-host>

Example:

    2ce35617-07ec-48c7-a184-0c45e645417a.cfargotunnel.com

This ensures:

-   No accidental publication of private IPs
-   CNAME publishing to Cloudflare Tunnel
-   Deterministic ownership
-   No implicit A-record creation from private load balancer IPs

Traffic flow:

Client → Cloudflare Edge → Tunnel → nginx-public

Public and internal DNS systems are intentionally isolated.

------------------------------------------------------------------------

## 4. Certificate Strategy

Internal ingress uses:

    ClusterIssuer/int-ca

This issuer is backed by an internal CA generated during bootstrap.

Characteristics:

-   Long-lived root
-   Stored in cert-manager namespace
-   Used exclusively for internal domains

Planned evolution:

-   Replace internal CA with step-ca in a future release

------------------------------------------------------------------------

## 5. Secret Lifecycle

Secrets are environment inputs and not Git-managed.

### RFC2136 TSIG Secret

-   Namespace: `external-dns-internal`
-   Generated during bootstrap
-   Required for dynamic zone updates

### Cloudflare API Token

-   Created in `cert-manager`
-   Duplicated into `external-dns-public`
-   Used exclusively for Cloudflare provider access

------------------------------------------------------------------------

## 6. Authority Boundaries

The system enforces the following constraints:

-   Internal zones never depend on public DNS
-   Public ingress must not use `.int.blackcircuit.ca`
-   Public publishing requires explicit annotation
-   DNS automation operates in `sync` mode with TXT ownership isolation

This prevents cross-boundary mutation and accidental data exposure.

------------------------------------------------------------------------

## 7. Future Evolution

Planned enhancements:

-   step-ca integration
-   Admission policies for ingress and DNS boundary enforcement
-   Secret encryption via SOPS
-   Cloudflare Tunnel lifecycle automation
-   Policy enforcement for hostname boundaries

------------------------------------------------------------------------

## 8. Version Summary (v0.4.0)

This release introduces:

-   Dual-provider DNS automation
-   RFC2136 dynamic internal DNS with TSIG
-   Cloudflare public DNS with annotation-gated publishing
-   ApplicationSet-driven provider deployment
-   Full create/update/delete lifecycle via sync policy
-   Deterministic bootstrap improvements

------------------------------------------------------------------------

End of document.
