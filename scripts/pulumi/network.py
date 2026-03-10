from dataclasses import dataclass
import ipaddress
import math

import pulumi
import pulumi_aws as aws

from config import BootstrapConfig
from naming import ResourceNames


@dataclass
class NetworkOutputs:
    vpc: aws.ec2.Vpc
    public_subnet_ids: pulumi.Output[list[str]]
    private_subnet_ids: pulumi.Output[list[str]]
    private_route_table_ids: pulumi.Output[list[str]]


def _derive_subnet_cidrs(vpc_cidr: str, availability_zone_count: int) -> tuple[list[str], list[str]]:
    vpc_network = ipaddress.ip_network(vpc_cidr)
    required_subnets = availability_zone_count * 2
    new_prefix = vpc_network.prefixlen + math.ceil(math.log2(required_subnets))

    if new_prefix > 27:
        raise ValueError(
            f"VPC CIDR '{vpc_cidr}' is too small to allocate {required_subnets} EKS subnets."
        )

    subnets = list(vpc_network.subnets(new_prefix=new_prefix))
    if len(subnets) < required_subnets:
        raise ValueError(
            f"VPC CIDR '{vpc_cidr}' does not provide enough subnets for {availability_zone_count} AZs."
        )

    public_cidrs = [str(subnets[i]) for i in range(availability_zone_count)]
    private_cidrs = [str(subnets[availability_zone_count + i]) for i in range(availability_zone_count)]
    return public_cidrs, private_cidrs


def create_network(config: BootstrapConfig, names: ResourceNames) -> NetworkOutputs:
    azs = aws.get_availability_zones(state="available")
    az_count = config.availability_zone_count
    if az_count > len(azs.names):
        raise ValueError(
            f"Requested {az_count} AZs but region only has {len(azs.names)} available."
        )

    if config.public_subnet_cidrs and config.private_subnet_cidrs:
        public_subnet_cidrs = config.public_subnet_cidrs
        private_subnet_cidrs = config.private_subnet_cidrs
    else:
        public_subnet_cidrs, private_subnet_cidrs = _derive_subnet_cidrs(config.vpc_cidr, az_count)

    vpc = aws.ec2.Vpc(
        names.vpc_name,
        cidr_block=config.vpc_cidr,
        enable_dns_hostnames=True,
        enable_dns_support=True,
        tags={
            **config.tags,
            "Name": names.vpc_name,
        },
    )

    igw = aws.ec2.InternetGateway(
        f"{names.prefix}-igw",
        vpc_id=vpc.id,
        tags={
            **config.tags,
            "Name": f"{names.prefix}-igw",
        },
    )

    public_route_table = aws.ec2.RouteTable(
        f"{names.prefix}-public-rt",
        vpc_id=vpc.id,
        routes=[
            aws.ec2.RouteTableRouteArgs(
                cidr_block="0.0.0.0/0",
                gateway_id=igw.id,
            )
        ],
        tags={
            **config.tags,
            "Name": f"{names.prefix}-public-rt",
        },
    )

    public_subnets: list[aws.ec2.Subnet] = []
    private_subnets: list[aws.ec2.Subnet] = []
    private_route_tables: list[aws.ec2.RouteTable] = []

    for i in range(az_count):
        az = azs.names[i]

        public_subnet = aws.ec2.Subnet(
            f"{names.prefix}-public-{i + 1}",
            vpc_id=vpc.id,
            availability_zone=az,
            cidr_block=public_subnet_cidrs[i],
            map_public_ip_on_launch=True,
            tags={
                **config.tags,
                "Name": f"{names.prefix}-public-{i + 1}",
                "kubernetes.io/role/elb": "1",
            },
        )
        public_subnets.append(public_subnet)

        aws.ec2.RouteTableAssociation(
            f"{names.prefix}-public-rta-{i + 1}",
            route_table_id=public_route_table.id,
            subnet_id=public_subnet.id,
        )

        private_subnet = aws.ec2.Subnet(
            f"{names.prefix}-private-{i + 1}",
            vpc_id=vpc.id,
            availability_zone=az,
            cidr_block=private_subnet_cidrs[i],
            map_public_ip_on_launch=False,
            tags={
                **config.tags,
                "Name": f"{names.prefix}-private-{i + 1}",
                "kubernetes.io/role/internal-elb": "1",
            },
        )
        private_subnets.append(private_subnet)

    if config.nat_gateway_strategy == "single":
        nat_eip = aws.ec2.Eip(
            f"{names.prefix}-nat-eip",
            domain="vpc",
            tags={
                **config.tags,
                "Name": f"{names.prefix}-nat-eip",
            },
        )

        nat_gateway = aws.ec2.NatGateway(
            f"{names.prefix}-nat",
            allocation_id=nat_eip.id,
            subnet_id=public_subnets[0].id,
            tags={
                **config.tags,
                "Name": f"{names.prefix}-nat",
            },
        )

        private_route_table = aws.ec2.RouteTable(
            f"{names.prefix}-private-rt",
            vpc_id=vpc.id,
            routes=[
                aws.ec2.RouteTableRouteArgs(
                    cidr_block="0.0.0.0/0",
                    nat_gateway_id=nat_gateway.id,
                )
            ],
            tags={
                **config.tags,
                "Name": f"{names.prefix}-private-rt",
            },
        )
        private_route_tables.append(private_route_table)

        for i, subnet in enumerate(private_subnets):
            aws.ec2.RouteTableAssociation(
                f"{names.prefix}-private-rta-{i + 1}",
                route_table_id=private_route_table.id,
                subnet_id=subnet.id,
            )
    else:
        for i, subnet in enumerate(private_subnets):
            nat_eip = aws.ec2.Eip(
                f"{names.prefix}-nat-eip-{i + 1}",
                domain="vpc",
                tags={
                    **config.tags,
                    "Name": f"{names.prefix}-nat-eip-{i + 1}",
                },
            )

            nat_gateway = aws.ec2.NatGateway(
                f"{names.prefix}-nat-{i + 1}",
                allocation_id=nat_eip.id,
                subnet_id=public_subnets[i].id,
                tags={
                    **config.tags,
                    "Name": f"{names.prefix}-nat-{i + 1}",
                },
            )

            private_route_table = aws.ec2.RouteTable(
                f"{names.prefix}-private-rt-{i + 1}",
                vpc_id=vpc.id,
                routes=[
                    aws.ec2.RouteTableRouteArgs(
                        cidr_block="0.0.0.0/0",
                        nat_gateway_id=nat_gateway.id,
                    )
                ],
                tags={
                    **config.tags,
                    "Name": f"{names.prefix}-private-rt-{i + 1}",
                },
            )
            private_route_tables.append(private_route_table)

            aws.ec2.RouteTableAssociation(
                f"{names.prefix}-private-rta-{i + 1}",
                route_table_id=private_route_table.id,
                subnet_id=subnet.id,
            )

    return NetworkOutputs(
        vpc=vpc,
        public_subnet_ids=pulumi.Output.all(*[s.id for s in public_subnets]),
        private_subnet_ids=pulumi.Output.all(*[s.id for s in private_subnets]),
        private_route_table_ids=pulumi.Output.all(*[rt.id for rt in private_route_tables]),
    )
