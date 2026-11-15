from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from stack.database import create_database, delete_database
from stack.util import error_code


def _db(aws, settings):
    return aws.rds.describe_db_instances(
        DBInstanceIdentifier=settings.db_instance_id)["DBInstances"][0]


def test_database_is_small_private_encrypted_and_has_no_backups(wenv):
    settings, aws = wenv
    create_database(settings, aws, waiter_delay=0)
    db = _db(aws, settings)
    assert db["DBInstanceClass"] == "db.t4g.micro"
    assert db["BackupRetentionPeriod"] == 0
    assert db["MultiAZ"] is False
    assert db["PubliclyAccessible"] is False
    assert db["StorageEncrypted"] is True


def test_database_security_group_allows_only_the_app_group(wenv):
    settings, aws = wenv
    out = create_database(settings, aws, waiter_delay=0)
    group = aws.ec2.describe_security_groups(GroupIds=[out.db_security_group_id])["SecurityGroups"][0]
    rules = [p for p in group["IpPermissions"] if p["FromPort"] == 5432]
    assert len(rules) == 1
    assert rules[0]["IpRanges"] == []
    assert [p["GroupId"] for p in rules[0]["UserIdGroupPairs"]] == [settings.app_sg_id]


def test_delete_leaves_nothing_behind(wenv):
    settings, aws = wenv
    create_database(settings, aws, waiter_delay=0).save()
    delete_database(settings, aws, sleep=lambda _: None, waiter_delay=0)
    assert aws.rds.describe_db_instances()["DBInstances"] == []
    assert aws.rds.describe_db_snapshots()["DBSnapshots"] == []
    with pytest.raises(ClientError) as err:
        aws.ssm.get_parameter(Name=settings.db_password_param)
    assert error_code(err.value) == "ParameterNotFound"
    names = [g["DBSubnetGroupName"] for g in aws.rds.describe_db_subnet_groups()["DBSubnetGroups"]]
    assert settings.db_subnet_group_name not in names
    assert not Path("db_outputs.env").exists()


def test_delete_is_safe_to_run_twice(wenv):
    settings, aws = wenv
    create_database(settings, aws, waiter_delay=0)
    delete_database(settings, aws, sleep=lambda _: None, waiter_delay=0)
    delete_database(settings, aws, sleep=lambda _: None, waiter_delay=0)


def test_create_twice_creates_no_duplicates_and_keeps_the_password(wenv):
    settings, aws = wenv
    first = create_database(settings, aws, waiter_delay=0)
    before = aws.ssm.get_parameter(Name=settings.db_password_param, WithDecryption=True)
    second = create_database(settings, aws, waiter_delay=0)
    after = aws.ssm.get_parameter(Name=settings.db_password_param, WithDecryption=True)
    assert first == second
    assert before["Parameter"]["Value"] == after["Parameter"]["Value"]
    assert len(aws.rds.describe_db_instances()["DBInstances"]) == 1


def test_password_is_random_hex_and_never_written_to_a_file(wenv):
    settings, aws = wenv
    out = create_database(settings, aws, waiter_delay=0)
    out.save()
    password = aws.ssm.get_parameter(Name=settings.db_password_param,
                                     WithDecryption=True)["Parameter"]["Value"]
    assert len(password) == 32 and int(password, 16) >= 0
    assert password not in Path("db_outputs.env").read_text()
