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
import base64
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import textwrap
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple, Type


# ---- Constants & Types -------------------------------------------------------
VALID_VIS = ("public", "private")

# ---- Utilities ---------------------------------------------------------------
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
    return repo_visibility == "private" or (repo_url and repo_url.startswith("git@"))


def known_hosts_path(repo_root: Path) -> Path:
    return repo_root / "bootstrap" / "generated" / "known_hosts"


def generate_known_hosts(repo_root: Path, host: str = "github.com") -> Path:
    out_path = known_hosts_path(repo_root)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f">>> Scanning {host} keys for known_hosts...")
    cmd = ["ssh-keyscan", "-t", "rsa,ecdsa,ed25519", host]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        stderr = proc.stderr.strip()
        raise RuntimeError(f"ssh-keyscan failed for {host}. {stderr}".strip())

    out_path.write_text(proc.stdout, encoding="utf-8")
    return out_path


def _kubectl(ctx: Context, *args: str, check: bool = True, capture: bool = False, text: bool = True, input_str: str | None = None):
    cmd = ["kubectl"]
    kube_ctx = getattr(ctx.args, "kube_context", None)
    if kube_ctx:
        cmd += ["--context", kube_ctx]
    cmd += list(args)
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=text,
        input=input_str,
    )

def _helm(ctx: Context, *args: str, check: bool = True, capture: bool = False, text: bool = True):
    cmd = ["helm"]
    kube_ctx = getattr(ctx.args, "kube_context", None)
    if kube_ctx:
        cmd += ["--kube-context", kube_ctx]
    cmd += list(args)
    return subprocess.run(cmd, check=check, capture_output=capture, text=text)

def _prompt(text: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default is not None and default != "" else ""
    while True:
        try:
            val = input(f"{text}{suffix}: ").strip()
        except EOFError:
            return default if default is not None else ""
        if val:
            return val
        if default is not None:
            return default

def _prompt_bool(text: str, default: bool) -> bool:
    d = "y" if default else "n"
    while True:
        try:
            val = input(f"{text} [y/n] [{d}]: ").strip().lower()
        except EOFError:
            return default
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
        try:
            val = input(f"{text} ({choices_str}) [{default}]: ").strip().lower()
        except EOFError:
            return default
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


def _generate_tsig_secret_base64(nbytes: int = 32) -> str:
    """Generate a base64 TSIG secret suitable for BIND9 (RFC2136)."""
    return base64.b64encode(secrets.token_bytes(nbytes)).decode("ascii")


def _read_secret_file(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def _write_secret_file(path: Path, secret_b64: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret_b64, encoding="utf-8")
    os.chmod(path, 0o600)


def _emit_bind9_snippet(
    out_path: Path,
    zone: str,
    keyname: str,
    algorithm: str,
    secret_b64: str,
    zone_file: str = "/var/lib/bind/db.int.blackcircuit.ca",
    update_policy: str | None = None,
) -> None:
    """Write a BIND9 configuration snippet for the TSIG key and update-policy."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    policy_block = update_policy or f"grant {keyname} zonesub ANY;"

    snippet = textwrap.dedent(f"""\
    key "{keyname}" {{
        algorithm {algorithm};
        secret "{secret_b64}";
    }};

    zone "{zone}" {{
        type master;
        file "{zone_file}";
        update-policy {{
            {policy_block}
        }};
    }};
    """)

    out_path.write_text(snippet, encoding="utf-8")
    os.chmod(out_path, 0o644)


def _ensure_namespace(namespace: str, kube_context: str | None = None) -> None:
    """Ensure a Kubernetes namespace exists."""
    ns_cmd = ["kubectl"]
    if kube_context:
        ns_cmd += ["--context", kube_context]
    ns_cmd += ["create", "namespace", namespace, "--dry-run=client", "-o", "yaml"]
    try:
        ns_yaml = subprocess.run(ns_cmd, check=True, capture_output=True, text=True).stdout
        apply_cmd = ["kubectl"]
        if kube_context:
            apply_cmd += ["--context", kube_context]
        apply_cmd += ["apply", "-f", "-"]
        subprocess.run(apply_cmd, check=True, input=ns_yaml, text=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f">>> WARNING: Failed ensuring namespace {namespace}: {e}", file=sys.stderr)


def _apply_kube_secret(
    namespace: str,
    secret_name: str,
    data: Dict[str, str],
    labels: Dict[str, str] | None = None,
    secret_type: str = "generic",
    kube_context: str | None = None,
) -> None:
    """Create/update a Kubernetes Secret (idempotent)."""
    _ensure_namespace(namespace, kube_context=kube_context)

    print(f">>> Applying Kubernetes secret: {namespace}/{secret_name}")
    create_cmd = ["kubectl"]
    if kube_context:
        create_cmd += ["--context", kube_context]
    
    create_cmd += ["-n", namespace, "create", "secret", secret_type, secret_name]
    for k, v in data.items():
        create_cmd.append(f"--from-literal={k}={v}")
    
    create_cmd += ["--dry-run=client", "-o", "yaml"]

    try:
        secret_yaml = subprocess.run(create_cmd, check=True, capture_output=True, text=True).stdout
        
        if labels:
            # A bit hacky but works for dry-run output to add labels
            import yaml
            secret_obj = yaml.safe_load(secret_yaml)
            if "metadata" not in secret_obj:
                secret_obj["metadata"] = {}
            if "labels" not in secret_obj["metadata"]:
                secret_obj["metadata"]["labels"] = {}
            secret_obj["metadata"]["labels"].update(labels)
            secret_yaml = yaml.dump(secret_obj)

        apply_cmd = ["kubectl"]
        if kube_context:
            apply_cmd += ["--context", kube_context]
        apply_cmd += ["apply", "-f", "-"]
        subprocess.run(apply_cmd, check=True, input=secret_yaml, text=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f">>> WARNING: Failed applying Secret {namespace}/{secret_name}: {e}", file=sys.stderr)
    except ImportError:
        # Fallback if yaml is not available (stdlib only)
        # For simplicity in stdlib-only env, we can just use kubectl label afterwards if labels are needed
        apply_cmd = ["kubectl"]
        if kube_context:
            apply_cmd += ["--context", kube_context]
        apply_cmd += ["apply", "-f", "-"]
        subprocess.run(apply_cmd, check=True, input=secret_yaml, text=True, capture_output=True)
        
        if labels:
            for lk, lv in labels.items():
                label_cmd = ["kubectl"]
                if kube_context:
                    label_cmd += ["--context", kube_context]
                label_cmd += ["-n", namespace, "label", "secret", secret_name, f"{lk}={lv}", "--overwrite"]
                subprocess.run(label_cmd, check=True, capture_output=True)


def _apply_rfc2136_tsig_secret(
    namespace: str,
    secret_name: str,
    secret_key: str,
    secret_file: Path,
    kube_context: str | None = None,
) -> None:
    """Create/update a Kubernetes Secret from the local TSIG secret file (idempotent)."""
    if not secret_file.exists() or not secret_file.is_file():
        print(f">>> RFC2136 TSIG secret file not found; skipping Secret apply: {secret_file}")
        return
    secret_value = secret_file.read_text(encoding="utf-8").strip()
    if not secret_value:
        print(f">>> RFC2136 TSIG secret file is empty; skipping Secret apply: {secret_file}")
        return

    _apply_kube_secret(namespace, secret_name, {secret_key: secret_value}, kube_context=kube_context)


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


# ---- Module Framework --------------------------------------------------------

@dataclass
class Context:
    repo_root: Path
    args: argparse.Namespace
    existing_env: Dict[str, str]
    env_values: Dict[str, str] = field(default_factory=dict)
    
    def get_existing(self, key: str, default: str = "") -> str:
        return self.existing_env.get(key, default)

    def set_env(self, key: str, value: str) -> None:
        self.env_values[key] = value


class BootstrapModule(ABC):
    @abstractmethod
    def add_args(self, parser: argparse.ArgumentParser) -> None:
        pass

    @abstractmethod
    def run(self, ctx: Context) -> None:
        pass


# ---- Feature Modules ---------------------------------------------------------

class CoreModule(BootstrapModule):
    def add_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--ref", "--repo-ref", dest="repo_ref", help="Override REPO_REF")

    def run(self, ctx: Context) -> None:
        args = ctx.args
        existing = ctx.existing_env
        
        # Initial defaults
        org_slug = existing.get("ORG_SLUG", "aethericforge")
        env_name = existing.get("ENV", "kubeadm")
        argo_ns = existing.get("ARGO_NAMESPACE", existing.get("ARGOCD_NAMESPACE", "argocd"))
        apply_root = _truthy(existing.get("APPLY_ROOT_APP", "true"))
        root_app_path = existing.get("ROOT_APP_PATH", "")
        repo_vis = existing.get("REPO_VISIBILITY", "public")
        github_repo = existing.get("GITHUB_REPO", "")
        repo_ref = existing.get("REPO_REF", "main")

        # CLI overrides
        if args.repo_ref:
            repo_ref = args.repo_ref.strip()

        if not args.non_interactive and not args.yes:
            print(f"Repo root: {ctx.repo_root}")
            print("")
            print("=== Bootstrap configuration ===")
            org_slug = _prompt("Org slug (values.<org>.yaml selector)", org_slug)
            _validate_slug(org_slug, "ORG_SLUG")

            env_name = _prompt("Environment name (values.<env>.yaml selector)", env_name)
            _validate_slug(env_name, "ENV")

            argo_ns = _prompt("Argo CD namespace", argo_ns)
            apply_root = _prompt_bool("Apply root app-of-apps", apply_root)

            default_root = f"gitops/clusters/{env_name}/root-app.yaml"
            root_app_path = _prompt("Root app path", root_app_path or default_root)

            repo_vis = _prompt_choice("GitHub repo visibility", VALID_VIS, repo_vis if repo_vis in VALID_VIS else "public")
            raw_repo = _prompt("GitHub repo (org/repo or URL) [optional]", github_repo)
            github_repo = normalize_github_repo(raw_repo) if raw_repo else ""
            repo_ref = _prompt("Git ref (branch/tag/sha)", repo_ref)

        _validate_slug(org_slug, "ORG_SLUG")
        _validate_slug(env_name, "ENV")
        if not root_app_path.strip():
            root_app_path = f"gitops/clusters/{env_name}/root-app.yaml"

        repo_url = github_clone_url(github_repo, repo_vis)

        ctx.set_env("ORG_SLUG", org_slug)
        ctx.set_env("ENV", env_name)
        ctx.set_env("ARGO_NAMESPACE", argo_ns)
        ctx.set_env("APPLY_ROOT_APP", "true" if apply_root else "false")
        ctx.set_env("ROOT_APP_PATH", root_app_path)
        ctx.set_env("REPO_VISIBILITY", repo_vis)
        ctx.set_env("GITHUB_REPO", github_repo)
        ctx.set_env("REPO_REF", repo_ref)
        ctx.set_env("GIT_REPO_URL", repo_url)


class SSHModule(BootstrapModule):
    def add_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--no-known-hosts", action="store_true", help="Skip using/generating known_hosts")
        parser.add_argument("--refresh-known-hosts", action="store_true", help="Force re-running ssh-keyscan")
        parser.add_argument("--ssh-key-file", help="Path to SSH private key")
        parser.add_argument("--repo-ssh-secret", help="Name of ArgoCD repository secret")

    def run(self, ctx: Context) -> None:
        repo_vis = ctx.env_values.get("REPO_VISIBILITY")
        repo_url = ctx.env_values.get("GIT_REPO_URL")
        
        if not needs_ssh_known_hosts(repo_vis, repo_url):
            return

        ssh_key_file = (ctx.args.ssh_key_file or ctx.get_existing("SSH_PRIVATE_KEY_FILE")).strip()
        repo_ssh_secret_name = (ctx.args.repo_ssh_secret or ctx.get_existing("REPO_SSH_SECRET_NAME", "repo-git-ssh")).strip() or "repo-git-ssh"

        if not ssh_key_file:
            default_key = str(Path.home() / ".ssh" / "id_ed25519")
            if not Path(default_key).exists():
                default_key = str(Path.home() / ".ssh" / "id_rsa")
            
            if ctx.args.non_interactive:
                print("ERROR: Private repo selected but no SSH key provided.", file=sys.stderr)
                sys.exit(2)
            
            ssh_key_file = _prompt("SSH private key file (for repo access)", default_key if Path(default_key).exists() else "")

        if ssh_key_file:
            p = Path(ssh_key_file).expanduser()
            if not p.exists() or not p.is_file():
                print(f"ERROR: SSH key file not found: {p}", file=sys.stderr)
                sys.exit(2)
            ssh_key_file = str(p.resolve())
            ctx.set_env("SSH_PRIVATE_KEY_FILE", ssh_key_file)
            ctx.set_env("REPO_SSH_SECRET_NAME", repo_ssh_secret_name)

        # known_hosts
        kh_path = None
        if not ctx.args.no_known_hosts:
            kh_path = known_hosts_path(ctx.repo_root)
            if not ctx.args.refresh_known_hosts and kh_path.exists() and kh_path.stat().st_size > 0:
                ctx.set_env("SSH_KNOWN_HOSTS_FILE", str(kh_path))
            else:
                try:
                    kh_path = generate_known_hosts(ctx.repo_root, "github.com")
                    ctx.set_env("SSH_KNOWN_HOSTS_FILE", str(kh_path))
                except Exception as e:
                    print(f"ERROR: {e}", file=sys.stderr)
                    sys.exit(2)

        # Apply Kubernetes Secret
        if ssh_key_file:
            print(f">>> Applying ArgoCD repo secret: {repo_ssh_secret_name}")
            ssh_key_content = Path(ssh_key_file).read_text(encoding="utf-8").strip()
            kh_content = ""
            if kh_path and kh_path.exists():
                kh_content = kh_path.read_text(encoding="utf-8").strip()
            
            data = {
                "type": "git",
                "url": repo_url,
                "sshPrivateKey": ssh_key_content,
            }
            if kh_content:
                data["sshKnownHosts"] = kh_content
            
            labels = {"argocd.argoproj.io/secret-type": "repository"}
            argo_ns = ctx.env_values.get("ARGO_NAMESPACE", "argocd")
            kube_ctx = getattr(ctx.args, "kube_context", None)
            
            _apply_kube_secret(
                namespace=argo_ns,
                secret_name=repo_ssh_secret_name,
                data=data,
                labels=labels,
                kube_context=kube_ctx
            )
            
            print(f">>> Restarting argocd-repo-server to pick up repo secret changes")
            rollout_cmd = ["kubectl"]
            if kube_ctx:
                rollout_cmd += ["--context", kube_ctx]
            rollout_cmd += ["-n", argo_ns, "rollout", "restart", "deploy/argocd-repo-server"]
            try:
                subprocess.run(rollout_cmd, check=True, capture_output=True)
                
                status_cmd = ["kubectl"]
                if kube_ctx:
                    status_cmd += ["--context", kube_ctx]
                status_cmd += ["-n", argo_ns, "rollout", "status", "deploy/argocd-repo-server", "--timeout=120s"]
                subprocess.run(status_cmd, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                print(f">>> WARNING: Failed restarting argocd-repo-server: {e}", file=sys.stderr)


class CloudflareModule(BootstrapModule):
    def add_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--cloudflare-token-file", help="Path to Cloudflare API token file")
        parser.add_argument("--cloudflare-secret-name", help="Name of Cloudflare token secret")
        parser.add_argument("--cloudflare-secret-namespace", help="Namespace for Cloudflare token secret")
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--cloudflare-token-duplicate-namespace", help="Duplicate secret to this namespace")
        group.add_argument("--no-cloudflare-token-duplicate", action="store_true", help="Disable duplicating secret")

    def run(self, ctx: Context) -> None:
        args = ctx.args
        existing = ctx.existing_env

        token_file = (args.cloudflare_token_file or existing.get("CLOUDFLARE_API_TOKEN_FILE", "")).strip()
        secret_name = (args.cloudflare_secret_name or existing.get("CLOUDFLARE_API_TOKEN_SECRET_NAME", "cloudflare-api-token")).strip() or "cloudflare-api-token"
        secret_ns = (args.cloudflare_secret_namespace or existing.get("CLOUDFLARE_API_TOKEN_SECRET_NAMESPACE", "cert-manager")).strip() or "cert-manager"
        
        dup_was_set = args.cloudflare_token_duplicate_namespace is not None or "CLOUDFLARE_API_TOKEN_DUPLICATE_NAMESPACE" in existing
        dup_ns = args.cloudflare_token_duplicate_namespace if args.cloudflare_token_duplicate_namespace is not None else existing.get("CLOUDFLARE_API_TOKEN_DUPLICATE_NAMESPACE", "").strip()

        if token_file:
            p = Path(token_file).expanduser()
            if not p.exists() or not p.is_file():
                print(f"ERROR: Cloudflare token file not found: {p}", file=sys.stderr)
                sys.exit(2)
            token_file = str(p.resolve())
            
            ctx.set_env("CLOUDFLARE_API_TOKEN_FILE", token_file)
            ctx.set_env("CLOUDFLARE_API_TOKEN_SECRET_NAME", secret_name)
            ctx.set_env("CLOUDFLARE_API_TOKEN_SECRET_NAMESPACE", secret_ns)
            if dup_was_set:
                ctx.set_env("CLOUDFLARE_API_TOKEN_DUPLICATE_NAMESPACE", dup_ns)

            # Apply Kubernetes Secret
            token = p.read_text(encoding="utf-8").strip()
            if not token:
                print(f"ERROR: Cloudflare token file is empty: {p}", file=sys.stderr)
                sys.exit(2)
            
            kube_ctx = getattr(args, "kube_context", None)
            
            # Primary namespace
            _apply_kube_secret(
                namespace=secret_ns,
                secret_name=secret_name,
                data={"api-token": token},
                kube_context=kube_ctx
            )
            
            # Duplicate namespace
            if dup_ns and dup_ns != secret_ns:
                _apply_kube_secret(
                    namespace=dup_ns,
                    secret_name=secret_name,
                    data={"api-token": token},
                    kube_context=kube_ctx
                )


class RFC2136Module(BootstrapModule):
    def __init__(self, prefix: str = "", env_prefix: str = "", description: str = "RFC2136"):
        self.prefix = prefix
        self.env_prefix = env_prefix
        self.description = description

    def add_args(self, parser: argparse.ArgumentParser) -> None:
        p = self.prefix
        parser.add_argument(f"--{p}rfc2136-host", help=f"{self.description} host")
        if p == "cm-":
            parser.add_argument(f"--{p}rfc2136-port", help=f"{self.description} port")
        parser.add_argument(f"--{p}rfc2136-zone", help=f"{self.description} zone")
        parser.add_argument(f"--{p}rfc2136-tsig-keyname", help=f"{self.description} TSIG key name")
        parser.add_argument(f"--{p}rfc2136-tsig-alg", help=f"{self.description} TSIG algorithm")
        parser.add_argument(f"--{p}rfc2136-tsig-secret-file", help=f"{self.description} TSIG secret file")
        parser.add_argument(f"--{p}rfc2136-tsig-generate", action="store_true", help=f"Generate {self.description} secret")
        parser.add_argument(f"--{p}emit-bind9-snippet", action="store_true", help=f"Emit BIND9 snippet for {self.description}")
        parser.add_argument(f"--{p}apply-rfc2136-tsig-secret", action="store_true", help=f"Apply K8s secret for {self.description}")

    def run(self, ctx: Context) -> None:
        args = ctx.args
        existing = ctx.existing_env
        p = self.prefix.replace("-", "_")
        ep = self.env_prefix

        host = (getattr(args, f"{p}rfc2136_host") or existing.get(f"{ep}RFC2136_HOST", "")).strip()
        zone = (getattr(args, f"{p}rfc2136_zone") or existing.get(f"{ep}RFC2136_ZONE", "")).strip()
        keyname = (getattr(args, f"{p}rfc2136_tsig_keyname") or existing.get(f"{ep}RFC2136_TSIG_KEYNAME", "")).strip()
        alg = (getattr(args, f"{p}rfc2136_tsig_alg") or existing.get(f"{ep}RFC2136_TSIG_ALG", "")).strip()
        secret_file_raw = (getattr(args, f"{p}rfc2136_tsig_secret_file") or existing.get(f"{ep}RFC2136_TSIG_SECRET_FILE", "")).strip()

        port = ""
        if hasattr(args, f"{p}rfc2136_port"):
            port = (getattr(args, f"{p}rfc2136_port") or existing.get(f"{ep}RFC2136_PORT", "5335")).strip()

        secret_path: Path | None = None
        if secret_file_raw:
            path = Path(secret_file_raw).expanduser()
            secret_path = (ctx.repo_root / path).resolve() if not path.is_absolute() else path

        # Generate
        if getattr(args, f"{p}rfc2136_tsig_generate"):
            if not secret_path:
                fname = "rfc2136-tsig.secret" if not self.prefix else f"{self.prefix}rfc2136-tsig.secret"
                secret_path = (ctx.repo_root / "bootstrap" / "inputs" / fname).resolve()
                secret_file_raw = os.path.relpath(secret_path, ctx.repo_root)
            if not secret_path.exists():
                secret_b64 = _generate_tsig_secret_base64()
                _write_secret_file(secret_path, secret_b64)
                print(f">>> Generated {self.description} TSIG secret file: {secret_path}")
            else:
                print(f">>> {self.description} TSIG secret file exists: {secret_path}")

        # Emit BIND9
        if getattr(args, f"{p}emit_bind9_snippet"):
            if not (zone and keyname and alg and secret_path and secret_path.exists()):
                print(f"WARNING: Missing inputs for {self.description} BIND snippet.", file=sys.stderr)
            else:
                secret_b64 = _read_secret_file(secret_path)
                fname = "rfc2136-tsig.conf" if not self.prefix else f"{self.prefix}rfc2136-tsig.conf"
                out_path = (ctx.repo_root / "bootstrap" / "generated" / "bind9" / fname).resolve()
                policy = None
                if self.prefix == "cm-":
                    policy = f'grant {keyname} name _acme-challenge.*.{zone}. TXT;'
                _emit_bind9_snippet(out_path, zone, keyname, alg, secret_b64, update_policy=policy)
                print(f">>> Wrote {self.description} BIND9 snippet: {out_path}")

        # Apply K8s Secret
        if getattr(args, f"{p}apply_rfc2136_tsig_secret") and secret_path and secret_path.exists():
            if self.prefix == "cm-":
                ns, name, key = "cert-manager", "rfc2136-tsig", "tsig-secret"
            else:
                ns = (existing.get("RFC2136_TSIG_SECRET_NAMESPACE", "external-dns-internal")).strip()
                name = (existing.get("RFC2136_TSIG_SECRET_NAME", "rfc2136-tsig")).strip()
                key = (existing.get("RFC2136_TSIG_SECRET_KEY", "tsig-secret")).strip()
            
            kube_ctx = getattr(args, "kube_context", None)
            _apply_rfc2136_tsig_secret(ns, name, key, secret_path, kube_context=kube_ctx)

        # Set Env Values
        was_set = any([
            getattr(args, f"{p}rfc2136_host") is not None,
            getattr(args, f"{p}rfc2136_zone") is not None,
            getattr(args, f"{p}rfc2136_tsig_keyname") is not None,
            getattr(args, f"{p}rfc2136_tsig_alg") is not None,
            getattr(args, f"{p}rfc2136_tsig_secret_file") is not None,
            getattr(args, f"{p}rfc2136_tsig_generate"),
            f"{ep}RFC2136_HOST" in existing
        ])
        if was_set:
            if host: ctx.set_env(f"{ep}RFC2136_HOST", host)
            if port: ctx.set_env(f"{ep}RFC2136_PORT", port)
            if zone: ctx.set_env(f"{ep}RFC2136_ZONE", zone)
            if keyname: ctx.set_env(f"{ep}RFC2136_TSIG_KEYNAME", keyname)
            if alg: ctx.set_env(f"{ep}RFC2136_TSIG_ALG", alg)
            if secret_file_raw: ctx.set_env(f"{ep}RFC2136_TSIG_SECRET_FILE", secret_file_raw)


class ArgoCDModule(BootstrapModule):
    def add_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--argo-version", help="Argo CD Helm chart version")
        parser.add_argument("--skip-argo-install", action="store_true", help="Skip Argo CD installation")
        parser.add_argument("--cm-crds-version", help="cert-manager version for CRDs")
        parser.add_argument("--cm-crds-mode", choices=["release", "helm-template"], help="How to install cert-manager CRDs")

    def run(self, ctx: Context) -> None:
        args = ctx.args
        existing = ctx.existing_env
        
        argo_version = (args.argo_version or existing.get("ARGO_HELM_CHART_VERSION", "7.7.12")).strip()
        cm_version = (args.cm_crds_version or existing.get("CERT_MANAGER_VERSION", "v1.14.4")).strip()
        cm_mode = (args.cm_crds_mode or existing.get("CERT_MANAGER_CRDS_MODE", "release")).strip()
        
        ctx.set_env("ARGO_HELM_CHART_VERSION", argo_version)
        ctx.set_env("CERT_MANAGER_VERSION", cm_version)
        ctx.set_env("CERT_MANAGER_CRDS_MODE", cm_mode)

        if not args.skip_argo_install:
            self._install_cm_crds(ctx, cm_version, cm_mode)
            self._install_argo(ctx, argo_version)

        if ctx.env_values.get("APPLY_ROOT_APP") == "true":
            self._apply_root_app(ctx)

    def _install_cm_crds(self, ctx: Context, version: str, mode: str) -> None:
        print(f">>> Installing cert-manager CRDs only ({version}, mode={mode})")
        kube_ctx = getattr(ctx.args, "kube_context", None)
        _ensure_namespace("cert-manager", kube_context=kube_ctx)

        if mode == "release":
            url = f"https://github.com/cert-manager/cert-manager/releases/download/{version}/cert-manager.crds.yaml"
            cmd = ["kubectl"]
            if kube_ctx:
                cmd += ["--context", kube_ctx]
            cmd += ["apply", "-f", url]
            subprocess.run(cmd, check=True)
        elif mode == "helm-template":
            repo_cmd = ["helm", "repo", "add", "jetstack", "https://charts.jetstack.io"]
            subprocess.run(repo_cmd, check=True, capture_output=True)
            subprocess.run(["helm", "repo", "update"], check=True, capture_output=True)
            
            template_cmd = [
                "helm", "template", "cert-manager-crds", "jetstack/cert-manager",
                "--version", version.lstrip("v"),
                "--namespace", "cert-manager",
                "--include-crds"
            ]
            template_proc = subprocess.run(template_cmd, check=True, capture_output=True, text=True)
            
            apply_cmd = ["kubectl"]
            if kube_ctx:
                apply_cmd += ["--context", kube_ctx]
            apply_cmd += ["apply", "-f", "-"]
            subprocess.run(apply_cmd, check=True, input=template_proc.stdout, text=True)
        
        print(">>> Waiting for cert-manager CRDs to be Established")
        get_cmd = ["kubectl", "get", "crd", "-o", "name"]
        if kube_ctx:
            get_cmd = ["kubectl", "--context", kube_ctx, "get", "crd", "-o", "name"]
        
        res = subprocess.run(get_cmd, check=True, capture_output=True, text=True)
        cm_crds = [line for line in res.stdout.splitlines() if line.endswith(".cert-manager.io")]
        
        if cm_crds:
            wait_cmd = ["kubectl"]
            if kube_ctx:
                wait_cmd += ["--context", kube_ctx]
            wait_cmd += ["wait", "--for=condition=Established", "--timeout=60s"] + cm_crds
            subprocess.run(wait_cmd, check=True, capture_output=True)

    def _install_argo(self, ctx: Context, version: str) -> None:
        argo_ns = ctx.env_values.get("ARGO_NAMESPACE", "argocd")
        print(f">>> Installing Argo CD via Helm (chart {version}) into namespace {argo_ns}")
        
        subprocess.run(["helm", "repo", "add", "argo", "https://argoproj.github.io/argo-helm"], check=True, capture_output=True)
        subprocess.run(["helm", "repo", "update"], check=True, capture_output=True)
        
        kube_ctx = getattr(ctx.args, "kube_context", None)
        _ensure_namespace(argo_ns, kube_context=kube_ctx)

        # Build Helm values arguments
        values_args = []
        repo_root = ctx.repo_root
        org_slug = ctx.env_values.get("ORG_SLUG")
        env_name = ctx.env_values.get("ENV")
        
        base_v = repo_root / "bootstrap" / "argocd" / "values.yaml"
        org_v = repo_root / "bootstrap" / "argocd" / f"values.{org_slug}.yaml"
        env_v = repo_root / "bootstrap" / "argocd" / f"values.{env_name}.yaml"
        
        if base_v.exists():
            values_args += ["-f", str(base_v)]
        if org_v.exists():
            values_args += ["-f", str(org_v)]
        if env_v.exists():
            values_args += ["-f", str(env_v)]
            
        upgrade_cmd = ["helm"]
        if kube_ctx:
            # Helm doesn't have a global --context like kubectl, it uses --kube-context
            upgrade_cmd += ["--kube-context", kube_ctx]
            
        upgrade_cmd += [
            "upgrade", "--install", "argocd", "argo/argo-cd",
            "--namespace", argo_ns,
            "--create-namespace",
            "--version", version,
            "--set", "dex.enabled=false",
            "--wait"
        ] + values_args
        
        subprocess.run(upgrade_cmd, check=True)
        print(">>> Argo CD install complete")

    def _apply_root_app(self, ctx: Context) -> None:
        root_app_path = ctx.env_values.get("ROOT_APP_PATH")
        if not root_app_path:
            return
            
        p = ctx.repo_root / root_app_path
        if not p.exists():
            print(f"ERROR: root app not found at {p}", file=sys.stderr)
            sys.exit(1)
            
        # Check if it's empty or doesn't have a kind
        content = p.read_text(encoding="utf-8")
        if not re.search(r"kind:\s*\S+", content):
            print(f">>> Root app {root_app_path} is empty; skipping apply.")
            return

        print(f">>> Applying root app: {root_app_path}")
        kube_ctx = getattr(ctx.args, "kube_context", None)
        apply_cmd = ["kubectl"]
        if kube_ctx:
            apply_cmd += ["--context", kube_ctx]
        apply_cmd += ["apply", "-f", str(p)]
        subprocess.run(apply_cmd, check=True)
        print(">>> Root app applied.")


class ValidationModule(BootstrapModule):
    def add_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--skip-validation", action="store_true", help="Skip tool and connectivity validation")

    def run(self, ctx: Context) -> None:
        if ctx.args.skip_validation:
            return

        print(">>> Running pre-flight validation")
        self._check_tools(["kubectl", "helm"])
        self._check_kube_connectivity(ctx)

    def _check_tools(self, tools: List[str]) -> None:
        for tool in tools:
            if not shutil.which(tool):
                print(f"ERROR: '{tool}' not found in PATH. Please install it.", file=sys.stderr)
                sys.exit(1)
            print(f"  ✓ {tool} found")

    def _check_kube_connectivity(self, ctx: Context) -> None:
        cmd = ["kubectl"]
        kube_ctx = getattr(ctx.args, "kube_context", None)
        if kube_ctx:
            cmd += ["--context", kube_ctx]
        cmd += ["cluster-info"]
        
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            context_str = kube_ctx if kube_ctx else "default"
            print(f"  ✓ Kubernetes connectivity verified (context: {context_str})")
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to connect to Kubernetes: {e.stderr}", file=sys.stderr)
            sys.exit(1)



class MetalLBModule(BootstrapModule):
    def add_args(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--with-metallb", action="store_true", help="Install MetalLB (CRDs + controller) via Helm")
        parser.add_argument("--metallb-namespace", default=None, help="MetalLB namespace (default: metallb-system)")
        parser.add_argument("--metallb-release", default=None, help="Helm release name (default: metallb)")
        parser.add_argument("--metallb-chart-version", default=None, help="Pinned MetalLB chart version (default: 0.14.5)")
        # Optional: keep chart repo override hooks if you ever need them
        parser.add_argument("--metallb-chart-repo", default=None, help="Helm repo URL (default: https://metallb.github.io/metallb)")
        parser.add_argument("--metallb-chart", default=None, help="Chart ref (default: metallb/metallb)")

    def run(self, ctx: Context) -> None:
        if not ctx.args.with_metallb:
            return

        # Defaults aligned with bootstrap/metallb/metallb.sh
        ns = (ctx.args.metallb_namespace or ctx.get_existing("METALLB_NAMESPACE", "metallb-system")).strip() or "metallb-system"
        release = (ctx.args.metallb_release or ctx.get_existing("METALLB_RELEASE", "metallb")).strip() or "metallb"
        repo = (ctx.args.metallb_chart_repo or ctx.get_existing("METALLB_CHART_REPO", "https://metallb.github.io/metallb")).strip()
        chart = (ctx.args.metallb_chart or ctx.get_existing("METALLB_CHART", "metallb/metallb")).strip() or "metallb/metallb"
        version = (ctx.args.metallb_chart_version or ctx.get_existing("METALLB_CHART_VERSION", "0.14.5")).strip() or "0.14.5"

        # Persist for repeatability
        ctx.set_env("METALLB_ENABLED", "true")
        ctx.set_env("METALLB_NAMESPACE", ns)
        ctx.set_env("METALLB_RELEASE", release)
        ctx.set_env("METALLB_CHART_REPO", repo)
        ctx.set_env("METALLB_CHART", chart)
        ctx.set_env("METALLB_CHART_VERSION", version)

        print(">>> Verifying cluster access (MetalLB)")
        _kubectl(ctx, "cluster-info", capture=True)

        print(f">>> Ensuring namespace: {ns}")
        _ensure_namespace(ns, kube_context=getattr(ctx.args, "kube_context", None))

        print(">>> Ensuring Helm repo: metallb")
        # Avoid failing if already added
        try:
            _helm(ctx, "repo", "add", "metallb", repo, capture=True)
        except subprocess.CalledProcessError:
            pass
        _helm(ctx, "repo", "update", capture=True)

        print(f">>> Installing/upgrading MetalLB ({chart} @ {version})")
        _helm(
            ctx,
            "upgrade", "--install", release, chart,
            "--namespace", ns,
            "--version", version,
            "--force-conflicts",
            "--wait",
            "--timeout", "5m0s",
        )

        print(">>> Waiting for MetalLB controller rollout")
        _kubectl(ctx, "-n", ns, "rollout", "status", "deployment/metallb-controller", "--timeout=5m", capture=True)

        print(">>> Waiting for MetalLB CRDs to be Established (if present)")
        crds = [
            "ipaddresspools.metallb.io",
            "bgppeers.metallb.io",
            "bgpadvertisements.metallb.io",
            "l2advertisements.metallb.io",
            "communities.metallb.io",
            "bfdprofiles.metallb.io",
        ]
        for crd in crds:
            # mirror shell behavior: only wait if CRD exists
            exists = subprocess.run(
                ["kubectl"] + (["--context", ctx.args.kube_context] if ctx.args.kube_context else []) + ["get", "crd", crd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0
            if exists:
                _kubectl(ctx, "wait", "--for=condition=Established", f"crd/{crd}", "--timeout=2m", capture=True)

        print(f"✅ MetalLB installed: release={release} namespace={ns} version={version}")
        print("Next: manage IPAddressPool/L2Advertisement via GitOps-root.")

# ---- Main --------------------------------------------------------------------

def main() -> int:
    modules: List[BootstrapModule] = [
        ValidationModule(),
        CoreModule(),
        SSHModule(),
        CloudflareModule(),
        RFC2136Module(description="RFC2136 (external-dns)"),
        RFC2136Module(prefix="cm-", env_prefix="CM_", description="RFC2136 (cert-manager)"),
        ArgoCDModule(),
        MetalLBModule(),
    ]

    p = argparse.ArgumentParser(
        prog="bootstrap.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Generate an env file and run bootstrap/argocd/bootstrap.sh.",
    )
    p.add_argument("--env-file", help="Path to write/read env file")
    p.add_argument("--env-name", help="Name of environment (resolves to bootstrap/env/<name>.env)")
    p.add_argument("--non-interactive", action="store_true", help="Do not prompt")
    p.add_argument("--yes", action="store_true", help="Accept defaults without prompting")
    p.add_argument("--kube-context", help="Optional kubectl context")

    for m in modules:
        m.add_args(p)
    
    args = p.parse_args()

    here = Path(__file__).resolve()
    repo_root = _find_repo_root(here.parent)
    
    # Environment file resolution
    if args.env_name:
        env_path = repo_root / "bootstrap" / "env" / f"{args.env_name}.env"
    elif args.env_file:
        env_path = Path(args.env_file)
    else:
        env_path = repo_root / "bootstrap" / "env" / "kubeadm.env"

    if not env_path.is_absolute():
        env_path = (repo_root / env_path).resolve()

    ctx = Context(repo_root=repo_root, args=args, existing_env=_parse_env_file(env_path))

    for m in modules:
        m.run(ctx)

    _write_env_file(env_path, ctx.env_values)

    print("\n=== Summary ===")
    print(f"Env file:        {env_path}")
    for k in ["ORG_SLUG", "ENV", "ARGO_NAMESPACE", "ROOT_APP_PATH"]:
        print(f"{k:<16} {ctx.env_values.get(k)}")
    if ctx.env_values.get("GITHUB_REPO"):
        print(f"REPO_VISIBILITY: {ctx.env_values.get('REPO_VISIBILITY')}")
        print(f"GITHUB_REPO:     {ctx.env_values.get('GITHUB_REPO')}")
        print(f"REPO_REF:        {ctx.env_values.get('REPO_REF')}")
        print(f"GIT_REPO_URL:    {ctx.env_values.get('GIT_REPO_URL')}")
    else:
        print(f"GITHUB_REPO:     (not set)")
        print(f"REPO_REF:        {ctx.env_values.get('REPO_REF')}")
    if ctx.env_values.get("SSH_KNOWN_HOSTS_FILE"):
        print(f"KNOWN_HOSTS:     {ctx.env_values.get('SSH_KNOWN_HOSTS_FILE')}")
    print("")
    print(">>> Bootstrap complete (Argo CD and core components handled by Python).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
