"""Is the deployed infrastructure built the way the design says? Run: pytest -m integration"""
import socket

import pytest

from stack.wclients import account_id
from stack.wpolicies import parameter_arn
from tests.whelpers import instance_ip

pytestmark = pytest.mark.integration


def _decision(aws, role_name, arn):
    role_arn = aws.iam.get_role(RoleName=role_name)["Role"]["Arn"]
    result = aws.iam.simulate_principal_policy(
        PolicySourceArn=role_arn, ActionNames=["ssm:GetParameter"], ResourceArns=[arn])
    return result["EvaluationResults"][0]["EvalDecision"]


def test_both_target_groups_are_healthy(healthy):
    assert healthy


def test_each_role_can_read_only_its_own_secret(waws, wsettings):
    account = account_id(waws)
    db_arn = parameter_arn(wsettings.region, account, wsettings.db_password_param)
    gemini_arn = parameter_arn(wsettings.region, account, wsettings.gemini_param_name)
    assert _decision(waws, wsettings.weather.role_name, db_arn) == "allowed"
    assert _decision(waws, wsettings.weather.role_name, gemini_arn) != "allowed"
    assert _decision(waws, wsettings.advisor.role_name, gemini_arn) == "allowed"
    assert _decision(waws, wsettings.advisor.role_name, db_arn) != "allowed"


def test_app_instances_are_reachable_only_through_the_alb(healthy, waws):
    for name in ("weather-app", "advisor-app"):
        ip = instance_ip(waws, name)
        assert ip, f"no running instance named {name}"
        with pytest.raises(OSError):
            socket.create_connection((ip, 3000), timeout=5)


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
