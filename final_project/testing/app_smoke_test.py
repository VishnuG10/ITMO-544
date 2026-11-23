import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path):
    """Read KEY=VALUE lines from a .env style file into a dict."""
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def get(url, timeout=30):
    """Return (status, parsed JSON or text). HTTP errors are returned, not raised."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body, status = resp.read().decode(), resp.status
    except urllib.error.HTTPError as err:
        body, status = err.read().decode(), err.code
    except (urllib.error.URLError, TimeoutError) as err:
        return 0, {"error": f"no connection: {err}"}
    try:
        return status, json.loads(body)
    except ValueError:
        return status, body


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", default="60616", help="zip code for the happy-path checks")
    args = parser.parse_args()

    env = load_dotenv(ROOT / ".env")
    alb = load_dotenv(ROOT / "stack_outputs.env")["ALB_DNS_NAME"]
    weather = f"http://{alb}:{env['WEATHER_LISTENER_PORT']}"
    advisor = f"http://{alb}:{env['ADVISOR_LISTENER_PORT']}"
    day = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    results = []

    def check(name, ok, detail=""):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

    def has_all(parts, names):
        return all(set(names) <= set(parts.get(period, {})) for period in ("morning", "night"))

    facts = ["temperature", "wind", "visibility", "uv", "humidity", "rain", "snow"]
    status, body = get(f"{weather}/weather?zip={args.zip}&date={day}")
    ok = status == 200 and has_all({k: v["items"] for k, v in body["periods"].items()}, facts)
    check("weather tier returns seven facts for Morning and Night", ok, f"status {status}")

    status, body = get(f"{weather}/weather?zip={args.zip}&date={day}")
    check("a repeat call is answered from the database (cache hit)",
          status == 200 and body.get("cache") == "hit",
          f"cache={body.get('cache') if isinstance(body, dict) else body}")

    status, body = get(f"{weather}/weather?zip=abc&date={day}")
    check("weather tier rejects a bad zip with 400", status == 400, f"status {status}")

    status, body = get(f"{weather}/weather?zip=00000&date={day}")
    check("weather tier returns 404 for an unknown zip", status == 404, f"status {status}")

    status, body = get(f"{weather}/weather?zip={args.zip}&date=2020-01-01")
    check("weather tier rejects a date outside the 7 day window with 400", status == 400,
          f"status {status}")

    t0 = time.time()
    status, body = get(f"{advisor}/advice?zip={args.zip}&date={day}&gender=neutral", timeout=45)
    advice_items = ["road_conditions", "dressing", "accessories", "activities", "feels_like", "getting_around"]
    ok = status == 200 and has_all(body["advice"], advice_items)
    check("advisor tier returns six advice items for Morning and Night", ok,
          f"status {status}, {time.time() - t0:.1f}s")
    if ok:
        print(f"      source={body.get('source')} model={body.get('model')} saved={body.get('saved')}")
        print(f"      morning dressing: {body['advice']['morning']['dressing']}")
        print(f"      morning activities: {body['advice']['morning']['activities']}")
    check("advice came from Gemini (not the fallback)",
          isinstance(body, dict) and body.get("source") == "gemini",
          "fallback means the key, quota, cap or model name needs a look")

    status, body = get(f"{advisor}/advice?zip=M5V%203L9&date={day}")
    check("advisor tier refuses a Canadian postal code with the US-only message",
          status == 400 and "US locations only" in str(body), f"status {status}")

    status, body = get(f"{advisor}/")
    check("advisor home page loads", status == 200 and "<form" in str(body), f"status {status}")

    print(f"\n{sum(results)} of {len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()