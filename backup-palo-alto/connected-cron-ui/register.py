#!/usr/bin/env python3
"""Tell CronBoard this job exists. Reads compose / CronJob env only. Does not change backup code."""

from __future__ import annotations

import atexit
import json
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


START_HINTS = (
    "running scheduled",
    "⏰",
    "starting backup",
)
END_HINTS = (
    "completed successfully",
    "backup failed",
    "backup run failed",
    "failed. see logs",
    "configuration saved",
    "backup completed",
)
QUIET_AFTER_LOG_SECONDS = 8
BLOCK_RETRY_SECONDS = 30 * 60


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def cronboard_on() -> bool:
    return env("USE_CRONBOARD_UI").lower() in {"1", "true", "yes", "on"}


def job_schedule() -> str:
    return env("CRONJOB_SCHEDULE") or env("JOB_SCHEDULE") or "0 9 * * 5"


class RunWatch:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.running = False
        self.finished_this_tick = False
        self.tick_id = ""
        self.last_run: str | None = None
        self.last_log_at: float | None = None
        self.stopping = False

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

    def mark_idle(self) -> None:
        with self._lock:
            self.stopping = True
            self.running = False
            self.finished_this_tick = True

    def status(self, expr: str) -> str:
        with self._lock:
            if self.stopping:
                return "Idle"
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
            if elapsed <= 12:
                if not self.last_run:
                    self.last_run = datetime.now(timezone.utc).isoformat()
                return "Running"
            return "Idle"


WATCH = RunWatch()
LOG_BUFFER: list[str] = []
LOG_LOCK = threading.Lock()
_SHUTDOWN = False


def _ingest(line: str) -> None:
    WATCH.on_line(line)
    text = line.rstrip()
    if not text or "cronboard register" in text.lower():
        return
    with LOG_LOCK:
        LOG_BUFFER.append(text[:2000])


def tail_log(path: str) -> None:
    """Follow the log from the start so short Kubernetes runs are not missed."""
    log_file = Path(path)
    while not log_file.exists():
        time.sleep(0.2)
    offset = 0
    while not _SHUTDOWN:
        try:
            size = log_file.stat().st_size
        except OSError:
            time.sleep(0.25)
            continue
        if size < offset:
            offset = 0
        if size == offset:
            time.sleep(0.2)
            continue
        with open(log_file, encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            while True:
                line = handle.readline()
                if not line:
                    offset = handle.tell()
                    break
                _ingest(line)


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
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
    except Exception:
        with LOG_LOCK:
            LOG_BUFFER[:0] = lines


def job_status() -> str:
    return WATCH.status(job_schedule())


def payload() -> dict:
    status = job_status()
    data = {
        "name": env("JOB_NAME") or env("PUSHGATEWAY_INSTANCE") or "backup-job",
        "schedule": job_schedule(),
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


def _final_ping(url: str) -> None:
    global _SHUTDOWN
    _SHUTDOWN = True
    WATCH.mark_idle()
    try:
        register_once(url)
        flush_logs(url)
    except Exception:
        pass


def main() -> None:
    if not cronboard_on():
        print("CronBoard off (set USE_CRONBOARD_UI=true to connect)", flush=True)
        return
    url = env("CRONBOARD_URL")
    if not url:
        raise SystemExit("CRONBOARD_URL is empty — set it in compose or the CronJob env")
    log_path = env("JOB_LOG_PATH", "/app/cronjob.log") or "/app/cronjob.log"
    Path(log_path).touch(exist_ok=True)
    threading.Thread(target=tail_log, args=(log_path,), daemon=True).start()
    interval = int(env("REGISTER_INTERVAL", "2") or "2")
    wait_blocked = int(env("BLOCK_RETRY_SECONDS", str(BLOCK_RETRY_SECONDS)) or BLOCK_RETRY_SECONDS)

    def stop(_signum=None, _frame=None):
        _final_ping(url)
        raise SystemExit(0)

    atexit.register(_final_ping, url)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    while not _SHUTDOWN:
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
