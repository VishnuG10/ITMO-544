"""Break things on purpose and check the stack copes. Slow. Run: pytest -m chaos -s"""
import random
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.whelpers import day, get_json, http_status

pytestmark = pytest.mark.chaos

CAP_ZIPS = ["60616", "10001", "94103"]
GENDERS = ["female", "male", "neutral"]


def test_going_over_the_gemini_cap_gives_labeled_fallback_advice(healthy, wsettings, weather_url,
                                                                 advisor_url):
    days = [day(offset) for offset in range(1, 6)]
    for zip_code in CAP_ZIPS:
        for date in days:
            assert get_json(f"{weather_url}/weather?zip={zip_code}&date={date}")[0] == 200
    combos = [(z, d, g) for z in CAP_ZIPS for d in days for g in GENDERS]
    picked = random.sample(combos, wsettings.gemini_per_minute + 6)
    if time.time() % 60 > 20:
        time.sleep(61 - time.time() % 60)

    def ask(combo):
        zip_code, date, gender = combo
        return get_json(f"{advisor_url}/advice?zip={zip_code}&date={date}&gender={gender}", timeout=45)

    with ThreadPoolExecutor(max_workers=len(picked)) as pool:
        answers = list(pool.map(ask, picked))
    assert all(status == 200 for status, _ in answers), [status for status, _ in answers]
    fresh = [body for _, body in answers if not body["saved"]]
    from_gemini = [body for body in fresh if body["source"] == "gemini"]
    fallback = [body for body in fresh if body["source"] == "fallback"]
    capped = [body for body in fallback if "limit" in body["note"]]
    print(f"\n{len(picked)} requests at once with a cap of {wsettings.gemini_per_minute} per minute: "
          f"{len(from_gemini)} from Gemini, {len(fallback)} labeled fallback "
          f"({len(capped)} because of the cap), {len(picked) - len(fresh)} saved reports")
    assert len(from_gemini) <= wsettings.gemini_per_minute
    if len(fresh) <= wsettings.gemini_per_minute:
        pytest.skip("too many of the requests were saved reports; run this test again in an hour")
    assert len(fallback) >= len(fresh) - wsettings.gemini_per_minute
    assert capped, "no report said the Gemini limit was reached"
    for body in fallback:
        assert body["model"] is None
        assert all(len(text) >= 10 for period in body["advice"].values() for text in period.values())
        assert body["periods"]["morning"]["items"]["temperature"]["text"]


def test_weather_tier_down_gives_a_degraded_report_and_then_recovers(healthy, waws, wsettings,
                                                                     weather_url, advisor_url):
    saved_url = f"{advisor_url}/advice?zip=60616&date={day(3)}&gender=neutral"
    new_url = f"{advisor_url}/advice?zip=33101&date={day(3)}&gender=neutral"
    status, before = get_json(saved_url, timeout=45)
    assert status == 200
    group = waws.autoscaling.describe_auto_scaling_groups(
        AutoScalingGroupNames=[wsettings.weather.asg_name])["AutoScalingGroups"][0]
    victim = next(i["InstanceId"] for i in group["Instances"]
                  if i["LifecycleState"] == "InService")
    waws.autoscaling.terminate_instance_in_auto_scaling_group(
        InstanceId=victim, ShouldDecrementDesiredCapacity=False)
    start = time.time()
    saw_outage, saw_clear_message, saw_saved, recovered_after, longest = False, False, False, None, 0.0
    while time.time() - start < 420:
        weather_up = http_status(f"{weather_url}/weather?zip=60616&date={day(3)}", timeout=5) == 200
        if not weather_up:
            saw_outage = True
            began = time.time()
            new_status, new_body = get_json(new_url, timeout=40)
            longest = max(longest, time.time() - began)
            if new_status == 502 and new_body and new_body["error"] == "weather_service_unavailable":
                saw_clear_message = True
            saved_status, saved_body = get_json(saved_url, timeout=40)
            if before["source"] == "gemini" and saved_status == 200 and saved_body["saved"]:
                saw_saved = True
            assert http_status(f"{advisor_url}/") == 200, "advisor page must stay up"
        elif saw_outage:
            recovered_after = time.time() - start
            break
        time.sleep(5)
    print(f"\nweather tier recovered after {recovered_after and round(recovered_after)} s; "
          f"slowest new report during the outage: {longest:.1f} s; "
          f"saved report still served: {saw_saved}")
    assert saw_outage, "the weather tier never went down"
    assert saw_clear_message, "the advisor never said that weather data is unavailable"
    assert longest < 30, "the advisor hung instead of failing quickly"
    assert recovered_after is not None and recovered_after <= 300
    if before["source"] == "gemini":
        assert saw_saved, "the saved report was not served while the weather tier was down"
    status, after = get_json(f"{weather_url}/weather?zip=60616&date={day(3)}")
    assert status == 200 and after["cache"] in ("hit", "stale", "miss")
