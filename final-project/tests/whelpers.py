"""Small helpers for the weather integration and chaos tests."""
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent


def http_status(url: str, timeout: float = 10) -> int:
    """The HTTP status of a GET, or 0 if there was no answer."""
    try:
        return requests.get(url, timeout=timeout).status_code
    except requests.RequestException:
        return 0


def get_json(url: str, timeout: float = 30):
    """Return (status, parsed JSON or None)."""
    try:
        resp = requests.get(url, timeout=timeout)
    except requests.RequestException:
        return 0, None
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, None


def wait_healthy(aws, outputs, tier_names=("weather", "advisor"), timeout: int = 420) -> bool:
    """Poll until every target of every tier is healthy, or give up after the timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ok = True
        for name in tier_names:
            targets = aws.elbv2.describe_target_health(
                TargetGroupArn=outputs.tg_arn(name))["TargetHealthDescriptions"]
            if not targets or any(t["TargetHealth"]["State"] != "healthy" for t in targets):
                ok = False
        if ok:
            return True
        time.sleep(15)
    return False


def instance_ip(aws, name_tag: str):
    """Public IP of the running instance with this Name tag, or None."""
    reservations = aws.ec2.describe_instances(Filters=[
        {"Name": "tag:Name", "Values": [name_tag]},
        {"Name": "instance-state-name", "Values": ["running"]},
    ])["Reservations"]
    for reservation in reservations:
        for instance in reservation["Instances"]:
            if instance.get("PublicIpAddress"):
                return instance["PublicIpAddress"]
    return None


def ssh(settings, ip: str, command: str) -> subprocess.CompletedProcess:
    """Run one command on an instance over SSH using the lab's key pair."""
    key = ROOT / f"{settings.key_name}.pem"
    return subprocess.run(
        ["ssh", "-i", str(key), "-o", "StrictHostKeyChecking=no", f"ec2-user@{ip}", command],
        capture_output=True, text=True, timeout=60)
