# GitOps Structure

This directory defines the declarative state of the platform.

Bootstrap installs Argo CD and applies the root application. From that point onward, Argo CD owns reconciliation of everything under `gitops/`.

---

## Directory Layout

```
gitops/
  clusters/
  apps/
  manifests/
```

---

## clusters/

Cluster-specific entrypoints.

Each environment (e.g., `kubeadm/`) contains a `root-app.yaml` that wires together all platform applications.

This is the only environment-specific configuration layer.

---

## apps/

Argo CD `Application` definitions.

These describe:

- Foundation components (cert-manager, ingress controllers, etc.)
- Providers (e.g., MetalLB pools)
- Platform components
- Workloads

Applications should remain environment-agnostic wherever possible.

---

## manifests/

Raw Kubernetes manifests that are not Helm charts.

Examples include:

- cert-manager issuers
- MetalLB IP pools
- Ingress definitions
- Platform-specific resources

These manifests are referenced by Applications.

---

## Design Principles

- Bootstrap installs controllers and seeds Argo CD.
- GitOps owns ongoing reconciliation.
- Avoid environment-specific logic in shared manifests.
- Do not commit secrets.
- Maintain separation between internal and public certificate flows.
- Controllers (CRDs + operators) may be bootstrap-installed.
- Runtime configuration (e.g., MetalLB IP pools, issuers, ingress rules) must be GitOps-managed.
