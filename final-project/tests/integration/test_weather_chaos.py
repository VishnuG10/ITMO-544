"""Break things on purpose and check the stack copes. Slow. Run: pytest -m chaos -s"""
import time

import pytest

from tests.whelpers import get_json, http_status, instance_ip, ssh

pytestmark = pytest.mark.chaos


def _db_rule(settings):
    return [{"IpProtocol": "tcp", "FromPort": settings.db_port, "ToPort": settings.db_port,
             "UserIdGroupPairs": [{"GroupId": settings.app_sg_id}]}]


def test_database_failure_does_not_become_a_weather_outage(healthy, waws, wsettings, wdb,
                                                           weather_url):
    waws.ec2.revoke_security_group_ingress(GroupId=wdb.db_security_group_id,
                                           IpPermissions=_db_rule(wsettings))
    try:
        ssh(wsettings, instance_ip(waws, "weather-app"), "sudo systemctl restart weather")
        time.sleep(5)
        status, body = get_json(f"{weather_url}/weather?zip=60616")
        assert status == 200
        assert body["cache"] == "unavailable"
        assert http_status(f"{weather_url}/history") == 503
    finally:
        waws.ec2.authorize_security_group_ingress(GroupId=wdb.db_security_group_id,
                                                  IpPermissions=_db_rule(wsettings))
    time.sleep(35)
    status, body = get_json(f"{weather_url}/weather?zip=60616")
    assert status == 200 and body["cache"] != "unavailable"


def test_advisor_degrades_and_weather_recovers(healthy, waws, wsettings, wouts, weather_url,
                                               advisor_url):
    group = waws.autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[wsettings.weather.asg_name])["AutoScalingGroups"][0]
    victim = next(i["InstanceId"] for i in group["Instances"]
                  if i["LifecycleState"] == "InService")
    waws.autoscaling.terminate_instance_in_auto_scaling_group(
        InstanceId=victim, ShouldDecrementDesiredCapacity=False)
    start = time.time()
    saw_outage, saw_502, recovered_after, longest_advice_call = False, False, None, 0.0
    while time.time() - start < 420:
        began = time.time()
        advice_status, _ = get_json(f"{advisor_url}/advice?zip=33101", timeout=40)
        longest_advice_call = max(longest_advice_call, time.time() - began)
        saw_502 = saw_502 or advice_status == 502
        assert http_status(f"{advisor_url}/") == 200, "advisor home page must stay up"
        if http_status(f"{weather_url}/weather?zip=60616", timeout=5) != 200:
            saw_outage = True
        elif saw_outage:
            recovered_after = time.time() - start
            break
        time.sleep(5)
    print(f"\nweather recovered after {recovered_after}s; "
          f"slowest advisor call during the outage: {longest_advice_call:.1f}s")
    assert saw_outage, "the weather service never went down"
    assert saw_502, "the advisor never reported weather_service_unavailable"
    assert longest_advice_call < 30, "the advisor hung instead of failing quickly"
    assert recovered_after is not None and recovered_after <= 300
