#!/usr/bin/env python3
"""Cost report: hours the stack and the database ran, Gemini calls counted, and what is not free."""
import argparse

from stack import usage

STACK_HOURLY = {
    "Application Load Balancer": 0.0225,
    "Load balancer capacity units (small traffic)": 0.008,
    "Two t3.micro instances (one per tier)": 2 * 0.0104,
    "Public IPv4 addresses (two instances, two for the ALB)": 4 * 0.005,
}
DATABASE_HOURLY = {
    "RDS db.t4g.micro instance": 0.016,
    "RDS storage, 20 GB of gp2": 20 * 0.115 / 730,
}
BUDGET = 2.00
FREE = ["Gemini (free tier)", "National Weather Service", "Open-Meteo (non-commercial use)",
        "zippopotam.us", "SSM standard parameters", "Amazon SNS email (within the free tier)"]


def price_lines(prices: dict) -> list[str]:
    """One line per priced item."""
    return [f"    {name:<56} ${price:.4f} per hour" for name, price in prices.items()]


def report(directory: str = ".") -> str:
    """Build the text of the report from usage_log.jsonl."""
    events = usage.read(directory)
    stack_hours, stack_running = usage.run_hours(events, "created", "deleted")
    db_hours, db_running = usage.run_hours(events, "db_created", "db_deleted")
    totals = usage.count_totals(events)
    stack_rate, db_rate = sum(STACK_HOURLY.values()), sum(DATABASE_HOURLY.values())
    cost = stack_hours * stack_rate + db_hours * db_rate
    running = "   (still running, delete it now)"
    lines = ["Weather Advisor cost report", "=" * 27, ""]
    lines.append(f"Hours the stack ran: {stack_hours:.2f}" + (running if stack_running else ""))
    lines.append(f"Stack cost per hour: ${stack_rate:.4f}")
    lines += price_lines(STACK_HOURLY)
    lines.append(f"Hours the database ran: {db_hours:.2f}" + (running if db_running else ""))
    lines.append(f"Database cost per hour: ${db_rate:.4f}")
    lines += price_lines(DATABASE_HOURLY)
    lines += ["", f"Estimated total: ${cost:.2f} of the ${BUDGET:.2f} budget ({cost / BUDGET * 100:.0f} percent)", ""]
    lines.append(f"Gemini calls counted: {totals['gemini_calls']} (failures: {totals['gemini_failures']})")
    lines.append(f"Emails accepted by Amazon SNS: {totals['emails_sent']}")
    lines.append("Free of charge here: " + ", ".join(FREE))
    lines += ["", "Prices are us-east-2 on-demand list prices written into this file.",
              "Compare the total with the Billing page in the console."]
    return "\n".join(lines)


def main() -> None:
    """Print the report for the usage log in this folder."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=".", help="folder that holds usage_log.jsonl")
    args = parser.parse_args()
    print(report(args.dir))


if __name__ == "__main__":
    main()
