from dataclasses import dataclass
import ipaddress
from typing import Dict, List, Literal, cast

import pulumi

NodeArch = Literal["arm64", "amd64"]
NatGatewayStrategy = Literal["single", "per-az"]


@dataclass
class BootstrapConfig:
    aws_region: str
    org_name: str
    environment: str
    system_name: str

    vpc_cidr: str
    availability_zone_count: int
    public_subnet_cidrs: List[str] | None
    private_subnet_cidrs: List[str] | None

    kubernetes_version: str
    cluster_endpoint_private_access: bool
    cluster_endpoint_public_access: bool
    cluster_public_access_cidrs: List[str]

    node_arch: NodeArch
    arm_instance_types: List[str]
    amd_instance_types: List[str]
    node_desired_size: int
    node_min_size: int
    node_max_size: int
    node_disk_size: int

    nat_gateway_strategy: NatGatewayStrategy
    office_lan_cidrs: List[str]
    enable_wireguard: bool
    wireguard_allowed_cidrs: List[str]
    wireguard_instance_type: str
    wireguard_ami_arch: NodeArch
    wireguard_ami_id: str | None
    wireguard_ssh_key_name: str | None
    wireguard_tunnel_cidr: str

    tags: Dict[str, str]


@dataclass
class NodeProfile:
    arch: NodeArch
    ami_type: str
    instance_types: List[str]


def _require_int(cfg: pulumi.Config, name: str, default: int) -> int:
    value = cfg.get_int(name)
    return value if value is not None else default


def _get_bool(cfg: pulumi.Config, name: str, default: bool) -> bool:
    value = cfg.get_bool(name)
    return default if value is None else value


def _get_string_list(cfg: pulumi.Config, name: str, default: List[str]) -> List[str]:
    value = cfg.get_object(name)
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Config value '{name}' must be a list of strings.")
    return cast(List[str], value)


def _get_optional_string_list(cfg: pulumi.Config, name: str) -> List[str] | None:
    value = cfg.get_object(name)
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"Config value '{name}' must be a list of strings.")
    typed = cast(List[str], value)
    return typed if typed else None


def _validate_subnet_overrides(
    vpc_cidr: str,
    availability_zone_count: int,
    public_subnet_cidrs: List[str] | None,
    private_subnet_cidrs: List[str] | None,
) -> None:
    if (public_subnet_cidrs is None) != (private_subnet_cidrs is None):
        raise ValueError(
            "bootstrap:publicSubnetCidrs and bootstrap:privateSubnetCidrs must both be set or both be omitted."
        )

    if public_subnet_cidrs is None or private_subnet_cidrs is None:
        return

    if len(public_subnet_cidrs) != availability_zone_count:
        raise ValueError(
            f"bootstrap:publicSubnetCidrs must contain exactly {availability_zone_count} entries."
        )
    if len(private_subnet_cidrs) != availability_zone_count:
        raise ValueError(
            f"bootstrap:privateSubnetCidrs must contain exactly {availability_zone_count} entries."
        )

    vpc_network = ipaddress.ip_network(vpc_cidr)
    all_subnets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in public_subnet_cidrs + private_subnet_cidrs:
        subnet = ipaddress.ip_network(cidr)
        if not subnet.subnet_of(vpc_network):
            raise ValueError(f"Subnet '{cidr}' is not contained in VPC CIDR '{vpc_cidr}'.")
        for existing in all_subnets:
            if subnet.overlaps(existing):
                raise ValueError(f"Subnet '{cidr}' overlaps with subnet '{existing}'.")
        all_subnets.append(subnet)


def get_bootstrap_config() -> BootstrapConfig:
    cfg = pulumi.Config("bootstrap")
    aws_cfg = pulumi.Config("aws")

    org_name = cfg.get("orgName") or "blackcircuit"
    environment = cfg.get("environment") or "dev"
    system_name = cfg.get("systemName") or "platform"

    tags = {
        "managedBy": "pulumi",
        "project": pulumi.get_project(),
        "stack": pulumi.get_stack(),
        "org": org_name,
        "env": environment,
        "system": system_name,
    }

    availability_zone_count = _require_int(cfg, "availabilityZoneCount", 2)
    if availability_zone_count < 1:
        raise ValueError("bootstrap:availabilityZoneCount must be at least 1.")
    vpc_cidr = cfg.get("vpcCidr") or "10.42.0.0/16"

    public_subnet_cidrs = _get_optional_string_list(cfg, "publicSubnetCidrs")
    private_subnet_cidrs = _get_optional_string_list(cfg, "privateSubnetCidrs")
    _validate_subnet_overrides(
        vpc_cidr=vpc_cidr,
        availability_zone_count=availability_zone_count,
        public_subnet_cidrs=public_subnet_cidrs,
        private_subnet_cidrs=private_subnet_cidrs,
    )

    cluster_endpoint_private_access = _get_bool(cfg, "clusterEndpointPrivateAccess", True)
    cluster_endpoint_public_access = _get_bool(cfg, "clusterEndpointPublicAccess", False)
    if not cluster_endpoint_private_access and not cluster_endpoint_public_access:
        raise ValueError(
            "Cluster endpoint would be unreachable. "
            "Set either bootstrap:clusterEndpointPrivateAccess or bootstrap:clusterEndpointPublicAccess."
        )

    office_lan_cidrs = _get_string_list(cfg, "officeLanCidrs", [])
    if cluster_endpoint_public_access:
        cluster_public_access_cidrs = _get_string_list(
            cfg,
            "clusterPublicAccessCidrs",
            office_lan_cidrs,
        )
        if not cluster_public_access_cidrs:
            raise ValueError(
                "bootstrap:clusterPublicAccessCidrs (or bootstrap:officeLanCidrs) must be set when "
                "bootstrap:clusterEndpointPublicAccess is true."
            )
    else:
        # Ignore any configured public CIDR list when public endpoint access is disabled.
        cluster_public_access_cidrs = []

    node_arch_raw = (cfg.get("nodeArch") or "arm64").lower()
    if node_arch_raw not in ("arm64", "amd64"):
        raise ValueError("bootstrap:nodeArch must be one of: arm64, amd64.")
    node_arch = cast(NodeArch, node_arch_raw)

    node_desired_size = _require_int(cfg, "nodeDesiredSize", 1)
    node_min_size = _require_int(cfg, "nodeMinSize", 1)
    node_max_size = _require_int(cfg, "nodeMaxSize", 2)
    if not (0 <= node_min_size <= node_desired_size <= node_max_size):
        raise ValueError(
            "Node scaling must satisfy: 0 <= nodeMinSize <= nodeDesiredSize <= nodeMaxSize."
        )

    node_disk_size = _require_int(cfg, "nodeDiskSize", 20)
    if node_disk_size < 20:
        raise ValueError("bootstrap:nodeDiskSize must be at least 20 GiB for EKS managed node groups.")

    nat_gateway_strategy_raw = (cfg.get("natGatewayStrategy") or "single").lower()
    if nat_gateway_strategy_raw not in ("single", "per-az"):
        raise ValueError("bootstrap:natGatewayStrategy must be either 'single' or 'per-az'.")
    nat_gateway_strategy = cast(NatGatewayStrategy, nat_gateway_strategy_raw)

    enable_wireguard = _get_bool(cfg, "enableWireGuard", False)
    wireguard_allowed_cidrs = _get_string_list(cfg, "wireGuardAllowedCidrs", office_lan_cidrs)
    if enable_wireguard and not wireguard_allowed_cidrs:
        raise ValueError(
            "bootstrap:wireGuardAllowedCidrs (or bootstrap:officeLanCidrs) must be set when "
            "bootstrap:enableWireGuard is true."
        )
    wireguard_instance_type = cfg.get("wireGuardInstanceType") or "t4g.nano"
    wireguard_ami_arch_raw = (cfg.get("wireGuardAmiArch") or "arm64").lower()
    if wireguard_ami_arch_raw not in ("arm64", "amd64"):
        raise ValueError("bootstrap:wireGuardAmiArch must be one of: arm64, amd64.")
    wireguard_ami_arch = cast(NodeArch, wireguard_ami_arch_raw)
    wireguard_ami_id = cfg.get("wireGuardAmiId") or None
    wireguard_ssh_key_name = cfg.get("wireGuardSshKeyName") or None
    wireguard_tunnel_cidr = cfg.get("wireGuardTunnelCidr") or "10.200.10.0/24"
    try:
        tunnel_network = ipaddress.ip_network(wireguard_tunnel_cidr)
    except ValueError as exc:
        raise ValueError("bootstrap:wireGuardTunnelCidr must be a valid CIDR block.") from exc
    if tunnel_network.overlaps(ipaddress.ip_network(vpc_cidr)):
        raise ValueError(
            "bootstrap:wireGuardTunnelCidr must not overlap bootstrap:vpcCidr."
        )

    return BootstrapConfig(
        aws_region=aws_cfg.get("region") or "ca-west-1",
        org_name=org_name,
        environment=environment,
        system_name=system_name,
        vpc_cidr=vpc_cidr,
        availability_zone_count=availability_zone_count,
        public_subnet_cidrs=public_subnet_cidrs,
        private_subnet_cidrs=private_subnet_cidrs,
        kubernetes_version=cfg.get("kubernetesVersion") or "1.33",
        cluster_endpoint_private_access=cluster_endpoint_private_access,
        cluster_endpoint_public_access=cluster_endpoint_public_access,
        cluster_public_access_cidrs=cluster_public_access_cidrs,
        node_arch=node_arch,
        arm_instance_types=_get_string_list(cfg, "armInstanceTypes", ["t4g.small"]),
        amd_instance_types=_get_string_list(cfg, "amdInstanceTypes", ["t3.small"]),
        node_desired_size=node_desired_size,
        node_min_size=node_min_size,
        node_max_size=node_max_size,
        node_disk_size=node_disk_size,
        nat_gateway_strategy=nat_gateway_strategy,
        office_lan_cidrs=office_lan_cidrs,
        enable_wireguard=enable_wireguard,
        wireguard_allowed_cidrs=wireguard_allowed_cidrs,
        wireguard_instance_type=wireguard_instance_type,
        wireguard_ami_arch=wireguard_ami_arch,
        wireguard_ami_id=wireguard_ami_id,
        wireguard_ssh_key_name=wireguard_ssh_key_name,
        wireguard_tunnel_cidr=wireguard_tunnel_cidr,
        tags=tags,
    )


def get_node_profile(config: BootstrapConfig) -> NodeProfile:
    if config.node_arch == "arm64":
        return NodeProfile(
            arch="arm64",
            ami_type="AL2023_ARM_64_STANDARD",
            instance_types=config.arm_instance_types,
        )

    return NodeProfile(
        arch="amd64",
        ami_type="AL2023_X86_64_STANDARD",
        instance_types=config.amd_instance_types,
    )
