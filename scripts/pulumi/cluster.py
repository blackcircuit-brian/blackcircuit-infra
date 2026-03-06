from dataclasses import dataclass

import pulumi
import pulumi_aws as aws
import pulumi_eks as eks

from config import BootstrapConfig, get_node_profile
from naming import ResourceNames
from network import NetworkOutputs


@dataclass
class ClusterOutputs:
    cluster: eks.Cluster
    kubeconfig: pulumi.Output[dict]


def create_cluster(
    config: BootstrapConfig,
    names: ResourceNames,
    network: NetworkOutputs,
) -> ClusterOutputs:
    node_profile = get_node_profile(config)

    aws_cfg = pulumi.Config("aws")
    aws_profile = aws_cfg.get("profile")

    node_role = aws.iam.Role(
        f"{names.prefix}-node-role",
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

    policy_arns = [
        "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
        "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
    ]

    attachments = []
    for i, policy_arn in enumerate(policy_arns):
        attachments.append(
            aws.iam.RolePolicyAttachment(
                f"{names.prefix}-node-policy-{i + 1}",
                role=node_role.name,
                policy_arn=policy_arn,
            )
        )

    cluster = eks.Cluster(
        names.cluster_name,
        name=names.cluster_name,
        version=config.kubernetes_version,
        vpc_id=network.vpc.id,
        public_subnet_ids=network.public_subnet_ids,
        private_subnet_ids=network.private_subnet_ids,
        endpoint_private_access=config.cluster_endpoint_private_access,
        endpoint_public_access=config.cluster_endpoint_public_access,
        public_access_cidrs=(
            config.cluster_public_access_cidrs if config.cluster_endpoint_public_access else None
        ),
        skip_default_node_group=True,
        create_oidc_provider=True,
        instance_roles=[node_role],
        tags={
            **config.tags,
            "Name": names.cluster_name,
        },
        provider_credential_opts=eks.KubeconfigOptionsArgs(
            profile_name=aws_profile
        ) if aws_profile else None,
        opts=pulumi.ResourceOptions(depends_on=attachments),
    )

    eks.ManagedNodeGroup(
        names.node_group_name,
        cluster=cluster,
        node_role=node_role,
        subnet_ids=network.private_subnet_ids,
        ami_type=node_profile.ami_type,
        instance_types=node_profile.instance_types,
        scaling_config=aws.eks.NodeGroupScalingConfigArgs(
            desired_size=config.node_desired_size,
            min_size=config.node_min_size,
            max_size=config.node_max_size,
        ),
        disk_size=config.node_disk_size,
        labels={
            "blackcircuit.ca/workload": "general",
        },
        tags={
            **config.tags,
            "Name": names.node_group_name,
        },
        opts=pulumi.ResourceOptions(depends_on=[cluster]),
    )

    return ClusterOutputs(
        cluster=cluster,
        kubeconfig=cluster.kubeconfig,
    )
