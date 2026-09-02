#!/usr/bin/env python3
"""Canonical state and planning logic for Gecko."""
from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
STATE_FILE = DATA / "gecko.json"
LOCK_FILE = DATA / ".gecko.lock"
TODAY_MD = DATA / "today.md"
BACKLOG_MD = DATA / "backlog.md"
THIS_WEEK_MD = DATA / "this-week.md"
ARCHIVE = DATA / "archive"
CALENDAR_FILE = DATA / "calendar.json"

TASK_TYPES = {"strategic", "hands-on", "admin"}
PRIORITIES = {"high", "normal", "low"}
TYPE_ORDER = {"strategic": 0, "hands-on": 1, "admin": 2}
PRIORITY_ORDER = {"high": 0, "normal": 1, "low": 2}


def local_now() -> datetime:
    return datetime.now().astimezone()


def iso_local(value: datetime) -> str:
    return value.astimezone().isoformat(timespec="seconds")


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.astimezone()


def minutes_label(minutes: int) -> str:
    hours, remainder = divmod(max(0, minutes), 60)
    if hours and remainder:
        return f"{hours}h {remainder}m"
    if hours:
        return f"{hours}h"
    return f"{remainder}m"


def default_state(target: date) -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "revision": 0,
        "activeDate": target.isoformat(),
        "settings": {
            "workdayStart": "09:00",
            "workdayEnd": "17:00",
            "idleThresholdMinutes": 2,
            "focusRatio": 0.8,
            "dailyReserveMinutes": 60,
        },
        "tasks": [],
        "days": {target.isoformat(): new_day(target)},
        "emailCandidates": [],
        "weeklyReports": [],
    }


def new_day(target: date) -> dict[str, Any]:
    return {
        "date": target.isoformat(),
        "frogTaskId": None,
        "frogDone": False,
        "taskIds": [],
        "calendarEvents": [],
        "afkIntervals": [],
        "closedAt": None,
        "summary": None,
    }


class GeckoStore:
    def __init__(
        self,
        root: Path = ROOT,
        now_provider: Callable[[], datetime] = local_now,
    ) -> None:
        self.root = Path(root)
        self.data = self.root / "data"
        self.state_file = self.data / "gecko.json"
        self.lock_file = self.data / ".gecko.lock"
        self.today_md = self.data / "today.md"
        self.backlog_md = self.data / "backlog.md"
        self.this_week_md = self.data / "this-week.md"
        self.archive = self.data / "archive"
        self.calendar_file = self.data / "calendar.json"
        self.now_provider = now_provider
        self._thread_lock = threading.RLock()

    def ensure_files(self) -> None:
        self.data.mkdir(parents=True, exist_ok=True)
        self.archive.mkdir(parents=True, exist_ok=True)
        if not self.calendar_file.exists():
            self._atomic_write(self.calendar_file, "[]\n")
        if not self.state_file.exists():
            state = default_state(self.now_provider().date())
            self._write_json(state)
            self._write_markdown(state)

    @contextmanager
    def _locked(self):
        self.ensure_files()
        with self._thread_lock:
            with self.lock_file.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)

    def _write_json(self, state: dict[str, Any]) -> None:
        self._atomic_write(
            self.state_file,
            json.dumps(state, indent=2, ensure_ascii=True) + "\n",
        )

    def _read_unlocked(self) -> dict[str, Any]:
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def read(self, target: date | None = None) -> dict[str, Any]:
        target = target or self.now_provider().date()
        with self._locked():
            state = self._read_unlocked()
            changed = self._roll_to(state, target)
            changed |= self._load_calendar_file(state, target)
            if changed:
                self._commit_unlocked(state)
            else:
                self._write_markdown(state)
            return deepcopy(state)

    def mutate(
        self,
        callback: Callable[[dict[str, Any]], Any],
        target: date | None = None,
    ) -> tuple[dict[str, Any], Any]:
        target = target or self.now_provider().date()
        with self._locked():
            state = self._read_unlocked()
            self._roll_to(state, target)
            self._load_calendar_file(state, target)
            result = callback(state)
            self._commit_unlocked(state)
            return deepcopy(state), result

    def _commit_unlocked(self, state: dict[str, Any]) -> None:
        state["revision"] = int(state.get("revision", 0)) + 1
        self._write_json(state)
        self._write_markdown(state)

    def _roll_to(self, state: dict[str, Any], target: date) -> bool:
        target_key = target.isoformat()
        active_key = state.get("activeDate")
        if active_key == target_key:
            state.setdefault("days", {}).setdefault(target_key, new_day(target))
            return False

        changed = False
        if active_key:
            old_day = state.setdefault("days", {}).setdefault(
                active_key, new_day(date.fromisoformat(active_key))
            )
            if not old_day.get("closedAt"):
                old_day["summary"] = self._day_summary(state, old_day)
                old_day["closedAt"] = iso_local(self.now_provider())
                self._write_archive_markdown(state, active_key)
                changed = True

            for task in state.get("tasks", []):
                if task.get("scheduledDate") == active_key and not task.get("done"):
                    task["scheduledDate"] = target_key
                    task["carryCount"] = int(task.get("carryCount", 0)) + 1
                    task["carriedFrom"] = active_key
                    changed = True

        day = state.setdefault("days", {}).setdefault(target_key, new_day(target))
        day["taskIds"] = [
            task["id"]
            for task in state.get("tasks", [])
            if task.get("scheduledDate") == target_key
        ]
        state["activeDate"] = target_key
        if not day.get("frogTaskId"):
            day["frogTaskId"] = self._choose_frog_id(state, target_key)
        return True

    def _load_calendar_file(self, state: dict[str, Any], target: date) -> bool:
        try:
            raw_events = json.loads(self.calendar_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        events = self._normalize_events(raw_events, target)
        day = state["days"][target.isoformat()]
        if events != day.get("calendarEvents", []):
            day["calendarEvents"] = events
            return True
        return False

    @staticmethod
    def _normalize_events(events: Iterable[dict[str, Any]], target: date) -> list[dict[str, Any]]:
        normalized = []
        for event in events:
            try:
                start = parse_datetime(str(event["start"]))
                end = parse_datetime(str(event["end"]))
            except (KeyError, TypeError, ValueError):
                continue
            day_start = datetime.combine(target, time.min, start.tzinfo)
            day_end = day_start + timedelta(days=1)
            if (
                end <= start
                or bool(event.get("isCanceled"))
                or bool(event.get("isAllDay"))
                or end <= day_start
                or start >= day_end
                or str(event.get("status", "busy")).lower() in {"free", "tentative"}
            ):
                continue
            normalized.append(
                {
                    "id": str(event.get("id") or f"event-{start.timestamp()}-{end.timestamp()}"),
                    "title": str(event.get("title") or event.get("subject") or "Busy"),
                    "start": iso_local(start),
                    "end": iso_local(end),
                    "status": "busy",
                    "source": str(event.get("source") or "outlook"),
                }
            )
        return sorted(normalized, key=lambda event: event["start"])

    def add_task(
        self,
        title: str,
        task_type: str,
        estimate_minutes: int = 30,
        priority: str = "normal",
        source: dict[str, Any] | None = None,
        planning: str = "unplanned",
    ) -> dict[str, Any]:
        clean_title = title.strip()
        if not clean_title:
            raise ValueError("Task title is required")
        if task_type not in TASK_TYPES:
            raise ValueError("Invalid task category")
        if priority not in PRIORITIES:
            raise ValueError("Invalid task priority")
        estimate_minutes = min(480, max(5, int(estimate_minutes)))

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            active = state["activeDate"]
            task = {
                "id": uuid.uuid4().hex[:12],
                "title": clean_title,
                "type": task_type,
                "done": False,
                "createdAt": iso_local(self.now_provider()),
                "completedAt": None,
                "scheduledDate": active,
                "estimateMinutes": estimate_minutes,
                "priority": priority,
                "planning": planning if planning in {"planned", "unplanned"} else "unplanned",
                "source": source or {"kind": "manual", "label": "Manual"},
                "carryCount": 0,
            }
            state["tasks"].append(task)
            state["days"][active]["taskIds"].append(task["id"])
            if not state["days"][active].get("frogTaskId"):
                state["days"][active]["frogTaskId"] = task["id"]
            return deepcopy(task)

        _, task = self.mutate(operation)
        return task

    def update_task(self, task_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        def operation(state: dict[str, Any]) -> dict[str, Any]:
            task = self._task_or_raise(state, task_id)
            if "type" in changes:
                if changes["type"] not in TASK_TYPES:
                    raise ValueError("Invalid task category")
                task["type"] = changes["type"]
            if "title" in changes:
                title = str(changes["title"]).strip()
                if not title:
                    raise ValueError("Task title is required")
                task["title"] = title
            if "done" in changes:
                task["done"] = bool(changes["done"])
                task["completedAt"] = iso_local(self.now_provider()) if task["done"] else None
                day = state["days"][state["activeDate"]]
                if day.get("frogTaskId") == task_id:
                    day["frogDone"] = task["done"]
            if "estimateMinutes" in changes:
                task["estimateMinutes"] = min(480, max(5, int(changes["estimateMinutes"])))
            if "priority" in changes:
                if changes["priority"] not in PRIORITIES:
                    raise ValueError("Invalid task priority")
                task["priority"] = changes["priority"]
            return deepcopy(task)

        state, task = self.mutate(operation)
        if task.get("done"):
            self._write_archive_markdown(state, task["scheduledDate"])
        return task

    def archive_task(self, task_id: str) -> dict[str, Any]:
        def operation(state: dict[str, Any]) -> dict[str, Any]:
            task = self._task_or_raise(state, task_id)
            task["done"] = True
            task["completedAt"] = iso_local(self.now_provider())
            task["archivedAt"] = iso_local(self.now_provider())
            day = state["days"][task["scheduledDate"]]
            if day.get("frogTaskId") == task_id:
                day["frogDone"] = True
            return deepcopy(task)

        state, task = self.mutate(operation)
        self._write_archive_markdown(state, task["scheduledDate"])
        return task

    def delete_task(self, task_id: str) -> dict[str, Any]:
        """Backward-compatible alias for the former destructive delete action."""
        return self.archive_task(task_id)

    def set_frog(self, task_id: str | None) -> None:
        def operation(state: dict[str, Any]) -> None:
            day = state["days"][state["activeDate"]]
            if task_id is not None:
                task = self._task_or_raise(state, task_id)
                if task.get("scheduledDate") != state["activeDate"]:
                    raise ValueError("The frog must be scheduled for today")
            day["frogTaskId"] = task_id
            day["frogDone"] = bool(task_id and self._task_or_raise(state, task_id).get("done"))

        self.mutate(operation)

    def toggle_frog(self) -> None:
        def operation(state: dict[str, Any]) -> dict[str, Any]:
            day = state["days"][state["activeDate"]]
            task_id = day.get("frogTaskId")
            if not task_id:
                raise ValueError("Choose a frog first")
            task = self._task_or_raise(state, task_id)
            task["done"] = not bool(task.get("done"))
            task["completedAt"] = iso_local(self.now_provider()) if task["done"] else None
            day["frogDone"] = task["done"]
            return deepcopy(task)

        state, task = self.mutate(operation)
        if task.get("done"):
            self._write_archive_markdown(state, task["scheduledDate"])

    def import_email(
        self,
        subject: str,
        task_type: str,
        sender: str = "",
        url: str = "",
        note: str = "",
        estimate_minutes: int = 30,
    ) -> dict[str, Any]:
        source = {
            "kind": "email",
            "label": "Email",
            "sender": sender.strip(),
            "url": url.strip(),
            "note": note.strip(),
        }
        return self.add_task(
            subject,
            task_type,
            estimate_minutes,
            source=source,
            planning="unplanned",
        )

    def import_calendar(self, events: Iterable[dict[str, Any]], target: date | None = None) -> None:
        target = target or self.now_provider().date()
        raw_events = list(events)
        normalized = self._normalize_events(raw_events, target)
        self._atomic_write(self.calendar_file, json.dumps(raw_events, indent=2) + "\n")

        def operation(state: dict[str, Any]) -> None:
            state["days"][target.isoformat()]["calendarEvents"] = normalized

        self.mutate(operation, target)

    def record_afk_intervals(
        self,
        intervals: Iterable[dict[str, Any]],
        target: date | None = None,
    ) -> None:
        target = target or self.now_provider().date()

        def operation(state: dict[str, Any]) -> None:
            day = state["days"][target.isoformat()]
            by_id = {item["id"]: item for item in day.get("afkIntervals", [])}
            for raw in intervals:
                try:
                    start = parse_datetime(str(raw["start"]))
                    end = parse_datetime(str(raw["end"]))
                except (KeyError, TypeError, ValueError):
                    continue
                clipped = self._clip_to_workday(state, target, start, end)
                if not clipped:
                    continue
                start, end = clipped
                interval_id = str(raw.get("id") or f"{raw.get('source', 'afk')}:{iso_local(start)}")
                by_id[interval_id] = {
                    "id": interval_id,
                    "start": iso_local(start),
                    "end": iso_local(end),
                    "source": str(raw.get("source") or "manual"),
                    "originalStart": str(raw.get("originalStart") or raw.get("start")),
                    "provisional": bool(raw.get("provisional", False)),
                }
            day["afkIntervals"] = sorted(by_id.values(), key=lambda item: item["start"])

        self.mutate(operation, target)

    def dashboard(self, target: date | None = None) -> dict[str, Any]:
        target = target or self.now_provider().date()
        state = self.read(target)
        target_key = target.isoformat()
        day = state["days"][target_key]
        tasks = [
            deepcopy(task)
            for task in state["tasks"]
            if task.get("scheduledDate") == target_key
        ]
        tasks.sort(
            key=lambda task: (
                task.get("done", False),
                PRIORITY_ORDER.get(task.get("priority"), 1),
                TYPE_ORDER.get(task.get("type"), 3),
                task.get("createdAt", ""),
            )
        )
        task_lookup = {task["id"]: task for task in tasks}
        frog = task_lookup.get(day.get("frogTaskId"))
        metrics = {}
        for task_type in TASK_TYPES:
            matching = [task for task in tasks if task["type"] == task_type]
            metrics[task_type] = {
                "completed": sum(bool(task.get("done")) for task in matching),
                "pending": sum(not bool(task.get("done")) for task in matching),
            }

        settings = state["settings"]
        workday_minutes = self._workday_minutes(settings)
        busy_minutes = self._busy_minutes(day.get("calendarEvents", []), target, settings)
        reserve = min(int(settings.get("dailyReserveMinutes", 60)), workday_minutes)
        focus_minutes = max(
            0,
            int((workday_minutes - busy_minutes - reserve) * float(settings.get("focusRatio", 0.8))),
        )
        planned_blocks = self._plan_blocks(
            state, target, tasks, day.get("calendarEvents", []), focus_minutes
        )
        history = self._history(state)
        afk = self._afk_summary(state, target)

        return {
            "app": "Gecko",
            "agent": {
                "name": "Gecko",
                "status": "live",
                "lastUpdated": iso_local(self.now_provider()),
            },
            "revision": state["revision"],
            "activeDate": state["activeDate"],
            "date": target_key,
            "settings": settings,
            "tasks": tasks,
            "frog": frog,
            "frogDone": bool(day.get("frogDone")),
            "metrics": metrics,
            "afk": afk,
            "capacity": {
                "busyMinutes": busy_minutes,
                "focusMinutes": focus_minutes,
                "workdayMinutes": workday_minutes,
                "reserveMinutes": reserve,
            },
            "calendar": {
                "events": deepcopy(day.get("calendarEvents", [])),
                "plannedBlocks": planned_blocks,
            },
            "history": history,
            "weekly": self._weekly_summary(state, target),
            "emailCandidates": deepcopy(state.get("emailCandidates", [])),
        }

    def weekly_reset(self, target: date | None = None) -> dict[str, Any]:
        target = target or self.now_provider().date()

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            report = self._weekly_summary(state, target)
            report["createdAt"] = iso_local(self.now_provider())
            reports = state.setdefault("weeklyReports", [])
            reports[:] = [item for item in reports if item.get("weekStart") != report["weekStart"]]
            reports.append(report)
            return deepcopy(report)

        state, report = self.mutate(operation, target)
        lines = [
            f"# Weekly Reset: {target.isoformat()}",
            "",
            f"- Days recorded: {report['daysRecorded']}",
            f"- Frogs completed: {report['frogsCompleted']} / {report['frogDays']}",
            f"- Planned tasks completed: {report['plannedCompleted']}",
            f"- Unplanned tasks completed: {report['unplannedCompleted']}",
            f"- AFK time: {report['afkMinutes']}m",
            "",
            "## Signal",
            report["signal"],
            "",
        ]
        self._atomic_write(self.this_week_md, "\n".join(lines))
        return report

    def _choose_frog_id(self, state: dict[str, Any], target_key: str) -> str | None:
        candidates = [
            task
            for task in state.get("tasks", [])
            if task.get("scheduledDate") == target_key and not task.get("done")
        ]
        candidates.sort(
            key=lambda task: (
                PRIORITY_ORDER.get(task.get("priority"), 1),
                TYPE_ORDER.get(task.get("type"), 3),
                -int(task.get("estimateMinutes", 30)),
                task.get("createdAt", ""),
            )
        )
        return candidates[0]["id"] if candidates else None

    @staticmethod
    def _task_or_raise(state: dict[str, Any], task_id: str) -> dict[str, Any]:
        for task in state.get("tasks", []):
            if task.get("id") == task_id:
                return task
        raise KeyError("Task not found")

    @staticmethod
    def _workday_minutes(settings: dict[str, Any]) -> int:
        start = time.fromisoformat(settings["workdayStart"])
        end = time.fromisoformat(settings["workdayEnd"])
        return max(0, (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute))

    def _busy_minutes(
        self,
        events: Iterable[dict[str, Any]], target: date, settings: dict[str, Any]
    ) -> int:
        timezone = self.now_provider().tzinfo
        work_start = datetime.combine(
            target, time.fromisoformat(settings["workdayStart"]), timezone
        )
        work_end = datetime.combine(target, time.fromisoformat(settings["workdayEnd"]), timezone)
        ranges = []
        for event in events:
            start = parse_datetime(event["start"])
            end = parse_datetime(event["end"])
            start, end = max(start, work_start), min(end, work_end)
            if end > start:
                ranges.append((start, end))
        return GeckoStore._union_minutes(ranges)

    @staticmethod
    def _union_minutes(ranges: Iterable[tuple[datetime, datetime]]) -> int:
        ordered = sorted((start, end) for start, end in ranges if end > start)
        if not ordered:
            return 0
        merged = [ordered[0]]
        for start, end in ordered[1:]:
            previous_start, previous_end = merged[-1]
            if start <= previous_end:
                merged[-1] = (previous_start, max(previous_end, end))
            else:
                merged.append((start, end))
        return round(sum((end - start).total_seconds() for start, end in merged) / 60)

    def _clip_to_workday(
        self,
        state: dict[str, Any],
        target: date,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, datetime] | None:
        timezone = self.now_provider().tzinfo
        settings = state["settings"]
        day_start = datetime.combine(target, time.fromisoformat(settings["workdayStart"]), timezone)
        day_end = datetime.combine(target, time.fromisoformat(settings["workdayEnd"]), timezone)
        clipped_start, clipped_end = max(start, day_start), min(end, day_end)
        return (clipped_start, clipped_end) if clipped_end > clipped_start else None

    def _afk_summary(self, state: dict[str, Any], target: date) -> dict[str, Any]:
        day = state["days"][target.isoformat()]
        intervals = day.get("afkIntervals", [])
        ranges = [(parse_datetime(item["start"]), parse_datetime(item["end"])) for item in intervals]
        workday_start = time.fromisoformat(state["settings"]["workdayStart"])
        lock_count = 0
        for item in intervals:
            if item.get("source") != "lock":
                continue
            try:
                original = parse_datetime(item.get("originalStart", item["start"]))
            except ValueError:
                continue
            if original.date() == target and original.time() >= workday_start:
                lock_count += 1
        return {
            "minutes": self._union_minutes(ranges),
            "lockCount": lock_count,
            "intervals": deepcopy(intervals),
            "updatedAt": iso_local(self.now_provider()),
        }

    def _plan_blocks(
        self,
        state: dict[str, Any],
        target: date,
        tasks: list[dict[str, Any]],
        events: list[dict[str, Any]],
        focus_minutes: int,
    ) -> list[dict[str, Any]]:
        timezone = self.now_provider().tzinfo
        settings = state["settings"]
        work_start = datetime.combine(target, time.fromisoformat(settings["workdayStart"]), timezone)
        work_end = datetime.combine(target, time.fromisoformat(settings["workdayEnd"]), timezone)
        busy = sorted(
            (
                max(work_start, parse_datetime(event["start"])),
                min(work_end, parse_datetime(event["end"])),
            )
            for event in events
            if parse_datetime(event["end"]) > work_start and parse_datetime(event["start"]) < work_end
        )
        free: list[tuple[datetime, datetime]] = []
        cursor = work_start
        for start, end in busy:
            if start > cursor:
                free.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < work_end:
            free.append((cursor, work_end))

        day = state["days"][target.isoformat()]
        frog_id = day.get("frogTaskId")
        open_tasks = [task for task in tasks if not task.get("done")]
        open_tasks.sort(
            key=lambda task: (
                task["id"] != frog_id,
                PRIORITY_ORDER.get(task.get("priority"), 1),
                TYPE_ORDER.get(task.get("type"), 3),
                task.get("createdAt", ""),
            )
        )
        blocks = []
        remaining_focus = focus_minutes
        slot_index = 0
        slot_cursor = free[0][0] if free else None
        for task in open_tasks:
            remaining = min(int(task.get("estimateMinutes", 30)), remaining_focus)
            while remaining > 0 and slot_index < len(free):
                slot_start, slot_end = free[slot_index]
                slot_cursor = max(slot_cursor or slot_start, slot_start)
                available = int((slot_end - slot_cursor).total_seconds() // 60)
                if available < 15:
                    slot_index += 1
                    slot_cursor = free[slot_index][0] if slot_index < len(free) else None
                    continue
                duration = min(remaining, available)
                block_end = slot_cursor + timedelta(minutes=duration)
                blocks.append(
                    {
                        "id": f"plan-{task['id']}-{len(blocks)}",
                        "taskId": task["id"],
                        "title": task["title"],
                        "type": task["type"],
                        "start": iso_local(slot_cursor),
                        "end": iso_local(block_end),
                        "isFrog": task["id"] == frog_id,
                    }
                )
                remaining -= duration
                remaining_focus -= duration
                slot_cursor = block_end
                if slot_cursor >= slot_end:
                    slot_index += 1
                    slot_cursor = free[slot_index][0] if slot_index < len(free) else None
            if remaining_focus <= 0:
                break
        return blocks

    def _day_summary(self, state: dict[str, Any], day: dict[str, Any]) -> dict[str, int]:
        ids = set(day.get("taskIds", []))
        tasks = [task for task in state.get("tasks", []) if task.get("id") in ids]
        completed = [task for task in tasks if task.get("done")]
        return {
            "completed": len(completed),
            "pending": len(tasks) - len(completed),
            "plannedCompleted": sum(task.get("planning") == "planned" for task in completed),
            "unplannedCompleted": sum(task.get("planning") != "planned" for task in completed),
            "afkMinutes": self._afk_summary(
                state, date.fromisoformat(day["date"])
            )["minutes"],
        }

    def _history(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        history = []
        for key in sorted(state.get("days", {})):
            day = state["days"][key]
            if not day.get("closedAt") and key != state.get("activeDate"):
                continue
            summary = day.get("summary") or self._day_summary(state, day)
            history.append(
                {
                    "date": key,
                    "frogDone": bool(day.get("frogDone")),
                    "completed": summary["completed"],
                    "pending": summary["pending"],
                    "afkMinutes": summary["afkMinutes"],
                    "closed": bool(day.get("closedAt")),
                }
            )
        return history

    def _weekly_summary(self, state: dict[str, Any], target: date) -> dict[str, Any]:
        week_start = target - timedelta(days=target.weekday())
        week_end = week_start + timedelta(days=6)
        days = [
            day
            for key, day in state.get("days", {}).items()
            if week_start <= date.fromisoformat(key) <= week_end
        ]
        summaries = [day.get("summary") or self._day_summary(state, day) for day in days]
        planned_completed = sum(summary["plannedCompleted"] for summary in summaries)
        unplanned_completed = sum(summary["unplannedCompleted"] for summary in summaries)
        afk_minutes = sum(summary["afkMinutes"] for summary in summaries)
        frog_days = sum(bool(day.get("frogTaskId")) for day in days)
        frogs_completed = sum(bool(day.get("frogDone")) for day in days)
        if not days:
            signal = "No workdays have been recorded yet."
        elif planned_completed + unplanned_completed == 0:
            signal = "There is not enough completed work yet to compare planned and unplanned load."
        elif unplanned_completed > planned_completed:
            signal = "Unplanned work is exceeding planned completions this week."
        else:
            signal = "Planned work is holding more of the completed workload this week."
        return {
            "weekStart": week_start.isoformat(),
            "weekEnd": week_end.isoformat(),
            "daysRecorded": len(days),
            "frogDays": frog_days,
            "frogsCompleted": frogs_completed,
            "plannedCompleted": planned_completed,
            "unplannedCompleted": unplanned_completed,
            "afkMinutes": afk_minutes,
            "signal": signal,
        }

    def _write_markdown(self, state: dict[str, Any]) -> None:
        active = state["activeDate"]
        day = state["days"][active]
        tasks = [
            task for task in state.get("tasks", []) if task.get("scheduledDate") == active
        ]
        frog = next(
            (task for task in tasks if task["id"] == day.get("frogTaskId")),
            None,
        )
        afk = self._afk_summary(state, date.fromisoformat(active))
        today_lines = [
            f"# Today: {active}",
            "",
            "## Frog",
            f"- [{'x' if day.get('frogDone') else ' '}] {frog['title'] if frog else 'No frog selected'}",
            "",
            "## Action Items",
        ]
        for task in tasks:
            source = task.get("source", {})
            details = [
                task["type"],
                f"{task.get('estimateMinutes', 30)}m",
                task.get("planning", "unplanned"),
                source.get("label", source.get("kind", "Manual")),
            ]
            today_lines.append(
                f"- [{'x' if task.get('done') else ' '}] {task['title']} | "
                + " | ".join(str(item) for item in details if item)
            )
        today_lines.extend(["", "## AFK", f"- AFK minutes: {afk['minutes']}", f"- Locks after 9:00: {afk['lockCount']}"])
        for interval in afk["intervals"]:
            today_lines.append(
                f"- Evidence: {interval['start']} to {interval['end']} | {interval['source']}"
            )
        self._atomic_write(self.today_md, "\n".join(today_lines) + "\n")

        backlog_lines = ["# Backlog", ""]
        for task in state.get("tasks", []):
            if task.get("done"):
                continue
            backlog_lines.append(
                f"- [ ] {task['title']} | {task['type']} | "
                f"{task.get('estimateMinutes', 30)}m | {task.get('priority', 'normal')}"
            )
        self._atomic_write(self.backlog_md, "\n".join(backlog_lines) + "\n")

    def _write_archive_markdown(self, state: dict[str, Any], day_key: str) -> None:
        day = state["days"][day_key]
        ids = set(day.get("taskIds", []))
        tasks = [task for task in state.get("tasks", []) if task.get("id") in ids]
        lines = [f"# Gecko Day: {day_key}", "", "## Frog"]
        frog = next((task for task in tasks if task["id"] == day.get("frogTaskId")), None)
        lines.append(
            f"- [{'x' if day.get('frogDone') else ' '}] {frog['title'] if frog else 'No frog selected'}"
        )
        lines.extend(["", "## Action Items"])
        for task in tasks:
            lines.append(
                f"- [{'x' if task.get('done') else ' '}] {task['title']} | "
                f"{task['type']} | {task.get('planning', 'unplanned')}"
            )
        afk = self._afk_summary(state, date.fromisoformat(day_key))
        lines.extend(["", "## AFK", f"- AFK minutes: {afk['minutes']}", ""])
        self._atomic_write(self.archive / f"{day_key}.md", "\n".join(lines))


store = GeckoStore()
