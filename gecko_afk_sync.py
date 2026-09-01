#!/usr/bin/env python3
"""Synchronize macOS lock and system-idle intervals into Gecko."""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path

from gecko_store import iso_local, store


ROOT = Path(__file__).resolve().parent
CURSOR = ROOT / "data" / "afk-sync.json"
LOCK_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+).+setting to ([01])")
IDLE_LINE = re.compile(r'"HIDIdleTime"\s*=\s*(\d+)')


def lock_intervals(now: datetime) -> list[dict]:
    scan_start = datetime.combine(now.date() - timedelta(days=1), time(17, 0), now.tzinfo)
    command = [
        "/usr/bin/log",
        "show",
        "--style",
        "compact",
        "--start",
        scan_start.strftime("%Y-%m-%d %H:%M:%S"),
        "--predicate",
        'process == "loginwindow" AND eventMessage CONTAINS[c] "setScreenIsLocked"',
    ]
    output = subprocess.run(command, text=True, capture_output=True, check=True).stdout
    intervals = []
    opened: datetime | None = None
    for line in output.splitlines():
        match = LOCK_LINE.search(line)
        if not match:
            continue
        raw_timestamp, flag = match.groups()
        timestamp = datetime.strptime(raw_timestamp, "%Y-%m-%d %H:%M:%S.%f").replace(
            tzinfo=now.tzinfo
        )
        if flag == "1":
            opened = timestamp
        elif opened and timestamp > opened:
            intervals.append(
                {
                    "id": f"lock:{opened.isoformat(timespec='milliseconds')}",
                    "start": iso_local(opened),
                    "end": iso_local(timestamp),
                    "originalStart": iso_local(opened),
                    "source": "lock",
                    "provisional": False,
                }
            )
            opened = None
    if opened and opened < now:
        intervals.append(
            {
                "id": f"lock:{opened.isoformat(timespec='milliseconds')}",
                "start": iso_local(opened),
                "end": iso_local(now),
                "originalStart": iso_local(opened),
                "source": "lock",
                "provisional": True,
            }
        )
    return intervals


def idle_interval(now: datetime, saved: dict) -> tuple[list[dict], dict]:
    output = subprocess.run(
        ["/usr/sbin/ioreg", "-c", "IOHIDSystem"],
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    match = IDLE_LINE.search(output)
    if not match:
        return [], saved
    idle_seconds = int(match.group(1)) / 1_000_000_000
    intervals = []
    open_idle = saved.get("openIdle")
    if idle_seconds >= 120:
        calculated_start = now - timedelta(seconds=idle_seconds)
        if not open_idle:
            open_idle = {
                "id": f"idle:{calculated_start.isoformat(timespec='seconds')}",
                "start": iso_local(calculated_start),
            }
        intervals.append(
            {
                "id": open_idle["id"],
                "start": open_idle["start"],
                "end": iso_local(now),
                "originalStart": open_idle["start"],
                "source": "idle",
                "provisional": True,
            }
        )
    elif open_idle:
        previous_end = saved.get("lastCheck")
        if previous_end:
            intervals.append(
                {
                    "id": open_idle["id"],
                    "start": open_idle["start"],
                    "end": previous_end,
                    "originalStart": open_idle["start"],
                    "source": "idle",
                    "provisional": False,
                }
            )
        open_idle = None
    return intervals, {"openIdle": open_idle}


def main() -> None:
    now = datetime.now().astimezone()
    saved = json.loads(CURSOR.read_text(encoding="utf-8")) if CURSOR.exists() else {}
    intervals = lock_intervals(now)
    idle, next_saved = idle_interval(now, saved)
    intervals.extend(idle)
    store.record_afk_intervals(intervals, now.date())
    next_saved["lastCheck"] = iso_local(now)
    CURSOR.write_text(json.dumps(next_saved, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
