import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from claw import FamilyCalendarClaw, NYSHA_SCHEDULE_ID
from intent import write_metadata_to_private_extended_properties
from tools import DEFAULT_TIMEZONE


class ScheduleProvider:
    def __init__(self, events=None, fail_after=None):
        self.events = list(events or [])
        self.fail_after = fail_after
        self.created = []
        self.updated = []
        self.deleted = []

    def list_events(self, **kwargs):
        return list(self.events)

    def get_event(self, event_id, calendar_id=None):
        return next(event for event in self.events if event["id"] == event_id)

    def create_event(self, **kwargs):
        if self.fail_after is not None and len(self.created) >= self.fail_after:
            raise RuntimeError("provider failure")
        event = {
            "id": f"created-{len(self.created) + 1}",
            "summary": kwargs["title"],
            "start": {"dateTime": kwargs["start_time"], "timeZone": kwargs["timezone"]},
            "end": {"dateTime": kwargs["end_time"], "timeZone": kwargs["timezone"]},
                "recurrence": kwargs.get("recurrence"),
                "extendedProperties": {"private": kwargs["private_extended_properties"]},
                "calendarId": kwargs.get("calendar_name"),
        }
        self.events.append(event)
        self.created.append(event)
        return event

    def update_event(self, **kwargs):
        event = self.get_event(kwargs["event_id"])
        event.update(
            {
                "summary": kwargs["title"],
                "start": {"dateTime": kwargs["start_time"], "timeZone": kwargs["timezone"]},
                "end": {"dateTime": kwargs["end_time"], "timeZone": kwargs["timezone"]},
                "recurrence": kwargs.get("recurrence"),
                "extendedProperties": {"private": kwargs["private_extended_properties"]},
                "description": kwargs.get("description"),
                "location": kwargs.get("location"),
                "attendees": kwargs.get("attendees"),
            }
        )
        self.updated.append(event)
        return event

    def delete_event(self, event_id, calendar_id=None):
        self.deleted.append(event_id)
        self.events = [event for event in self.events if event["id"] != event_id]


def _event(event_id, title, start, end, metadata=None):
    event = {
        "id": event_id,
        "summary": title,
        "start": {"dateTime": start, "timeZone": DEFAULT_TIMEZONE},
        "end": {"dateTime": end, "timeZone": DEFAULT_TIMEZONE},
        "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=MO"],
        "calendarId": "nysha-calendar-id",
    }
    if metadata:
        event["extendedProperties"] = {"private": write_metadata_to_private_extended_properties(metadata)}
    return event


def _rows():
    return [
        {"weekday": "Monday", "start_time": "15:30", "end_time": "16:30", "title": "Homework", "confidence": 1},
        {"weekday": "Tuesday", "start_time": "16:30", "end_time": "17:00", "title": "Journal", "confidence": 1},
        {"weekday": "Wednesday", "start_time": "14:30", "end_time": "14:45", "title": "Free Time", "confidence": 1},
        {"weekday": "Thursday", "start_time": "16:30", "end_time": "17:00", "title": "Math Drill", "confidence": 1},
        {"weekday": "Friday", "start_time": "17:00", "end_time": "18:00", "title": "PE", "confidence": 1},
    ]


class WeeklyScheduleSyncTest(unittest.TestCase):
    reference = datetime(2026, 8, 23, 12, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

    def test_preview_and_confirmation_create_recurring_rows(self):
        provider = ScheduleProvider()
        claw = FamilyCalendarClaw.from_provider(provider)

        preview = claw.preview_weekly_schedule_sync(
            {
                "school_year": "2026-27",
                "complete": True,
                "rows": _rows(),
            },
            reference_time=self.reference,
        )

        self.assertIn("Changes: 5", preview)
        self.assertEqual(claw.pending_action.action, "confirm_schedule_sync")
        self.assertTrue(claw.handle_pending_response("yes"))
        self.assertEqual(len(provider.created), 5)
        self.assertTrue(all(event["recurrence"] for event in provider.created))
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-08-24T15:30:00-07:00")
        self.assertTrue(claw.undo_stack)
        self.assertEqual(claw.undo_last_action(), "Undid Nysha's school schedule update.")
        self.assertEqual(len(provider.deleted), 5)

    def test_changed_rows_update_matching_slot_and_remove_only_managed_events(self):
        provider = ScheduleProvider(
            [
                _event(
                    "monday",
                    "Homework",
                    "2026-08-03T15:00:00-07:00",
                    "2026-08-03T16:00:00-07:00",
                    {
                        "schedule_id": NYSHA_SCHEDULE_ID,
                        "schedule_key": "monday:15:00:16:00",
                    },
                ),
                _event(
                    "stale",
                    "Old Journal",
                    "2026-08-05T13:00:00-07:00",
                    "2026-08-05T13:30:00-07:00",
                    {
                        "schedule_id": NYSHA_SCHEDULE_ID,
                        "schedule_key": "wednesday:13:00:13:30",
                    },
                ),
                _event(
                    "unrelated",
                    "Parent meeting",
                    "2026-08-05T15:30:00-07:00",
                    "2026-08-05T16:30:00-07:00",
                ),
            ]
        )
        provider.events[0]["description"] = "Notes: keep this context"
        provider.events[0]["location"] = "Room 13"
        provider.events[0]["attendees"] = [{"email": "parent@example.test"}]
        claw = FamilyCalendarClaw.from_provider(provider)

        claw.preview_weekly_schedule_sync(
            {
                "school_year": "2026-27",
                "complete": True,
                "rows": _rows(),
            },
            reference_time=self.reference,
        )

        self.assertEqual(len(claw.pending_action.payload["changes"]), 5)
        self.assertEqual(claw.pending_action.payload["removals"][0]["id"], "stale")
        claw.handle_pending_response("yes")
        self.assertEqual(provider.updated[0]["id"], "monday")
        self.assertEqual(provider.updated[0]["start"]["dateTime"], "2026-08-03T15:30:00-07:00")
        self.assertEqual(provider.updated[0]["description"], "keep this context")
        self.assertEqual(provider.updated[0]["location"], "Room 13")
        self.assertEqual(provider.updated[0]["attendees"], [{"email": "parent@example.test"}])
        self.assertEqual(provider.deleted, ["stale"])
        self.assertIn("unrelated", [event["id"] for event in provider.events])

    def test_correction_rebuilds_preview_without_writing(self):
        provider = ScheduleProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        rows = _rows()
        rows[2] = {
            "weekday": "Wednesday",
            "start_time": "14:45",
            "end_time": "15:30",
            "title": "Homework",
            "confidence": 1,
        }
        claw.preview_weekly_schedule_sync(
            {
                "school_year": "2026-27",
                "complete": True,
                "rows": rows,
            },
            reference_time=self.reference,
        )

        self.assertTrue(claw.handle_pending_response("Wednesday ends at 4:30 PM"))
        desired = next(
            item for item in claw.pending_action.payload["desired"] if item["weekday"] == "wednesday"
        )
        self.assertEqual(desired["end_time"], "16:30")
        self.assertEqual(provider.created, [])

    def test_uncertain_rows_do_not_create_pending_action(self):
        provider = ScheduleProvider()
        claw = FamilyCalendarClaw.from_provider(provider)

        message = claw.preview_weekly_schedule_sync(
            {
                "school_year": "2026-27",
                "complete": True,
                "rows": [
                    {**row, "confidence": 0.5 if row["weekday"] == "Monday" else row["confidence"]}
                    for row in _rows()
                ],
            },
            reference_time=self.reference,
        )

        self.assertIn("could not read", message)
        self.assertIsNone(claw.pending_action)

    def test_incomplete_image_does_not_create_destructive_pending_action(self):
        claw = FamilyCalendarClaw.from_provider(ScheduleProvider())

        message = claw.preview_weekly_schedule_sync(
            {"school_year": "2026-27", "complete": False, "rows": _rows()},
            reference_time=self.reference,
        )

        self.assertIn("complete Monday-Friday", message)
        self.assertIsNone(claw.pending_action)

    def test_provider_failure_rolls_back_partial_creates(self):
        provider = ScheduleProvider(fail_after=2)
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.preview_weekly_schedule_sync(
            {"school_year": "2026-27", "complete": True, "rows": _rows()},
            reference_time=self.reference,
        )

        self.assertTrue(claw.handle_pending_response("yes"))
        self.assertEqual(provider.events, [])
        self.assertEqual(claw.undo_stack, [])


if __name__ == "__main__":
    unittest.main()
