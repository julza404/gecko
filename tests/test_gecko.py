from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from gecko_store import GeckoStore


PACIFIC = timezone(timedelta(hours=-7))


class Clock:
    def __init__(self, value: datetime):
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class GeckoStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.clock = Clock(datetime(2026, 9, 1, 10, 0, tzinfo=PACIFIC))
        self.store = GeckoStore(self.root, self.clock)
        self.store.ensure_files()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_new_store_has_no_seed_data(self) -> None:
        dashboard = self.store.dashboard()
        self.assertEqual([], dashboard["tasks"])
        self.assertIsNone(dashboard["frog"])
        self.assertEqual(1, len(dashboard["history"]))

    def test_task_changes_and_archiving_persist_to_json_and_markdown(self) -> None:
        task = self.store.add_task("Write decision memo", "strategic", 60)
        self.store.update_task(task["id"], {"type": "hands-on", "done": True})
        dashboard = self.store.dashboard()
        saved = next(item for item in dashboard["tasks"] if item["id"] == task["id"])
        self.assertEqual("hands-on", saved["type"])
        self.assertTrue(saved["done"])
        self.assertIn("Write decision memo | hands-on", (self.root / "data" / "today.md").read_text())
        self.assertIn(
            "- [x] Write decision memo",
            (self.root / "data" / "archive" / "2026-09-01.md").read_text(),
        )

        self.store.delete_task(task["id"])
        dashboard = self.store.dashboard()
        self.assertEqual(1, len(dashboard["tasks"]))
        self.assertTrue(dashboard["tasks"][0]["done"])
        persisted = json.loads((self.root / "data" / "gecko.json").read_text())
        self.assertTrue(persisted["tasks"][0]["done"])
        self.assertIn("archivedAt", persisted["tasks"][0])

    def test_rollover_archives_day_and_carries_only_open_tasks(self) -> None:
        open_task = self.store.add_task("Open work", "strategic", 45)
        done_task = self.store.add_task("Finished work", "admin", 15)
        self.store.update_task(done_task["id"], {"done": True})
        self.clock.value = datetime(2026, 9, 2, 9, 0, tzinfo=PACIFIC)

        dashboard = self.store.dashboard()
        self.assertEqual(["Open work"], [task["title"] for task in dashboard["tasks"]])
        self.assertEqual(1, dashboard["tasks"][0]["carryCount"])
        self.assertTrue((self.root / "data" / "archive" / "2026-09-01.md").exists())
        saved = json.loads((self.root / "data" / "gecko.json").read_text())
        finished = next(task for task in saved["tasks"] if task["id"] == done_task["id"])
        self.assertEqual("2026-09-01", finished["scheduledDate"])
        carried = next(task for task in saved["tasks"] if task["id"] == open_task["id"])
        self.assertEqual("2026-09-02", carried["scheduledDate"])

    def test_afk_is_clipped_deduplicated_and_union_counted(self) -> None:
        intervals = [
            {
                "id": "overnight",
                "start": "2026-08-31T18:00:00-07:00",
                "end": "2026-09-01T09:04:00-07:00",
                "originalStart": "2026-08-31T18:00:00-07:00",
                "source": "lock",
            },
            {
                "id": "idle-one",
                "start": "2026-09-01T09:02:00-07:00",
                "end": "2026-09-01T09:07:00-07:00",
                "source": "idle",
            },
        ]
        self.store.record_afk_intervals(intervals)
        self.store.record_afk_intervals(intervals)
        afk = self.store.dashboard()["afk"]
        self.assertEqual(7, afk["minutes"])
        self.assertEqual(0, afk["lockCount"])
        self.assertEqual(2, len(afk["intervals"]))

    def test_calendar_blocks_begin_after_verified_busy_time(self) -> None:
        self.store.add_task("Focused work", "strategic", 60)
        self.store.import_calendar(
            [
                {
                    "id": "meeting",
                    "title": "Verified meeting",
                    "start": "2026-09-01T09:00:00-07:00",
                    "end": "2026-09-01T10:00:00-07:00",
                    "status": "busy",
                }
            ],
            date(2026, 9, 1),
        )
        dashboard = self.store.dashboard()
        self.assertEqual(60, dashboard["capacity"]["busyMinutes"])
        self.assertEqual("2026-09-01T10:00:00-07:00", dashboard["calendar"]["plannedBlocks"][0]["start"])

    def test_busy_time_and_plans_are_limited_to_realistic_workday_capacity(self) -> None:
        self.store.add_task("Morning strategy", "strategic", 240)
        self.store.add_task("Afternoon review", "admin", 240)
        self.store.import_calendar(
            [
                {
                    "id": "before-hours",
                    "title": "Early call",
                    "start": "2026-09-01T07:00:00-07:00",
                    "end": "2026-09-01T09:00:00-07:00",
                },
                {
                    "id": "midday",
                    "title": "Planning",
                    "start": "2026-09-01T12:00:00-07:00",
                    "end": "2026-09-01T13:00:00-07:00",
                },
            ]
        )

        dashboard = self.store.dashboard()
        self.assertEqual(60, dashboard["capacity"]["busyMinutes"])
        self.assertEqual(288, dashboard["capacity"]["focusMinutes"])
        planned = sum(
            int((datetime.fromisoformat(block["end"]) - datetime.fromisoformat(block["start"])).total_seconds() / 60)
            for block in dashboard["calendar"]["plannedBlocks"]
        )
        self.assertEqual(288, planned)

    def test_calendar_source_retains_events_for_each_day(self) -> None:
        events = [
            {
                "id": "first-day",
                "title": "Tuesday meeting",
                "start": "2026-09-01T10:00:00-07:00",
                "end": "2026-09-01T11:00:00-07:00",
            },
            {
                "id": "second-day",
                "title": "Wednesday meeting",
                "start": "2026-09-02T10:00:00-07:00",
                "end": "2026-09-02T11:00:00-07:00",
            },
        ]
        self.store.import_calendar(events)
        self.assertEqual(1, len(self.store.dashboard()["calendar"]["events"]))

        self.clock.value = datetime(2026, 9, 2, 10, 0, tzinfo=PACIFIC)
        dashboard = self.store.dashboard()
        self.assertEqual(["Wednesday meeting"], [event["title"] for event in dashboard["calendar"]["events"]])
        saved_events = json.loads((self.root / "data" / "calendar.json").read_text())
        self.assertEqual(events, saved_events)

    def test_closed_day_history_does_not_change_when_carried_work_finishes(self) -> None:
        task = self.store.add_task("Carry this work", "strategic", 30)
        self.clock.value = datetime(2026, 9, 2, 9, 0, tzinfo=PACIFIC)
        self.store.dashboard()
        self.store.update_task(task["id"], {"done": True})

        history = self.store.dashboard()["history"]
        first_day = next(day for day in history if day["date"] == "2026-09-01")
        self.assertEqual(0, first_day["completed"])
        self.assertEqual(1, first_day["pending"])


if __name__ == "__main__":
    unittest.main()
