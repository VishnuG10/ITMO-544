#!/usr/bin/env python3
"""Tear the weather stack down: the Python replacement for delete_weather_stack.sh."""
import argparse
import logging

from stack.wclients import WeatherClients
from stack.wconfig import WeatherSettings
from stack.wstack import delete_weather_stack


def main() -> None:
    """Read .env and remove everything the create script built."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("botocore").setLevel(logging.WARNING)
    settings = WeatherSettings.from_file(args.env)
    delete_weather_stack(settings, WeatherClients.create(settings.region))


if __name__ == "__main__":
    main()
