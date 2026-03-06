# SOPS Workflow

This repository commits encrypted Kubernetes secrets as `*.enc.yaml`.

## 1. Install required binaries

Install:

- `sops`: https://github.com/getsops/sops/releases
- `age` (recommended): https://github.com/FiloSottile/age
- `ksops`: https://github.com/viaduct-ai/kustomize-sops

Common options:

```bash
# Install age via apt (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y age

# Install sops binary (Linux, including Raspberry Pi)
SOPS_VERSION=v3.10.2
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64) SOPS_ARCH=amd64 ;;
  aarch64|arm64) SOPS_ARCH=arm64 ;;
  armv7l) SOPS_ARCH=armv7 ;;
  *) echo "Unsupported arch: $ARCH"; exit 1 ;;
esac
curl -fsSL "https://github.com/getsops/sops/releases/download/${SOPS_VERSION}/sops-${SOPS_VERSION}.linux.${SOPS_ARCH}" -o /tmp/sops
chmod +x /tmp/sops
sudo install -m 0755 /tmp/sops /usr/local/bin/sops
sops --version

# Install ksops binary (Linux)
KSOPS_VERSION=v4.4.0
curl -fsSL "https://github.com/viaduct-ai/kustomize-sops/releases/download/${KSOPS_VERSION}/ksops_${KSOPS_VERSION}_Linux_x86_64.tar.gz" -o /tmp/ksops.tgz
tar -xzf /tmp/ksops.tgz -C /tmp
sudo install -m 0755 /tmp/ksops /usr/local/bin/ksops
ksops version
```

If you prefer package managers (`brew`, `choco`, `winget`, etc.), use those instead.

## 2. Create encryption keys

### Option A: `age` (recommended)

Generate key pair:

```bash
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
```

Get the public recipient key:

```bash
grep '^# public key:' ~/.config/sops/age/keys.txt
```

Use that `age1...` value in `.sops.yaml`:

```yaml
creation_rules:
  - path_regex: .*\.enc\.yaml$
    encrypted_regex: "^(data|stringData)$"
    age: ["age1...your-recipient..."]
```

### Option B: GPG (if your team already uses it)

Generate a GPG key and use its fingerprint:

```bash
gpg --full-generate-key
gpg --list-secret-keys --keyid-format LONG
```

Then configure `.sops.yaml` with:

```yaml
creation_rules:
  - path_regex: .*\.enc\.yaml$
    encrypted_regex: "^(data|stringData)$"
    pgp: ["<fingerprint>"]
```

## 3. Create a `secrets.enc.yaml` file

Create a plaintext template:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: example-secret
  namespace: default
type: Opaque
stringData:
  username: REPLACE_ME
  password: REPLACE_ME
```

Encrypt it in-place:

```bash
sops -e -i path/to/secrets.enc.yaml
```

You can also create and edit in one step:

```bash
sops path/to/secrets.enc.yaml
```

## 4. Edit encrypted files safely

Use SOPS directly (never put real secret values in plaintext files):

```bash
sops platform/cloudflare-ddns/base/secrets.enc.yaml
```

## 5. Validate and commit

- Ensure files remain encrypted before commit (contain a `sops:` block).
- Commit `*.enc.yaml` files to Git.
- Do not commit unencrypted secret files.

## 6. Render encrypted secrets with Kustomize

When overlays include KSOPS generators, build with plugin flags:

```bash
kustomize build --enable-helm --enable-alpha-plugins --enable-exec clusters/single/dev
```

If plugin discovery fails, set:

```bash
export KUSTOMIZE_PLUGIN_HOME="${HOME}/.config/kustomize/plugin"
```
