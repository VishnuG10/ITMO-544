"""Is the deployed infrastructure built the way the design says? Run: pytest -m integration"""
import base64
import gzip
import socket

import pytest

from stack.wclients import account_id
from stack.wpolicies import parameter_arn, topic_arn
from tests.whelpers import instance_ip

pytestmark = pytest.mark.integration


def _decision(aws, role_name, action, arn):
    role_arn = aws.iam.get_role(RoleName=role_name)["Role"]["Arn"]
    result = aws.iam.simulate_principal_policy(
        PolicySourceArn=role_arn, ActionNames=[action], ResourceArns=[arn])
    return result["EvaluationResults"][0]["EvalDecision"]


def test_both_target_groups_are_healthy(healthy):
    assert healthy


def test_each_role_is_allowed_and_denied_what_it_should_be(waws, wsettings):
    account = account_id(waws)
    db_arn = parameter_arn(wsettings.region, account, wsettings.db_password_param)
    gemini_arn = parameter_arn(wsettings.region, account, wsettings.gemini_param_name)
    sns_arn = topic_arn(wsettings.region, account, wsettings.sns_topic_name)
    weather, advisor = wsettings.weather.role_name, wsettings.advisor.role_name

    assert _decision(waws, weather, "ssm:GetParameter", db_arn) == "allowed"
    assert _decision(waws, weather, "ssm:GetParameter", gemini_arn) != "allowed"
    assert _decision(waws, weather, "sns:Publish", sns_arn) != "allowed"
    assert _decision(waws, weather, "sns:Subscribe", sns_arn) != "allowed"

    assert _decision(waws, advisor, "ssm:GetParameter", gemini_arn) == "allowed"
    assert _decision(waws, advisor, "ssm:GetParameter", db_arn) == "allowed"
    for action in ("sns:ListSubscriptionsByTopic", "sns:Subscribe", "sns:Publish"):
        assert _decision(waws, advisor, action, sns_arn) == "allowed"
    assert _decision(waws, advisor, "sns:DeleteTopic", sns_arn) != "allowed"
    assert _decision(waws, advisor, "ssm:PutParameter", gemini_arn) != "allowed"
    assert _decision(waws, advisor, "sns:Publish", sns_arn + "-other") != "allowed"


def test_app_instances_are_reachable_only_through_the_alb(healthy, waws):
    for name in ("weather-app", "advisor-app"):
        ip = instance_ip(waws, name)
        assert ip, f"no running instance named {name}"
        with pytest.raises(OSError):
            socket.create_connection((ip, 3000), timeout=5)


def test_app_security_group_accepts_the_app_port_only_from_the_alb(waws, wsettings, wouts):
    group = waws.ec2.describe_security_groups(GroupIds=[wsettings.app_sg_id])["SecurityGroups"][0]
    rules = [p for p in group["IpPermissions"] if p["FromPort"] == wsettings.app_port]
    assert len(rules) == 1
    assert rules[0]["IpRanges"] == []
    assert [pair["GroupId"] for pair in rules[0]["UserIdGroupPairs"]] == [wouts.alb_security_group_id]


def test_no_secret_is_in_the_user_data(waws, wsettings):
    for name in (wsettings.gemini_param_name, wsettings.db_password_param):
        secret = waws.ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]
        for tier in wsettings.tiers:
            version = waws.ec2.describe_launch_template_versions(
                LaunchTemplateName=tier.lt_name, Versions=["$Latest"])["LaunchTemplateVersions"][0]
            text = gzip.decompress(base64.b64decode(version["LaunchTemplateData"]["UserData"])).decode()
            assert text.startswith("#!/usr/bin/python3")
            assert secret not in text


def test_database_port_is_not_reachable_from_this_computer(wdb):
    with pytest.raises(OSError):
        socket.create_connection((wdb.db_host, 5432), timeout=6)


def test_database_has_no_backups_and_no_public_address(waws, wsettings):
    db = waws.rds.describe_db_instances(
        DBInstanceIdentifier=wsettings.db_instance_id)["DBInstances"][0]
    assert db["BackupRetentionPeriod"] == 0
    assert db["PubliclyAccessible"] is False
    assert db["MultiAZ"] is False
    assert db["StorageEncrypted"] is True
