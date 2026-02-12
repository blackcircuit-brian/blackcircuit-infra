# Changelog

All notable changes to this project will be documented in this file.

This project follows Semantic Versioning.

---

## [0.3.0] - 2026-02-12

### Added

- Python-based bootstrap entrypoint (`bootstrap.py`)
- Deterministic teardown script with namespace and finalizer handling
- MetalLB controller installation integrated into bootstrap
- Dual ingress-nginx controllers (public / private separation)
- Internal self-signed CA (`int-ca`) for `*.int.blackcircuit.ca`
- SSH repository secret bootstrap support (`repo-git-ssh`)
- Cloudflare DNS01 token bootstrap support (public domains only)
- Formal architecture, operations, and GitOps documentation

### Changed

- kubeadm is now the reference cluster environment
- Internal domains no longer use public ACME validation
- Ingress is fully managed via GitOps (no standalone ingress YAML)
- Bootstrap explicitly separates prerequisite installation from GitOps reconciliation

### Removed

- k3d-first documentation references
- Legacy ingress bootstrap hooks
- Manual ingress manifest management outside GitOps

---

## Future

Planned improvements:

- Replace internal self-signed CA with step-ca
- Optional external-dns integration
- Secret management hardening (e.g., SOPS)
