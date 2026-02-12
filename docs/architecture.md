# Architecture Overview

This document describes the architectural model of the Black Circuit GitOps bootstrap platform (v0.3).

The guiding principle is deterministic infrastructure: the cluster must be rebuildable from scratch with minimal manual intervention and no hidden state.

---

## 1. Control Plane Model

The system is built around an **Argo CD app-of-apps pattern**.

Bootstrap installs Argo CD and seeds a root application. From that point onward, Argo CD owns reconciliation of the platform.

### Responsibility Boundary

Bootstrap owns:

- Installing Argo CD (via Helm)
- Installing prerequisite CRDs/controllers (e.g., MetalLB controller)
- Creating non-GitOps secrets (repo SSH key, optional DNS token)
- Applying the root app-of-apps

GitOps owns:

- All platform components (cert-manager, ingress, MetalLB pools, etc.)
- All application workloads
- Ongoing configuration drift correction

Bootstrap should be idempotent and safe to re-run.

---

## 2. Networking Model

### MetalLB

MetalLB is installed during bootstrap (controller + CRDs only).

Address pools are GitOps-managed via:

    gitops/manifests/metallb-pools/

Two pools are defined:

- public
- private

These map directly to ingress controllers.

---

### Ingress Controllers

Two independent ingress-nginx controllers exist:

| Controller      | Namespace               | IngressClass   | Purpose                |
|----------------|------------------------|----------------|------------------------|
| nginx-public   | ingress-nginx          | nginx-public   | Public-facing ingress  |
| nginx-private  | ingress-nginx-private  | nginx-private  | Internal-only ingress  |

Each controller uses a distinct:

- --controller-class
- --ingress-class
- MetalLB IP pool

This separation prevents class collisions and cross-wiring.

---

## 3. Certificate Strategy

The platform distinguishes between internal and public domains.

### Internal Domain

    *.int.blackcircuit.ca

This zone is internal-only and not publicly delegated.

Public ACME (e.g., Let’s Encrypt) cannot validate it.

Therefore internal ingress uses:

    ClusterIssuer/int-ca

This issuer is backed by an internally generated root CA:

- self-signed bootstrap issuer
- root CA stored in cert-manager namespace
- long-lived root certificate
- short-lived leaf certificates

Clients must trust the internal root CA manually.

---

### Public Domains (Optional)

If public DNS is delegated (e.g., via Cloudflare), the platform may use:

    ClusterIssuer/letsencrypt-*-dns01

DNS01 validation requires:

- Publicly resolvable domain
- Cloudflare API token secret
- Proper zone delegation

Public and internal certificate flows are intentionally separate.

---

## 4. Secret Lifecycle

Certain secrets are intentionally not GitOps-managed.

### Repo Access

- Secret: argocd/repo-git-ssh
- Created during bootstrap if SSH key provided
- Required for private repository access

### DNS Token (Public Only)

- Secret: cert-manager/cloudflare-api-token
- Created during bootstrap if DNS01 is enabled

Secrets are considered environment inputs, not declarative configuration.

---

## 5. Determinism & Rebuild Guarantees

A valid platform state satisfies:

- Bootstrap completes without manual patching
- Argo CD applications converge to Healthy/Synced
- Internal ingress serves TLS via internal CA
- Public ingress (if configured) obtains ACME certificates
- Teardown fully removes state and allows clean re-bootstrap

Finalizers and CRD ordering are explicitly handled during teardown.

---

## 6. Future Evolution

Planned improvements include:

- Replacing internal self-signed CA with step-ca
- Optional external-dns integration
- Formalized secret management (e.g., SOPS)

These changes should preserve the existing bootstrap contract.

---

## 7. Architectural Principles

- Prefer explicitness over magic
- Separate bootstrap concerns from reconciliation concerns
- Avoid hidden controller coupling
- Ensure internal domains do not depend on public infrastructure
- Minimize mutable state outside Git

This platform favors clarity and reproducibility over flexibility.
