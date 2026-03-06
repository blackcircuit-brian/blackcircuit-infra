# GitHub Setup for Argo CD Bootstrap

This runbook covers the minimum GitHub-side setup required before Argo CD can pull this repo.

## 0) Local tooling prerequisite (non-snap recommended)

`kustomize build --enable-helm` shells out to `helm`. Snap-installed binaries can fail with
permission errors in this flow due to confinement. Prefer direct binaries in `~/.local/bin`.

Install pinned versions:

```bash
set -euo pipefail
mkdir -p "$HOME/.local/bin"

# Helm (official tarball)
HELM_VERSION=v3.15.4
curl -fsSL "https://get.helm.sh/helm-${HELM_VERSION}-linux-amd64.tar.gz" -o /tmp/helm.tgz
tar -xzf /tmp/helm.tgz -C /tmp
install -m 0755 /tmp/linux-amd64/helm "$HOME/.local/bin/helm"

# Kustomize (official release)
KUSTOMIZE_VERSION=v5.4.3
curl -fsSL "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2F${KUSTOMIZE_VERSION}/kustomize_${KUSTOMIZE_VERSION}_linux_amd64.tar.gz" -o /tmp/kustomize.tgz
tar -xzf /tmp/kustomize.tgz -C /tmp
install -m 0755 /tmp/kustomize "$HOME/.local/bin/kustomize"

export PATH="$HOME/.local/bin:$PATH"
helm version
kustomize version
```

## 1) Protect the deployment branch

Recommended branch for cluster reconciliation: `v0.5`.

In GitHub branch protection for `v0.5`:

- Require pull requests before merging.
- Require status checks to pass.
- Select check: `Validate Kustomize / validate`.
- Restrict direct pushes (except trusted admins if needed).

## 2) Add Argo CD deploy key (read-only)

Generate a dedicated keypair locally:

```bash
ssh-keygen -t ed25519 -C "argocd-repo" -f ~/.ssh/argocd-repo -N ""
```

In GitHub repo settings:

- Go to `Settings -> Deploy keys -> Add deploy key`.
- Title: `argocd-repo`.
- Key: contents of `~/.ssh/argocd-repo.pub`.
- Leave write access disabled (read-only).

## 3) Create Argo CD repository secret in cluster

Preferred (Pulumi bootstrap-managed, not GitOps-managed):

1. Add bootstrap inputs:

```bash
cd scripts/pulumi
pulumi stack select dev
pulumi config set bootstrap:argoRepoSshPrivateKeyFile ~/.ssh/argocd-repo
pulumi config set bootstrap:sopsAgeKeyFile ~/.config/sops/age/keys.txt
pulumi up -y
```

`bootstrap:argoRepoKnownHostsAutoScan` defaults to `true`, so known hosts are discovered with `ssh-keyscan` from `bootstrap:argoRepoUrl` unless you set `bootstrap:argoRepoKnownHosts` or `bootstrap:argoRepoKnownHostsFile`.

Fallback (imperative/manual bootstrap):

Use the private key and GitHub host key to create the secret in each Argo CD namespace you deploy:

```bash
ssh-keyscan github.com > /tmp/github_known_hosts

kubectl -n argocd-dev create secret generic repo-git-ssh \
  --from-literal=url=git@github.com:blackcircuit-brian/blackcircuit-infra.git \
  --from-literal=type=git \
  --from-file=sshPrivateKey=$HOME/.ssh/argocd-repo \
  --from-file=known_hosts=/tmp/github_known_hosts \
  --dry-run=client -o yaml | kubectl apply -f -
```

Repeat for `argocd-test` / `argocd-prod` if those controllers are active.

Create `sops-age` as well:

```bash
kubectl -n argocd-dev create secret generic sops-age \
  --from-file=keys.txt="$HOME/.config/sops/age/keys.txt" \
  --dry-run=client -o yaml | kubectl apply -f -
```

## 4) Verify Argo CD can connect

```bash
kubectl -n argocd-dev get secret repo-git-ssh
kubectl -n argocd-dev logs deploy/argocd-repo-server | tail -n 50
```

## 5) Bootstrap target path

Current infra entrypoints are environment overlays:

- `clusters/single/dev`
- `clusters/single/test`
- `clusters/single/prod`

When creating Argo CD Application(s), set the `path` to the matching overlay.

## Notes

- Keep deploy keys and private keys out of Git.
- The CI workflow validates manifest rendering only; it does not apply to a cluster.
- MetalLB pool ranges still need real env-specific addresses before apply.
