from dataclasses import dataclass

from config import BootstrapConfig


@dataclass
class ResourceNames:
    prefix: str
    cluster_name: str
    vpc_name: str
    node_group_name: str


def build_names(config: BootstrapConfig) -> ResourceNames:
    prefix = f"{config.org_name}-{config.system_name}-{config.environment}"

    return ResourceNames(
        prefix=prefix,
        cluster_name=f"{prefix}-eks",
        vpc_name=f"{prefix}-vpc",
        node_group_name=f"{prefix}-ng",
    )