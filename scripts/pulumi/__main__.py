from pathlib import Path
import subprocess

import pulumi
import pulumi_kubernetes as k8s

from cluster import ClusterOutputs, create_cluster
from config import get_bootstrap_config
from naming import build_names
from network import create_network
from wireguard import create_wireguard_gateway


def _secret_from_file(path_value: str, config_key: str) -> pulumi.Output[str]:
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise ValueError(f"{config_key} points to a missing file: {path}")
    return pulumi.Output.secret(path.read_text(encoding="utf-8"))


def _extract_ssh_host(repo_url: str) -> str | None:
    # Supports SSH-style URLs like git@github.com:org/repo.git and ssh://git@github.com/org/repo.git.
    if repo_url.startswith("ssh://"):
        without_scheme = repo_url[len("ssh://") :]
        host_part = without_scheme.split("/", 1)[0]
        if "@" in host_part:
            host_part = host_part.split("@", 1)[1]
        return host_part.split(":", 1)[0] if host_part else None

    if "@" in repo_url and ":" in repo_url:
        after_at = repo_url.split("@", 1)[1]
        host = after_at.split(":", 1)[0]
        return host or None

    return None


def _scan_known_hosts(repo_url: str) -> pulumi.Output[str]:
    host = _extract_ssh_host(repo_url)
    if host is None:
        raise ValueError(
            "Could not infer SSH host from bootstrap:argoRepoUrl. "
            "Set bootstrap:argoRepoKnownHosts / bootstrap:argoRepoKnownHostsFile explicitly."
        )

    cmd = ["ssh-keyscan", "-t", "rsa,ecdsa,ed25519", host]
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise ValueError(
            "ssh-keyscan was not found on PATH. "
            "Install OpenSSH client or set bootstrap:argoRepoKnownHosts / bootstrap:argoRepoKnownHostsFile."
        ) from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() if exc.stderr else "no stderr"
        raise ValueError(
            f"ssh-keyscan failed for host '{host}': {stderr}. "
            "Set bootstrap:argoRepoKnownHosts / bootstrap:argoRepoKnownHostsFile explicitly."
        ) from exc

    known_hosts = result.stdout.strip()
    if not known_hosts:
        raise ValueError(
            f"ssh-keyscan returned no host keys for '{host}'. "
            "Set bootstrap:argoRepoKnownHosts / bootstrap:argoRepoKnownHostsFile explicitly."
        )
    return pulumi.Output.secret(known_hosts + "\n")


def _resolve_secret(
    cfg: pulumi.Config,
    secret_key: str,
    file_key: str,
) -> pulumi.Output[str] | None:
    inline_value = cfg.get(secret_key)
    inline_value_is_set = inline_value is not None and inline_value.strip() != ""
    secret_value = cfg.get_secret(secret_key) if inline_value_is_set else None
    file_value = cfg.get(file_key)
    file_value_is_set = file_value is not None and file_value.strip() != ""

    if secret_value is not None and file_value_is_set:
        pulumi.log.warn(f"Both bootstrap:{secret_key} and bootstrap:{file_key} are set; using bootstrap:{secret_key}.")

    if secret_value is not None:
        return secret_value
    if not file_value_is_set:
        return None
    return _secret_from_file(file_value, f"bootstrap:{file_key}")


def create_bootstrap_secrets(config, platform: ClusterOutputs) -> None:
    cfg = pulumi.Config("bootstrap")

    argo_repo_url = cfg.get("argoRepoUrl") or "git@github.com:blackcircuit-brian/blackcircuit-infra.git"
    argo_repo_ssh_private_key = _resolve_secret(
        cfg,
        "argoRepoSshPrivateKey",
        "argoRepoSshPrivateKeyFile",
    )
    argo_repo_known_hosts = _resolve_secret(
        cfg,
        "argoRepoKnownHosts",
        "argoRepoKnownHostsFile",
    )
    argo_repo_known_hosts_auto_scan = cfg.get_bool("argoRepoKnownHostsAutoScan")
    if argo_repo_known_hosts_auto_scan is None:
        argo_repo_known_hosts_auto_scan = True

    sops_age_key = _resolve_secret(cfg, "sopsAgeKey", "sopsAgeKeyFile")

    provider = k8s.Provider(
        "bootstrap-k8s",
        kubeconfig=pulumi.Output.json_dumps(platform.kubeconfig),
        opts=pulumi.ResourceOptions(
            depends_on=[platform.cluster, platform.node_group],
        ),
    )

    argocd_namespace_name = f"argocd-{config.environment}"
    argocd_namespace = k8s.core.v1.Namespace(
        f"{config.environment}-argocd-namespace",
        metadata={"name": argocd_namespace_name},
        opts=pulumi.ResourceOptions(
            provider=provider,
            depends_on=[platform.cluster, platform.node_group],
        ),
    )

    if (
        argo_repo_ssh_private_key is not None
        and argo_repo_known_hosts is None
        and argo_repo_known_hosts_auto_scan
    ):
        pulumi.log.info("bootstrap:argoRepoKnownHosts not set; scanning known_hosts from bootstrap:argoRepoUrl.")
        argo_repo_known_hosts = _scan_known_hosts(argo_repo_url)

    if argo_repo_ssh_private_key is not None and argo_repo_known_hosts is not None:
        k8s.core.v1.Secret(
            f"{config.environment}-argocd-repo-git-ssh",
            metadata={
                "name": "repo-git-ssh",
                "namespace": argocd_namespace_name,
                "labels": {
                    "argocd.argoproj.io/secret-type": "repository",
                },
            },
            type="Opaque",
            string_data={
                "url": argo_repo_url,
                "type": "git",
                "sshPrivateKey": argo_repo_ssh_private_key,
                "known_hosts": argo_repo_known_hosts,
            },
            opts=pulumi.ResourceOptions(provider=provider, depends_on=[argocd_namespace]),
        )
    elif argo_repo_ssh_private_key is not None or argo_repo_known_hosts is not None:
        pulumi.log.warn(
            "Both bootstrap:argoRepoSshPrivateKey (or ...File) and bootstrap:argoRepoKnownHosts (or ...File) are required to create argocd repo-git-ssh secret."
        )

    if sops_age_key is not None:
        k8s.core.v1.Secret(
            f"{config.environment}-argocd-sops-age",
            metadata={
                "name": "sops-age",
                "namespace": argocd_namespace_name,
            },
            type="Opaque",
            string_data={
                "keys.txt": sops_age_key,
            },
            opts=pulumi.ResourceOptions(provider=provider, depends_on=[argocd_namespace]),
        )

config = get_bootstrap_config()
names = build_names(config)

network = create_network(config, names)
platform = create_cluster(config, names, network)
wireguard = create_wireguard_gateway(
    config,
    names,
    network,
    platform.cluster.cluster_security_group_id,
) if config.enable_wireguard else None

create_bootstrap_secrets(config, platform)

pulumi.export("awsRegion", config.aws_region)
pulumi.export("vpcId", network.vpc.id)
pulumi.export("publicSubnetIds", network.public_subnet_ids)
pulumi.export("privateSubnetIds", network.private_subnet_ids)
pulumi.export("clusterName", platform.cluster.eks_cluster.name)
pulumi.export("clusterSecurityGroupId", platform.cluster.cluster_security_group_id)
pulumi.export("kubeconfig", pulumi.Output.secret(platform.kubeconfig))
pulumi.export("wireGuardEnabled", config.enable_wireguard)
pulumi.export("wireGuardInstanceId", wireguard.instance_id if wireguard else None)
pulumi.export("wireGuardPublicIp", wireguard.public_ip if wireguard else None)
pulumi.export("wireGuardSecurityGroupId", wireguard.security_group_id if wireguard else None)
