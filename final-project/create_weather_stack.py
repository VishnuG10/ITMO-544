#!/usr/bin/env python3
"""Build the weather stack: the Python replacement for create_weather_stack.sh."""
import argparse
import logging
import sys

from stack.database import DbOutputs
from stack.network import StackError
from stack.wclients import WeatherClients
from stack.wconfig import WeatherSettings
from stack.wstack import create_weather_stack


def main() -> None:
    """Read .env and db_outputs.env, build the stack, and write the outputs files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("botocore").setLevel(logging.WARNING)
    settings = WeatherSettings.from_file(args.env)
    try:
        db = DbOutputs.load()
    except FileNotFoundError:
        sys.exit("db_outputs.env was not found. Run python3 create_database.py first (Part E).")
    try:
        outputs = create_weather_stack(settings, WeatherClients.create(settings.region), db.db_host)
    except StackError as err:
        sys.exit(str(err))
    outputs.save()

    dns = outputs.alb_dns_name
    print()
    print(f"Done. ALB DNS name: {dns}")
    print(f"Weather service: http://{dns}:{settings.weather.listener_port}/weather?zip=60616"
          " (saved lookups: /history)")
    print(f"Advisor page:    http://{dns}:{settings.advisor.listener_port}/")
    print("Give the target groups 2 to 3 minutes to report healthy.")


if __name__ == "__main__":
    main()
