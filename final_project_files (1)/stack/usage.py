"""A small log of when the stack and the database were built and removed. budget.py reads it."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

LOG_FILE = "usage_log.jsonl"
COUNT_KEYS = ("gemini_calls", "gemini_failures", "emails_sent")


def record(event: str, directory: str | Path = ".", now: float | None = None, **details) -> None:
    """Append one event to the log."""
    entry = {"event": event, "time": now if now is not None else time.time(), **details}
    with open(Path(directory) / LOG_FILE, "a") as handle:
        handle.write(json.dumps(entry) + "\n")


def read(directory: str | Path = ".") -> list[dict]:
    """All events in the order they were written. A missing log is an empty one."""
    path = Path(directory) / LOG_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def fetch_usage(advisor_url: str, timeout: int = 15) -> dict | None:
    """The advisor's counts for today, or None if the stack cannot be reached."""
    try:
        with urllib.request.urlopen(advisor_url + "/usage", timeout=timeout) as response:
            return json.loads(response.read())
    except Exception:
        return None


def run_hours(events: list[dict], start: str, end: str, now: float | None = None) -> tuple[float, bool]:
    """Hours between each start event and the next end event. Returns (hours, still_running)."""
    now = now if now is not None else time.time()
    total, started = 0.0, None
    for item in events:
        if item["event"] == start and started is None:
            started = item["time"]
        elif item["event"] == end and started is not None:
            total += max(item["time"] - started, 0) / 3600
            started = None
    if started is not None:
        total += max(now - started, 0) / 3600
    return total, started is not None


def count_totals(events: list[dict]) -> dict:
    """Add up the daily counts saved when stacks were deleted. For one date the highest reading wins."""
    best: dict[str, dict] = {}
    for item in events:
        counts = item.get("usage")
        if item["event"] == "deleted" and counts:
            old = best.get(counts["date"], {})
            best[counts["date"]] = {key: max(counts.get(key, 0), old.get(key, 0)) for key in COUNT_KEYS}
    return {key: sum(day[key] for day in best.values()) for key in COUNT_KEYS}
