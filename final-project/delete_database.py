#!/usr/bin/env python3
"""Delete the database with no snapshot and no backup"""
import argparse
import logging

from stack.database import delete_database
from stack.wclients import WeatherClients
from stack.wconfig import WeatherSettings


def main() -> None:
    """Read .env and remove the database and everything built for it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("botocore").setLevel(logging.WARNING)
    settings = WeatherSettings.from_file(args.env)
    delete_database(settings, WeatherClients.create(settings.region))


if __name__ == "__main__":
    main()
