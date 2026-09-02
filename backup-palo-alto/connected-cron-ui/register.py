#!/usr/bin/env python3
"""Tell CronBoard this job exists. Reads docker-compose env only. Does not change backup code."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


START_HINTS = (
    "running scheduled",
    "⏰",
)
END_HINTS = (
    "completed successfully",
    "backup failed",
    "backup run failed",
    "failed. see logs",
)
QUIET_AFTER_LOG_SECONDS = 8
BLOCK_RETRY_SECONDS = 30 * 60


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def cronboard_on() -> bool:
    return env("USE_CRONBOARD_UI").lower() in {"1", "true", "yes", "on"}


class RunWatch:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.finished_this_tick = False
        self.tick_id = ""
        self.last_run: str | None = None
        self.last_log_at: float | None = None

    def on_line(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        low = text.lower()
        with self._lock:
            self.last_log_at = time.time()
            if any(hint in low or hint in text for hint in START_HINTS):
                self.running = True
                self.finished_this_tick = False
                self.last_run = datetime.now(timezone.utc).isoformat()
            if any(hint in low for hint in END_HINTS):
                self.running = False
                self.finished_this_tick = True

    def status(self, expr: str) -> str:
        now = datetime.now(timezone.utc)
        prev = None
        try:
            from croniter import croniter

            prev = croniter(expr, now).get_prev(datetime)
            nxt = croniter(expr, now).get_next(datetime)
            until_next = (nxt - now).total_seconds()
            if until_next <= 2:
                elapsed = 0.0
                prev = nxt
            else:
                elapsed = (now - prev).total_seconds()
        except Exception:
            elapsed = 999.0
        tick_id = prev.isoformat() if prev else ""
        with self._lock:
            if tick_id and tick_id != self.tick_id:
                self.tick_id = tick_id
                self.finished_this_tick = False
                self.last_log_at = None
            if self.finished_this_tick:
                return "Idle"
            if self.last_log_at is not None:
                quiet = time.time() - self.last_log_at
                if quiet >= QUIET_AFTER_LOG_SECONDS:
                    self.running = False
                    self.finished_this_tick = True
                    return "Idle"
                return "Running"
            if self.running:
                return "Running"
            # cron just fired; wait for backup log lines
            if elapsed <= 12:
                if not self.last_run:
                    self.last_run = datetime.now(timezone.utc).isoformat()
                return "Running"
            return "Idle"


WATCH = RunWatch()
LOG_BUFFER: list[str] = []
LOG_LOCK = threading.Lock()


def tail_log(path: str) -> None:
    while not os.path.exists(path):
        time.sleep(0.4)
    with open(path, encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        while True:
            line = handle.readline()
            if not line:
                time.sleep(0.25)
                continue
            WATCH.on_line(line)
            text = line.rstrip()
            if not text or "cronboard register" in text.lower():
                continue
            with LOG_LOCK:
                LOG_BUFFER.append(text[:2000])


def flush_logs(url: str) -> None:
    with LOG_LOCK:
        lines = LOG_BUFFER[:]
        LOG_BUFFER.clear()
    if not lines:
        return
    body = json.dumps(
        {
            "name": env("JOB_NAME") or env("PUSHGATEWAY_INSTANCE") or "backup-job",
            "lines": lines,
        }
    ).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/logs",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        resp.read()


def job_status() -> str:
    expr = env("CRONJOB_SCHEDULE")
    if not expr:
        return "Idle"
    return WATCH.status(expr)


def payload() -> dict:
    status = job_status()
    data = {
        "name": env("JOB_NAME") or env("PUSHGATEWAY_INSTANCE") or "backup-job",
        "schedule": env("CRONJOB_SCHEDULE") or "0 9 * * 5",
        "description": env("JOB_DESCRIPTION", "Backup job"),
        "runtime": env("JOB_RUNTIME", "docker"),
        "host_name": env("HOST_NAME"),
        "status": status,
    }
    node_ip = env("NODE_IP") or env("HOST_IP")
    if node_ip:
        data["host_address"] = node_ip
    if WATCH.last_run:
        data["last_run"] = WATCH.last_run
    elif status == "Running":
        data["last_run"] = datetime.now(timezone.utc).isoformat()
    return data


def register_once(url: str) -> str:
    body = json.dumps(payload()).encode()
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/register",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode())
    return data.get("status", "unknown")


def main() -> None:
    if not cronboard_on():
        print("CronBoard off (set USE_CRONBOARD_UI=true to connect)", flush=True)
        return
    url = env("CRONBOARD_URL")
    if not url:
        raise SystemExit("CRONBOARD_URL is empty — set it in compose or the CronJob env")
    log_path = env("JOB_LOG_PATH", "/app/cronjob.log") or "/app/cronjob.log"
    threading.Thread(target=tail_log, args=(log_path,), daemon=True).start()
    interval = int(env("REGISTER_INTERVAL", "2") or "2")
    wait_blocked = int(env("BLOCK_RETRY_SECONDS", str(BLOCK_RETRY_SECONDS)) or BLOCK_RETRY_SECONDS)
    while True:
        try:
            status = register_once(url)
            if status == "blocked":
                print(
                    f"denied 3 times; will ask again in {wait_blocked // 60} minutes",
                    flush=True,
                )
                time.sleep(wait_blocked)
                continue
            flush_logs(url)
        except urllib.error.URLError as exc:
            print(f"cronboard unreachable: {exc}", flush=True)
        except Exception as exc:
            print(f"register error: {exc}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    main()
