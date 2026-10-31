"""Fixtures for the weather unit tests."""
import pytest
from moto import mock_aws

from stack.wclients import WeatherClients
from stack.wconfig import WeatherSettings
from tests.wsupport import WFAKE_VALUES, make_app_dirs


@pytest.fixture
def wfake(tmp_path):
    """Fake settings pointing at tiny fake app folders."""
    make_app_dirs(tmp_path)
    return WeatherSettings.from_values(WFAKE_VALUES, base_dir=tmp_path)


@pytest.fixture
def wenv(tmp_path, monkeypatch):
    """A fake AWS account holding what earlier labs and Part C would already have created."""
    make_app_dirs(tmp_path)
    monkeypatch.chdir(tmp_path)
    with mock_aws():
        aws = WeatherClients.create("us-east-2")
        vpc = aws.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"][0]
        sg = aws.ec2.create_security_group(
            GroupName="app-sg", Description="app", VpcId=vpc["VpcId"])["GroupId"]
        aws.ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 3000, "ToPort": 3000,
            "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
        aws.ec2.create_key_pair(KeyName="test-key")
        ami = aws.ec2.describe_images()["Images"][0]["ImageId"]
        aws.ssm.put_parameter(Name=WFAKE_VALUES["GEMINI_PARAM_NAME"], Type="SecureString",
                              Value="fake-gemini-key")
        values = dict(WFAKE_VALUES, APP_SECURITY_GROUP_ID=sg, AMI_ID=ami)
        yield WeatherSettings.from_values(values, base_dir=tmp_path), aws
