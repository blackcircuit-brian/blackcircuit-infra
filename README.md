# Black Circuit GitOps Bootstrap (v0)

This repository provides a minimal, opinionated bootstrap flow for bringing up **Argo CD** in a Kubernetes cluster and establishing a **GitOps app-of-apps** control plane.

The focus is on:
- clarity over flexibility,
- predictable behavior,
- and an onboarding flow suitable for local k3d-based development clusters.

---

## What this does (v0)

At a high level, the bootstrap process is split into **phases**:

### 1. GitOps control plane (`PHASE=gitops`, default)
- Installs Argo CD into the cluster using Helm
- Applies a root *app-of-apps* Application
- Hands off ongoing reconciliation to Argo CD

This phase **does not** require ingress, TLS, or DNS.

### 2. Ingress setup (`PHASE=ingress`)
- Reserved for installing an ingress provider (and later MetalLB)
- Applies an Argo CD Ingress manifest if present

This phase assumes Argo CD already exists.

### 3. Combined (`PHASE=all`)
- Runs the GitOps phase first
- Then runs the ingress phase

---

## Prerequisites

- A running Kubernetes cluster (k3d recommended)
- `kubectl`
- `helm`

> Cluster creation (k3d), MetalLB, and ingress providers are intentionally **out of scope** for the initial GitOps phase and will be layered in later.

---

## Repository layout (relevant parts)

```
.
├── bootstrap/
│   ├── argocd/
│   │   ├── bootstrap.sh        # main bootstrap script
│   │   ├── values.yaml         # base Argo CD values
│   │   ├── values.<org>.yaml   # optional org overrides
│   │   ├── values.<env>.yaml   # optional env overrides
│   │   └── ingress.yaml        # (optional) Argo CD ingress
│   └── ingress/
│       └── install.sh          # (optional) ingress provider install
└── gitops/
    └── clusters/
        └── <env>/
            └── root-app.yaml   # app-of-apps entrypoint
```

---

## Quick start (GitOps only)

By default, the script runs in `gitops` phase.

```bash
cd bootstrap/argocd
./bootstrap.sh
```

This will:
- ensure the Argo CD namespace exists,
- install Argo CD via Helm,
- apply the root Application defined at:

```
gitops/clusters/<ENV>/root-app.yaml
```

Defaults:
- `ENV=test-k3d`
- `ORG_SLUG=aethericforge`

You can override these via environment variables:

```bash
ENV=dev ORG_SLUG=myorg ./bootstrap.sh
```

---

## Accessing Argo CD (no ingress)

Until ingress is enabled, access Argo CD via port-forward:

```bash
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

Then open:

```
https://localhost:8080
```

Retrieve the initial admin password:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret   -o jsonpath="{.data.password}" | base64 -d
```

---

## Phase control

The bootstrap behavior is controlled by the `PHASE` environment variable:

| Phase     | Behavior |
|-----------|----------|
| `gitops`  | Install Argo CD and apply root app (default) |
| `ingress` | Only run ingress-related hooks |
| `all`     | Run gitops phase, then ingress phase |

Examples:

```bash
# GitOps only (default)
./bootstrap.sh

# Ingress only
PHASE=ingress ./bootstrap.sh

# GitOps + ingress
PHASE=all ./bootstrap.sh
```

---

## Optional ingress hooks

Ingress-related steps are **intentionally optional** and only run if the corresponding files exist.

### Ingress provider install
If present, this script will be executed during the ingress phase:

```
bootstrap/ingress/install.sh
```

This is where an ingress controller and (later) MetalLB would be installed.

### Argo CD ingress
If present, this manifest will be applied during the ingress phase:

```
bootstrap/argocd/ingress.yaml
```

This allows Argo CD ingress to be managed independently of the core bootstrap.

---

## Design principles

- **GitOps first**: Argo CD is the control plane; everything else is layered on.
- **Explicit phases**: control plane and ingress concerns are separated.
- **Minimal assumptions**: no DNS, TLS, or ingress required to get started.
- **Deterministic inputs**: all behavior is driven by environment variables.

---

## What’s intentionally out of scope (for now)

- Cluster creation (k3d automation)
- MetalLB configuration
- Ingress controller selection
- cert-manager issuers and TLS automation
- external-dns and DNS01 flows

These will be added incrementally once the GitOps baseline is stable.

---

## Next steps

Planned improvements include:
- A Python-based bootstrap wrapper to collect user input and generate an env file
- Optional ingress + TLS automation
- Expanded documentation once v0 stabilizes
