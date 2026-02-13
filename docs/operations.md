# Black Circuit Kubernetes Platform

## Operations Guide -- v0.4.0

------------------------------------------------------------------------

## 1. Purpose

This document defines the operational model for the Black Circuit GitOps
Kubernetes platform.

It serves as a runbook for:

-   Day-to-day operations
-   Incident response
-   DNS troubleshooting
-   GitOps lifecycle management
-   Controlled environment changes

This guide assumes v0.4.0 architecture.

------------------------------------------------------------------------

## 2. Control Plane Model

The platform operates under a strict GitOps reconciliation model.

Authoritative system of record: Git.

Reconciliation engine: Argo CD.

Manual changes to managed resources will be reverted.

------------------------------------------------------------------------

## 3. DNS Operations

### 3.1 Internal DNS (`int.blackcircuit.ca`)

Authority:

-   BIND9 (authoritative)
-   Port 5335
-   Updates via RFC2136 + TSIG
-   Managed by `external-dns-internal`
-   Policy: `sync`

Traffic path:

Client → Pi-hole (53) → BIND (5335)

#### Validate Internal Record

Authoritative check:

    dig @pi.int.blackcircuit.ca -p 5335 host.int.blackcircuit.ca

Resolver path check:

    dig @pi.int.blackcircuit.ca host.int.blackcircuit.ca

If record exists authoritatively but not via resolver:

    pihole restartdns

------------------------------------------------------------------------

### 3.2 Public DNS (`blackcircuit.ca`)

Authority:

-   Cloudflare
-   Managed by `external-dns-public`
-   Policy: `sync`
-   Annotation-gated publishing

Publishing requires ingress annotation:

    external-dns.alpha.kubernetes.io/target=<tunnel-host>

Validate public record:

    dig @1.1.1.1 host.blackcircuit.ca

------------------------------------------------------------------------

### 3.3 DNS Incident Checklist

Internal record missing:

1.  Check external-dns-internal logs
2.  Check BIND journal permissions
3.  Validate TSIG secret exists
4.  Query authoritative port 5335
5.  Flush Pi-hole cache

Public record missing:

1.  Confirm ingress annotation present
2.  Confirm ingress class is nginx-public
3.  Check external-dns-public logs
4.  Confirm record exists in Cloudflare
5.  Verify proxy status

------------------------------------------------------------------------

## 4. Argo CD Operations

### Check Application Health

    kubectl -n argocd get applications

### Force Sync

    kubectl -n argocd annotate application <app> argocd.argoproj.io/refresh=hard

### Restart Controller

    kubectl -n argocd rollout restart deployment argocd-application-controller

------------------------------------------------------------------------

## 5. Bootstrap Operations

Bootstrap is used only for:

-   Initial cluster bring-up
-   Rebuild scenarios
-   Secret generation

Standard invocation:

    ./bootstrap.py --env-file bootstrap/env/<env>.env

Bootstrap inputs must not be committed to Git.

------------------------------------------------------------------------

## 6. Certificate Operations

Internal certificates:

-   Issuer: ClusterIssuer/int-ca
-   Used for \*.int.blackcircuit.ca

If internal TLS fails:

1.  Check cert-manager pods
2.  Check certificate resource
3.  Confirm secret exists
4.  Verify CA root trust

Future evolution: step-ca replacement.

------------------------------------------------------------------------

## 7. Ingress Operations

Two ingress classes:

-   nginx-private (internal)
-   nginx-public (public)

Boundary rule:

-   Public ingress must not use `.int.blackcircuit.ca`
-   Internal ingress must not publish to Cloudflare

Validate ingress:

    kubectl get ingress -A

------------------------------------------------------------------------

## 8. Firewall & Network Assumptions

Internal DNS requires:

-   IPv4 + IPv6 access from cluster to BIND port 5335
-   Writable zone journal directory

Changes to firewall rules must preserve RFC2136 access.

------------------------------------------------------------------------

## 9. Clean Rebuild Procedure

1.  Run teardown script
2.  Verify namespace cleanup
3.  Re-run bootstrap
4.  Confirm Argo sync completes
5.  Validate DNS and ingress endpoints

A rebuild must require no manual DNS edits.

------------------------------------------------------------------------

## 10. Authority Boundaries

Internal DNS is independent of public DNS.

TXT ownership IDs must not be changed after deployment.

Manual DNS edits in managed zones will be reverted.

------------------------------------------------------------------------

## 11. Future Improvements

-   step-ca integration
-   Admission control for hostname enforcement
-   Secret encryption (SOPS)
-   Tunnel lifecycle automation
-   Policy validation for DNS publishing

------------------------------------------------------------------------

End of document.
