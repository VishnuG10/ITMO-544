"""Retrieve real API responses for one ZIP code and report which of the ten outputs each source covers."""
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

USER_AGENT = "itmo544-final-project (student coursework)"
FIXTURES = Path(__file__).parent / "fixtures" / "real"
PERIODS = {"Morning": range(6, 12), "Night": range(18, 24)}
NWS_LAYERS = ["temperature", "apparentTemperature", "windSpeed", "windGust", "visibility",
              "relativeHumidity", "probabilityOfPrecipitation", "quantitativePrecipitation",
              "snowfallAmount", "skyCover"]
OPEN_METEO_VARS = ["temperature_2m", "apparent_temperature", "wind_speed_10m", "wind_gusts_10m",
                   "visibility", "relative_humidity_2m", "precipitation_probability", "precipitation",
                   "snowfall", "uv_index"]
DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?")


def fetch(url, accept="application/json"):
    """Return (status, parsed JSON or None) for a GET request."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except ValueError:
            return error.code, None
    except (urllib.error.URLError, TimeoutError) as error:
        return 0, {"error": str(error)}


def save(name, body):
    """Write one real response to the fixtures folder."""
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(json.dumps(body, indent=2))


def parse_valid_time(text):
    """Turn '2026-10-31T18:00:00+00:00/PT3H' into (start, hours)."""
    start_text, duration = text.split("/")
    start = datetime.fromisoformat(start_text)
    d, h, m = (int(g or 0) for g in DURATION.fullmatch(duration).groups())
    return start, d * 24 + h + (1 if m else 0)


def hourly_from_layer(layer, tz):
    """Expand a gridpoint layer into {local hour start: value}."""
    hours = {}
    for item in layer.get("values", []):
        if item["value"] is None:
            continue
        start, count = parse_valid_time(item["validTime"])
        for step in range(count):
            hours[(start + timedelta(hours=step)).astimezone(tz).replace(tzinfo=None)] = item["value"]
    return hours


def hourly_from_open_meteo(body, name):
    """Expand one Open-Meteo hourly variable into {local hour start: value}."""
    times = body["hourly"]["time"]
    values = body["hourly"][name]
    return {datetime.fromisoformat(t): v for t, v in zip(times, values) if v is not None}


def coverage(hours, first_day, days=7):
    """Count covered hours per day and period, as a list of (Morning, Night) tuples."""
    rows = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        counts = []
        for hour_range in PERIODS.values():
            counts.append(sum(1 for h in hour_range if datetime(day.year, day.month, day.day, h) in hours))
        rows.append(tuple(counts))
    return rows


def describe(source, name, unit, hours, first_day):
    """Print one line of the source map for one field."""
    if not hours:
        print(f"{source:<11}{name:<28}{unit:<22}NOT RETURNED")
        return
    last = max(hours)
    example = hours[min(hours)]
    cells = " ".join(f"{m}/{n}" for m, n in coverage(hours, first_day))
    print(f"{source:<11}{name:<28}{unit:<22}e.g. {str(example)[:7]:<8}last {last:%m-%d %H:%M}  M/N hours per day: {cells}")


def main():
    zip_code = sys.argv[1] if len(sys.argv) > 1 else "60616"
    print(f"== 1. ZIP lookup for {zip_code} ==")
    status, zbody = fetch(f"https://api.zippopotam.us/us/{zip_code}")
    save(f"zippopotam_{zip_code}", zbody)
    print("status", status)
    if status != 200:
        return
    place = zbody["places"][0]
    lat, lon = place["latitude"], place["longitude"]
    print(place["place name"], place["state abbreviation"], lat, lon)

    status, missing = fetch("https://api.zippopotam.us/us/00000")
    save("zippopotam_00000", missing)
    print("unknown ZIP 00000 ->", status, missing)

    print("== 2. NWS points ==")
    status, points = fetch(f"https://api.weather.gov/points/{lat},{lon}", "application/geo+json")
    save("nws_points", points)
    props = points["properties"]
    tz = ZoneInfo(props["timeZone"])
    print("status", status, "timeZone", props["timeZone"], "grid", props["gridId"], props["gridX"], props["gridY"])

    print("== 3. NWS forecast, forecastHourly, gridpoints ==")
    for name, key in (("nws_forecast", "forecast"), ("nws_forecast_hourly", "forecastHourly"),
                      ("nws_gridpoints", "forecastGridData")):
        status, body = fetch(props[key], "application/geo+json")
        save(name, body)
        print(name, "status", status)
        if name == "nws_forecast":
            periods = body["properties"]["periods"]
            print("  forecast periods:", len(periods), "last ends", periods[-1]["endTime"])
            print("  period fields:", sorted(periods[0]))
        if name == "nws_forecast_hourly":
            periods = body["properties"]["periods"]
            print("  hourly periods:", len(periods), "last ends", periods[-1]["endTime"])
            print("  hourly fields:", sorted(periods[0]))
        if name == "nws_gridpoints":
            grid = body["properties"]

    print("== 4. Open-Meteo (candidate second source) ==")
    query = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "hourly": ",".join(OPEN_METEO_VARS), "timezone": "auto",
        "forecast_days": 7, "temperature_unit": "fahrenheit", "wind_speed_unit": "mph",
        "precipitation_unit": "inch"})
    status, meteo = fetch(f"https://api.open-meteo.com/v1/forecast?{query}")
    save("open_meteo", meteo)
    print("status", status)

    first_day = datetime.now(tz).date()
    print(f"== 5. Coverage per field, local dates {first_day} to {first_day + timedelta(days=6)} ==")
    print("Each pair is Morning/Night hours covered (6 is full, 0 is a gap).")
    for layer_name in NWS_LAYERS:
        layer = grid.get(layer_name)
        if layer is None:
            describe("NWS grid", layer_name, "", {}, first_day)
            continue
        describe("NWS grid", layer_name, layer.get("uom", ""), hourly_from_layer(layer, tz), first_day)
    if status == 200:
        units = meteo.get("hourly_units", {})
        for var in OPEN_METEO_VARS:
            if var not in meteo.get("hourly", {}):
                describe("Open-Meteo", var, "", {}, first_day)
                continue
            describe("Open-Meteo", var, units.get(var, ""), hourly_from_open_meteo(meteo, var), first_day)
    print(f"Real responses saved in {FIXTURES}")


if __name__ == "__main__":
    main()
