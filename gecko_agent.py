#!/usr/bin/env python3
"""Command-line controls for the Gecko productivity agent."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from gecko_store import TASK_TYPES, store


def morning(target: date) -> dict:
    dashboard = store.dashboard(target)
    frog = dashboard.get("frog")
    print(
        f"Gecko planned {target.isoformat()}: "
        f"{dashboard['capacity']['focusMinutes']}m focus. "
        f"Frog: {frog['title'] if frog else 'not selected'}"
    )
    return dashboard


def main() -> None:
    parser = argparse.ArgumentParser(description="Gecko daily-planning agent")
    commands = parser.add_subparsers(dest="command", required=True)

    morning_parser = commands.add_parser("morning", help="Close the previous day and plan today")
    morning_parser.add_argument("--date", type=date.fromisoformat, default=date.today())

    commands.add_parser("weekly-reset", help="Write this week's factual summary")

    add_parser = commands.add_parser("add", help="Add an action to today's plan")
    add_parser.add_argument("title")
    add_parser.add_argument("--type", choices=sorted(TASK_TYPES), default="strategic")
    add_parser.add_argument("--minutes", type=int, default=30)
    add_parser.add_argument("--priority", choices=["high", "normal", "low"], default="normal")

    email_parser = commands.add_parser("email", help="Create an action from an email")
    email_parser.add_argument("subject")
    email_parser.add_argument("--type", choices=sorted(TASK_TYPES), default="admin")
    email_parser.add_argument("--from", dest="sender", default="")
    email_parser.add_argument("--url", default="")
    email_parser.add_argument("--note", default="")
    email_parser.add_argument("--minutes", type=int, default=30)

    calendar_parser = commands.add_parser("calendar-import", help="Import verified calendar JSON")
    calendar_parser.add_argument("file", type=Path)
    calendar_parser.add_argument("--date", type=date.fromisoformat, default=date.today())

    arguments = parser.parse_args()
    if arguments.command == "morning":
        morning(arguments.date)
    elif arguments.command == "weekly-reset":
        report = store.weekly_reset()
        print(json.dumps(report, indent=2))
    elif arguments.command == "add":
        task = store.add_task(
            arguments.title,
            arguments.type,
            arguments.minutes,
            arguments.priority,
        )
        print(f"Added to Gecko: {task['title']}")
    elif arguments.command == "email":
        task = store.import_email(
            arguments.subject,
            arguments.type,
            arguments.sender,
            arguments.url,
            arguments.note,
            arguments.minutes,
        )
        print(f"Imported email action: {task['title']}")
    else:
        events = json.loads(arguments.file.read_text(encoding="utf-8"))
        if not isinstance(events, list):
            raise SystemExit("Calendar JSON must contain a list of events")
        store.import_calendar(events, arguments.date)
        print(f"Imported {len(events)} calendar records for {arguments.date.isoformat()}")


if __name__ == "__main__":
    main()
