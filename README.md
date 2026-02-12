# Black Circuit GitOps Bootstrap (v0.3)

This repository provides a deterministic, opinionated bootstrap workflow
for establishing a **GitOps control plane** in Kubernetes using **Argo
CD**.

The goal is not flexibility --- it is reproducibility.

This bootstrap now includes:

-   Argo CD installation
-   App-of-apps root wiring
-   MetalLB installation (controller only; pools via GitOps)
-   ingress-nginx (public + private)
-   cert-manager
-   internal CA for `*.int.blackcircuit.ca`
-   optional public ACME issuers (DNS01 via Cloudflare)
-   SSH repo secret handling for private Git

------------------------------------------------------------------------

## Design Goals

-   Deterministic cluster rebuild
-   Clear separation of bootstrap vs GitOps ownership
-   Minimal manual intervention
-   Explicit secret lifecycle
-   Clean teardown capability

------------------------------------------------------------------------

## Bootstrap Model

Bootstrap is split into **phases**, driven by `bootstrap.py`.

### Phase: `gitops` (default)

-   Installs Argo CD via Helm
-   Applies the root app-of-apps
-   Installs MetalLB controller (CRDs + controller only)
-   Hands reconciliation to Argo CD

### Phase: `ingress`

-   Applies ingress-related applications
-   Requires Argo CD to already exist

### Phase: `all`

-   Runs `gitops` phase
-   Then runs `ingress` phase

------------------------------------------------------------------------

## Prerequisites

-   A running Kubernetes cluster (kubeadm reference environment)
-   `kubectl`
-   `helm`
-   Cluster-admin privileges

Cluster creation itself is out of scope.

------------------------------------------------------------------------

## Quick Start (kubeadm reference)

``` bash
./bootstrap.py --env-file bootstrap/env/kubeadm.env
```

Optional flags:

``` bash
--ssh-key-file ~/.ssh/id_ed25519
--cloudflare-token-file ~/.cloudflare-token
--phase gitops|ingress|all
--non-interactive
```

Bootstrap will:

-   Install Argo CD
-   Create repo access secret if SSH key provided
-   Install MetalLB controller
-   Apply root app-of-apps
-   Allow Argo CD to reconcile the platform

------------------------------------------------------------------------

## Repository Layout (Relevant)

    bootstrap/
      argocd/
        bootstrap.sh
        teardown.sh
      metallb/
        metallb.sh
    bootstrap.py

    gitops/
      clusters/<env>/root-app.yaml
      apps/
      manifests/

Bootstrap installs prerequisites.

GitOps owns everything else.

------------------------------------------------------------------------

## Certificates

Internal domain:

    *.int.blackcircuit.ca

This domain is internal-only and not publicly delegated.

Public ACME (Let's Encrypt) **cannot validate internal-only domains**.

Therefore:

-   Internal ingress uses `ClusterIssuer/int-ca`
-   Public ingress (if configured) may use `letsencrypt-*-dns01`

The internal CA root certificate must be installed into client trust
stores.

------------------------------------------------------------------------

## Secrets Managed by Bootstrap

Certain secrets are intentionally **not GitOps-managed**:

-   `argocd/repo-git-ssh`
-   `cert-manager/cloudflare-api-token` (public DNS01 only)

These are bootstrap responsibilities.

------------------------------------------------------------------------

## Accessing Argo CD

Without ingress:

``` bash
kubectl -n argocd port-forward svc/argocd-server 8080:443
```

With internal ingress:

    https://argocd.int.blackcircuit.ca

(Requires trusting internal root CA.)

------------------------------------------------------------------------

## Clean Rebuild

Teardown:

``` bash
bootstrap/argocd/teardown.sh
```

Teardown must remove:

-   Argo CD applications
-   cert-manager resources
-   namespaces
-   lingering finalizers

A clean rebuild must succeed without manual intervention.

------------------------------------------------------------------------

## Out of Scope

-   Cluster provisioning automation
-   step-ca (future replacement for internal CA)
-   external-dns automation
