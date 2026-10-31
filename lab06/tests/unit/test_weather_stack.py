"""The weather stack code, run against moto. No account, no cost."""
import base64
import json

from stack.database import create_database
from stack.wclients import account_id
from stack.wconfig import WeatherSettings
from stack.wpolicies import parameter_arn
from stack.wstack import (advisor_user_data, create_weather_stack, delete_weather_stack,
                          weather_user_data)
from tests.wsupport import ROOT, WFAKE_VALUES


def _create_all(settings, aws):
    db = create_database(settings, aws, waiter_delay=0)
    return db, create_weather_stack(settings, aws, db.db_host, iam_wait=0)


def _app_sg_rules(aws, settings):
    group = aws.ec2.describe_security_groups(GroupIds=[settings.app_sg_id])["SecurityGroups"][0]
    return [p for p in group["IpPermissions"] if p["FromPort"] == 3000]


def _user_data(aws, tier):
    version = aws.ec2.describe_launch_template_versions(
        LaunchTemplateName=tier.lt_name)["LaunchTemplateVersions"][0]
    return base64.b64decode(version["LaunchTemplateData"]["UserData"]).decode()


def test_each_role_reads_only_its_own_secret(wenv):
    settings, aws = wenv
    _create_all(settings, aws)
    account = account_id(aws)
    db_arn = parameter_arn(settings.region, account, settings.db_password_param)
    gemini_arn = parameter_arn(settings.region, account, settings.gemini_param_name)
    weather = aws.iam.get_role_policy(RoleName=settings.weather.role_name,
                                      PolicyName=settings.weather.policy_name)["PolicyDocument"]
    advisor = aws.iam.get_role_policy(RoleName=settings.advisor.role_name,
                                      PolicyName=settings.advisor.policy_name)["PolicyDocument"]
    assert weather["Statement"][0]["Action"] == ["ssm:GetParameter"]
    assert weather["Statement"][0]["Resource"] == db_arn
    assert advisor["Statement"][0]["Action"] == ["ssm:GetParameter"]
    assert advisor["Statement"][0]["Resource"] == gemini_arn
    assert settings.gemini_param_name not in json.dumps(weather)
    assert settings.db_password_param not in json.dumps(advisor)


def test_alb_is_built_first_and_user_data_gets_the_right_addresses(wenv):
    settings, aws = wenv
    db, outputs = _create_all(settings, aws)
    password = aws.ssm.get_parameter(Name=settings.db_password_param,
                                     WithDecryption=True)["Parameter"]["Value"]
    advisor_ud = _user_data(aws, settings.advisor)
    weather_ud = _user_data(aws, settings.weather)
    assert f"WEATHER_URL=http://{outputs.alb_dns_name}:8080" in advisor_ud
    assert f"DB_HOST={db.db_host}" in weather_ud
    assert password not in weather_ud and password not in advisor_ud


def test_user_data_stays_under_16_kb_for_fake_apps(wfake):
    for text in (weather_user_data(wfake, "db.example.com"),
                 advisor_user_data(wfake, "alb.example.com")):
        assert len(text.encode()) < 16 * 1024


def test_user_data_stays_under_16_kb_for_the_real_apps():
    settings = WeatherSettings.from_values(WFAKE_VALUES, base_dir=ROOT)
    for text in (weather_user_data(settings, "db.example.com"),
                 advisor_user_data(settings, "alb.example.com")):
        assert len(text.encode()) < 16 * 1024


def test_app_security_group_only_accepts_the_alb(wenv):
    settings, aws = wenv
    _, outputs = _create_all(settings, aws)
    rules = _app_sg_rules(aws, settings)
    assert len(rules) == 1
    assert rules[0]["IpRanges"] == []
    assert rules[0]["UserIdGroupPairs"][0]["GroupId"] == outputs.alb_security_group_id


def test_create_twice_creates_no_duplicates(wenv):
    settings, aws = wenv
    _, first = _create_all(settings, aws)
    _, second = _create_all(settings, aws)
    assert first == second
    assert len(aws.elbv2.describe_load_balancers()["LoadBalancers"]) == 1
    assert len(aws.elbv2.describe_target_groups()["TargetGroups"]) == 2
    assert len(aws.autoscaling.describe_auto_scaling_groups()["AutoScalingGroups"]) == 2
    assert len(aws.iam.list_roles()["Roles"]) == 2
    assert len(aws.ec2.describe_launch_templates()["LaunchTemplates"]) == 2
    assert len(_app_sg_rules(aws, settings)) == 1


def test_delete_removes_everything_and_restores_the_app_sg(wenv):
    settings, aws = wenv
    _create_all(settings, aws)
    delete_weather_stack(settings, aws, sleep=lambda _: None)
    assert aws.elbv2.describe_load_balancers()["LoadBalancers"] == []
    assert aws.elbv2.describe_target_groups()["TargetGroups"] == []
    assert aws.autoscaling.describe_auto_scaling_groups()["AutoScalingGroups"] == []
    assert aws.ec2.describe_launch_templates()["LaunchTemplates"] == []
    assert aws.iam.list_roles()["Roles"] == []
    rules = _app_sg_rules(aws, settings)
    assert rules[0]["IpRanges"] == [{"CidrIp": "0.0.0.0/0"}]
    assert rules[0]["UserIdGroupPairs"] == []
    aws.ssm.get_parameter(Name=settings.gemini_param_name)


def test_delete_removes_unexpected_inline_policies(wenv):
    settings, aws = wenv
    _create_all(settings, aws)
    aws.iam.put_role_policy(RoleName=settings.weather.role_name, PolicyName="left-over-policy",
                            PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
                                {"Effect": "Allow", "Action": "s3:ListAllMyBuckets",
                                 "Resource": "*"}]}))
    delete_weather_stack(settings, aws, sleep=lambda _: None)
    assert aws.iam.list_roles()["Roles"] == []
