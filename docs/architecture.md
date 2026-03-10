# Black Circuit Kubernetes Architecture

## v0.5

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

v0.5 maintains a strict separation between internal and public DNS
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

The platform uses step-ca as the active internal ACME issuer.

### 4.1 Active Internal Issuer

Internal ingress uses:

    ClusterIssuer/step-ca-int-acme

Issuer endpoint:

    https://step-ca.step-ca.svc.cluster.local/acme/acme/directory

Characteristics:

-   ACME account registration handled by cert-manager
-   Internal trust anchored by the step-ca root/intermediate bundle
-   HTTP-01 challenges solved via `nginx-private`

------------------------------------------------------------------------

### 4.2 Internal PKI Service (step-ca)

step-ca is GitOps-managed and authoritative for internal certificate issuance.

Characteristics:

-   Deployed via ApplicationSet (providers)
-   ACME-capable
-   ClusterIP service (443 → 9000)
-   Persistent data in `/home/step` on PVC-backed storage
-   Secrets not managed in Git
-   Fully reconciled by Argo CD

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

-   Admission policies for ingress and DNS boundary enforcement
-   Secret encryption via SOPS
-   Cloudflare Tunnel lifecycle automation
-   Policy enforcement for hostname boundaries

------------------------------------------------------------------------


## 8. Version Summary (v0.5)

This release introduces:

-   Dual-provider DNS automation
-   RFC2136 dynamic internal DNS with TSIG
-   Cloudflare public DNS with annotation-gated publishing
-   ApplicationSet-driven provider deployment
-   step-ca promoted to active ACME issuer for internal ingress
-   Internal ingress-nginx moved to internal AWS NLB `LoadBalancer`
-   Deterministic bootstrap improvements

------------------------------------------------------------------------

End of document.
