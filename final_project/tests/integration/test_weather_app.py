import subprocess
import sys
import time

import pytest
import requests

from tests.whelpers import ROOT, day, get_json

pytestmark = pytest.mark.integration

CANDIDATE_ZIPS = ["30301", "80202", "97201", "02108", "73301"]
FACTS = ["temperature", "wind", "visibility", "uv", "humidity", "rain", "snow"]
ADVICE = ["road_conditions", "dressing", "accessories", "activities", "feels_like", "getting_around"]
NON_US = ["M5V 3L9", "SW1A 1AA", "6061", "606160", "abcde"]


def test_weather_twice_returns_miss_then_hit(healthy, weather_url):
    for zip_code in CANDIDATE_ZIPS:
        status, first = get_json(f"{weather_url}/weather?zip={zip_code}&date={day()}")
        assert status == 200
        if first["cache"] == "miss":
            status, second = get_json(f"{weather_url}/weather?zip={zip_code}&date={day()}")
            assert status == 200 and second["cache"] == "hit"
            assert second["periods"] == first["periods"]
            return
    pytest.skip("every candidate zip was already cached; wait 30 minutes and re-run")


def test_a_real_report_has_all_ten_items_in_both_periods(healthy, advisor_url):
    began = time.time()
    status, body = get_json(f"{advisor_url}/advice?zip=60616&date={day(2)}&gender=neutral", timeout=40)
    took = time.time() - began
    print(f"\nnew report took {took:.1f} s (source={body['source']}, saved={body['saved']})")
    assert status == 200
    assert body["city"] == "Chicago" and body["timezone"] == "America/Chicago"
    for period in ("morning", "night"):
        items = body["periods"][period]["items"]
        assert list(items) == FACTS
        for name in FACTS:
            assert items[name]["text"]
            assert items[name]["available"] or items[name]["reason"]
        assert items["temperature"]["available"] and items["temperature"]["source"]
        assert items["rain"]["category"] in ("Heavy", "Mild", "No")
        assert items["snow"]["category"] in ("Heavy", "Mild", "No")
        assert sorted(body["advice"][period]) == sorted(ADVICE)
        assert all(len(body["advice"][period][name]) >= 10 for name in ADVICE)
    assert body["source"] in ("gemini", "fallback")
    assert body["saved"] or took < 20


def test_the_same_request_again_is_fast(healthy, advisor_url):
    url = f"{advisor_url}/advice?zip=60616&date={day(2)}&gender=neutral"
    get_json(url, timeout=40)
    began = time.time()
    status, body = get_json(url, timeout=40)
    took = time.time() - began
    print(f"\ncached report took {took:.2f} s (saved={body['saved']}, weather={body['weatherCache']})")
    assert status == 200
    assert body["saved"] or body["weatherCache"] == "hit"
    assert took < 3


def test_postal_codes_from_other_countries_are_refused(healthy, advisor_url, weather_url):
    for code in NON_US:
        resp = requests.get(f"{advisor_url}/advice", params={"zip": code, "date": day()}, timeout=15)
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_zip"
        assert "US locations only" in resp.json()["message"]
        assert requests.get(f"{weather_url}/weather", params={"zip": code, "date": day()},
                            timeout=15).status_code == 400


def test_an_unknown_zip_and_bad_dates_have_their_own_answers(healthy, advisor_url):
    status, body = get_json(f"{advisor_url}/advice?zip=00000&date={day()}")
    assert status == 404 and body["error"] == "zip_not_found"
    for bad, error in ((day(-3), "date_out_of_range"), (day(12), "date_out_of_range"),
                       ("2026-02-30", "invalid_date"), ("tomorrow", "invalid_date")):
        status, body = get_json(f"{advisor_url}/advice?zip=60616&date={bad}")
        assert status == 400 and body["error"] == error


def test_the_page_escapes_what_was_typed(healthy, advisor_url):
    hostile = '"><script>alert(1)</script>'
    resp = requests.post(f"{advisor_url}/report", timeout=15, data={
        "zip": hostile, "date": day(), "gender": "neutral", "email": "someone@example.com"})
    assert resp.status_code == 400
    assert "<script>alert(1)" not in resp.text
    assert "US locations only" in resp.text


def test_the_report_page_shows_the_table_and_the_email_button(healthy, advisor_url):
    resp = requests.post(f"{advisor_url}/report", timeout=40, data={
        "zip": "60616", "date": day(2), "gender": "female", "email": "someone@example.com"})
    assert resp.status_code == 200
    for label in ("Temperature", "Wind speed", "Visibility", "UV", "Humidity", "Rain", "Snow",
                  "Suggested road conditions", "Suggested dressing", "Suggested accessories to carry",
                  "Suggested activities", "How it will feel", "Getting around"):
        assert f"<th>{label}</th>" in resp.text
    assert "Morning" in resp.text and "Night" in resp.text
    assert "Suggestion source:" in resp.text
    assert "Email this report to someone@example.com" in resp.text


def test_the_usage_page_has_counts_only(healthy, advisor_url):
    status, body = get_json(f"{advisor_url}/usage")
    assert status == 200
    assert sorted(body) == ["date", "emails_sent", "gemini_calls", "gemini_daily_cap", "gemini_failures"]


def test_smoke_script_passes(healthy):
    result = subprocess.run([sys.executable, "app_smoke_test.py"], cwd=ROOT / "testing",
                            capture_output=True, text=True, timeout=300)
    print("\n" + result.stdout)
    assert result.returncode == 0, result.stdout