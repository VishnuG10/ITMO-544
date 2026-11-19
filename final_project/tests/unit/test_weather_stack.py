"""The weather stack code, run against moto. No account, no cost."""
import base64
import gzip
import json

import pytest

from stack.config import ConfigError
from stack.database import create_database
from stack.network import StackError
from stack.wclients import account_id
from stack.wconfig import WeatherSettings
from stack.wpolicies import advisor_policy, parameter_arn, ssm_read_policy, topic_arn
from stack.wstack import (USER_DATA_LIMIT, advisor_user_data, create_weather_stack,
                          delete_weather_stack, weather_user_data)
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
    return gzip.decompress(base64.b64decode(version["LaunchTemplateData"]["UserData"])).decode()


def _topics(aws):
    return [t["TopicArn"].rsplit(":", 1)[1] for t in aws.sns.list_topics()["Topics"]]


def test_missing_new_keys_are_named():
    values = {k: v for k, v in WFAKE_VALUES.items() if k not in ("SNS_TOPIC_NAME", "GEMINI_DAILY_CAP")}
    with pytest.raises(ConfigError, match="SNS_TOPIC_NAME, GEMINI_DAILY_CAP"):
        WeatherSettings.from_values(values)


def test_limits_must_be_whole_numbers_above_zero():
    for bad in ("0", "-5", "ten", "2.5"):
        with pytest.raises(ConfigError, match="GEMINI_PER_MINUTE"):
            WeatherSettings.from_values(dict(WFAKE_VALUES, GEMINI_PER_MINUTE=bad))


def test_policy_documents_have_no_wildcards():
    db_arn = parameter_arn("us-east-2", "111122223333", "/t444/db-password")
    gemini_arn = parameter_arn("us-east-2", "111122223333", "/t444/gemini-api-key")
    sns_arn = topic_arn("us-east-2", "111122223333", "t-weather-reports")
    for policy in (ssm_read_policy(db_arn), advisor_policy(db_arn, gemini_arn, sns_arn)):
        assert "*" not in json.dumps(policy)
        for statement in policy["Statement"]:
            assert statement["Effect"] == "Allow"
            assert all(":" in action for action in statement["Action"])
    actions = [a for s in advisor_policy(db_arn, gemini_arn, sns_arn)["Statement"] for a in s["Action"]]
    assert sorted(actions) == ["sns:ListSubscriptionsByTopic", "sns:Publish", "sns:Subscribe",
                               "ssm:GetParameter"]


def test_each_role_has_only_what_its_tier_uses(wenv):
    settings, aws = wenv
    _create_all(settings, aws)
    account = account_id(aws)
    db_arn = parameter_arn(settings.region, account, settings.db_password_param)
    gemini_arn = parameter_arn(settings.region, account, settings.gemini_param_name)
    sns_arn = topic_arn(settings.region, account, settings.sns_topic_name)
    weather = aws.iam.get_role_policy(RoleName=settings.weather.role_name,
                                      PolicyName=settings.weather.policy_name)["PolicyDocument"]
    advisor = aws.iam.get_role_policy(RoleName=settings.advisor.role_name,
                                      PolicyName=settings.advisor.policy_name)["PolicyDocument"]
    assert weather == ssm_read_policy(db_arn)
    assert advisor == advisor_policy(db_arn, gemini_arn, sns_arn)
    assert settings.gemini_param_name not in json.dumps(weather)
    assert "sns:" not in json.dumps(weather)
    assert "*" not in json.dumps(weather) + json.dumps(advisor)


def test_resources_are_created_in_the_order_they_depend_on(wenv):
    settings, aws = wenv
    db = create_database(settings, aws, waiter_delay=0)
    calls = []
    for client in (aws.sns, aws.iam, aws.ec2, aws.elbv2, aws.autoscaling):
        client.meta.events.register("before-call", lambda model, **kwargs: calls.append(model.name))
    create_weather_stack(settings, aws, db.db_host, iam_wait=0)
    wanted = ["CreateTopic", "CreateRole", "CreateTargetGroup", "CreateLoadBalancer", "CreateListener",
              "CreateLaunchTemplate", "CreateAutoScalingGroup"]
    first = [calls.index(name) for name in wanted]
    assert first == sorted(first)
    last_template = len(calls) - 1 - calls[::-1].index("CreateLaunchTemplate")
    assert last_template < calls.index("CreateAutoScalingGroup")


def test_alb_is_built_first_and_user_data_gets_the_right_addresses(wenv):
    settings, aws = wenv
    db, outputs = _create_all(settings, aws)
    password = aws.ssm.get_parameter(Name=settings.db_password_param,
                                     WithDecryption=True)["Parameter"]["Value"]
    sns_arn = topic_arn(settings.region, account_id(aws), settings.sns_topic_name)
    advisor_ud = _user_data(aws, settings.advisor)
    weather_ud = _user_data(aws, settings.weather)
    assert f"WEATHER_URL=http://{outputs.alb_dns_name}:8080" in advisor_ud
    assert f"SNS_TOPIC_ARN={sns_arn}" in advisor_ud
    assert "GEMINI_DAILY_CAP=200" in advisor_ud and "GEMINI_MODEL=gemini-test-model" in advisor_ud
    assert f"DB_HOST={db.db_host}" in weather_ud and f"DB_HOST={db.db_host}" in advisor_ud
    assert "GEMINI" not in weather_ud and "SNS" not in weather_ud
    for text in (weather_ud, advisor_ud):
        assert password not in text
        assert "fake-gemini-key" not in text


def test_boot_script_is_python_and_runs_no_shell(wfake):
    for raw in (weather_user_data(wfake, "db.example.com"),
                advisor_user_data(wfake, "db.example.com", "alb.example.com", "arn:aws:sns:x:1:t")):
        text = gzip.decompress(raw).decode()
        assert text.startswith("#!/usr/bin/python3")
        compile(text, "boot", "exec")
        assert "shell=True" not in text and "os.system" not in text
        assert "#!/bin/bash" not in text and "bash" not in text
        assert "/opt/app/app.js" in text and "/opt/app/package.json" in text


def test_user_data_stays_under_16_kb_for_fake_apps(wfake):
    for raw in (weather_user_data(wfake, "db.example.com"),
                advisor_user_data(wfake, "db.example.com", "alb.example.com", "arn:aws:sns:x:1:t")):
        assert len(raw) < USER_DATA_LIMIT


def test_user_data_stays_under_16_kb_for_the_real_apps():
    settings = WeatherSettings.from_values(WFAKE_VALUES, base_dir=ROOT)
    host = "itmo-544-weather-db.abcdefghijkl.us-east-2.rds.amazonaws.com"
    alb = "itmo-544-weather-alb-1234567890.us-east-2.elb.amazonaws.com"
    for raw in (weather_user_data(settings, host),
                advisor_user_data(settings, host, alb, "arn:aws:sns:us-east-2:111122223333:topic")):
        assert len(raw) < USER_DATA_LIMIT
        text = gzip.decompress(raw).decode()
        files = {}
        exec(text.split("\n\n\ndef run")[0], files)
        app_js = (ROOT / ("weather-app" if "NWS_USER_AGENT" in text else "advisor-app") / "app.js")
        assert files["FILES"]["/opt/app/app.js"] == app_js.read_text(encoding="utf-8")


def test_a_boot_script_that_is_too_large_stops_with_a_clear_message(wfake):
    (wfake.weather.app_dir / "app.js").write_text("".join(f"// {i * 7919}\n" for i in range(20000)))
    with pytest.raises(StackError, match="over the 16384 byte limit"):
        weather_user_data(wfake, "db.example.com")


def test_app_security_group_only_accepts_the_alb(wenv):
    settings, aws = wenv
    _, outputs = _create_all(settings, aws)
    rules = _app_sg_rules(aws, settings)
    assert len(rules) == 1
    assert rules[0]["IpRanges"] == []
    assert rules[0]["UserIdGroupPairs"][0]["GroupId"] == outputs.alb_security_group_id


def test_the_report_topic_is_created(wenv):
    settings, aws = wenv
    _create_all(settings, aws)
    assert _topics(aws) == [settings.sns_topic_name]


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
    assert len(_topics(aws)) == 1


def test_delete_removes_everything_and_restores_the_app_sg(wenv):
    settings, aws = wenv
    _create_all(settings, aws)
    delete_weather_stack(settings, aws, sleep=lambda _: None)
    assert aws.elbv2.describe_load_balancers()["LoadBalancers"] == []
    assert aws.elbv2.describe_target_groups()["TargetGroups"] == []
    assert aws.autoscaling.describe_auto_scaling_groups()["AutoScalingGroups"] == []
    assert aws.ec2.describe_launch_templates()["LaunchTemplates"] == []
    assert aws.iam.list_roles()["Roles"] == []
    assert _topics(aws) == []
    rules = _app_sg_rules(aws, settings)
    assert rules[0]["IpRanges"] == [{"CidrIp": "0.0.0.0/0"}]
    assert rules[0]["UserIdGroupPairs"] == []
    aws.ssm.get_parameter(Name=settings.gemini_param_name)


def test_delete_is_safe_to_run_twice(wenv):
    settings, aws = wenv
    _create_all(settings, aws)
    delete_weather_stack(settings, aws, sleep=lambda _: None)
    delete_weather_stack(settings, aws, sleep=lambda _: None)
    assert _topics(aws) == []


def test_delete_removes_unexpected_inline_policies(wenv):
    settings, aws = wenv
    _create_all(settings, aws)
    aws.iam.put_role_policy(RoleName=settings.weather.role_name, PolicyName="left-over-policy",
                            PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": [
                                {"Effect": "Allow", "Action": "s3:ListAllMyBuckets",
                                 "Resource": "*"}]}))
    delete_weather_stack(settings, aws, sleep=lambda _: None)
    assert aws.iam.list_roles()["Roles"] == []


def test_a_missing_gemini_key_stops_with_a_clear_message(wenv):
    settings, aws = wenv
    aws.ssm.delete_parameter(Name=settings.gemini_param_name)
    db = create_database(settings, aws, waiter_delay=0)
    with pytest.raises(StackError, match="was not found"):
        create_weather_stack(settings, aws, db.db_host, iam_wait=0)
    assert _topics(aws) == []


def test_no_bash_files_are_in_the_project():
    folders = [ROOT, ROOT / "stack", ROOT / "testing", ROOT / "tests", ROOT / "tests" / "unit",
               ROOT / "tests" / "integration", ROOT / "weather-app", ROOT / "advisor-app"]
    scripts = [p for folder in folders for p in folder.glob("*.sh")]
    assert scripts == []
    assert not (ROOT / "previous").exists()
    for folder in folders[:3]:
        for path in folder.glob("*.py"):
            assert not path.read_text().startswith("#!/bin/")
