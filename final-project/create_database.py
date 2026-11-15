#!/usr/bin/env python3
"""Create the PostgreSQL database"""
import argparse
import logging

from stack.database import create_database
from stack.wclients import WeatherClients
from stack.wconfig import WeatherSettings


def main() -> None:
    """Read .env, build the database and write db_outputs.env."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("botocore").setLevel(logging.WARNING)
    settings = WeatherSettings.from_file(args.env)
    outputs = create_database(settings, WeatherClients.create(settings.region))
    outputs.save()

    print()
    print(f"Done. Database endpoint: {outputs.db_host}")
    print("Automated backups are OFF (retention 0) and no final snapshot will be taken on delete.")
    print("Next: python3 create_weather_stack.py. When testing is finished: "
          "python3 delete_weather_stack.py then python3 delete_database.py")

if __name__ == "__main__":
    main()
