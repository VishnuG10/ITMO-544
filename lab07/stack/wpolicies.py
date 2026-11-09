"""IAM policy documents that let one role read exactly one SSM parameter."""
from __future__ import annotations


def parameter_arn(region: str, account: str, name: str) -> str:
    """The ARN of an SSM parameter."""
    return f"arn:aws:ssm:{region}:{account}:parameter{name}"


def ssm_read_policy(arn: str) -> dict:
    """Allow ssm:GetParameter on exactly one parameter and nothing else."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["ssm:GetParameter"],
            "Resource": arn,
        }],
    }
