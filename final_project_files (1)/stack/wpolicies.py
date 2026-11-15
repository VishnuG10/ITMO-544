"""IAM policy documents for the two tiers. Every statement names exact actions and exact resources."""
from __future__ import annotations


def parameter_arn(region: str, account: str, name: str) -> str:
    """The ARN of an SSM parameter."""
    return f"arn:aws:ssm:{region}:{account}:parameter{name}"


def topic_arn(region: str, account: str, name: str) -> str:
    """The ARN of an SNS topic."""
    return f"arn:aws:sns:{region}:{account}:{name}"


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


def advisor_policy(db_arn: str, gemini_arn: str, sns_arn: str) -> dict:
    """Read the two secrets the advisor uses, and subscribe and publish on the one report topic."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["ssm:GetParameter"], "Resource": [db_arn, gemini_arn]},
            {"Effect": "Allow",
             "Action": ["sns:ListSubscriptionsByTopic", "sns:Subscribe", "sns:Publish"],
             "Resource": sns_arn},
        ],
    }
