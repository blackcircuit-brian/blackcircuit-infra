# Black Circuit Kubernetes Platform

## Operations Guide -- v0.4.2

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



## 6. Certificate & PKI Operations

### 6.1 Current Issuer Model

Internal certificates:

-   Issuer: ClusterIssuer/int-ca
-   Used for *.int.blackcircuit.ca

The bootstrap-generated internal CA remains authoritative in v0.4.2.

If internal TLS fails:

1.  Check cert-manager pods
2.  Check Certificate resource status
3.  Confirm secret exists
4.  Verify CA root trust on client
5.  Confirm ingress class alignment

------------------------------------------------------------------------

### 6.2 Internal PKI Service (step-ca)

v0.4.2 deploys step-ca as a GitOps-managed internal PKI service.

Characteristics:

-   Namespace: `step-ca`
-   Deployment managed via ApplicationSet (providers)
-   ClusterIP service (443 → 8443)
-   Static PersistentVolume (Retain policy)
-   ACME endpoint enabled
-   Secrets are not GitOps-managed

Required Kubernetes secret:

    step-ca-secrets

Required keys:

-   password
-   provisioner_password

Data path (default static PV):

    /var/lib/step-ca

------------------------------------------------------------------------

### 6.3 Validating step-ca Health

Check application status:

    kubectl -n step-ca get pods
    kubectl -n step-ca get pvc
    kubectl -n step-ca get svc

Check logs:

    kubectl -n step-ca logs deploy/step-ca

Validate ACME directory (from inside cluster):

    curl -k https://step-ca.step-ca.svc.cluster.local/acme/k8s-int/directory

Expected behavior:
- JSON response from ACME directory
- HTTP error types such as `accountDoesNotExist` are valid responses

------------------------------------------------------------------------

### 6.4 Boundary Note

cert-manager does not yet use step-ca in v0.4.2.

Issuer migration to ACME is planned for a subsequent release.

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

-   cert-manager ACME issuer cutover to step-ca
-   Admission control for hostname enforcement
-   Secret encryption (SOPS)
-   Tunnel lifecycle automation
-   Policy validation for DNS publishing

------------------------------------------------------------------------

End of document.
