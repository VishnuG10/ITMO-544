"""IAM policy documents as plain Python dictionaries.

Keeping them as pure functions means we can test them without touching AWS.
"""
from __future__ import annotations


def trust_policy() -> dict:
    """Who may assume the role: the EC2 service, nobody else."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "ec2.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }],
    }
