#!/usr/bin/env python3
"""
bootstrap.py

Interactive front-end for bootstrap/argocd/bootstrap.sh.

- Prompts for key inputs with defaults (interactive mode)
- Supports non-interactive mode for CI / scripted runs
- Writes a .env file
- (Private repos) generates a cluster-local known_hosts file via ssh-keyscan github.com
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
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import shutil


VALID_PHASES = ("gitops", "ingress", "all")
VALID_VIS = ("public", "private")


def normalize_github_repo(value: str) -> str:
    """Normalize GitHub repo input to org/repo (no .git).

    Accepts:
      - org/repo
      - https://github.com/org/repo(.git)
      - git@github.com:org/repo(.git)
    """
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
    """Return True if we should generate known_hosts (SSH-based repo access)."""
    if repo_visibility != "private":
        return False
    return repo_url.startswith("git@")


def generate_known_hosts(repo_root: Path, host: str = "github.com") -> Path:
    """Generate a cluster-local known_hosts file using ssh-keyscan.

    This is a pragmatic dev/test choice. It avoids touching ~/.ssh/known_hosts and keeps
    trust material scoped to the repo/cluster bootstrap artifacts.
    """
    if shutil.which("ssh-keyscan") is None:
        raise RuntimeError("ssh-keyscan not found in PATH (required for private repo SSH access)")

    out_dir = repo_root / "bootstrap" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "known_hosts"

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

    # Future-friendly fields (not necessarily used by bootstrap.sh today)
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
            """
        ),
    )
    p.add_argument("--env-file", dest="env_file", default=None, help="Path to write/read env file (default: bootstrap/env/test-k3d.env)")
    p.add_argument("--non-interactive", action="store_true", help="Do not prompt; fail if required values are missing")
    p.add_argument("--phase", choices=VALID_PHASES, default=None, help="Override PHASE (gitops|ingress|all)")
    p.add_argument("--yes", action="store_true", help="In interactive mode, accept defaults without prompting (best with --env-file)" )
    p.add_argument("--no-known-hosts", action="store_true", help="Skip generating known_hosts even for private repos (dev-only escape hatch)")
    return p.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])

    here = Path(__file__).resolve()
    repo_root = _find_repo_root(here.parent)

    bootstrap_sh = repo_root / "bootstrap" / "argocd" / "bootstrap.sh"
    if not bootstrap_sh.exists():
        print(f"ERROR: Could not find bootstrap.sh at: {bootstrap_sh}", file=sys.stderr)
        return 2

    default_env_file = repo_root / "bootstrap" / "env" / "test-k3d.env"
    if args.env_file:
        env_path = Path(args.env_file)
        env_path = (repo_root / env_path).resolve() if not env_path.is_absolute() else env_path
    else:
        env_path = default_env_file

    existing = _parse_env_file(env_path)

    cfg = BootstrapConfig(
        org_slug=existing.get("ORG_SLUG", "aethericforge"),
        env=existing.get("ENV", "test-k3d"),
        argo_namespace=existing.get("ARGO_NAMESPACE", existing.get("ARGOCD_NAMESPACE", "argocd")),
        phase=existing.get("PHASE", "gitops"),
        apply_root_app=_truthy(existing.get("APPLY_ROOT_APP", "true")),
        root_app_path=existing.get("ROOT_APP_PATH", ""),
        repo_visibility=existing.get("REPO_VISIBILITY", "public"),
        github_repo=existing.get("GITHUB_REPO", ""),
        repo_ref=existing.get("REPO_REF", "main"),
    )

    if args.phase:
        cfg.phase = args.phase

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

    cfg.normalize()
    repo_url = github_clone_url(cfg.github_repo, cfg.repo_visibility)

    known_hosts_path = ""
    if not args.no_known_hosts and needs_ssh_known_hosts(cfg.repo_visibility, repo_url):
        try:
            kh = generate_known_hosts(repo_root, "github.com")
            known_hosts_path = str(kh)
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

    if known_hosts_path:
        # Used in later steps when wiring repo-server; harmless if ignored today.
        env_values["SSH_KNOWN_HOSTS_FILE"] = known_hosts_path

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
        if repo_url:
            print(f"GIT_REPO_URL:    {repo_url}")
    else:
        print("GITHUB_REPO:     (not set)")
    if known_hosts_path:
        print(f"KNOWN_HOSTS:     {known_hosts_path}")
    print("")

    cmd = ["bash", str(bootstrap_sh), "--env-file", str(env_path)]
    print("Running:")
    print("  " + " ".join(shlex.quote(c) for c in cmd))
    print("")

    try:
        subprocess.run(cmd, cwd=str(repo_root), check=True)
    except subprocess.CalledProcessError as e:
        print(f"\nBootstrap failed with exit code {e.returncode}", file=sys.stderr)
        return e.returncode

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
