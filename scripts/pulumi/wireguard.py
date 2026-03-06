from dataclasses import dataclass

import pulumi
import pulumi_aws as aws

from config import BootstrapConfig
from naming import ResourceNames
from network import NetworkOutputs


@dataclass
class WireGuardOutputs:
    instance_id: pulumi.Output[str]
    public_ip: pulumi.Output[str]
    security_group_id: pulumi.Output[str]


def _resolve_ami(config: BootstrapConfig) -> pulumi.Output[str] | str:
    if config.wireguard_ami_id:
        return config.wireguard_ami_id

    # Use AWS-managed SSM aliases so this keeps working across AL2023 release naming changes.
    parameter_name = (
        "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
        if config.wireguard_ami_arch == "arm64"
        else "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
    )
    parameter = aws.ssm.get_parameter(name=parameter_name)
    return parameter.value


def create_wireguard_gateway(
    config: BootstrapConfig,
    names: ResourceNames,
    network: NetworkOutputs,
    cluster_security_group_id: pulumi.Input[str],
) -> WireGuardOutputs:
    ami_id = _resolve_ami(config)
    user_data = """#!/bin/bash
set -euxo pipefail
dnf install -y wireguard-tools
cat >/etc/sysctl.d/99-wireguard.conf <<'EOF'
net.ipv4.ip_forward=1
net.ipv6.conf.all.forwarding=1
EOF
sysctl --system
"""

    role = aws.iam.Role(
        f"{names.prefix}-wg-role",
        assume_role_policy=aws.iam.get_policy_document(
            statements=[
                aws.iam.GetPolicyDocumentStatementArgs(
                    actions=["sts:AssumeRole"],
                    principals=[
                        aws.iam.GetPolicyDocumentStatementPrincipalArgs(
                            type="Service",
                            identifiers=["ec2.amazonaws.com"],
                        )
                    ],
                )
            ]
        ).json,
        tags=config.tags,
    )

    aws.iam.RolePolicyAttachment(
        f"{names.prefix}-wg-ssm-core",
        role=role.name,
        policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    )

    profile = aws.iam.InstanceProfile(
        f"{names.prefix}-wg-profile",
        role=role.name,
        tags=config.tags,
    )

    security_group = aws.ec2.SecurityGroup(
        f"{names.prefix}-wg-sg",
        vpc_id=network.vpc.id,
        ingress=[
            aws.ec2.SecurityGroupIngressArgs(
                protocol="-1",
                from_port=0,
                to_port=0,
                cidr_blocks=[config.vpc_cidr],
                description="Allow VPC-routed traffic through gateway",
            ),
            aws.ec2.SecurityGroupIngressArgs(
                protocol="udp",
                from_port=51820,
                to_port=51820,
                cidr_blocks=config.wireguard_allowed_cidrs,
                description="WireGuard tunnel ingress",
            )
        ],
        egress=[
            aws.ec2.SecurityGroupEgressArgs(
                protocol="-1",
                from_port=0,
                to_port=0,
                cidr_blocks=["0.0.0.0/0"],
            )
        ],
        tags={
            **config.tags,
            "Name": f"{names.prefix}-wg-sg",
        },
    )

    aws.ec2.SecurityGroupRule(
        f"{names.prefix}-wg-to-eks-api-443",
        type="ingress",
        security_group_id=cluster_security_group_id,
        protocol="tcp",
        from_port=443,
        to_port=443,
        source_security_group_id=security_group.id,
        description="Allow EKS API access from WireGuard gateway",
    )

    instance = aws.ec2.Instance(
        f"{names.prefix}-wg",
        ami=ami_id,
        instance_type=config.wireguard_instance_type,
        subnet_id=network.public_subnet_ids.apply(lambda subnet_ids: subnet_ids[0]),
        vpc_security_group_ids=[security_group.id],
        iam_instance_profile=profile.name,
        key_name=config.wireguard_ssh_key_name,
        user_data=user_data,
        associate_public_ip_address=True,
        source_dest_check=False,
        tags={
            **config.tags,
            "Name": f"{names.prefix}-wg",
            "role": "wireguard-gateway",
        },
        opts=pulumi.ResourceOptions(
            # Keep the gateway stable across runs even if the SSM "latest" AMI alias moves.
            ignore_changes=["ami"],
            # If a replacement is still required, avoid two concurrent gateways.
            delete_before_replace=True,
        ),
    )

    eip = aws.ec2.Eip(
        f"{names.prefix}-wg-eip",
        instance=instance.id,
        domain="vpc",
        tags={
            **config.tags,
            "Name": f"{names.prefix}-wg-eip",
        },
    )

    if config.nat_gateway_strategy == "single":
        aws.ec2.Route(
            f"{names.prefix}-wg-tunnel-route-1",
            route_table_id=network.private_route_table_ids.apply(lambda route_table_ids: route_table_ids[0]),
            destination_cidr_block=config.wireguard_tunnel_cidr,
            network_interface_id=instance.primary_network_interface_id,
        )
    else:
        for i in range(config.availability_zone_count):
            aws.ec2.Route(
                f"{names.prefix}-wg-tunnel-route-{i + 1}",
                route_table_id=network.private_route_table_ids.apply(
                    lambda route_table_ids, idx=i: route_table_ids[idx]
                ),
                destination_cidr_block=config.wireguard_tunnel_cidr,
                network_interface_id=instance.primary_network_interface_id,
            )

    return WireGuardOutputs(
        instance_id=instance.id,
        public_ip=eip.public_ip,
        security_group_id=security_group.id,
    )
