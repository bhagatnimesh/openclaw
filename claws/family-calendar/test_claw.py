import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claw import FamilyCalendarClaw, match_events, run_cli
from intent import extract_intent
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


class FamilyCalendarClawTest(unittest.TestCase):
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
        self.assertEqual(provider.created[0]["summary"], "Dinner")
        self.assertEqual(provider.created[0]["description"], "with Rahul")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-03T19:00:00-07:00")
        self.assertEqual(provider.created[0]["end"]["dateTime"], "2026-07-03T20:00:00-07:00")
        self.assertEqual(provider.created[0]["start"]["timeZone"], DEFAULT_TIMEZONE)

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
        self.assertEqual(provider.created[0]["description"], "get some tshirts for India trip")
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
        self.assertEqual(provider.created[0]["description"], "Flight from SFO at 2:00 PM")
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

        with redirect_stdout(output):
            message = claw.create_event_from_request("Add appointment Friday")

        self.assertEqual(message, "Please provide: time.")
        self.assertEqual(provider.created, [])

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
                self.delete_requests = []

            def create_event_from_request(self, request):
                self.create_requests.append(request)
                print("Created calendar event: Dinner.")

            def list_events_from_request(self, request):
                self.list_requests.append(request)
                print("Calendar events:")

            def delete_event_from_request(self, request):
                self.delete_requests.append(request)
                print("Deleted calendar event: Dinner.")

        fake_claw = FakeClaw()
        inputs = iter(
            [
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

        self.assertEqual(fake_claw.list_requests, ["What do we have tomorrow?"])
        self.assertEqual(fake_claw.create_requests, ["Add dinner Friday at 7 with Rahul"])
        self.assertEqual(fake_claw.delete_requests, ["Cancel dinner with Rahul"])
        self.assertIn("Family Calendar Claw", output.getvalue())
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


if __name__ == "__main__":
    unittest.main()
