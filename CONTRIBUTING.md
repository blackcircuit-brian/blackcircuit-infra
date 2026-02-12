# Contributing to Black Circuit Infrastructure

Thank you for your interest in contributing to Black Circuit Infrastructure.

This repository represents the internal development foundation of the Black Circuit ecosystem. Contributions are evaluated with a strong emphasis on architectural consistency, long-term maintainability, and alignment with project governance.

---

## Guiding Principles

* **Internal First:** Black Circuit is the primary development environment. Public releases occur separately through Aetheric Forge when explicitly approved.
* **Minimalism Over Complexity:** Contributions should favor clarity and stability rather than feature expansion.
* **Reproducibility:** All infrastructure changes must be declarative and compatible with the existing GitOps workflow. Changes must converge under Argo CD without manual intervention.
* **Architectural Integrity:** Changes should respect established namespace boundaries, deployment patterns, and repository structure.
* **Certificate Strategy Boundaries:** Internal domains (`*.int.blackcircuit.ca`) must not depend on public ACME validation. Public and internal certificate flows must remain separate.

---

## Contribution Process

1. Create a feature branch from the main branch.
2. Implement changes using declarative manifests, charts, or documented configuration.
3. Ensure changes deploy cleanly to the kubeadm reference environment and do not assume k3d-specific behavior.
4. Submit a Pull Request with a clear description of:

   * Purpose of the change
   * Impact on platform or workloads
   * Any operational considerations

Pull Requests may be revised, restructured, or declined to maintain overall design coherence.

---

## Scope of Contributions

Appropriate contributions include:

* Platform infrastructure improvements
* Deployment automation enhancements
* Documentation updates
* Stability or observability improvements

The following are generally out of scope unless explicitly requested:

* Public-facing tooling
* External integrations intended for Aetheric Forge publication
* Large architectural redesigns without prior discussion

---

## Coding and Configuration Standards

* Prefer declarative configuration over imperative scripting.
* Maintain consistent naming conventions and namespace boundaries.
* Avoid environment-specific logic embedded directly in manifests.
* Keep changes modular and reviewable.

---

## Licensing

By contributing to this repository, you agree that your contributions fall under the terms defined in the `LICENSE` file unless a separate agreement is provided.

---

## Governance

Final decisions regarding architecture, acceptance of contributions, and repository direction are made by the Black Circuit maintainers.

This project evolves deliberately. Thoughtful proposals are encouraged; rushed changes are not.

---

## Bootstrap and Teardown Requirements

All changes must preserve deterministic bootstrap and teardown behavior.

Specifically:

* A clean cluster must bootstrap without manual patching.
* Teardown must allow immediate re-bootstrap without residual state.
* No change may introduce hidden dependencies on existing cluster state.
* cert-manager resources must not block namespace deletion.
* Bootstrap-only secrets (e.g., repo SSH, DNS tokens) must not be committed to Git.

Any change that affects bootstrap ordering, CRDs, or controller lifecycle must be tested against a clean cluster rebuild.

