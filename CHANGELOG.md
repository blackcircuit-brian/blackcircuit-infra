# Changelog

All notable changes to this project will be documented in this file.

This project follows Semantic Versioning.

------------------------------------------------------------------------

## \[0.4.1\] - 2026-02-13

### Changed

-   MetalLB runtime configuration (`IPAddressPool`, `L2Advertisement`)
    moved under GitOps management.
-   Added `metallb-pools` application to the `providers` ApplicationSet.
-   Clarified ownership boundaries:
    -   Bootstrap installs MetalLB controller and CRDs.
    -   GitOps reconciles runtime configuration.

### Fixed

-   Corrected intermediate `ApplicationSet` structural issue affecting
    reconciliation.

------------------------------------------------------------------------

## \[0.4.0\] - 2026-02-12

### Added

-   Dual DNS control plane with strict authority separation:
    -   `external-dns-internal` (RFC2136 → BIND9)
    -   `external-dns-public` (Cloudflare)
-   ApplicationSet-based provider deployment model.
-   Deterministic GitOps root wiring under `gitops/appsets/`.
-   Phase-driven bootstrap workflow (`gitops`, `ingress`, `all`).
-   MetalLB controller installation for LoadBalancer support.
-   Dual ingress-nginx controllers (public / private separation).
-   cert-manager installation with internal bootstrap CA.
-   `ClusterIssuer/int-ca` for `*.int.blackcircuit.ca`.
-   TSIG secret generation for internal DNS updates.
-   SSH repository secret bootstrap support for Argo CD.

### Changed

-   DNS elevated to a first-class control-plane component.
-   Explicit separation between bootstrap-owned controllers and
    GitOps-managed resources.
-   Enforced ingress class and DNS provider alignment model.

------------------------------------------------------------------------

## \[0.3.0\] - 2026-02-12

### Added

-   Python-based bootstrap entrypoint (`bootstrap.py`)
-   Deterministic teardown script with namespace and finalizer handling
-   MetalLB controller installation integrated into bootstrap
-   Dual ingress-nginx controllers (public / private separation)
-   Internal self-signed CA (`int-ca`) for `*.int.blackcircuit.ca`
-   SSH repository secret bootstrap support (`repo-git-ssh`)
-   Cloudflare DNS01 token bootstrap support (public domains only)
-   Formal architecture, operations, and GitOps documentation

### Changed

-   kubeadm is now the reference cluster environment
-   Internal domains no longer use public ACME validation
-   Ingress is fully managed via GitOps (no standalone ingress YAML)
-   Bootstrap explicitly separates prerequisite installation from GitOps
    reconciliation

### Removed

-   k3d-first documentation references
-   Legacy ingress bootstrap hooks
-   Manual ingress manifest management outside GitOps

------------------------------------------------------------------------

## Future

Planned improvements:

-   Replace internal self-signed CA with step-ca
-   Optional external-dns integration
-   Secret management hardening (e.g., SOPS)
