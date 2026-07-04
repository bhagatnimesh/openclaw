import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claw import FamilyCalendarClaw, _format_created_event_message, match_events, run_cli
from intent import (
    extract_intent,
    read_metadata_from_description,
    write_metadata_to_description,
)
from tools import DEFAULT_TIMEZONE


class FakeProvider:
    def __init__(self):
        self.created = []
        self.list_calls = []
        self.deleted = []
        self.updated = []
        self.events = []

    def create_event(
        self,
        title,
        start_time,
        end_time,
        timezone=DEFAULT_TIMEZONE,
        description=None,
        location=None,
        recurrence=None,
    ):
        event = {
            "id": "event-123",
            "htmlLink": "https://calendar.google.com/calendar/event?eid=event-123",
            "summary": title,
            "start": {"dateTime": start_time, "timeZone": timezone},
            "end": {"dateTime": end_time, "timeZone": timezone},
            "description": description,
            "location": location,
            "recurrence": recurrence,
        }
        self.created.append(event)
        return event

    def list_events(self, time_min, time_max, max_results=10):
        self.list_calls.append(
            {
                "time_min": time_min,
                "time_max": time_max,
                "max_results": max_results,
            }
        )
        return self.events[:max_results]

    def delete_event(self, event_id):
        self.deleted.append(event_id)
        return None

    def update_event(
        self,
        event_id,
        title,
        start_time,
        end_time,
        timezone=DEFAULT_TIMEZONE,
        description=None,
        location=None,
    ):
        event = {
            "id": event_id,
            "summary": title,
            "start": {"dateTime": start_time, "timeZone": timezone},
            "end": {"dateTime": end_time, "timeZone": timezone},
            "description": description,
            "location": location,
        }
        self.updated.append(event)
        return event


def _fake_event(
    summary,
    start,
    end,
    description=None,
    location=None,
):
    return {
        "id": summary.lower().replace(" ", "-"),
        "summary": summary,
        "start": {"dateTime": start},
        "end": {"dateTime": end},
        "description": description,
        "location": location,
    }


class FamilyCalendarClawTest(unittest.TestCase):
    def test_created_event_message_falls_back_to_event_id_without_link(self):
        start = datetime(2026, 7, 3, 19, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        end = datetime(2026, 7, 3, 20, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = _format_created_event_message(
            event_title="Dinner",
            start=start,
            end=end,
            timezone=DEFAULT_TIMEZONE,
            recurrence=None,
            recurrence_label=None,
            event_link=None,
            event_id="event-123",
        )

        self.assertIn("(event id: event-123)", message)

    def test_create_event_from_simple_request(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        output = StringIO()

        with redirect_stdout(output):
            message = claw.create_event_from_request(
                "Add dinner Friday at 7 with Rahul",
                reference_time=reference,
            )

        self.assertIn("Created calendar event: Dinner", message)
        self.assertIn("Created calendar event: Dinner", output.getvalue())
        self.assertIn("https://calendar.google.com/calendar/event?eid=event-123", message)
        self.assertNotIn("event id:", message)
        self.assertEqual(provider.created[0]["summary"], "Dinner")
        notes, metadata = read_metadata_from_description(provider.created[0]["description"])
        self.assertEqual(notes, "with Rahul")
        self.assertEqual(metadata["owner"], "unknown")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-03T19:00:00-07:00")
        self.assertEqual(provider.created[0]["end"]["dateTime"], "2026-07-03T20:00:00-07:00")
        self.assertEqual(provider.created[0]["start"]["timeZone"], DEFAULT_TIMEZONE)

    def test_create_event_from_request_stores_ai_assistant_help(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "\n".join(
                    [
                        "Add Nysha school meeting tomorrow at 4pm",
                        "I want AI assistant",
                        "Help: find the teacher email and draft quick talking points",
                        "Context: ask about waitlist status",
                    ]
                ),
                reference_time=reference,
            )

        self.assertEqual(provider.created[0]["summary"], "Nysha school meeting")
        notes, metadata = read_metadata_from_description(provider.created[0]["description"])
        self.assertIn("Assistant help: Find the teacher email", notes)
        self.assertIn("Assistant context: ask about waitlist status", notes)
        self.assertTrue(metadata["assistant_help_needed"])
        self.assertEqual(metadata["assistant_name"], "Noah")
        self.assertEqual(
            metadata["assistant_help_request"],
            "Find the teacher email and draft quick talking points",
        )
        self.assertEqual(
            metadata["assistant_context"],
            "ask about waitlist status",
        )

    def test_create_event_from_great_mall_request(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add an event I want to go to Great Mall tomorrow to get some "
                "tshirts for India trip. I want to go in the afternoon around "
                "1PM. I dont want to spend a lot of time.",
                reference_time=reference,
            )

        self.assertEqual(provider.created[0]["summary"], "Great Mall shopping for India trip")
        notes, metadata = read_metadata_from_description(provider.created[0]["description"])
        self.assertEqual(notes, "get some tshirts for India trip")
        self.assertEqual(metadata["category"], "shopping")
        self.assertEqual(provider.created[0]["location"], "Great Mall")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-03T13:00:00-07:00")
        self.assertEqual(provider.created[0]["end"]["dateTime"], "2026-07-03T14:00:00-07:00")

    def test_create_event_from_flight_leave_request(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "I have a flight at 2PM from SFO, So I need to leave at 11:30 "
                "on Friday next week",
                reference_time=reference,
            )

        self.assertEqual(provider.created[0]["summary"], "Leave for SFO flight")
        self.assertEqual(provider.created[0]["location"], "SFO")
        notes, metadata = read_metadata_from_description(provider.created[0]["description"])
        self.assertEqual(notes, "Flight from SFO at 2:00 PM")
        self.assertEqual(metadata["category"], "travel")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-10T11:30:00-07:00")
        self.assertEqual(provider.created[0]["end"]["dateTime"], "2026-07-10T12:30:00-07:00")

    def test_create_event_from_request_asks_for_missing_date(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        output = StringIO()

        with redirect_stdout(output):
            message = claw.create_event_from_request("Add dinner at 7 with Rahul")

        self.assertEqual(message, "Please provide: date.")
        self.assertEqual(provider.created, [])

    def test_create_event_from_request_asks_for_missing_time(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        output = StringIO()
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(output):
            message = claw.create_event_from_request(
                "Add appointment Friday",
                reference_time=reference,
            )

        self.assertEqual(message, "Please provide a time for Appointment on Friday, July 3.")
        self.assertEqual(provider.created, [])

    def test_pickup_without_time_asks_for_time_with_parsed_event(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "Niyati picks up Navya from school on Tuesday next week",
                reference_time=reference,
            )

        self.assertEqual(
            message,
            "Please provide a time for Navya school pickup on Tuesday, July 7.",
        )
        self.assertEqual(provider.created, [])

    def test_pickup_missing_time_followup_creates_original_event(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "Niyati picks up Navya from school on Tuesday next week",
                reference_time=reference,
            )

        self.assertEqual(
            message,
            "Please provide a time for Navya school pickup on Tuesday, July 7.",
        )
        self.assertIsNotNone(claw.pending_action)

        with redirect_stdout(StringIO()):
            handled = claw.handle_pending_response("6 PM")

        self.assertTrue(handled)
        self.assertIsNone(claw.pending_action)
        self.assertEqual(provider.created[0]["summary"], "Navya school pickup")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-07T18:00:00-07:00")
        self.assertEqual(provider.created[0]["end"]["dateTime"], "2026-07-07T19:00:00-07:00")
        self.assertEqual(provider.created[0]["location"], "school")
        notes, metadata = read_metadata_from_description(provider.created[0]["description"])
        self.assertEqual(notes, "Niyati picks up Navya from school")
        self.assertEqual(metadata["owner"], "mom")
        self.assertEqual(metadata["person"], "Navya")
        self.assertEqual(metadata["category"], "school")

    def test_preparation_followup_updates_last_created_event(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Kids should be picked Thursday from school at 6 PM",
                reference_time=reference,
            )

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "add carry snacks for the kids they will be hungry",
                reference_time=reference,
            )

        self.assertEqual(message, "Added preparation notes to Kids should be picked from school.")
        self.assertEqual(provider.updated[0]["id"], "event-123")
        self.assertEqual(provider.updated[0]["summary"], "Kids should be picked from school")
        self.assertEqual(provider.updated[0]["start"]["dateTime"], "2026-07-09T18:00:00-07:00")
        notes, metadata = read_metadata_from_description(provider.updated[0]["description"])
        self.assertEqual(notes, "Preparation: carry snacks for the kids they will be hungry")
        self.assertTrue(metadata["preparation_needed"])
        self.assertEqual(
            metadata["preparation_notes"],
            "carry snacks for the kids they will be hungry",
        )

    def test_context_followup_updates_last_created_event_after_preparation(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Kids should be picked Thursday from school at 8 PM",
                reference_time=reference,
            )
            claw.create_event_from_request(
                "add carry snacks for the kids they will be hungry",
                reference_time=reference,
            )
            message = claw.create_event_from_request(
                "one more thing Nysha needs to be taken to art class",
                reference_time=reference,
            )

        self.assertEqual(message, "Added note to Kids should be picked from school.")
        self.assertEqual(provider.updated[-1]["id"], "event-123")
        notes, metadata = read_metadata_from_description(provider.updated[-1]["description"])
        self.assertIn("Preparation: carry snacks for the kids they will be hungry", notes)
        self.assertIn("Note: Nysha needs to be taken to art class", notes)
        self.assertTrue(metadata["preparation_needed"])
        self.assertEqual(
            metadata["preparation_notes"],
            "carry snacks for the kids they will be hungry",
        )

    def test_create_recurring_weekday_event(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "Add school pickup every weekday at 3pm",
                reference_time=reference,
            )

        self.assertEqual(provider.created[0]["summary"], "School pickup")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-02T15:00:00-07:00")
        self.assertEqual(provider.created[0]["end"]["dateTime"], "2026-07-02T16:00:00-07:00")
        self.assertEqual(provider.created[0]["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"])
        self.assertIn("repeating every weekday", message)

    def test_create_recurring_specific_weekday_event(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add Navya gymnastics every Saturday at 10am",
                reference_time=reference,
            )

        self.assertEqual(provider.created[0]["summary"], "Navya gymnastics")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-04T10:00:00-07:00")
        self.assertEqual(provider.created[0]["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=SA"])

    def test_create_recurring_natural_order_with_count(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "creating recurring event nysha gymanstics 10 AM saturday for next 12 weeks",
                reference_time=reference,
            )

        self.assertEqual(provider.created[0]["summary"], "Nysha gymnastics")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-04T10:00:00-07:00")
        self.assertEqual(provider.created[0]["end"]["dateTime"], "2026-07-04T11:00:00-07:00")
        self.assertEqual(provider.created[0]["recurrence"], ["RRULE:FREQ=WEEKLY;COUNT=12;BYDAY=SA"])
        self.assertIn("every Saturday", message)
        self.assertIn("12 occurrences", message)
        self.assertIn("Saturday, September 19", message)

    def test_create_recurring_daily_event(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add medication every day at 8am",
                reference_time=reference,
            )

        self.assertEqual(provider.created[0]["summary"], "Medication")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-02T08:00:00-07:00")
        self.assertEqual(provider.created[0]["recurrence"], ["RRULE:FREQ=DAILY"])

    def test_list_events_from_tomorrow_request(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "late",
                "summary": "Soccer",
                "start": {"dateTime": "2026-07-03T18:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T19:00:00-07:00"},
                "location": "Park",
            },
            {
                "id": "early",
                "summary": "Breakfast",
                "start": {"dateTime": "2026-07-03T08:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T09:00:00-07:00"},
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        output = StringIO()

        with redirect_stdout(output):
            message = claw.list_events_from_request(
                "What do we have tomorrow?",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls[0]["time_min"], "2026-07-03T00:00:00-07:00")
        self.assertEqual(provider.list_calls[0]["time_max"], "2026-07-04T00:00:00-07:00")
        self.assertLess(message.find("Breakfast"), message.find("Soccer"))
        self.assertIn("- Breakfast: 8:00 AM to 9:00 AM", message)
        self.assertIn("- Soccer: 6:00 PM to 7:00 PM at Park", message)
        self.assertIn("Calendar events:", output.getvalue())

    def test_list_events_from_next_seven_days_request(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.list_events_from_request(
                "Show me the next 7 days",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls[0]["time_min"], "2026-07-02T12:00:00-07:00")
        self.assertEqual(provider.list_calls[0]["time_max"], "2026-07-09T12:00:00-07:00")

    def test_list_events_filters_dad_responsibility_tomorrow(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "nysha-dentist",
                "summary": "Nysha dentist appointment",
                "start": {"dateTime": "2026-07-03T15:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T16:00:00-07:00"},
                "description": write_metadata_to_description(
                    None,
                    {
                        "owner": "dad",
                        "person": "Nysha",
                        "category": "medical",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            },
            {
                "id": "navya-gymnastics",
                "summary": "Navya gymnastics",
                "start": {"dateTime": "2026-07-03T10:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T11:00:00-07:00"},
                "description": write_metadata_to_description(
                    None,
                    {
                        "owner": "mom",
                        "person": "Navya",
                        "category": "activity",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.list_events_from_request(
                "What am I responsible for tomorrow?",
                reference_time=reference,
            )

        self.assertIn("Nysha dentist appointment", message)
        self.assertNotIn("Navya gymnastics", message)

    def test_list_events_filters_mom_weekend(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "dad-errand",
                "summary": "Dad errand",
                "start": {"dateTime": "2026-07-04T09:00:00-07:00"},
                "end": {"dateTime": "2026-07-04T10:00:00-07:00"},
                "description": write_metadata_to_description(
                    None,
                    {
                        "owner": "dad",
                        "person": "family",
                        "category": "household",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            },
            {
                "id": "navya-gymnastics",
                "summary": "Navya gymnastics",
                "start": {"dateTime": "2026-07-04T10:00:00-07:00"},
                "end": {"dateTime": "2026-07-04T11:00:00-07:00"},
                "description": write_metadata_to_description(
                    None,
                    {
                        "owner": "mom",
                        "person": "Navya",
                        "category": "activity",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.list_events_from_request(
                "What is mom handling this weekend?",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls[0]["time_min"], "2026-07-04T00:00:00-07:00")
        self.assertEqual(provider.list_calls[0]["time_max"], "2026-07-06T00:00:00-07:00")
        self.assertIn("Navya gymnastics", message)
        self.assertNotIn("Dad errand", message)

    def test_list_events_filters_preparation_next_week(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "passport",
                "summary": "Passport renewal appointment",
                "start": {"dateTime": "2026-07-10T11:00:00-07:00"},
                "end": {"dateTime": "2026-07-10T12:00:00-07:00"},
                "description": write_metadata_to_description(
                    "need documents",
                    {
                        "owner": "unknown",
                        "person": "family",
                        "category": "travel",
                        "preparation_needed": True,
                        "preparation_notes": "need documents",
                    },
                ),
            },
            {
                "id": "dinner",
                "summary": "Dinner",
                "start": {"dateTime": "2026-07-08T19:00:00-07:00"},
                "end": {"dateTime": "2026-07-08T20:00:00-07:00"},
                "description": write_metadata_to_description(
                    None,
                    {
                        "owner": "unknown",
                        "person": "family",
                        "category": "social",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.preparation_from_request(
                "What needs preparation next week?",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls[0]["time_min"], "2026-07-06T00:00:00-07:00")
        self.assertEqual(provider.list_calls[0]["time_max"], "2026-07-13T00:00:00-07:00")
        self.assertIn("Passport renewal appointment", message)
        self.assertIn("- Gather required documents", message)
        self.assertNotIn("Dinner", message)

    def test_preparation_checklist_uses_metadata_notes_and_unknown_owner(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "passport",
                "summary": "Passport renewal appointment",
                "start": {"dateTime": "2026-07-03T11:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T12:00:00-07:00"},
                "description": write_metadata_to_description(
                    "need documents",
                    {
                        "owner": "unknown",
                        "person": "family",
                        "category": "travel",
                        "preparation_needed": True,
                        "preparation_notes": "need documents",
                    },
                ),
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.preparation_from_request(
                "What should we prepare for?",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls[0]["time_min"], "2026-07-02T12:00:00-07:00")
        self.assertEqual(provider.list_calls[0]["time_max"], "2026-08-01T12:00:00-07:00")
        self.assertIn("Passport renewal appointment — Friday 11:00 AM (urgent)", message)
        self.assertIn("Why: prep notes; marked prep-needed; appointment, paperwork, travel", message)
        self.assertIn("Suggested deadline: ASAP", message)
        self.assertNotIn("Owner:", message)
        self.assertIn("- Gather required documents", message)
        self.assertIn("- Complete forms", message)
        self.assertIn("- Prepare photos", message)
        self.assertIn("- Confirm appointment", message)
        self.assertIn("- Assign owner", message)
        self.assertEqual(provider.created, [])
        self.assertEqual(provider.updated, [])
        self.assertEqual(provider.deleted, [])

    def test_preparation_checklist_infers_action_needed_this_week(self):
        provider = FakeProvider()
        provider.events = [
            _fake_event(
                "Nysha dentist appointment",
                "2026-07-03T15:00:00-07:00",
                "2026-07-03T16:00:00-07:00",
                description=write_metadata_to_description(
                    None,
                    {
                        "owner": "dad",
                        "person": "Nysha",
                        "category": "",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            ),
            _fake_event(
                "Dinner with Rahul",
                "2026-07-03T19:00:00-07:00",
                "2026-07-03T20:00:00-07:00",
            ),
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.preparation_from_request(
                "What needs action this week?",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls[0]["time_min"], "2026-06-29T00:00:00-07:00")
        self.assertEqual(provider.list_calls[0]["time_max"], "2026-07-06T00:00:00-07:00")
        self.assertIn("Nysha dentist appointment — Friday 3:00 PM (urgent)", message)
        self.assertIn("Owner: dad", message)
        self.assertIn("Why: appointment, medical", message)
        self.assertIn("Suggested deadline: ASAP", message)
        self.assertIn("- Bring insurance card", message)
        self.assertIn("- Complete forms", message)
        self.assertIn("- Bring prior notes", message)
        self.assertIn("- Confirm transport", message)
        self.assertNotIn("Dinner with Rahul", message)
        self.assertNotIn("- Assign owner", message)

    def test_preparation_checklist_targets_specific_trip(self):
        provider = FakeProvider()
        provider.events = [
            _fake_event(
                "Great Mall shopping for India trip",
                "2026-07-03T13:00:00-07:00",
                "2026-07-03T14:00:00-07:00",
                description=write_metadata_to_description(
                    "Buy T-shirts for India trip.",
                    {
                        "owner": "dad",
                        "person": "family",
                        "category": "shopping",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            ),
            _fake_event(
                "Soccer practice",
                "2026-07-04T10:00:00-07:00",
                "2026-07-04T11:00:00-07:00",
            ),
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.preparation_from_request(
                "What do we need to do before the India trip?",
                reference_time=reference,
            )

        self.assertIn("Great Mall shopping for India trip — Friday 1:00 PM", message)
        self.assertIn("Owner: dad", message)
        self.assertIn("Why: prep notes; shopping for travel", message)
        self.assertIn("- Buy T-shirts for India trip.", message)
        self.assertIn("- Make shopping list", message)
        self.assertIn("- Confirm sizes and quantities", message)
        self.assertIn("- Bring bags", message)
        self.assertNotIn("- Check travel documents", message)
        self.assertNotIn("- Pack medicines", message)
        self.assertNotIn("Soccer practice", message)

    def test_preparation_checklist_infers_activity_gear(self):
        provider = FakeProvider()
        provider.events = [
            _fake_event(
                "Navya gymnastics",
                "2026-07-04T10:00:00-07:00",
                "2026-07-04T11:00:00-07:00",
                description=write_metadata_to_description(
                    None,
                    {
                        "owner": "mom",
                        "person": "Navya",
                        "category": "activity",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            )
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.preparation_from_request(
                "What needs action this week?",
                reference_time=reference,
            )

        self.assertIn("Navya gymnastics — Saturday 10:00 AM (urgent)", message)
        self.assertIn("Owner: mom", message)
        self.assertIn("Why: activity", message)
        self.assertIn("- Pack gear", message)
        self.assertIn("- Set out clothes", message)
        self.assertIn("- Confirm pickup/drop-off", message)

    def test_preparation_checklist_infers_school_logistics(self):
        provider = FakeProvider()
        provider.events = [
            _fake_event(
                "Nysha school performance",
                "2026-07-06T09:00:00-07:00",
                "2026-07-06T10:00:00-07:00",
                description=write_metadata_to_description(
                    None,
                    {
                        "owner": "both",
                        "person": "Nysha",
                        "category": "school",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            )
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.preparation_from_request(
                "What should we prepare for?",
                reference_time=reference,
            )

        self.assertIn("Nysha school performance — Monday 9:00 AM", message)
        self.assertIn("Owner: both", message)
        self.assertIn("Why: school", message)
        self.assertIn("Suggested deadline: By Sunday 9:00 AM", message)
        self.assertIn("- Complete school forms", message)
        self.assertIn("- Pack costume or materials", message)
        self.assertIn("- Confirm drop-off/pickup plan", message)

    def test_list_events_for_named_pickup_adult_without_date(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "art-pickup",
                "summary": "Nysha art class pickup",
                "start": {"dateTime": "2026-07-06T18:00:00-07:00"},
                "end": {"dateTime": "2026-07-06T19:00:00-07:00"},
                "location": "art class",
                "description": write_metadata_to_description(
                    "Niyati picks up Nysha from art class",
                    {
                        "owner": "unknown",
                        "person": "Nysha",
                        "category": "school",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            },
            {
                "id": "gymnastics",
                "summary": "Navya gymnastics",
                "start": {"dateTime": "2026-07-04T10:00:00-07:00"},
                "end": {"dateTime": "2026-07-04T11:00:00-07:00"},
                "description": write_metadata_to_description(
                    None,
                    {
                        "owner": "mom",
                        "person": "Navya",
                        "category": "activity",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.list_events_from_request(
                "what events are for Niyati",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls[0]["time_min"], "2026-07-02T12:00:00-07:00")
        self.assertEqual(provider.list_calls[0]["time_max"], "2026-08-01T12:00:00-07:00")
        self.assertIn("Nysha art class pickup", message)
        self.assertNotIn("Navya gymnastics", message)

    def test_list_events_reports_no_events(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        output = StringIO()

        with redirect_stdout(output):
            message = claw.list_events_from_request("What's on Friday?")

        self.assertEqual(message, "No calendar events found for that time.")
        self.assertIn("No calendar events found for that time.", output.getvalue())

    def test_delete_event_exact_match(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "dinner-1",
                "summary": "Dinner with Rahul",
                "start": {"dateTime": "2026-07-03T19:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T20:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        output = StringIO()

        with redirect_stdout(output):
            message = claw.delete_event_from_request("Cancel dinner with Rahul")

        self.assertEqual(provider.deleted, [])
        self.assertIn("I found this event: Dinner with Rahul", message)
        self.assertIn("Delete it? yes/no", output.getvalue())

        with redirect_stdout(StringIO()):
            claw.handle_pending_response("yes")

        self.assertEqual(provider.deleted, ["dinner-1"])

    def test_delete_event_partial_title_match(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "mall-1",
                "summary": "Great Mall shopping for India trip",
                "start": {"dateTime": "2026-07-03T13:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T14:00:00-07:00"},
                "location": "Great Mall",
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.delete_event_from_request("Delete the Great Mall event")

        self.assertEqual(provider.deleted, [])
        self.assertIn("Great Mall shopping for India trip", message)

    def test_delete_event_date_constrained_match(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "shopping-tomorrow",
                "summary": "Shopping",
                "start": {"dateTime": "2026-07-03T13:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T14:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.delete_event_from_request(
                "Remove tomorrow's shopping event",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls[0]["time_min"], "2026-07-03T00:00:00-07:00")
        self.assertEqual(provider.list_calls[0]["time_max"], "2026-07-04T00:00:00-07:00")
        self.assertEqual(provider.deleted, [])
        self.assertIn("I found this event: Shopping", message)

    def test_delete_airport_request_matches_sfo_event(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "sfo-1",
                "summary": "Leave for SFO flight",
                "start": {"dateTime": "2026-07-03T11:30:00-07:00"},
                "end": {"dateTime": "2026-07-03T12:30:00-07:00"},
                "location": "SFO",
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.delete_event_from_request(
                "delete tomorrows airport event",
                reference_time=reference,
            )

        self.assertEqual(provider.deleted, [])
        self.assertEqual(provider.list_calls[0]["time_min"], "2026-07-03T00:00:00-07:00")
        self.assertIn("Leave for SFO flight", message)
        self.assertIn("Delete it? yes/no", message)

    def test_delete_dentist_matches_dental_appointment(self):
        events = [
            {
                "id": "dental-1",
                "summary": "Dental appointment",
                "start": {"dateTime": "2026-07-03T11:30:00-07:00"},
                "end": {"dateTime": "2026-07-03T12:30:00-07:00"},
            }
        ]

        matches = match_events("delete dentist event", events)

        self.assertEqual(matches[0]["event"]["id"], "dental-1")
        self.assertGreaterEqual(matches[0]["score"], 3)

    def test_delete_event_multiple_matches_require_clarification(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "dinner-1",
                "summary": "Dinner with Rahul",
                "start": {"dateTime": "2026-07-03T19:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T20:00:00-07:00"},
            },
            {
                "id": "dinner-2",
                "summary": "Dinner with Rahul",
                "start": {"dateTime": "2026-07-10T19:00:00-07:00"},
                "end": {"dateTime": "2026-07-10T20:00:00-07:00"},
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.delete_event_from_request("Cancel dinner with Rahul")

        self.assertEqual(provider.deleted, [])
        self.assertIn("Multiple matching events found", message)
        self.assertIn("1. Dinner with Rahul", message)
        self.assertIn("2. Dinner with Rahul", message)

    def test_delete_choice_requires_confirmation_before_delete(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "dinner-1",
                "summary": "Dinner with Rahul",
                "start": {"dateTime": "2026-07-03T19:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T20:00:00-07:00"},
            },
            {
                "id": "dinner-2",
                "summary": "Dinner with Rahul",
                "start": {"dateTime": "2026-07-10T19:00:00-07:00"},
                "end": {"dateTime": "2026-07-10T20:00:00-07:00"},
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.delete_event_from_request("Cancel dinner with Rahul")
            claw.handle_pending_response("2")

        self.assertEqual(provider.deleted, [])

        with redirect_stdout(StringIO()):
            claw.handle_pending_response("yes")

        self.assertEqual(provider.deleted, ["dinner-2"])

    def test_delete_event_no_match(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "soccer-1",
                "summary": "Soccer",
                "start": {"dateTime": "2026-07-03T18:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T19:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.delete_event_from_request("Cancel dinner with Rahul")

        self.assertEqual(provider.deleted, [])
        self.assertEqual(
            message,
            "I couldn't find a matching event. Try including the event name or date.",
        )

    def test_move_event_exact_match_requires_confirmation(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "dinner-1",
                "summary": "Dinner with Rahul",
                "start": {"dateTime": "2026-07-03T19:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T20:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.update_event_from_request(
                "Move dinner with Rahul to Saturday at 7",
                reference_time=reference,
            )

        self.assertEqual(provider.updated, [])
        self.assertIn("Move it to Dinner with Rahul Saturday, July 4 7:00 PM", message)

        with redirect_stdout(StringIO()):
            claw.handle_pending_response("yes")

        self.assertEqual(provider.updated[0]["id"], "dinner-1")
        self.assertEqual(provider.updated[0]["start"]["dateTime"], "2026-07-04T19:00:00-07:00")
        self.assertEqual(provider.updated[0]["end"]["dateTime"], "2026-07-04T20:00:00-07:00")

    def test_move_event_fuzzy_match(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "mall-1",
                "summary": "Great Mall shopping for India trip",
                "start": {"dateTime": "2026-07-03T13:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T14:00:00-07:00"},
                "location": "Great Mall",
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.update_event_from_request(
                "Reschedule the Great Mall event to tomorrow at 2pm",
                reference_time=reference,
            )

        self.assertEqual(provider.updated, [])
        self.assertIn("Great Mall shopping for India trip", message)

        with redirect_stdout(StringIO()):
            claw.handle_pending_response("yes")

        self.assertEqual(provider.updated[0]["start"]["dateTime"], "2026-07-03T14:00:00-07:00")

    def test_move_event_multiple_matches(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "dinner-1",
                "summary": "Dinner with Rahul",
                "start": {"dateTime": "2026-07-03T19:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T20:00:00-07:00"},
            },
            {
                "id": "dinner-2",
                "summary": "Dinner with Rahul",
                "start": {"dateTime": "2026-07-10T19:00:00-07:00"},
                "end": {"dateTime": "2026-07-10T20:00:00-07:00"},
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.update_event_from_request("Move dinner with Rahul to Saturday at 7")

        self.assertEqual(provider.updated, [])
        self.assertIn("Multiple matching events found. Which one should I move?", message)
        self.assertIn("1. Dinner with Rahul", message)
        self.assertIn("2. Dinner with Rahul", message)

    def test_move_event_preserves_duration_location_description(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "dental-1",
                "summary": "Dental appointment",
                "start": {"dateTime": "2026-07-03T10:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T11:30:00-07:00"},
                "location": "Dental Office",
                "description": "Bring insurance card",
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.update_event_from_request(
                "Change dentist appointment to next Tuesday at 3pm",
                reference_time=reference,
            )
            claw.handle_pending_response("yes")

        self.assertEqual(provider.updated[0]["summary"], "Dental appointment")
        self.assertEqual(provider.updated[0]["location"], "Dental Office")
        self.assertEqual(provider.updated[0]["description"], "Bring insurance card")
        self.assertEqual(provider.updated[0]["start"]["dateTime"], "2026-07-07T15:00:00-07:00")
        self.assertEqual(provider.updated[0]["end"]["dateTime"], "2026-07-07T16:30:00-07:00")

    def test_move_event_preserves_original_date_when_missing_new_date(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "sfo-1",
                "summary": "Leave for SFO flight",
                "start": {"dateTime": "2026-07-10T11:30:00-07:00"},
                "end": {"dateTime": "2026-07-10T12:30:00-07:00"},
                "location": "SFO",
                "description": "Flight from SFO at 2:00 PM",
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.update_event_from_request("Move my SFO event to 11am")
            claw.handle_pending_response("yes")

        self.assertEqual(provider.updated[0]["start"]["dateTime"], "2026-07-10T11:00:00-07:00")
        self.assertEqual(provider.updated[0]["end"]["dateTime"], "2026-07-10T12:00:00-07:00")

    def test_move_event_relative_one_hour_later(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "mall-1",
                "summary": "Great Mall visit",
                "start": {"dateTime": "2026-07-03T13:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T14:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.update_event_from_request(
                "move great mall visit event by 1 hour later"
            )

        self.assertEqual(provider.updated, [])
        self.assertIn("Move it to Great Mall visit Friday, July 3 2:00 PM–3:00 PM", message)

        with redirect_stdout(StringIO()):
            claw.handle_pending_response("yes")

        self.assertEqual(provider.updated[0]["start"]["dateTime"], "2026-07-03T14:00:00-07:00")
        self.assertEqual(provider.updated[0]["end"]["dateTime"], "2026-07-03T15:00:00-07:00")

    def test_move_event_relative_up_by_one_hour(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "mall-1",
                "summary": "Great Mall visit",
                "start": {"dateTime": "2026-07-03T14:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T15:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.update_event_from_request("move great mall visit up by 1 hour")

        self.assertEqual(provider.updated, [])
        self.assertIn("Move it to Great Mall visit Friday, July 3 1:00 PM–2:00 PM", message)

        with redirect_stdout(StringIO()):
            claw.handle_pending_response("yes")

        self.assertEqual(provider.updated[0]["start"]["dateTime"], "2026-07-03T13:00:00-07:00")
        self.assertEqual(provider.updated[0]["end"]["dateTime"], "2026-07-03T14:00:00-07:00")

    def test_move_event_relative_thirty_minutes_earlier(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "mall-1",
                "summary": "Great Mall visit",
                "start": {"dateTime": "2026-07-03T13:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T14:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.update_event_from_request("move great mall visit event 30 minutes earlier")
            claw.handle_pending_response("yes")

        self.assertEqual(provider.updated[0]["start"]["dateTime"], "2026-07-03T12:30:00-07:00")
        self.assertEqual(provider.updated[0]["end"]["dateTime"], "2026-07-03T13:30:00-07:00")

    def test_push_event_relative_fifteen_minutes(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "dental-1",
                "summary": "Dental appointment",
                "start": {"dateTime": "2026-07-03T10:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T11:30:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.update_event_from_request("push dentist by 15 minutes")
            claw.handle_pending_response("yes")

        self.assertEqual(provider.updated[0]["start"]["dateTime"], "2026-07-03T10:15:00-07:00")
        self.assertEqual(provider.updated[0]["end"]["dateTime"], "2026-07-03T11:45:00-07:00")

    def test_delete_confirmation_required_before_delete(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "mall-1",
                "summary": "Great Mall shopping",
                "start": {"dateTime": "2026-07-03T13:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T14:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.delete_event_from_request("Delete shopping event")

        self.assertEqual(provider.deleted, [])

        with redirect_stdout(StringIO()):
            claw.handle_pending_response("yes")

        self.assertEqual(provider.deleted, ["mall-1"])

    def test_no_cancels_pending_delete(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "mall-1",
                "summary": "Great Mall shopping",
                "start": {"dateTime": "2026-07-03T13:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T14:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.delete_event_from_request("Delete shopping event")
            claw.handle_pending_response("no")

        self.assertEqual(provider.deleted, [])
        self.assertIsNone(claw.pending_action)

    def test_run_cli_processes_request_until_exit(self):
        class FakeClaw:
            def __init__(self):
                self.create_requests = []
                self.list_requests = []
                self.preparation_requests = []
                self.delete_requests = []

            def create_event_from_request(self, request):
                self.create_requests.append(request)
                print("Created calendar event: Dinner.")

            def list_events_from_request(self, request):
                self.list_requests.append(request)
                print("Calendar events:")

            def preparation_from_request(self, request):
                self.preparation_requests.append(request)
                print("Preparation checklist:")

            def delete_event_from_request(self, request):
                self.delete_requests.append(request)
                print("Deleted calendar event: Dinner.")

        fake_claw = FakeClaw()
        inputs = iter(
            [
                "What should we prepare for?",
                "What do we have tomorrow?",
                "Add dinner Friday at 7 with Rahul",
                "Cancel dinner with Rahul",
                "exit",
            ]
        )
        output = StringIO()

        def fake_input(prompt):
            print(prompt, end="")
            return next(inputs)

        with redirect_stdout(output), patch("builtins.input", fake_input):
            run_cli(fake_claw)

        self.assertEqual(fake_claw.preparation_requests, ["What should we prepare for?"])
        self.assertEqual(fake_claw.list_requests, ["What do we have tomorrow?"])
        self.assertEqual(fake_claw.create_requests, ["Add dinner Friday at 7 with Rahul"])
        self.assertEqual(fake_claw.delete_requests, ["Cancel dinner with Rahul"])
        self.assertIn("Family Calendar Claw", output.getvalue())
        self.assertIn("> Preparation checklist:", output.getvalue())
        self.assertIn("> Calendar events:", output.getvalue())
        self.assertIn("> Created calendar event: Dinner.", output.getvalue())
        self.assertIn("> Deleted calendar event: Dinner.", output.getvalue())


class MilestoneOneRegressionTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

    def test_real_phrase_great_mall_create(self):
        request = (
            "Add an event I want to go to Great Mall tomorrow to get some tshirts "
            "for india trip. I want to go in the afternoon around 1PM. I dont "
            "want to spend a lot of time."
        )
        intent = extract_intent(request, now=self.now)

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Great Mall shopping for india trip")
        self.assertEqual(intent["date"], "2026-07-03")
        self.assertEqual(intent["start_time"], "13:00")
        self.assertEqual(intent["duration_minutes"], 60)
        self.assertEqual(intent["location"], "Great Mall")
        self.assertEqual(intent["description"], "get some tshirts for india trip")
        self.assertEqual(intent["missing_fields"], [])

        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(request, reference_time=self.now)

        self.assertEqual(provider.created[0]["summary"], "Great Mall shopping for india trip")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-03T13:00:00-07:00")
        self.assertIn("Created calendar event: Great Mall shopping for india trip", message)
        self.assertIn("Friday, July 3 from 1:00 PM to 2:00 PM", message)

    def test_real_phrase_sfo_leave_time_wins_over_flight_time(self):
        request = (
            "I have a flight at 2PM from SFO, So I need to leave at 11:30 "
            "on Friday next week"
        )
        intent = extract_intent(request, now=self.now)

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Leave for SFO flight")
        self.assertEqual(intent["date"], "2026-07-10")
        self.assertEqual(intent["start_time"], "11:30")
        self.assertEqual(intent["location"], "SFO")
        self.assertEqual(intent["description"], "Flight from SFO at 2:00 PM")
        self.assertEqual(intent["missing_fields"], [])

        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(request, reference_time=self.now)

        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-10T11:30:00-07:00")
        self.assertEqual(provider.created[0]["end"]["dateTime"], "2026-07-10T12:30:00-07:00")
        self.assertIn("Created calendar event: Leave for SFO flight", message)
        self.assertIn("Friday, July 10 from 11:30 AM to 12:30 PM", message)

    def test_real_phrase_delay_great_mall_by_an_hour(self):
        request = "Delay the great mall shopping by an hour"
        intent = extract_intent(request, now=self.now)

        self.assertEqual(intent["intent"], "update_event")
        self.assertEqual(intent["query"], "great mall shopping")
        self.assertIsNone(intent["new_start_time"])
        self.assertEqual(intent["relative_delta_minutes"], 60)
        self.assertEqual(intent["missing_fields"], [])

        provider = FakeProvider()
        provider.events = [
            {
                "id": "mall-1",
                "summary": "Great Mall shopping",
                "start": {"dateTime": "2026-07-03T13:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T14:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        with redirect_stdout(StringIO()):
            message = claw.update_event_from_request(request, reference_time=self.now)

        self.assertEqual(provider.updated, [])
        self.assertIn("Move it to Great Mall shopping Friday, July 3 2:00 PM–3:00 PM", message)

    def test_real_phrase_move_great_mall_visit_up_by_one_hour(self):
        request = "move great mall visit up by 1 hour"
        intent = extract_intent(request, now=self.now)

        self.assertEqual(intent["intent"], "update_event")
        self.assertEqual(intent["query"], "great mall visit")
        self.assertIsNone(intent["new_start_time"])
        self.assertEqual(intent["relative_delta_minutes"], -60)
        self.assertEqual(intent["missing_fields"], [])

        provider = FakeProvider()
        provider.events = [
            {
                "id": "mall-1",
                "summary": "Great Mall visit",
                "start": {"dateTime": "2026-07-03T14:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T15:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        with redirect_stdout(StringIO()):
            message = claw.update_event_from_request(request, reference_time=self.now)

        self.assertEqual(provider.updated, [])
        self.assertIn("Move it to Great Mall visit Friday, July 3 1:00 PM–2:00 PM", message)

    def test_real_phrase_recurring_nysha_gymnastics_for_twelve_weeks(self):
        request = "creating recurring event nysha gymnastics 10 AM saturday for next 12 weeks"
        intent = extract_intent(request, now=self.now)

        self.assertEqual(intent["intent"], "create_event")
        self.assertEqual(intent["title"], "Nysha gymnastics")
        self.assertEqual(intent["date"], "2026-07-04")
        self.assertEqual(intent["start_time"], "10:00")
        self.assertEqual(intent["duration_minutes"], 60)
        self.assertEqual(intent["recurrence"], ["RRULE:FREQ=WEEKLY;COUNT=12;BYDAY=SA"])
        self.assertEqual(intent["missing_fields"], [])

        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(request, reference_time=self.now)

        self.assertEqual(provider.created[0]["recurrence"], ["RRULE:FREQ=WEEKLY;COUNT=12;BYDAY=SA"])
        self.assertIn("every Saturday", message)
        self.assertIn("12 occurrences", message)
        self.assertIn("Saturday, September 19", message)

    def test_real_phrase_delete_tomorrows_airport_event(self):
        request = "delete tomorrows airport event"
        intent = extract_intent(request, now=self.now)

        self.assertEqual(intent["intent"], "delete_event")
        self.assertEqual(intent["query"], "airport")
        self.assertEqual(intent["search_start"], "2026-07-03T00:00:00-07:00")
        self.assertEqual(intent["search_end"], "2026-07-04T00:00:00-07:00")
        self.assertEqual(intent["missing_fields"], [])

        provider = FakeProvider()
        provider.events = [
            {
                "id": "sfo-1",
                "summary": "Leave for SFO flight",
                "start": {"dateTime": "2026-07-03T11:30:00-07:00"},
                "end": {"dateTime": "2026-07-03T12:30:00-07:00"},
                "location": "SFO",
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        with redirect_stdout(StringIO()):
            message = claw.delete_event_from_request(request, reference_time=self.now)

        self.assertEqual(provider.deleted, [])
        self.assertEqual(provider.list_calls[0]["time_min"], "2026-07-03T00:00:00-07:00")
        self.assertIn("I found this event: Leave for SFO flight", message)
        self.assertIn("Delete it? yes/no", message)

    def test_next_week_family_briefing_groups_metadata_and_planning_items(self):
        provider = FakeProvider()
        provider.events = [
            _fake_event(
                "Navya school pickup",
                "2026-07-06T18:00:00-07:00",
                "2026-07-06T19:00:00-07:00",
                write_metadata_to_description(
                    "Niyati picks up Navya from school",
                    {
                        "owner": "mom",
                        "person": "Navya",
                        "category": "school",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            ),
            _fake_event(
                "Passport renewal appointment",
                "2026-07-07T11:00:00-07:00",
                "2026-07-07T12:00:00-07:00",
                write_metadata_to_description(
                    "Need documents",
                    {
                        "owner": "unknown",
                        "person": "family",
                        "category": "household",
                        "preparation_needed": True,
                        "preparation_notes": "Bring birth certificates and photos",
                    },
                ),
            ),
            _fake_event(
                "Nysha dentist appointment",
                "2026-07-07T11:30:00-07:00",
                "2026-07-07T12:30:00-07:00",
                write_metadata_to_description(
                    "",
                    {
                        "owner": "dad",
                        "person": "Nysha",
                        "category": "medical",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            ),
            _fake_event(
                "Family photos",
                "2026-07-09T10:00:00-07:00",
                "2026-07-09T11:00:00-07:00",
            ),
            _fake_event(
                "Navya art class",
                "2026-07-09T15:00:00-07:00",
                "2026-07-09T16:00:00-07:00",
                write_metadata_to_description(
                    "",
                    {
                        "owner": "mom",
                        "person": "Navya",
                        "category": "activity",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            ),
            _fake_event(
                "Grocery pickup",
                "2026-07-09T17:00:00-07:00",
                "2026-07-09T18:00:00-07:00",
                write_metadata_to_description(
                    "",
                    {
                        "owner": "dad",
                        "person": "family",
                        "category": "shopping",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            ),
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.briefing_from_request(
                "What is coming up next week?",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls[0]["time_min"], "2026-07-06T00:00:00-07:00")
        self.assertEqual(provider.list_calls[0]["time_max"], "2026-07-13T00:00:00-07:00")
        self.assertEqual(provider.list_calls[0]["max_results"], 100)
        self.assertIn("Family calendar briefing for next week:", message)
        self.assertIn(
            "Next week has 6 events. Thursday is busiest with 3 events. "
            "There is 1 conflict and 1 prep-needed item.",
            message,
        )
        self.assertIn("Events by day:", message)
        self.assertIn("- Monday, July 6:", message)
        self.assertIn("Navya school pickup (mom, Navya)", message)
        self.assertIn("Passport renewal appointment (unassigned prep needed)", message)
        self.assertIn("Preparation-needed events:", message)
        self.assertIn("- Passport renewal appointment: Bring birth certificates and photos", message)
        self.assertIn("Unassigned events:", message)
        self.assertIn("- Passport renewal appointment", message)
        self.assertIn("Potential conflicts or busy days:", message)
        self.assertIn(
            "- Tuesday, July 7: Passport renewal appointment overlaps Nysha dentist appointment",
            message,
        )
        self.assertIn("- Thursday, July 9: 3 events", message)
        self.assertIn("Things to clarify:", message)
        self.assertIn("- Who owns Passport renewal appointment?", message)
        self.assertNotIn("- Does Family photos need preparation?", message)

    def test_briefing_deduplicates_unassigned_and_caps_clarifications(self):
        provider = FakeProvider()
        provider.events = [
            _fake_event(
                "Passport renewal appointment",
                "2026-07-06T09:00:00-07:00",
                "2026-07-06T10:00:00-07:00",
                write_metadata_to_description(
                    "",
                    {
                        "owner": "unknown",
                        "person": "family",
                        "category": "travel",
                        "preparation_needed": True,
                        "preparation_notes": "",
                    },
                ),
            ),
            _fake_event(
                "Nysha dentist appointment",
                "2026-07-06T09:30:00-07:00",
                "2026-07-06T10:30:00-07:00",
                write_metadata_to_description(
                    "",
                    {
                        "owner": "unknown",
                        "person": "Nysha",
                        "category": "medical",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            ),
            _fake_event(
                "Navya school pickup",
                "2026-07-07T15:00:00-07:00",
                "2026-07-07T16:00:00-07:00",
                write_metadata_to_description(
                    "",
                    {
                        "owner": "unknown",
                        "person": "Navya",
                        "category": "school",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            ),
            _fake_event(
                "Airport ride",
                "2026-07-08T12:00:00-07:00",
                "2026-07-08T13:00:00-07:00",
                write_metadata_to_description(
                    "",
                    {
                        "owner": "unknown",
                        "person": "family",
                        "category": "travel",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            ),
            _fake_event(
                "School form deadline",
                "2026-07-09T08:00:00-07:00",
                "2026-07-09T09:00:00-07:00",
            ),
            _fake_event(
                "Family photos",
                "2026-07-10T10:00:00-07:00",
                "2026-07-10T11:00:00-07:00",
            ),
            _fake_event(
                "Family photos",
                "2026-07-10T10:00:00-07:00",
                "2026-07-10T11:00:00-07:00",
            ),
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.briefing_from_request(
                "Give me next week's family calendar briefing",
                reference_time=reference,
            )

        unassigned_section = message.split("Unassigned events:\n", 1)[1].split(
            "\nPotential conflicts or busy days:",
            1,
        )[0]
        clarify_section = message.split("Things to clarify:\n", 1)[1]
        clarify_questions = [
            line for line in clarify_section.splitlines() if line.startswith("- ")
        ]

        self.assertEqual(unassigned_section.count("- Family photos"), 1)
        self.assertLessEqual(len(clarify_questions), 5)
        self.assertIn("- Who owns Passport renewal appointment?", clarify_section)
        self.assertIn("- What preparation is needed for Passport renewal appointment?", clarify_section)
        self.assertIn(
            "- Can Passport renewal appointment and Nysha dentist appointment both be covered?",
            clarify_section,
        )
        self.assertNotIn("- Does Family photos need preparation?", clarify_section)

    def test_briefing_accepts_schedule_summary_wording(self):
        provider = FakeProvider()
        provider.events = [
            _fake_event(
                "Navya gymnastics",
                "2026-07-06T10:00:00-07:00",
                "2026-07-06T11:00:00-07:00",
                write_metadata_to_description(
                    "",
                    {
                        "owner": "mom",
                        "person": "Navya",
                        "category": "activity",
                        "preparation_needed": False,
                        "preparation_notes": "",
                    },
                ),
            )
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.briefing_from_request(
                "Can you summarize our schedule next week?",
                reference_time=reference,
            )

        self.assertIn("Family calendar briefing for next week:", message)
        self.assertIn("Navya gymnastics (mom, Navya)", message)


if __name__ == "__main__":
    unittest.main()
