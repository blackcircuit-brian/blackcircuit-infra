#!/usr/bin/env python3
"""
bootstrap.py

Interactive front-end for bootstrap/argocd/bootstrap.sh.

- Prompts for key inputs with defaults (interactive mode)
- Supports non-interactive mode for CI / scripted runs
- Writes a .env file
- (Private repos) uses a repo-local known_hosts file; generates it via ssh-keyscan github.com if missing
- Invokes bootstrap.sh with --env-file

Stdlib-only on purpose for OSS friendliness.
"""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
import textwrap
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import shutil


VALID_PHASES = ("gitops", "ingress", "all")
VALID_VIS = ("public", "private")


def normalize_github_repo(value: str) -> str:
    """Normalize GitHub repo input to org/repo (no .git)."""
    v = value.strip()
    if not v:
        return ""

    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", v)
    if m:
        return f"{m.group(1)}/{m.group(2)}"

    m = re.match(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$", v)
    if m:
        return f"{m.group(1)}/{m.group(2)}"

    m = re.match(r"^([^/]+)/([^/]+?)(?:\.git)?$", v)
    if m:
        return f"{m.group(1)}/{m.group(2)}"

    raise ValueError(f"Unrecognized GitHub repo format: {value!r}")


def github_clone_url(org_repo: str, visibility: str) -> str:
    """Return the clone URL for a normalized org/repo and visibility."""
    if not org_repo:
        return ""
    if visibility == "private":
        return f"git@github.com:{org_repo}.git"
    return f"https://github.com/{org_repo}.git"


def needs_ssh_known_hosts(repo_visibility: str, repo_url: str) -> bool:
    """Return True if we should use known_hosts (SSH-based repo access)."""
    return repo_visibility == "private" and repo_url.startswith("git@")


def known_hosts_path(repo_root: Path) -> Path:
    """Repo-local known_hosts file path."""
    return repo_root / "bootstrap" / "generated" / "known_hosts"


def generate_known_hosts(repo_root: Path, host: str = "github.com") -> Path:
    """Generate a repo-local known_hosts file using ssh-keyscan."""
    if shutil.which("ssh-keyscan") is None:
        raise RuntimeError("ssh-keyscan not found in PATH (required for private repo SSH access)")

    out_path = known_hosts_path(repo_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ssh-keyscan", "-t", "rsa,ecdsa,ed25519", host]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        stderr = proc.stderr.strip()
        raise RuntimeError(f"ssh-keyscan failed for {host}. {stderr}".strip())

    out_path.write_text(proc.stdout, encoding="utf-8")
    return out_path


@dataclass
class BootstrapConfig:
    org_slug: str = "aethericforge"
    env: str = "test-k3d"
    argo_namespace: str = "argocd"
    phase: str = "gitops"
    apply_root_app: bool = True
    root_app_path: str = ""  # derived if empty

    # Git source (used by bootstrap.sh for root Application rendering)
    repo_visibility: str = "public"  # public|private
    github_repo: str = ""           # org/repo
    repo_ref: str = "main"          # branch/tag/sha

    def normalize(self) -> None:
        if self.phase not in VALID_PHASES:
            raise ValueError(f"Invalid PHASE: {self.phase}. Expected one of: {', '.join(VALID_PHASES)}")
        if self.repo_visibility not in VALID_VIS:
            raise ValueError(f"Invalid REPO_VISIBILITY: {self.repo_visibility}. Expected one of: {', '.join(VALID_VIS)}")

        if not self.root_app_path.strip():
            self.root_app_path = f"gitops/clusters/{self.env}/root-app.yaml"

        self.org_slug = self.org_slug.strip()
        self.env = self.env.strip()
        self.argo_namespace = self.argo_namespace.strip()
        self.phase = self.phase.strip().lower()
        self.repo_visibility = self.repo_visibility.strip().lower()
        self.github_repo = self.github_repo.strip()
        self.repo_ref = self.repo_ref.strip()


def _prompt(text: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default is not None and default != "" else ""
    while True:
        val = input(f"{text}{suffix}: ").strip()
        if val:
            return val
        if default is not None:
            return default


def _prompt_bool(text: str, default: bool) -> bool:
    d = "y" if default else "n"
    while True:
        val = input(f"{text} [y/n] [{d}]: ").strip().lower()
        if not val:
            return default
        if val in ("y", "yes", "true", "1"):
            return True
        if val in ("n", "no", "false", "0"):
            return False
        print("Please enter y or n.")


def _prompt_choice(text: str, choices: tuple[str, ...], default: str) -> str:
    choices_str = "/".join(choices)
    while True:
        val = input(f"{text} ({choices_str}) [{default}]: ").strip().lower()
        if not val:
            return default
        if val in choices:
            return val
        print(f"Please choose one of: {choices_str}")


def _parse_env_file(path: Path) -> Dict[str, str]:
    """Minimal .env parser."""
    out: Dict[str, str] = {}
    if not path.exists():
        return out

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def _quote_env_value(v: str) -> str:
    v = v.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{v}"'


def _write_env_file(path: Path, values: Dict[str, str]) -> None:
    lines = [
        "# Generated by bootstrap.py",
        "# You can edit this file and re-run bootstrap.py to reuse values.",
        "",
    ]
    for k in sorted(values.keys()):
        lines.append(f"{k}={_quote_env_value(values[k])}")
    lines.append("")  # trailing newline

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _find_repo_root(start: Path) -> Path:
    cur = start.resolve()
    for _ in range(40):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return start.resolve()


def _validate_slug(value: str, field: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9\-]*", value):
        raise ValueError(f"{field} must be alphanumeric/dash (start with alnum). Got: {value!r}")


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("true", "1", "yes", "y")



def _run_metallb(repo_root: Path, script_path: Path, kube_context: Optional[str], version: Optional[str]) -> None:
    """Run MetalLB bootstrap installer as a pre-step (intentionally outside PHASE).

    MetalLB (CRDs + controller) is installed via bootstrap to avoid GitOps/CRD lifecycle issues.
    MetalLB configuration (IPAddressPool/L2Advertisement) should remain in GitOps-root.
    """
    if not script_path.exists():
        raise RuntimeError(f"MetalLB script not found at: {script_path}")

    env = dict(os.environ)
    if kube_context:
        env["KUBE_CONTEXT"] = kube_context
    if version:
        env["METALLB_CHART_VERSION"] = version

    cmd = ["bash", str(script_path)]
    print("Running MetalLB bootstrap:")
    print("  " + " ".join(shlex.quote(c) for c in cmd))
    if kube_context:
        print(f"  (KUBE_CONTEXT={kube_context})")
    if version:
        print(f"  (METALLB_CHART_VERSION={version})")
    print("")

    subprocess.run(cmd, cwd=str(repo_root), env=env, check=True)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="bootstrap.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Generate an env file and run bootstrap/argocd/bootstrap.sh.",
        epilog=textwrap.dedent(
            """\
            Examples:
              ./bootstrap.py
              ./bootstrap.py --env-file bootstrap/env/test-k3d.env
              ./bootstrap.py --env-file bootstrap/env/test-k3d.env --non-interactive
              ./bootstrap.py --env-file bootstrap/env/test-k3d.env --non-interactive --phase all
              ./bootstrap.py --env-file bootstrap/env/test-k3d.env --non-interactive --ref v0.3
              ./bootstrap.py --env-file bootstrap/env/test-k3d.env --non-interactive --ref v0.3 --refresh-known-hosts
            """
        ),
    )
    p.add_argument("--env-file", dest="env_file", default=None, help="Path to write/read env file (default: bootstrap/env/test-k3d.env)")
    p.add_argument("--non-interactive", action="store_true", help="Do not prompt; fail if required values are missing")
    p.add_argument("--phase", choices=VALID_PHASES, default=None, help="Override PHASE (gitops|ingress|all)")
    p.add_argument("--yes", action="store_true", help="In interactive mode, accept defaults without prompting (best with --env-file)" )
    p.add_argument("--no-known-hosts", action="store_true", help="Skip using/generating known_hosts even for private repos (dev-only escape hatch)" )
    p.add_argument("--refresh-known-hosts", action="store_true", help="Force re-running ssh-keyscan for github.com (overwrites cached bootstrap/generated/known_hosts)" )
    p.add_argument("--ref", "--repo-ref", dest="repo_ref", default=None, help="Override REPO_REF (branch/tag/sha) written to env and used for Argo root app targetRevision" )
    p.add_argument("--with-metallb", action="store_true", help="Run MetalLB install as a pre-step (CRDs + controller) before Argo bootstrap")
    p.add_argument("--metallb-script", default=None, help="Path to MetalLB installer script (default: bootstrap/metallb.sh)")
    p.add_argument("--metallb-version", default=None, help="Optional MetalLB chart/version forwarded to installer (env: METALLB_CHART_VERSION)")
    p.add_argument("--kube-context", default=None, help="Optional kubectl context forwarded to installer (env: KUBE_CONTEXT)")

    p.add_argument("--ssh-key-file", default=None, help="Path to SSH private key for ArgoCD repo access (private repos)")
    p.add_argument("--repo-ssh-secret", default=None, help="Name of ArgoCD repository secret to create/update (default: repo-git-ssh)")
    p.add_argument("--cloudflare-token-file", default=None, help="Path to Cloudflare API token file (for DNS-01 issuance)")
    p.add_argument("--cloudflare-secret-name", default=None, help="Name of Cloudflare token secret (default: cloudflare-api-token)")
    p.add_argument("--cloudflare-secret-namespace", default=None, help="Namespace for Cloudflare token secret (default: cert-manager)")
    return p.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])

    here = Path(__file__).resolve()
    repo_root = _find_repo_root(here.parent)

    bootstrap_sh = repo_root / "bootstrap" / "argocd" / "bootstrap.sh"
    if not bootstrap_sh.exists():
        print(f"ERROR: Could not find bootstrap.sh at: {bootstrap_sh}", file=sys.stderr)
        return 2

    default_env_file = repo_root / "bootstrap" / "env" / "kubeadm.env"
    if args.env_file:
        env_path = Path(args.env_file)
        env_path = (repo_root / env_path).resolve() if not env_path.is_absolute() else env_path
    else:
        env_path = default_env_file

    existing = _parse_env_file(env_path)

    cfg = BootstrapConfig(
        org_slug=existing.get("ORG_SLUG", "aethericforge"),
        env=existing.get("ENV", "kubeadm"),
        argo_namespace=existing.get("ARGO_NAMESPACE", existing.get("ARGOCD_NAMESPACE", "argocd")),
        phase=existing.get("PHASE", "gitops"),
        apply_root_app=_truthy(existing.get("APPLY_ROOT_APP", "true")),
        root_app_path=existing.get("ROOT_APP_PATH", ""),
        repo_visibility=existing.get("REPO_VISIBILITY", "public"),
        github_repo=existing.get("GITHUB_REPO", ""),
        repo_ref=existing.get("REPO_REF", "main"),
    )

    # CLI overrides
    if args.phase:
        cfg.phase = args.phase
    if args.repo_ref:
        cfg.repo_ref = args.repo_ref.strip()

    if args.non_interactive:
        try:
            cfg.normalize()
            _validate_slug(cfg.org_slug, "ORG_SLUG")
            _validate_slug(cfg.env, "ENV")
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
    else:
        if not args.yes:
            print(f"Repo root: {repo_root}")
            print(f"Bootstrap script: {bootstrap_sh}")
            print("")
            print("=== Bootstrap configuration ===")
            cfg.org_slug = _prompt("Org slug (values.<org>.yaml selector)", cfg.org_slug)
            _validate_slug(cfg.org_slug, "ORG_SLUG")

            cfg.env = _prompt("Environment name (values.<env>.yaml selector)", cfg.env)
            _validate_slug(cfg.env, "ENV")

            cfg.argo_namespace = _prompt("Argo CD namespace", cfg.argo_namespace)
            cfg.phase = _prompt_choice("Phase", VALID_PHASES, cfg.phase if cfg.phase in VALID_PHASES else "gitops")
            cfg.apply_root_app = _prompt_bool("Apply root app-of-apps", cfg.apply_root_app)

            default_root = f"gitops/clusters/{cfg.env}/root-app.yaml"
            cfg.root_app_path = _prompt("Root app path", cfg.root_app_path or default_root)

            cfg.repo_visibility = _prompt_choice("GitHub repo visibility", VALID_VIS, cfg.repo_visibility if cfg.repo_visibility in VALID_VIS else "public")
            raw_repo = _prompt("GitHub repo (org/repo or URL) [optional]", cfg.github_repo)
            try:
                cfg.github_repo = normalize_github_repo(raw_repo) if raw_repo else ""
            except ValueError as e:
                print(str(e), file=sys.stderr)
                return 2

            cfg.repo_ref = _prompt("Git ref (branch/tag/sha)", cfg.repo_ref)

    cfg.normalize()
    repo_url = github_clone_url(cfg.github_repo, cfg.repo_visibility)


    # SSH key + Argo repo secret support (private repos)
    ssh_key_file = (args.ssh_key_file or existing.get("SSH_PRIVATE_KEY_FILE", "")).strip()
    repo_ssh_secret_name = (args.repo_ssh_secret or existing.get("REPO_SSH_SECRET_NAME", "repo-git-ssh")).strip() or "repo-git-ssh"

    if needs_ssh_known_hosts(cfg.repo_visibility, repo_url):
        if not ssh_key_file:
            # Pick a sensible default if present
            default_key = str(Path.home() / ".ssh" / "id_ed25519")
            if not Path(default_key).exists():
                default_key = str(Path.home() / ".ssh" / "id_rsa")
            if args.non_interactive:
                print(
                    "ERROR: Private repo selected but no SSH key provided. "
                    "Use --ssh-key-file (or set SSH_PRIVATE_KEY_FILE in the env file).",
                    file=sys.stderr,
                )
                return 2
            ssh_key_file = _prompt(
                "SSH private key file (for repo access)",
                default_key if Path(default_key).exists() else "",
            )
        if ssh_key_file:
            p = Path(ssh_key_file).expanduser()
            if not p.exists() or not p.is_file():
                print(f"ERROR: SSH key file not found: {p}", file=sys.stderr)
                return 2
            ssh_key_file = str(p.resolve())

    # Cloudflare token secret support (DNS-01 via Cloudflare)
    cloudflare_token_file = (args.cloudflare_token_file or existing.get("CLOUDFLARE_API_TOKEN_FILE", "")).strip()
    cloudflare_secret_name = (args.cloudflare_secret_name or existing.get("CLOUDFLARE_API_TOKEN_SECRET_NAME", "cloudflare-api-token")).strip() or "cloudflare-api-token"
    cloudflare_secret_namespace = (args.cloudflare_secret_namespace or existing.get("CLOUDFLARE_API_TOKEN_SECRET_NAMESPACE", "cert-manager")).strip() or "cert-manager"

    if cloudflare_token_file:
        p = Path(cloudflare_token_file).expanduser()
        if not p.exists() or not p.is_file():
            print(f"ERROR: Cloudflare token file not found: {p}", file=sys.stderr)
            return 2
        cloudflare_token_file = str(p.resolve())


    # known_hosts cache-first behavior for private SSH repos
    known_hosts_file = ""
    known_hosts_source = ""
    if not args.no_known_hosts and needs_ssh_known_hosts(cfg.repo_visibility, repo_url):
        kh_path = known_hosts_path(repo_root)
        if not args.refresh_known_hosts and kh_path.exists() and kh_path.stat().st_size > 0:
            known_hosts_file = str(kh_path)
            known_hosts_source = "cached"
        else:
            try:
                kh = generate_known_hosts(repo_root, "github.com")
                known_hosts_file = str(kh)
                known_hosts_source = "generated"
            except Exception as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 2

    env_values: Dict[str, str] = {
        "ORG_SLUG": cfg.org_slug,
        "ENV": cfg.env,
        "ARGO_NAMESPACE": cfg.argo_namespace,
        "PHASE": cfg.phase,
        "APPLY_ROOT_APP": "true" if cfg.apply_root_app else "false",
        "ROOT_APP_PATH": cfg.root_app_path,
        "REPO_VISIBILITY": cfg.repo_visibility,
        "GITHUB_REPO": cfg.github_repo,
        "REPO_REF": cfg.repo_ref,
        "GIT_REPO_URL": repo_url,
    }

    if needs_ssh_known_hosts(cfg.repo_visibility, repo_url) and ssh_key_file:
        env_values["SSH_PRIVATE_KEY_FILE"] = ssh_key_file
        env_values["REPO_SSH_SECRET_NAME"] = repo_ssh_secret_name

    if cloudflare_token_file:
        env_values["CLOUDFLARE_API_TOKEN_FILE"] = cloudflare_token_file
        env_values["CLOUDFLARE_API_TOKEN_SECRET_NAME"] = cloudflare_secret_name
        env_values["CLOUDFLARE_API_TOKEN_SECRET_NAMESPACE"] = cloudflare_secret_namespace

    if known_hosts_file:
        env_values["SSH_KNOWN_HOSTS_FILE"] = known_hosts_file

    # MetalLB bootstrap metadata (informational; not GitOps-managed)
    if args.with_metallb:
        env_values["METALLB_ENABLED"] = "true"
        if args.metallb_version:
            env_values["METALLB_CHART_VERSION"] = args.metallb_version

    _write_env_file(env_path, env_values)

    print("\n=== Summary ===")
    print(f"Env file:        {env_path}")
    print(f"PHASE:           {cfg.phase}")
    print(f"ORG_SLUG:        {cfg.org_slug}")
    print(f"ENV:             {cfg.env}")
    print(f"ARGO_NAMESPACE:  {cfg.argo_namespace}")
    print(f"ROOT_APP_PATH:   {cfg.root_app_path}")
    if cfg.github_repo:
        print(f"REPO_VISIBILITY: {cfg.repo_visibility}")
        print(f"GITHUB_REPO:     {cfg.github_repo}")
        print(f"REPO_REF:        {cfg.repo_ref}")
        if repo_url:
            print(f"GIT_REPO_URL:    {repo_url}")
    else:
        print("GITHUB_REPO:     (not set)")
        print(f"REPO_REF:        {cfg.repo_ref}")
    if known_hosts_file:
        suffix = f" ({known_hosts_source})" if known_hosts_source else ""
        print(f"KNOWN_HOSTS:     {known_hosts_file}{suffix}")
    print("")

    cmd = ["bash", str(bootstrap_sh), "--env-file", str(env_path)]
    print("Running:")
    print("  " + " ".join(shlex.quote(c) for c in cmd))
    print("")

    try:
        # Optional, explicit pre-step. Kept out of PHASE intentionally.
        if args.with_metallb:
            default_mb = repo_root / "bootstrap" / "metallb" / "metallb.sh"
            mb_script = Path(args.metallb_script) if args.metallb_script else default_mb
            mb_script = (repo_root / mb_script).resolve() if not mb_script.is_absolute() else mb_script
            _run_metallb(
                repo_root=repo_root,
                script_path=mb_script,
                kube_context=args.kube_context,
                version=args.metallb_version,
            )

        subprocess.run(cmd, cwd=str(repo_root), check=True)
    except subprocess.CalledProcessError as e:
        print(f"\nBootstrap failed with exit code {e.returncode}", file=sys.stderr)
        return e.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
