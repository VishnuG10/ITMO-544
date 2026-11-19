#!/usr/bin/env python3
import argparse
import logging

from stack import usage
from stack.wclients import WeatherClients
from stack.wconfig import WeatherSettings
from stack.wstack import WeatherOutputs, delete_weather_stack


def main() -> None:
    """Read .env, save the advisor's counts, and remove everything the create script built."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=".env", help="path to the settings file")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("botocore").setLevel(logging.WARNING)
    settings = WeatherSettings.from_file(args.env)
    counts = None
    try:
        dns = WeatherOutputs.load().alb_dns_name
        counts = usage.fetch_usage(f"http://{dns}:{settings.advisor.listener_port}")
    except FileNotFoundError:
        pass
    delete_weather_stack(settings, WeatherClients.create(settings.region))
    usage.record("deleted", usage=counts)
    print("Stack removed.")


if __name__ == "__main__":
    main()
