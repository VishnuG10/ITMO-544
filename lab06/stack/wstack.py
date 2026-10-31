"""Create and delete the weather advisor stack: roles, network, ALB, launch templates, ASGs."""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from botocore.exceptions import ClientError

from . import balancer, compute, network, scaling
from .config import Tier
from .policies import trust_policy
from .util import error_code, ignore_errors, retry_on
from .wclients import WeatherClients, account_id
from .wconfig import WeatherSettings
from .wpolicies import parameter_arn, ssm_read_policy

log = logging.getLogger("stack")

RDS_CA_URL = "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem"


@dataclass
class WeatherOutputs:
    """Identifiers the create step generates, saved for the tests and the experiments."""
    vpc_id: str
    subnet_a: str
    subnet_b: str
    alb_security_group_id: str
    alb_arn: str
    alb_dns_name: str
    weather_tg_arn: str
    advisor_tg_arn: str

    def tg_arn(self, tier_name: str) -> str:
        """The target group ARN for one tier."""
        return {"weather": self.weather_tg_arn, "advisor": self.advisor_tg_arn}[tier_name]

    def save(self, directory: str | Path = ".") -> None:
        """Write stack_outputs.json for Python and stack_outputs.env for the shell."""
        directory = Path(directory)
        data = asdict(self)
        (directory / "stack_outputs.json").write_text(json.dumps(data, indent=2) + "\n")
        lines = [f"{key.upper()}={value}" for key, value in data.items()]
        (directory / "stack_outputs.env").write_text("\n".join(lines) + "\n")

    @classmethod
    def load(cls, directory: str | Path = ".") -> "WeatherOutputs":
        """Read stack_outputs.json."""
        return cls(**json.loads((Path(directory) / "stack_outputs.json").read_text()))

    @staticmethod
    def remove(directory: str | Path = ".") -> None:
        """Delete both output files if they exist."""
        for name in ("stack_outputs.json", "stack_outputs.env"):
            (Path(directory) / name).unlink(missing_ok=True)


def build_user_data(tier: Tier, region: str, extra_env: list[str],
                    extra_setup: list[str] | None = None) -> str:
    """The boot script: write the app files, install Node, run the app as a systemd service."""
    app_js = base64.b64encode((tier.app_dir / "app.js").read_bytes()).decode()
    package_json = base64.b64encode((tier.app_dir / "package.json").read_bytes()).decode()
    env_block = "\n".join(extra_env)
    setup_block = "\n".join(extra_setup or [])
    return f"""#!/bin/bash
dnf update -y
dnf install -y nodejs
mkdir -p /opt/app
{setup_block}
echo "{app_js}" | base64 -d > /opt/app/app.js
echo "{package_json}" | base64 -d > /opt/app/package.json
cd /opt/app
npm install --omit=dev
cat > /etc/systemd/system/{tier.name}.service << 'SERVICE'
[Unit]
Description={tier.name} app
After=network.target
[Service]
Environment=PORT=3000
Environment=AWS_REGION={region}
{env_block}
WorkingDirectory=/opt/app
ExecStart=/usr/bin/node /opt/app/app.js
Restart=always
User=ec2-user
[Install]
WantedBy=multi-user.target
SERVICE
chown -R ec2-user:ec2-user /opt/app
systemctl daemon-reload
systemctl enable {tier.name}.service
systemctl start {tier.name}.service
"""


def weather_user_data(settings: WeatherSettings, db_host: str) -> str:
    """User-data for the weather tier. It gets the database address, never the password."""
    env = [
        f'Environment="NWS_USER_AGENT=(itmo-444-544 weather lab, {settings.contact_email})"',
        f"Environment=DB_HOST={db_host}",
        f"Environment=DB_NAME={settings.db_name}",
        f"Environment=DB_USER={settings.db_user}",
        f"Environment=DB_PASSWORD_PARAM={settings.db_password_param}",
        f"Environment=CACHE_SECONDS={settings.cache_seconds}",
    ]
    setup = ["dnf install -y postgresql15", f"curl -sS -o /opt/app/rds-ca.pem {RDS_CA_URL}"]
    return build_user_data(settings.weather, settings.region, env, setup)


def advisor_user_data(settings: WeatherSettings, alb_dns: str) -> str:
    """User-data for the advisor tier. It needs the ALB address to reach the weather tier."""
    env = [
        f"Environment=WEATHER_URL=http://{alb_dns}:{settings.weather.listener_port}",
        f"Environment=GEMINI_KEY_PARAM={settings.gemini_param_name}",
        f"Environment=GEMINI_MODEL={settings.gemini_model}",
        f"Environment=KEY_CACHE_SECONDS={settings.key_cache_seconds}",
    ]
    return build_user_data(settings.advisor, settings.region, env)


def ensure_role_and_profile(aws: WeatherClients, tier: Tier, policy: dict) -> None:
    """Create the role, its one-secret inline policy, and an instance profile holding the role."""
    with ignore_errors("EntityAlreadyExists"):
        aws.iam.create_role(RoleName=tier.role_name,
                            AssumeRolePolicyDocument=json.dumps(trust_policy()))
    aws.iam.put_role_policy(RoleName=tier.role_name, PolicyName=tier.policy_name,
                            PolicyDocument=json.dumps(policy))
    with ignore_errors("EntityAlreadyExists"):
        aws.iam.create_instance_profile(InstanceProfileName=tier.profile_name)
    profile = aws.iam.get_instance_profile(InstanceProfileName=tier.profile_name)
    if tier.role_name not in [r["RoleName"] for r in profile["InstanceProfile"]["Roles"]]:
        aws.iam.add_role_to_instance_profile(
            InstanceProfileName=tier.profile_name, RoleName=tier.role_name)
    log.info("%s role and instance profile are ready.", tier.name)


def delete_role_and_profile(aws: WeatherClients, tier: Tier) -> None:
    """Remove the profile and role, deleting every inline and managed policy on it first."""
    with ignore_errors("NoSuchEntity"):
        aws.iam.remove_role_from_instance_profile(
            InstanceProfileName=tier.profile_name, RoleName=tier.role_name)
    with ignore_errors("NoSuchEntity"):
        aws.iam.delete_instance_profile(InstanceProfileName=tier.profile_name)
    with ignore_errors("NoSuchEntity"):
        for name in aws.iam.list_role_policies(RoleName=tier.role_name)["PolicyNames"]:
            aws.iam.delete_role_policy(RoleName=tier.role_name, PolicyName=name)
    with ignore_errors("NoSuchEntity"):
        attached = aws.iam.list_attached_role_policies(RoleName=tier.role_name)
        for policy in attached["AttachedPolicies"]:
            aws.iam.detach_role_policy(RoleName=tier.role_name, PolicyArn=policy["PolicyArn"])
    with ignore_errors("NoSuchEntity"):
        aws.iam.delete_role(RoleName=tier.role_name)
    log.info("Removed %s role and instance profile.", tier.name)


def ensure_launch_template(aws: WeatherClients, settings: WeatherSettings, tier: Tier,
                           user_data: str) -> int:
    """Create the launch template, or publish a new version if it already exists."""
    data = {
        "ImageId": settings.ami_id,
        "InstanceType": settings.instance_type,
        "KeyName": settings.key_name,
        "SecurityGroupIds": [settings.app_sg_id],
        "IamInstanceProfile": {"Name": tier.profile_name},
        "UserData": base64.b64encode(user_data.encode()).decode(),
        "TagSpecifications": [{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": f"{tier.name}-app"}],
        }],
    }
    if not compute.launch_template_exists(aws, tier):
        aws.ec2.create_launch_template(LaunchTemplateName=tier.lt_name, LaunchTemplateData=data)
        log.info("Created launch template %s", tier.lt_name)
        return 1
    response = aws.ec2.create_launch_template_version(
        LaunchTemplateName=tier.lt_name, SourceVersion="1", LaunchTemplateData=data)
    version = response["LaunchTemplateVersion"]["VersionNumber"]
    log.info("Template %s exists, published version %s", tier.lt_name, version)
    return version


def create_weather_stack(settings: WeatherSettings, aws: WeatherClients, db_host: str,
                         iam_wait: int = 15, sleep=time.sleep) -> WeatherOutputs:
    """Build the whole weather stack. The ALB comes before the launch templates."""
    log.info("== 1. Checking the Gemini key and the database ==")
    try:
        aws.ssm.get_parameter(Name=settings.gemini_param_name)
    except ClientError as err:
        if error_code(err) == "ParameterNotFound":
            raise network.StackError(
                f"Parameter {settings.gemini_param_name} was not found. "
                "Create it first (Part C of the lab).") from err
        raise

    log.info("== 2. IAM roles and instance profiles, one secret each ==")
    account = account_id(aws)
    db_arn = parameter_arn(settings.region, account, settings.db_password_param)
    gemini_arn = parameter_arn(settings.region, account, settings.gemini_param_name)
    ensure_role_and_profile(aws, settings.weather, ssm_read_policy(db_arn))
    ensure_role_and_profile(aws, settings.advisor, ssm_read_policy(gemini_arn))
    log.info("Waiting %ss for IAM propagation...", iam_wait)
    sleep(iam_wait)

    log.info("== 3. Default VPC and subnets in both AZs ==")
    vpc_id = network.default_vpc_id(aws)
    subnet_a = network.subnet_in_az(aws, vpc_id, settings.az_a)
    subnet_b = network.subnet_in_az(aws, vpc_id, settings.az_b)

    log.info("== 4. Security groups ==")
    alb_sg_id = network.ensure_alb_security_group(aws, settings, vpc_id)
    network.restrict_app_security_group(aws, settings, alb_sg_id)

    log.info("== 5. Target groups and the load balancer (the ALB comes first this time) ==")
    tg_arns = {tier.name: balancer.ensure_target_group(aws, settings, tier, vpc_id)
               for tier in settings.tiers}
    alb_arn, alb_dns = balancer.ensure_load_balancer(aws, settings, [subnet_a, subnet_b], alb_sg_id)
    for tier in settings.tiers:
        balancer.ensure_listener(aws, alb_arn, tier.listener_port, tg_arns[tier.name])

    log.info("== 6. Launch templates (the advisor needs the ALB address) ==")
    ensure_launch_template(aws, settings, settings.weather, weather_user_data(settings, db_host))
    ensure_launch_template(aws, settings, settings.advisor, advisor_user_data(settings, alb_dns))

    log.info("== 7. Auto Scaling Groups (these launch the instances) ==")
    for tier in settings.tiers:
        scaling.ensure_asg(aws, tier, tg_arns[tier.name], [subnet_a, subnet_b])

    return WeatherOutputs(
        vpc_id=vpc_id, subnet_a=subnet_a, subnet_b=subnet_b, alb_security_group_id=alb_sg_id,
        alb_arn=alb_arn, alb_dns_name=alb_dns,
        weather_tg_arn=tg_arns["weather"], advisor_tg_arn=tg_arns["advisor"],
    )


def delete_weather_stack(settings: WeatherSettings, aws: WeatherClients, sleep=time.sleep,
                         directory: str | Path = ".") -> None:
    """Delete by name. The Gemini key parameter and the database are deliberately left alone."""
    log.info("== Deleting Auto Scaling Groups (this also terminates their instances) ==")
    for tier in settings.tiers:
        scaling.delete_asg(aws, tier.asg_name, timeout=600, sleep=sleep)

    log.info("== Deleting the load balancer and target groups ==")
    balancer.delete_load_balancer(aws, settings.alb_name)
    for tier in settings.tiers:
        retry_on({"ResourceInUse"},
                 lambda name=tier.tg_name: balancer.delete_target_group(aws, name),
                 attempts=10, delay=15, sleep=sleep)

    log.info("== Restoring the app security group and deleting the ALB security group ==")
    alb_sg_id = network.find_security_group(aws, settings.alb_sg_name, network.default_vpc_id(aws))
    network.restore_app_security_group(aws, settings, alb_sg_id)
    if alb_sg_id:
        network.delete_alb_security_group(aws, alb_sg_id, sleep=sleep)

    log.info("== Deleting launch templates ==")
    for tier in settings.tiers:
        compute.delete_launch_template(aws, tier)

    log.info("== Removing IAM instance profiles and roles ==")
    for tier in settings.tiers:
        delete_role_and_profile(aws, tier)

    WeatherOutputs.remove(directory)
    log.info("Weather stack removed.")
