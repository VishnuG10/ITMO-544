"""The boto3 clients the weather stack needs."""
from __future__ import annotations

from dataclasses import dataclass

import boto3


@dataclass
class WeatherClients:
    """One client per AWS service used by the weather stack."""
    ec2: object
    iam: object
    elbv2: object
    autoscaling: object
    ssm: object
    rds: object
    sts: object

    @classmethod
    def create(cls, region: str) -> "WeatherClients":
        """Build every client for one region."""
        session = boto3.session.Session(region_name=region)
        return cls(
            ec2=session.client("ec2"), iam=session.client("iam"),
            elbv2=session.client("elbv2"), autoscaling=session.client("autoscaling"),
            ssm=session.client("ssm"), rds=session.client("rds"), sts=session.client("sts"),
        )


def account_id(aws: WeatherClients) -> str:
    """The 12 digit id of the account these credentials belong to."""
    return aws.sts.get_caller_identity()["Account"]
