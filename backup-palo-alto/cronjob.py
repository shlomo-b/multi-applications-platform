"""Internal cron scheduling for Palo Alto backup.

This module contains only the cron-loop logic. The actual backup work
is implemented in `palo_alto_backup.run_backup_once`.
"""
import os
import sys
import time
from datetime import datetime, timezone

from croniter import croniter

from palo_alto_backup import run_backup_once

# CRON expression controlling when the backup runs.
# Default: every 2 minutes.
CRONJOB_SCHEDULE = os.environ.get("CRONJOB_SCHEDULE", "*/2 * * * *")


def _describe_cron(expr: str) -> str:
    """
    Return a short, human‑readable description for common cron expressions.

    This is best‑effort: if we don't recognize the pattern, we just echo the
    raw cron expression.
    """
    parts = expr.split()
    if len(parts) != 5:
        return f"according to cron expression '{expr}'"

    minute, hour, day, month, weekday = parts

    # Every N minutes: */N * * * *
    if minute.startswith("*/") and hour == "*" and day == "*" and month == "*" and weekday == "*":
        try:
            n = int(minute[2:])
            unit = "minute" if n == 1 else "minutes"
            return f"every {n} {unit}"
        except ValueError:
            pass

    # Every day at HH:MM:  M H * * *
    if day == "*" and month == "*" and weekday == "*":
        if minute.isdigit() and hour.isdigit():
            return f"every day at {hour.zfill(2)}:{minute.zfill(2)}"

    # Every week on weekday at HH:MM: M H * * W
    if day == "*" and month == "*" and weekday not in ("*", "?"):
        if minute.isdigit() and hour.isdigit():
            weekdays = {
                "0": "Sunday",
                "1": "Monday",
                "2": "Tuesday",
                "3": "Wednesday",
                "4": "Thursday",
                "5": "Friday",
                "6": "Saturday",
            }
            name = weekdays.get(weekday, weekday)
            return f"every week on {name} at {hour.zfill(2)}:{minute.zfill(2)}"

    # Every month on day D at HH:MM: M H D * *
    if month == "*" and weekday == "*" and day not in ("*", "?"):
        if minute.isdigit() and hour.isdigit():
            return f"every month on day {day} at {hour.zfill(2)}:{minute.zfill(2)}"

    # Fallback
    return f"according to cron expression '{expr}'"


def run_cron_loop() -> None:
    """Run backup on a cron-like schedule controlled by env vars."""
    try:
        base = datetime.now(timezone.utc)
        iterator = croniter(CRONJOB_SCHEDULE, base)
    except (ValueError, TypeError) as e:
        print(f"❌ Invalid CRONJOB_SCHEDULE '{CRONJOB_SCHEDULE}': {e}")
        print("   Falling back to a single run and exit.")
        success = run_backup_once()
        sys.exit(0 if success else 1)

    desc = _describe_cron(CRONJOB_SCHEDULE)
    print(
        f"ℹ️  CRONJOB_ENABLED=true. This cron job will run {desc} "
        f"(cron='{CRONJOB_SCHEDULE}'). Starting backup..."
    )
    while True:
        next_run = iterator.get_next(datetime)
        now = datetime.now(timezone.utc)
        sleep_seconds = (next_run - now).total_seconds()
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        print(f"⏰ Running scheduled Palo Alto backup at {datetime.now(timezone.utc).isoformat()}")
        success = run_backup_once()
        if success:
            print("✅ Scheduled Palo Alto backup completed successfully.")
        else:
            print("❌ Scheduled Palo Alto backup failed. See logs for details.")


if __name__ == "__main__":
    run_cron_loop()

