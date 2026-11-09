"""Do the two tiers behave correctly through the load balancer? Run: pytest -m integration"""
import subprocess
import sys

import pytest

from tests.whelpers import ROOT, get_json

pytestmark = pytest.mark.integration

CANDIDATE_ZIPS = ["30301", "80202", "97201", "02108", "73301"]


def test_weather_twice_returns_miss_then_hit(healthy, weather_url):
    for zip_code in CANDIDATE_ZIPS:
        status, first = get_json(f"{weather_url}/weather?zip={zip_code}")
        assert status == 200
        if first["cache"] == "miss":
            status, second = get_json(f"{weather_url}/weather?zip={zip_code}")
            assert status == 200 and second["cache"] == "hit"
            return
    pytest.skip("every candidate zip was already cached; wait 10 minutes and re-run")


def test_history_lists_the_lookup(healthy, weather_url):
    get_json(f"{weather_url}/weather?zip=60616")
    status, body = get_json(f"{weather_url}/history")
    assert status == 200 and body["count"] >= 1
    assert "60616" in [row["zip"] for row in body["lookups"]]


def test_bad_zip_is_400_and_unknown_zip_is_404(healthy, weather_url):
    assert get_json(f"{weather_url}/weather?zip=abc")[0] == 400
    assert get_json(f"{weather_url}/weather?zip=00000")[0] == 404


def test_advice_is_returned(healthy, advisor_url):
    status, body = get_json(f"{advisor_url}/advice?zip=60616")
    assert status == 200
    assert body["advice"]
    assert body["source"] in ("gemini", "fallback")


def test_smoke_script_passes(healthy):
    result = subprocess.run([sys.executable, "app_smoke_test.py"], cwd=ROOT / "testing",
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout
