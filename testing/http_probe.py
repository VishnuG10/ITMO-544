"""Polls an HTTP endpoint and reports up/down transitions."""


import time

import requests


def probe_once(url: str, timeout: float = 3.0) -> bool:
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code < 500
    except requests.RequestException:
        return False


def wait_for_state(
    url: str,
    want_up: bool,
    poll_interval: float = 2.0,
    max_wait: float = 600.0,
    log_rows: list | None = None,
):
    start = time.monotonic()

    while time.monotonic() - start < max_wait:
        elapsed = time.monotonic() - start
        is_up = probe_once(url)

        if log_rows is not None:
            log_rows.append([round(elapsed, 2), is_up])

        if is_up == want_up:
            return elapsed

        time.sleep(poll_interval)

    return None
