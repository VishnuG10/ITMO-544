"""The PostgreSQL database: security group, subnet group, password and instance."""
from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from botocore.exceptions import ClientError

from . import network
from .util import error_code, ignore_errors, retry_on
from .wclients import WeatherClients
from .wconfig import WeatherSettings

log = logging.getLogger("stack")
OUTPUTS_FILE = "db_outputs.env"


@dataclass
class DbOutputs:
    """The database address and security group id. Never the password."""
    db_host: str
    db_security_group_id: str

    def save(self, directory: str | Path = ".") -> None:
        """Write db_outputs.env."""
        text = f"DB_HOST={self.db_host}\nDB_SECURITY_GROUP_ID={self.db_security_group_id}\n"
        (Path(directory) / OUTPUTS_FILE).write_text(text)

    @classmethod
    def load(cls, directory: str | Path = ".") -> "DbOutputs":
        """Read db_outputs.env, raising FileNotFoundError if the database was not created."""
        values = {}
        for line in (Path(directory) / OUTPUTS_FILE).read_text().splitlines():
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
        return cls(values["DB_HOST"], values["DB_SECURITY_GROUP_ID"])

    @staticmethod
    def remove(directory: str | Path = ".") -> None:
        """Delete db_outputs.env if it exists."""
        (Path(directory) / OUTPUTS_FILE).unlink(missing_ok=True)


def _from_group(port: int, group_id: str) -> list[dict]:
    """A security group rule whose source is another security group."""
    return [{"IpProtocol": "tcp", "FromPort": port, "ToPort": port,
             "UserIdGroupPairs": [{"GroupId": group_id}]}]


def find_db(aws: WeatherClients, settings: WeatherSettings) -> dict | None:
    """Return the DB instance description, or None if it does not exist."""
    try:
        return aws.rds.describe_db_instances(
            DBInstanceIdentifier=settings.db_instance_id)["DBInstances"][0]
    except ClientError as err:
        if error_code(err) != "DBInstanceNotFound":
            raise
        return None


def ensure_db_security_group(aws: WeatherClients, settings: WeatherSettings, vpc_id: str) -> str:
    """Create the database security group allowing port 5432 from the app group only."""
    group_id = network.find_security_group(aws, settings.db_sg_name, vpc_id)
    if group_id is None:
        group_id = aws.ec2.create_security_group(
            GroupName=settings.db_sg_name,
            Description="PostgreSQL access from the lab app instances only",
            VpcId=vpc_id,
        )["GroupId"]
        log.info("Created database security group %s", group_id)
    with ignore_errors("InvalidPermission.Duplicate"):
        aws.ec2.authorize_security_group_ingress(
            GroupId=group_id, IpPermissions=_from_group(settings.db_port, settings.app_sg_id))
    return group_id


def ensure_subnet_group(aws: WeatherClients, settings: WeatherSettings, subnets: list[str]) -> None:
    """Create the DB subnet group."""
    with ignore_errors("DBSubnetGroupAlreadyExists"):
        aws.rds.create_db_subnet_group(
            DBSubnetGroupName=settings.db_subnet_group_name,
            DBSubnetGroupDescription="Lab subnets for the weather database",
            SubnetIds=subnets,
        )


def ensure_db_password(aws: WeatherClients, settings: WeatherSettings) -> None:
    """Store a random password as a SecureString if none exists yet."""
    try:
        aws.ssm.get_parameter(Name=settings.db_password_param)
        log.info("Password parameter already exists, keeping it.")
    except ClientError as err:
        if error_code(err) != "ParameterNotFound":
            raise
        aws.ssm.put_parameter(Name=settings.db_password_param, Type="SecureString",
                              Value=secrets.token_hex(16))
        log.info("Stored a new random database password in SSM.")


def _read_password(aws: WeatherClients, settings: WeatherSettings) -> str:
    """Read the password back from SSM for the one create call that needs it."""
    out = aws.ssm.get_parameter(Name=settings.db_password_param, WithDecryption=True)
    return out["Parameter"]["Value"]


def ensure_db_instance(aws: WeatherClients, settings: WeatherSettings, sg_id: str) -> None:
    """Create the PostgreSQL instance: small, private, encrypted, single-AZ, no backups."""
    if find_db(aws, settings):
        log.info("Database %s already exists, skipping.", settings.db_instance_id)
        return
    password = _read_password(aws, settings)
    aws.rds.create_db_instance(
        DBInstanceIdentifier=settings.db_instance_id,
        DBInstanceClass="db.t4g.micro",
        Engine="postgres",
        AllocatedStorage=20,
        StorageType="gp2",
        MasterUsername=settings.db_user,
        MasterUserPassword=password,
        DBName=settings.db_name,
        DBSubnetGroupName=settings.db_subnet_group_name,
        VpcSecurityGroupIds=[sg_id],
        AvailabilityZone=settings.az_a,
        BackupRetentionPeriod=0,
        MultiAZ=False,
        PubliclyAccessible=False,
        AutoMinorVersionUpgrade=False,
        DeletionProtection=False,
        StorageEncrypted=True,
    )
    del password
    log.info("Creating database %s", settings.db_instance_id)


def wait_available(aws: WeatherClients, settings: WeatherSettings, delay: int = 30) -> str:
    """Wait until the database is available and return its address."""
    aws.rds.get_waiter("db_instance_available").wait(
        DBInstanceIdentifier=settings.db_instance_id,
        WaiterConfig={"Delay": delay, "MaxAttempts": 60})
    return find_db(aws, settings)["Endpoint"]["Address"]


def create_database(settings: WeatherSettings, aws: WeatherClients,
                    waiter_delay: int = 30) -> DbOutputs:
    """Build everything the database needs, wait for it, and return its address."""
    log.info("== 1. Default VPC and subnets in both AZs ==")
    vpc_id = network.default_vpc_id(aws)
    subnets = [network.subnet_in_az(aws, vpc_id, settings.az_a),
               network.subnet_in_az(aws, vpc_id, settings.az_b)]
    log.info("== 2. Database security group (port 5432, from the app security group only) ==")
    sg_id = ensure_db_security_group(aws, settings, vpc_id)
    log.info("== 3. DB subnet group ==")
    ensure_subnet_group(aws, settings, subnets)
    log.info("== 4. Database password (random, stored as a SecureString, never printed) ==")
    ensure_db_password(aws, settings)
    log.info("== 5. The PostgreSQL instance (free tier size, no backups) ==")
    ensure_db_instance(aws, settings, sg_id)
    log.info("Waiting for the database to become available (usually 5 to 10 minutes)...")
    host = wait_available(aws, settings, delay=waiter_delay)
    return DbOutputs(db_host=host, db_security_group_id=sg_id)


def delete_database(settings: WeatherSettings, aws: WeatherClients, directory: str | Path = ".",
                    sleep=time.sleep, waiter_delay: int = 30) -> None:
    """Delete the database with no snapshot and no kept backups, then everything around it."""
    log.info("== Deleting the database (no final snapshot, no kept backups) ==")
    db = find_db(aws, settings)
    if db is None:
        log.info("Database %s not found, nothing to delete.", settings.db_instance_id)
    else:
        if db["DBInstanceStatus"] != "deleting":
            aws.rds.delete_db_instance(
                DBInstanceIdentifier=settings.db_instance_id,
                SkipFinalSnapshot=True, DeleteAutomatedBackups=True)
        log.info("Waiting for the database to be deleted (a few minutes)...")
        aws.rds.get_waiter("db_instance_deleted").wait(
            DBInstanceIdentifier=settings.db_instance_id,
            WaiterConfig={"Delay": waiter_delay, "MaxAttempts": 60})

    log.info("== Deleting the DB subnet group ==")
    with ignore_errors("DBSubnetGroupNotFoundFault", "DBSubnetGroupNotFound"):
        retry_on({"InvalidDBSubnetGroupStateFault"},
                 lambda: aws.rds.delete_db_subnet_group(
                     DBSubnetGroupName=settings.db_subnet_group_name),
                 attempts=10, delay=15, sleep=sleep)

    log.info("== Deleting the database security group ==")
    group_id = network.find_security_group(aws, settings.db_sg_name, network.default_vpc_id(aws))
    if group_id:
        with ignore_errors("InvalidGroup.NotFound"):
            retry_on({"DependencyViolation"},
                     lambda: aws.ec2.delete_security_group(GroupId=group_id),
                     attempts=10, delay=15, sleep=sleep)

    log.info("== Deleting the database password parameter ==")
    with ignore_errors("ParameterNotFound"):
        aws.ssm.delete_parameter(Name=settings.db_password_param)
    DbOutputs.remove(directory)
    log.info("Database removed. Check that no snapshots or backups remain.")
