import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stack.config import load_dotenv

REAL = ROOT / "weather-app" / "fixtures" / "real"
PERIODS = {"Morning": range(6, 12), "Night": range(18, 24)}
OUTPUTS = [
    ("Temperature", "temperature", "temperature_2m"),
    ("Wind speed", "windSpeed", "wind_speed_10m"),
    ("Wind gusts", "windGust", "wind_gusts_10m"),
    ("Visibility", "visibility", "visibility"),
    ("UV", None, "uv_index"),
    ("Humidity", "relativeHumidity", "relative_humidity_2m"),
    ("Rain and snow: chance", "probabilityOfPrecipitation", "precipitation_probability"),
    ("Rain: amount", "quantitativePrecipitation", "precipitation"),
    ("Snow: amount", "snowfallAmount", "snowfall"),
]
DURATION = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?")


def fetch(url: str, user_agent: str):
    """Return (status, parsed JSON or None) for a GET request. Status 0 means no connection."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent,
                                                   "Accept": "application/geo+json, application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read())
        except ValueError:
            return err.code, None
    except (urllib.error.URLError, TimeoutError, ValueError):
        return 0, None


def save(name: str, body) -> None:
    """Write one real response to the fixtures folder."""
    REAL.mkdir(parents=True, exist_ok=True)
    (REAL / f"{name}.json").write_text(json.dumps(body, indent=1) + "\n")


def layer_hours(layer: dict, tz: ZoneInfo) -> dict:
    """One NWS gridpoint layer as {local hour: value}."""
    hours = {}
    for item in layer.get("values", []):
        if item["value"] is None:
            continue
        start_text, duration = item["validTime"].split("/")
        days, hrs, minutes = (int(g or 0) for g in DURATION.fullmatch(duration).groups())
        start = datetime.fromisoformat(start_text)
        for step in range(days * 24 + hrs + (1 if minutes else 0)):
            hours[(start + timedelta(hours=step)).astimezone(tz).replace(tzinfo=None)] = item["value"]
    return hours


def layer_steps(layer: dict) -> str:
    """The shortest and longest time one value of a layer covers."""
    steps = []
    for item in layer.get("values", []):
        days, hrs, minutes = (int(g or 0) for g in DURATION.fullmatch(item["validTime"].split("/")[1]).groups())
        steps.append(days * 24 + hrs + (1 if minutes else 0))
    if not steps:
        return ""
    return f"{min(steps)} h" if min(steps) == max(steps) else f"{min(steps)} to {max(steps)} h"


def meteo_hours(meteo: dict, name: str) -> dict:
    """One Open-Meteo hourly list as {local hour: value}."""
    return {datetime.fromisoformat(t): v
            for t, v in zip(meteo["hourly"]["time"], meteo["hourly"][name]) if v is not None}


def coverage(hours: dict, first_day, period: str) -> str:
    """Hours covered in one period on each of seven days, for example '6 6 6 6 3 0 0'."""
    counts = []
    for offset in range(7):
        day = first_day + timedelta(days=offset)
        counts.append(sum(1 for h in PERIODS[period] if datetime(day.year, day.month, day.day, h) in hours))
    return " ".join(str(c) for c in counts)


def row(output, source, field, unit, hours, resolution, first_day) -> list:
    """One line of the evidence table."""
    if not hours:
        return [output, source, field, unit, "NOT RETURNED", "", "", "", ""]
    first = min(hours)
    return [output, source, field, unit, hours[first], f"{max(hours):%Y-%m-%d %H:%M}", resolution,
            coverage(hours, first_day, "Morning"), coverage(hours, first_day, "Night")]


def main() -> int:
    """Fetch, save, print and write the evidence for one zip code."""
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--zip", default="60616", help="a real five digit US zip code")
    args = parser.parse_args()
    env = load_dotenv(ROOT / ".env") if (ROOT / ".env").exists() else {}
    agent = f"(itmo-444-544 weather lab, {env.get('CONTACT_EMAIL', 'student@example.com')})"

    print(f"== 1. Zip lookup: api.zippopotam.us/us/{args.zip} ==")
    status, lookup = fetch(f"https://api.zippopotam.us/us/{args.zip}", agent)
    print("status", status)
    if status != 200:
        print("The zip code was not found or the service did not answer.")
        return 1
    save(f"zippopotam_{args.zip}", lookup)
    place = lookup["places"][0]
    lat, lon = f"{float(place['latitude']):.4f}", f"{float(place['longitude']):.4f}"
    print("fields:", sorted(lookup), "place fields:", sorted(place))
    print(place["place name"], place["state abbreviation"], lat, lon)
    status, unknown = fetch("https://api.zippopotam.us/us/00000", agent)
    save("zippopotam_00000", {"status": status, "body": unknown})
    print("unknown zip 00000 -> status", status, "body", unknown)

    print(f"== 2. NWS points: api.weather.gov/points/{lat},{lon} ==")
    status, points = fetch(f"https://api.weather.gov/points/{lat},{lon}", agent)
    print("status", status)
    if status != 200:
        print("The National Weather Service did not answer. Try again in a minute.")
        return 1
    save(f"nws_points_{args.zip}", points)
    props = points["properties"]
    tz_name = props["timeZone"]
    tz = ZoneInfo(tz_name)
    print("timeZone", tz_name, "grid", props["gridId"], props["gridX"], props["gridY"])

    print("== 3. NWS text forecast, hourly forecast and raw grid ==")
    text_fields, hourly_fields, text_ends, hourly_ends = [], [], "", ""
    status, forecast = fetch(props["forecast"], agent)
    if status == 200:
        save(f"nws_forecast_{args.zip}", forecast)
        periods = forecast["properties"]["periods"]
        text_fields, text_ends = sorted(periods[0]), periods[-1]["endTime"]
        print("forecast: status 200,", len(periods), "periods, last ends", text_ends)
        print("  fields of one period:", text_fields)
    else:
        print("forecast: status", status)
    status, hourly = fetch(props["forecastHourly"], agent)
    if status == 200:
        save(f"nws_forecast_hourly_{args.zip}", hourly)
        periods = hourly["properties"]["periods"]
        hourly_fields, hourly_ends = sorted(periods[0]), periods[-1]["endTime"]
        print("forecastHourly: status 200,", len(periods), "periods, last ends", hourly_ends)
        print("  fields of one period:", hourly_fields)
    else:
        print("forecastHourly: status", status)
    status, grid_body = fetch(props["forecastGridData"], agent)
    print("forecastGridData: status", status)
    if status != 200:
        print("The raw grid did not answer. Try again in a minute.")
        return 1
    save(f"nws_grid_{args.zip}", grid_body)
    grid = grid_body["properties"]
    layers = sorted(k for k, v in grid.items() if isinstance(v, dict) and "values" in v)
    print("  layers in the grid:", layers)

    print("== 4. Open-Meteo: api.open-meteo.com/v1/forecast ==")
    variables = [name for _, _, name in OUTPUTS]
    query = urllib.parse.urlencode({"latitude": lat, "longitude": lon, "hourly": ",".join(variables),
                                    "timezone": tz_name, "forecast_days": 7})
    status, meteo = fetch(f"https://api.open-meteo.com/v1/forecast?{query}", agent)
    print("status", status)
    if status != 200:
        print("Open-Meteo did not answer. Try again in a minute.")
        return 1
    save(f"open_meteo_{args.zip}", meteo)
    print("  units:", meteo["hourly_units"])

    first_day = datetime.fromisoformat(meteo["hourly"]["time"][0]).date()
    rows = []
    for output, layer_name, variable in OUTPUTS:
        layer = grid.get(layer_name) if layer_name else None
        if layer_name is None:
            rows.append([output, "NWS grid", "no such layer", "", "NOT RETURNED", "", "", "", ""])
        else:
            rows.append(row(output, "NWS grid", layer_name, (layer or {}).get("uom", ""),
                            layer_hours(layer, tz) if layer else {}, layer_steps(layer or {}), first_day))
        rows.append(row(output, "Open-Meteo", variable, meteo["hourly_units"].get(variable, ""),
                        meteo_hours(meteo, variable) if variable in meteo["hourly"] else {}, "1 h",
                        first_day))

    header = ["Output", "Source", "Field", "Unit", "Example value", "Last hour covered",
              "Resolution", "Morning hours, days 1 to 7", "Night hours, days 1 to 7"]
    lines = [
        f"Recorded {datetime.now(tz):%Y-%m-%d %H:%M} local time for zip {args.zip} "
        f"({place['place name']}, {place['state abbreviation']}), time zone {tz_name}, "
        f"NWS grid {props['gridId']} {props['gridX']},{props['gridY']}.", "",
        "Each number under Morning and Night is how many of the 6 hours of that period the source "
        "covers, for days 1 to 7. A 0 is a gap.", "",
        "| " + " | ".join(header) + " |", "|" + "---|" * len(header),
    ]
    lines += ["| " + " | ".join(str(cell) for cell in r) + " |" for r in rows]
    lines += [
        "", "**Other responses**", "",
        f"- Zip lookup fields: `{sorted(lookup)}`, place fields: `{sorted(place)}`.",
        f"- Unknown zip `00000`: HTTP status {status_of_unknown(REAL)}.",
        f"- NWS text forecast (`forecast`): last period ends {text_ends}. Fields of one period: `{text_fields}`.",
        f"- NWS hourly forecast (`forecastHourly`): last period ends {hourly_ends}. "
        f"Fields of one period: `{hourly_fields}`.",
        f"- NWS raw grid (`forecastGridData`) layers: `{layers}`.",
        f"- Open-Meteo units: `{meteo['hourly_units']}`.",
    ]
    print(f"== 5. Evidence for local dates {first_day} to {first_day + timedelta(days=6)} ==")
    print("Copy everything between the two lines into section 2.3 of Final Project.md.")
    print("-" * 100)
    for line in lines:
        print(line)
    print("-" * 100)
    print(f"Real responses saved in {REAL.relative_to(ROOT)}")
    return 0


def status_of_unknown(folder: Path) -> int:
    """The status that was recorded for the unknown zip code."""
    return json.loads((folder / "zippopotam_00000.json").read_text())["status"]


if __name__ == "__main__":
    sys.exit(main())