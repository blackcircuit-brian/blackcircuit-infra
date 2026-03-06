import pulumi

from cluster import create_cluster
from config import get_bootstrap_config
from naming import build_names
from network import create_network
from wireguard import create_wireguard_gateway

config = get_bootstrap_config()
names = build_names(config)

network = create_network(config, names)
platform = create_cluster(config, names, network)
wireguard = create_wireguard_gateway(config, names, network) if config.enable_wireguard else None

pulumi.export("awsRegion", config.aws_region)
pulumi.export("vpcId", network.vpc.id)
pulumi.export("publicSubnetIds", network.public_subnet_ids)
pulumi.export("privateSubnetIds", network.private_subnet_ids)
pulumi.export("clusterName", platform.cluster.eks_cluster.name)
pulumi.export("kubeconfig", pulumi.Output.secret(platform.kubeconfig))
pulumi.export("wireGuardEnabled", config.enable_wireguard)
pulumi.export("wireGuardInstanceId", wireguard.instance_id if wireguard else None)
pulumi.export("wireGuardPublicIp", wireguard.public_ip if wireguard else None)
pulumi.export("wireGuardSecurityGroupId", wireguard.security_group_id if wireguard else None)
