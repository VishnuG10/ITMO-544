"""The cost report and the usage log behind it. No account, no cost."""
import budget
from stack import usage

HOUR = 3600


def _log(tmp_path, events):
    for name, at, details in events:
        usage.record(name, directory=tmp_path, now=at, **details)


def test_hours_are_counted_between_create_and_delete(tmp_path):
    _log(tmp_path, [("created", 0, {}), ("deleted", 2 * HOUR, {}),
                    ("created", 10 * HOUR, {}), ("deleted", 10.5 * HOUR, {})])
    hours, running = usage.run_hours(usage.read(tmp_path), "created", "deleted", now=20 * HOUR)
    assert round(hours, 2) == 2.5
    assert running is False


def test_a_stack_that_was_not_deleted_is_reported_as_running(tmp_path):
    _log(tmp_path, [("created", 0, {})])
    hours, running = usage.run_hours(usage.read(tmp_path), "created", "deleted", now=3 * HOUR)
    assert hours == 3
    assert running is True
    assert "still running, delete it now" in budget.report(tmp_path)


def test_creating_twice_and_deleting_twice_do_not_double_count(tmp_path):
    _log(tmp_path, [("created", 0, {}), ("created", HOUR, {}), ("deleted", 2 * HOUR, {}),
                    ("deleted", 3 * HOUR, {})])
    hours, running = usage.run_hours(usage.read(tmp_path), "created", "deleted", now=9 * HOUR)
    assert hours == 2
    assert running is False


def test_the_database_hours_are_counted_separately(tmp_path):
    _log(tmp_path, [("db_created", 0, {}), ("created", HOUR, {}), ("deleted", 2 * HOUR, {}),
                    ("db_deleted", 4 * HOUR, {})])
    events = usage.read(tmp_path)
    assert usage.run_hours(events, "created", "deleted", now=9 * HOUR) == (1, False)
    assert usage.run_hours(events, "db_created", "db_deleted", now=9 * HOUR) == (4, False)


def test_counts_are_added_over_days_and_the_highest_reading_of_a_day_wins(tmp_path):
    day1 = {"date": "2026-11-16", "gemini_calls": 12, "gemini_failures": 1, "emails_sent": 2}
    later = {"date": "2026-11-16", "gemini_calls": 20, "gemini_failures": 1, "emails_sent": 3}
    day2 = {"date": "2026-11-17", "gemini_calls": 5, "gemini_failures": 0, "emails_sent": 1}
    _log(tmp_path, [("created", 0, {}), ("deleted", HOUR, {"usage": day1}),
                    ("created", 2 * HOUR, {}), ("deleted", 3 * HOUR, {"usage": later}),
                    ("created", 30 * HOUR, {}), ("deleted", 31 * HOUR, {"usage": day2}),
                    ("created", 40 * HOUR, {}), ("deleted", 41 * HOUR, {"usage": None})])
    assert usage.count_totals(usage.read(tmp_path)) == {
        "gemini_calls": 25, "gemini_failures": 1, "emails_sent": 4}


def test_the_report_has_real_numbers(tmp_path):
    counts = {"date": "2026-11-16", "gemini_calls": 12, "gemini_failures": 1, "emails_sent": 2}
    _log(tmp_path, [("db_created", 0, {}), ("created", 0, {}),
                    ("deleted", 10 * HOUR, {"usage": counts}), ("db_deleted", 10 * HOUR, {})])
    text = budget.report(tmp_path)
    assert "Hours the stack ran: 10.00" in text
    assert "Hours the database ran: 10.00" in text
    assert "Stack cost per hour: $0.0713" in text
    assert "Estimated total: $0.90 of the $2.00 budget (45 percent)" in text
    assert "Gemini calls counted: 12 (failures: 1)" in text
    assert "Emails accepted by Amazon SNS: 2" in text
    assert "still running" not in text


def test_an_empty_log_gives_zero_hours(tmp_path):
    text = budget.report(tmp_path)
    assert "Hours the stack ran: 0.00" in text
    assert "Estimated total: $0.00" in text
