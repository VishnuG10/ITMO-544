#!/usr/bin/env python3
"""Part H helpers written with boto3, so every change to the account comes from Python.

Usage (run from lab06/):
    python3 testing/experiments.py gemini break
    python3 testing/experiments.py gemini delete
    python3 testing/experiments.py db-rule revoke
    python3 testing/experiments.py db-rule restore
    python3 testing/experiments.py terminate-weather
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stack.database import DbOutputs
from stack.util import ignore_errors
from stack.wclients import WeatherClients
from stack.wconfig import WeatherSettings
from stack.wstack import WeatherOutputs


def http_get(url: str, timeout: float):
    """Return (status, parsed JSON or None). Status 0 means no connection."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body, status = resp.read().decode(), resp.status
    except urllib.error.HTTPError as err:
        body, status = err.read().decode(), err.code
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, None
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


def set_gemini_key(aws, settings, value: str) -> None:
    """Overwrite the Gemini parameter with the given value."""
    aws.ssm.put_parameter(Name=settings.gemini_param_name, Type="SecureString",
                          Value=value, Overwrite=True)


def db_rule(aws, settings, action: str) -> None:
    """Revoke or restore the rule that lets the app security group reach port 5432."""
    db_sg = DbOutputs.load(ROOT).db_security_group_id
    rule = [{"IpProtocol": "tcp", "FromPort": settings.db_port, "ToPort": settings.db_port,
             "UserIdGroupPairs": [{"GroupId": settings.app_sg_id}]}]
    if action == "revoke":
        with ignore_errors("InvalidPermission.NotFound"):
            aws.ec2.revoke_security_group_ingress(GroupId=db_sg, IpPermissions=rule)
    else:
        with ignore_errors("InvalidPermission.Duplicate"):
            aws.ec2.authorize_security_group_ingress(GroupId=db_sg, IpPermissions=rule)
    print(f"database rule: {action} done")


def terminate_weather(aws, settings, outputs) -> None:
    """Terminate the weather instance and time how long the service takes to answer again."""
    group = aws.autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[settings.weather.asg_name])["AutoScalingGroups"][0]
    victim = next(i["InstanceId"] for i in group["Instances"] if i["LifecycleState"] == "InService")
    weather = f"http://{outputs.alb_dns_name}:{settings.weather.listener_port}"
    advisor = f"http://{outputs.alb_dns_name}:{settings.advisor.listener_port}"
    print(f"Terminating {victim} ...")
    aws.autoscaling.terminate_instance_in_auto_scaling_group(
        InstanceId=victim, ShouldDecrementDesiredCapacity=False)
    start = time.time()
    saw_outage = False
    recovered_after = None
    while time.time() - start < 420:
        w_status, _ = http_get(f"{weather}/weather?zip=60616", 5)
        began = time.time()
        a_status, a_body = http_get(f"{advisor}/advice?zip=33101", 25)
        home, _ = http_get(f"{advisor}/", 10)
        print(f"t+{time.time() - start:5.0f}s  weather={w_status}  advisor={a_status}"
              f" ({time.time() - began:.1f}s, {a_body.get('error') if a_body else '-'})"
              f"  advisor home page={home}")
        if w_status != 200:
            saw_outage = True
        elif saw_outage:
            recovered_after = time.time() - start
            break
        time.sleep(5)
    if not saw_outage:
        print("The weather service never went down, so there was nothing to recover from.")
        return
    if recovered_after is None:
        print("The weather service did not recover within 420 seconds.")
        return
    print(f"Weather service recovered after {recovered_after:.0f} seconds")
    _, body = http_get(f"{weather}/weather?zip=60616", 10)
    print("cache for 60616 after recovery =", body.get("cache") if body else "?")


def main() -> None:
    """Parse the command and run it."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    gem = sub.add_parser("gemini")
    gem.add_argument("action", choices=["break", "delete"])
    rule = sub.add_parser("db-rule")
    rule.add_argument("action", choices=["revoke", "restore"])
    sub.add_parser("terminate-weather")
    args = parser.parse_args()

    settings = WeatherSettings.from_file(ROOT / ".env")
    aws = WeatherClients.create(settings.region)

    if args.command == "gemini":
        if args.action == "break":
            set_gemini_key(aws, settings, "not-a-real-key")
        else:
            with ignore_errors("ParameterNotFound"):
                aws.ssm.delete_parameter(Name=settings.gemini_param_name)
        print(f"gemini key: {args.action} done")
    elif args.command == "db-rule":
        db_rule(aws, settings, args.action)
    else:
        terminate_weather(aws, settings, WeatherOutputs.load(ROOT))


if __name__ == "__main__":
    main()
