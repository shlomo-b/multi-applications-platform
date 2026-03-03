"""Internal cron scheduling for Juniper switch backup.

This module contains only the cron-loop logic. The actual backup work
is implemented in `juniper-sw.run_backup_once`.
"""
import importlib.util
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from croniter import croniter


def _load_run_backup_once():
    """
    Dynamically load run_backup_once from juniper-sw.py.

    The file name uses a dash, so we can't use a normal Python import.
    """
    module_path = Path(__file__).with_name("juniper-sw.py")
    spec = importlib.util.spec_from_file_location("juniper_sw_module", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run_backup_once"):
        raise AttributeError("juniper-sw.py does not define run_backup_once()")
    return module.run_backup_once


run_backup_once = _load_run_backup_once()

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
        f"ℹ️  CRONJOB_ENABLED=true. This Juniper cron job will run {desc} "
        f"(cron='{CRONJOB_SCHEDULE}'). Starting backup..."
    )
    while True:
        next_run = iterator.get_next(datetime)
        now = datetime.now(timezone.utc)
        sleep_seconds = (next_run - now).total_seconds()
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

        print(f"⏰ Running scheduled Juniper backup at {datetime.now(timezone.utc).isoformat()}")
        success = run_backup_once()
        if success:
            print("✅ Scheduled Juniper backup completed successfully.")
        else:
            print("❌ Scheduled Juniper backup failed. See logs for details.")


if __name__ == "__main__":
    run_cron_loop()

