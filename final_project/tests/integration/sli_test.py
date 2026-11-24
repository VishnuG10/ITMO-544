import math
import statistics
import time

import pytest
import requests

from tests.whelpers import day

pytestmark = pytest.mark.integration

SAMPLES = 20
SLO_MS = 120


def measure(session, url, samples=SAMPLES):
    """Send the requests one after another. Returns (milliseconds of each successful request, every status)."""
    latencies, statuses = [], []
    for _ in range(samples):
        began = time.perf_counter()
        try:
            status = session.get(url, timeout=10).status_code
        except requests.RequestException:
            status = 0
        elapsed = (time.perf_counter() - began) * 1000
        statuses.append(status)
        if status == 200:
            latencies.append(elapsed)
    return latencies, statuses


def percentile(values, share):
    """The nearest-rank percentile of a list of numbers."""
    ordered = sorted(values)
    return ordered[max(math.ceil(share * len(ordered)) - 1, 0)]


def report(name, url, latencies, statuses):
    """Print the SLI report and return True when the SLO is met."""
    average = statistics.mean(latencies) if latencies else float("inf")
    passed = len(latencies) == len(statuses) and average < SLO_MS
    lines = [f"\nSLI: {name}, {len(statuses)} samples",
             f"  URL       {url}",
             f"  Success   {len(latencies)} of {len(statuses)} returned HTTP 200"]
    if latencies:
        lines += [f"  Average   {average:.1f} ms",
                  f"  Min       {min(latencies):.1f} ms",
                  f"  Median    {statistics.median(latencies):.1f} ms",
                  f"  p95       {percentile(latencies, 0.95):.1f} ms",
                  f"  Max       {max(latencies):.1f} ms",
                  "  Samples   " + ", ".join(f"{value:.0f}" for value in latencies) + " ms"]
    failures = [status for status in statuses if status != 200]
    if failures:
        lines.append("  Failed    HTTP status " + ", ".join(str(status or "none") for status in failures))
    if 429 in failures:
        lines.append("  Note      429 means the visitor limit was reached. Raise VISITOR_PER_MINUTE in .env")
    lines += [f"  SLO       average under {SLO_MS} ms, every request successful",
              f"  Result    {'PASS' if passed else 'FAIL'}"]
    print("\n".join(lines))
    return passed


def test_sli_advisor_page(healthy, advisor_url):
    url = f"{advisor_url}/"
    with requests.Session() as session:
        session.get(url, timeout=10)
        latencies, statuses = measure(session, url)
    assert report("advisor page response time", url, latencies, statuses), "the SLO was missed"


def test_sli_saved_report(healthy, advisor_url, wsettings):
    if wsettings.visitor_per_minute < SAMPLES + 5:
        pytest.skip(f"VISITOR_PER_MINUTE is {wsettings.visitor_per_minute}. Raise it to at least {SAMPLES + 5} "
                    "in .env and run create_weather_stack.py, or the visitor limit stops the samples")
    url = f"{advisor_url}/advice?zip=60616&date={day(2)}&gender=neutral"
    with requests.Session() as session:
        warm_up = session.get(url, timeout=45)
        if warm_up.status_code == 429:
            pytest.skip("the visitor limit for this minute is already used up. Wait a minute and run again")
        assert warm_up.status_code == 200, f"the warm-up request returned HTTP {warm_up.status_code}"
        if warm_up.json().get("source") != "gemini":
            pytest.skip("the warm-up report used the rule-based fallback, so it was not saved. Run again in a minute")
        latencies, statuses = measure(session, url)
    assert report("saved report response time", url, latencies, statuses), "the SLO was missed"