"""Launch template lookups shared by the create and delete steps."""
from __future__ import annotations

import logging

from botocore.exceptions import ClientError

from .clients import AwsClients
from .config import Tier
from .util import error_code

log = logging.getLogger("stack")


def launch_template_exists(aws: AwsClients, tier: Tier) -> bool:
    try:
        aws.ec2.describe_launch_templates(LaunchTemplateNames=[tier.lt_name])
        return True
    except ClientError as err:
        if error_code(err) != "InvalidLaunchTemplateName.NotFoundException":
            raise
        return False


def delete_launch_template(aws: AwsClients, tier: Tier) -> None:
    if launch_template_exists(aws, tier):
        aws.ec2.delete_launch_template(LaunchTemplateName=tier.lt_name)
        log.info("Deleted launch template %s", tier.lt_name)
