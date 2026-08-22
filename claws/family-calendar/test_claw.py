import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claw import FamilyCalendarClaw, PendingAction, _format_created_event_message, match_events, run_cli
from intent import (
    extract_intent,
    read_metadata_from_event,
    read_metadata_from_description,
    write_metadata_to_description,
)
from tools import DEFAULT_TIMEZONE


GUEST_EMAIL_ENV = {
    "N4OS_CALENDAR_DAD_GUEST_EMAIL": "dad@example.test",
    "N4OS_CALENDAR_MOM_GUEST_EMAIL": "mom@example.test",
}
FAMILY_ATTENDEES = [
    {"email": "dad@example.test", "displayName": "Dad"},
    {"email": "mom@example.test", "displayName": "Mom"},
]


class FakeFieldExtractor:
    def __init__(self, fields=None, error=None, primary=False):
        self.fields = fields or {}
        self.error = error
        self.calls = []
        self.primary_calendar_api_context = primary

    def extract(self, request, now=None, baseline_intent=None, context=None):
        self.calls.append(
            {
                "request": request,
                "now": now,
                "baseline_intent": baseline_intent,
                "context": context,
            }
        )
        if self.error is not None:
            raise self.error
        return self.fields


class FakeProvider:
    def __init__(self):
        self.created = []
        self.list_calls = []
        self.deleted = []
        self.updated = []
        self.events = []
        self.get_calls = []
        self.get_call_details = []
        self.created_calendar_names = []
        self.deleted_calls = []
        self.list_calendar_names = []

    def create_event(
        self,
        title,
        start_time,
        end_time,
        timezone=DEFAULT_TIMEZONE,
        description=None,
        location=None,
        recurrence=None,
        attendees=None,
        private_extended_properties=None,
        calendar_name=None,
        notify_attendees=False,
        all_day=False,
        event_label_background_color=None,
    ):
        self.created_calendar_names.append(calendar_name)
        start = {"date": start_time} if all_day else {"dateTime": start_time, "timeZone": timezone}
        end = {"date": end_time} if all_day else {"dateTime": end_time, "timeZone": timezone}
        event = {
            "id": "event-123",
            "htmlLink": "https://calendar.google.com/calendar/event?eid=event-123",
            "summary": title,
            "start": start,
            "end": end,
            "description": description,
            "location": location,
            "recurrence": recurrence,
            "attendees": attendees,
            "notify_attendees": notify_attendees,
            "all_day": all_day,
        }
        if calendar_name:
            event["calendarId"] = calendar_name
        if private_extended_properties:
            event["extendedProperties"] = {
                "private": private_extended_properties,
            }
        self.created.append(event)
        return event

    def list_events(
        self,
        time_min,
        time_max,
        max_results=10,
        calendar_name=None,
        writable=False,
        query=None,
    ):
        self.list_calendar_names.append(calendar_name)
        self.list_calls.append(
            {
                "time_min": time_min,
                "time_max": time_max,
                "max_results": max_results,
                "writable": writable,
                "query": query,
            }
        )
        return self.events[:max_results]

    def get_event(self, event_id, calendar_id=None):
        self.get_calls.append(event_id)
        self.get_call_details.append({"event_id": event_id, "calendar_id": calendar_id})
        return next(event for event in self.events if event.get("id") == event_id)

    def delete_event(self, event_id, calendar_id=None):
        self.deleted.append(event_id)
        self.deleted_calls.append({"event_id": event_id, "calendar_id": calendar_id})
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
        attendees=None,
        private_extended_properties=None,
        calendar_id=None,
        notify_attendees=False,
    ):
        event = {
            "id": event_id,
            "summary": title,
            "start": {"dateTime": start_time, "timeZone": timezone},
            "end": {"dateTime": end_time, "timeZone": timezone},
            "description": description,
            "location": location,
            "attendees": attendees,
            "notify_attendees": notify_attendees,
        }
        if calendar_id:
            event["calendarId"] = calendar_id
        if private_extended_properties:
            event["extendedProperties"] = {
                "private": private_extended_properties,
            }
        self.updated.append(event)
        return event


class FailingProvider(FakeProvider):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def create_event(self, *args, **kwargs):
        raise self.error


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
    def test_typed_event_id_bypasses_request_search_window(self):
        provider = FakeProvider()
        event = _fake_event(
            "Future planning",
            "2026-10-01T15:00:00-07:00",
            "2026-10-01T16:00:00-07:00",
        )
        provider.events = [event]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 11, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = claw.update_event_from_request(
            "move it to Friday at 3 PM",
            reference_time=reference,
            event_id=event["id"],
        )

        self.assertEqual(provider.get_calls, [event["id"]])
        self.assertEqual(provider.list_calls, [])
        self.assertIn("Future planning", message)

    def test_typed_event_id_uses_named_calendar_id(self):
        provider = FakeProvider()
        event = _fake_event(
            "Future planning",
            "2026-10-01T15:00:00-07:00",
            "2026-10-01T16:00:00-07:00",
        )
        event["calendarId"] = "nysha-school-id"
        provider.events = [event]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 11, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        claw.assign_owner_from_request(
            "assign it to nimesh",
            reference_time=reference,
            event_id=event["id"],
            calendar_id="nysha-school-id",
        )

        self.assertEqual(
            provider.get_call_details,
            [{"event_id": event["id"], "calendar_id": "nysha-school-id"}],
        )
        self.assertEqual(provider.updated[0]["calendarId"], "nysha-school-id")

    def test_pronoun_move_uses_last_named_calendar_event_without_default_search(self):
        provider = FakeProvider()
        event = _fake_event(
            "Art class",
            "2026-08-15T10:00:00-07:00",
            "2026-08-15T11:00:00-07:00",
        )
        event["calendarId"] = "nysha-school-id"
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.last_created_event = event
        reference = datetime(2026, 8, 12, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = claw.update_event_from_request(
            "move it to Friday at 3 PM",
            reference_time=reference,
        )

        self.assertEqual(provider.list_calls, [])
        self.assertIn("Art class", message)
        self.assertIsNotNone(claw.pending_action)
        self.assertEqual(claw.pending_action.event["calendarId"], "nysha-school-id")

    def test_pronoun_delete_uses_last_named_calendar_event_without_default_search(self):
        provider = FakeProvider()
        event = _fake_event(
            "Art class",
            "2026-08-15T10:00:00-07:00",
            "2026-08-15T11:00:00-07:00",
        )
        event["calendarId"] = "nysha-school-id"
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.last_created_event = event
        reference = datetime(2026, 8, 12, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = claw.delete_event_from_request("delete it", reference_time=reference)

        self.assertEqual(provider.list_calls, [])
        self.assertIn("Art class", message)
        self.assertIsNotNone(claw.pending_action)
        self.assertEqual(claw.pending_action.event["calendarId"], "nysha-school-id")

    def test_pronoun_delete_reuses_created_event_when_calendar_name_is_repeated(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 12, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add a recurring event to Nysha school calendar "
                "for Art class at 10 AM every Saturday",
                reference_time=reference,
            )
            message = claw.delete_event_from_request(
                "Delete it from Nysha school calendar",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls, [])
        self.assertIn("Art class", message)
        self.assertIsNotNone(claw.pending_action)
        self.assertEqual(claw.pending_action.event["calendarId"], "Nysha school calendar")

    def test_missing_time_followup_can_rename_event_with_instead(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 12, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add dentist Friday",
                reference_time=reference,
            )
            handled = claw.handle_pending_response(
                "Lunch instead at 1 PM",
                reference_time=reference,
            )

        self.assertTrue(handled)
        self.assertEqual(provider.created[0]["summary"], "Lunch")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-08-14T13:00:00-07:00")

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
        self.assertNotIn("N4OS_METADATA", provider.created[0]["description"] or "")
        self.assertIn("extendedProperties", provider.created[0])
        notes, metadata = read_metadata_from_event(provider.created[0])
        self.assertEqual(notes, "with Rahul")
        self.assertEqual(metadata["owner"], "unknown")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-03T19:00:00-07:00")
        self.assertEqual(provider.created[0]["end"]["dateTime"], "2026-07-03T20:00:00-07:00")

    def test_create_event_adds_family_guests_to_invite(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 11, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", GUEST_EMAIL_ENV, clear=False):
            message = claw.create_event_from_request(
                "/calendar Navya & Nysha are scheduled at 12:20 pm on 8/29 -Just Kids Pediatric Dentistry & Orthodontics - Downtown\n"
                "Add guest: family",
                reference_time=reference,
            )

        self.assertIn("Created calendar event", message)
        self.assertEqual(provider.created[0]["attendees"], FAMILY_ATTENDEES)

    def test_create_recurring_event_uses_named_calendar_subject(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 12, 9, 32, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = claw.create_event_from_request(
            "Add a recurring event to Nysha school calendar for Art class at 10 am every Saturday",
            reference_time=reference,
        )

        self.assertEqual(provider.created[0]["summary"], "Art class")
        self.assertEqual(provider.created_calendar_names, ["Nysha school calendar"])
        self.assertEqual(provider.created[0]["calendarId"], "Nysha school calendar")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-08-15T10:00:00-07:00")
        self.assertEqual(provider.created[0]["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=SA"])
        self.assertIn("Created calendar event: Art class", message)
        self.assertNotIn("To Nysha school calendar", message)

    def test_create_event_strips_trailing_add_to_named_calendar(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 12, 17, 57, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = claw.create_event_from_request(
            "/event learning bee parent teacher meeting 8/28 5 pm add to Nysha school calendar",
            reference_time=reference,
        )

        self.assertEqual(provider.created[0]["summary"], "Learning bee parent teacher meeting")
        self.assertEqual(provider.created_calendar_names, ["Nysha school calendar"])
        self.assertEqual(provider.created[0]["calendarId"], "Nysha school calendar")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-08-28T17:00:00-07:00")
        self.assertIn("Created calendar event: Learning bee parent teacher meeting", message)
        self.assertNotIn("add to Nysha school calendar", message)

    def test_create_event_intent_strips_target_calendar_phrase_at_insert_boundary(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)

        message = claw._create_event_from_intent(
            {
                "intent": "create_event",
                "title": "Learning bee parent teacher meeting add to Nysha school calendar",
                "date": "2026-08-28",
                "start_time": "17:00",
                "duration_minutes": 60,
                "timezone": DEFAULT_TIMEZONE,
                "description": None,
                "location": None,
                "recurrence": None,
                "attendees": [],
                "metadata": {},
                "target_calendar": "Nysha school calendar",
            }
        )

        self.assertEqual(provider.created[0]["summary"], "Learning bee parent teacher meeting")
        self.assertEqual(provider.created_calendar_names, ["Nysha school calendar"])
        self.assertIn("Created calendar event: Learning bee parent teacher meeting", message)
        self.assertNotIn("add to Nysha school calendar", message)

    def test_create_event_keeps_calendar_title_words_on_default_calendar(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 12, 17, 57, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = claw.create_event_from_request(
            "Add event to renew calendar subscription 8/28 5 pm",
            reference_time=reference,
        )

        self.assertEqual(provider.created[0]["summary"], "Renew calendar subscription")
        self.assertEqual(provider.created_calendar_names, [None])
        self.assertNotIn("calendarId", provider.created[0])
        self.assertIn("Created calendar event: Renew calendar subscription", message)

    def test_create_event_keeps_action_title_ending_in_calendar(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 12, 17, 57, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = claw.create_event_from_request(
            "Add event to renew calendar 8/28 5 pm",
            reference_time=reference,
        )

        self.assertEqual(provider.created[0]["summary"], "Renew calendar")
        self.assertEqual(provider.created_calendar_names, [None])
        self.assertNotIn("calendarId", provider.created[0])
        self.assertIn("Created calendar event: Renew calendar", message)

    def test_create_event_to_named_calendar_without_subject_asks_for_title(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 12, 17, 57, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = claw.create_event_from_request(
            "Add event to Nysha school calendar 8/28 5 pm",
            reference_time=reference,
        )

        self.assertEqual(message, "Please provide: title.")
        self.assertEqual(provider.created, [])

    def test_add_guests_followup_updates_last_event(self):
        provider = FakeProvider()
        event = _fake_event(
            "Dentist",
            "2026-08-29T12:20:00-07:00",
            "2026-08-29T13:20:00-07:00",
        )
        event["attendees"] = [{"email": "dad@example.test", "displayName": "Dad"}]
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.last_created_event = event

        with patch.dict("os.environ", GUEST_EMAIL_ENV, clear=False):
            message = claw.add_guests_from_request("add mom and dad to the invite")

        self.assertIn("Added guests to Dentist", message)
        self.assertEqual(
            provider.updated[0]["attendees"],
            [
                {"email": "dad@example.test", "displayName": "Dad"},
                {"email": "mom@example.test", "displayName": "Mom"},
            ],
        )
        self.assertTrue(provider.updated[0]["notify_attendees"])

    def test_ai_field_extractor_adds_guests_to_last_event(self):
        provider = FakeProvider()
        event = _fake_event(
            "Learning bee parent teacher meeting",
            "2026-08-28T17:00:00-07:00",
            "2026-08-28T18:00:00-07:00",
        )
        extractor = FakeFieldExtractor(
            {
                "action": "add_guests",
                "confidence": 0.94,
                "slots": {"guest_aliases": ["mom", "dad"]},
                "missing_fields": [],
            }
        )
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.field_extractor = extractor
        claw.last_created_event = event
        reference = datetime(2026, 8, 12, 18, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", GUEST_EMAIL_ENV, clear=False):
            message = claw.create_event_from_request(
                "please add guests to the invite",
                reference_time=reference,
            )

        self.assertEqual(
            extractor.calls[0]["context"],
            {
                "last_created_event": {
                    "event_id": "learning-bee-parent-teacher-meeting",
                    "title": "Learning bee parent teacher meeting",
                }
            },
        )
        self.assertIn("Added guests to Learning bee parent teacher meeting", message)
        self.assertEqual(provider.updated[0]["attendees"], [
            {"email": "mom@example.test", "displayName": "Mom"},
            {"email": "dad@example.test", "displayName": "Dad"},
        ])

    def test_add_guests_target_query_searches_named_writable_calendar(self):
        provider = FakeProvider()
        provider.events = [
            _fake_event(
                "Dentist appointment",
                "2026-12-29T12:20:00-08:00",
                "2026-12-29T13:20:00-08:00",
            )
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.last_created_event = _fake_event(
            "Family dinner",
            "2026-08-13T18:00:00-07:00",
            "2026-08-13T19:00:00-07:00",
        )
        reference = datetime(2026, 8, 12, 18, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.add_guests_from_intent(
                {
                    "intent": "add_guests",
                    "query": "dentist appointment",
                    "target_calendar": "Nysha school calendar",
                    "attendees": [FAMILY_ATTENDEES[0]],
                    "missing_fields": [],
                },
                reference_time=reference,
            )

        self.assertIn("Added guest to Dentist appointment", message)
        self.assertEqual(provider.updated[0]["id"], "dentist-appointment")
        self.assertEqual(provider.list_calendar_names, ["Nysha school calendar"])
        self.assertTrue(provider.list_calls[0]["writable"])
        self.assertEqual(provider.list_calls[0]["query"], "dentist appointment")
        self.assertLess(provider.list_calls[0]["time_min"], "2020-01-01")
        self.assertGreater(provider.list_calls[0]["time_max"], "2030-01-01")

    def test_ai_field_extractor_failure_falls_back_to_deterministic_parse(self):
        provider = FakeProvider()
        extractor = FakeFieldExtractor(error=RuntimeError("model unavailable"))
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.field_extractor = extractor
        reference = datetime(2026, 8, 12, 18, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = claw.create_event_from_request(
            "Add dentist tomorrow at 3 PM",
            reference_time=reference,
        )

        self.assertIn("Created calendar event: Dentist", message)
        self.assertEqual(provider.created[0]["summary"], "Dentist")

    def test_ai_field_extractor_cannot_turn_create_request_into_guest_update(self):
        provider = FakeProvider()
        event = _fake_event(
            "Learning bee parent teacher meeting",
            "2026-08-28T17:00:00-07:00",
            "2026-08-28T18:00:00-07:00",
        )
        extractor = FakeFieldExtractor(
            {
                "action": "add_guests",
                "confidence": 0.94,
                "slots": {
                    "title": "Dinner",
                    "date": "2026-08-13",
                    "start_time": "18:00",
                    "guest_aliases": ["mom", "dad"],
                },
                "missing_fields": [],
            }
        )
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.field_extractor = extractor
        claw.last_created_event = event
        reference = datetime(2026, 8, 12, 18, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", GUEST_EMAIL_ENV, clear=False):
            message = claw.create_event_from_request(
                "invite mom and dad to dinner tomorrow at 6",
                reference_time=reference,
            )

        self.assertIn("Created calendar event: Dinner", message)
        self.assertEqual(provider.created[0]["summary"], "Dinner")
        self.assertEqual(provider.updated, [])

    def test_ai_field_extractor_baseline_redacts_guest_emails(self):
        provider = FakeProvider()
        extractor = FakeFieldExtractor(
            {
                "action": "create_event",
                "confidence": 0.94,
                "slots": {},
                "missing_fields": [],
            }
        )
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.field_extractor = extractor
        reference = datetime(2026, 8, 12, 18, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", GUEST_EMAIL_ENV, clear=False):
            claw.create_event_from_request(
                "Add dentist tomorrow at 3 PM\nAdd guest: family",
                reference_time=reference,
            )

        baseline_intent = extractor.calls[0]["baseline_intent"]
        self.assertEqual(
            baseline_intent["attendees"],
            [{"displayName": "Dad"}, {"displayName": "Mom"}],
        )
        self.assertNotIn("dad@example.test", repr(baseline_intent))

    def test_api_context_ai_primary_creates_rich_calendar_event(self):
        provider = FakeProvider()
        extractor = FakeFieldExtractor(
            {
                "action": "create_event",
                "confidence": 0.97,
                "slots": {
                    "title": "Soccer tournament",
                    "date": "2026-09-05",
                    "start_time": "09:00",
                    "duration_minutes": 240,
                    "calendar_name": "sports calendar",
                    "location": "Memorial Park",
                    "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=SA"],
                },
                "missing_fields": [],
            },
            primary=True,
        )
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.field_extractor = extractor
        reference = datetime(2026, 8, 12, 18, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", GUEST_EMAIL_ENV, clear=False):
            message = claw.create_event_from_request(
                "/calendar Put soccer tournament Sept 5 9am to 1pm on sports calendar at Memorial Park",
                reference_time=reference,
            )

        self.assertIn("Created calendar event: Soccer tournament", message)
        created = provider.created[0]
        self.assertEqual(created["summary"], "Soccer tournament")
        self.assertEqual(created["start"]["dateTime"], "2026-09-05T09:00:00-07:00")
        self.assertEqual(created["end"]["dateTime"], "2026-09-05T13:00:00-07:00")
        self.assertEqual(created["calendarId"], "sports calendar")
        self.assertEqual(created["location"], "Memorial Park")
        self.assertEqual(created["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=SA"])
        self.assertEqual(created["attendees"], [])
        self.assertFalse(created["notify_attendees"])

    def test_api_context_ai_primary_creates_all_day_event(self):
        provider = FakeProvider()
        extractor = FakeFieldExtractor(
            {
                "action": "create_event",
                "confidence": 0.96,
                "slots": {
                    "title": "School holiday",
                    "date": "2026-09-07",
                    "all_day": True,
                    "calendar_name": "kids calendar",
                },
                "missing_fields": [],
            },
            primary=True,
        )
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.field_extractor = extractor
        reference = datetime(2026, 8, 12, 18, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        message = claw.create_event_from_request(
            "/calendar School holiday on Sep 7 all day to kids calendar",
            reference_time=reference,
        )

        self.assertIn("Created calendar event: School holiday", message)
        self.assertIn("all day", message)
        created = provider.created[0]
        self.assertEqual(created["start"], {"date": "2026-09-07"})
        self.assertEqual(created["end"], {"date": "2026-09-08"})
        self.assertTrue(created["all_day"])
        self.assertEqual(created["calendarId"], "kids calendar")

    def test_add_guests_followup_requires_configured_contacts(self):
        provider = FakeProvider()
        event = _fake_event(
            "Dentist",
            "2026-08-29T12:20:00-07:00",
            "2026-08-29T13:20:00-07:00",
        )
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.last_created_event = event

        with patch.dict("os.environ", {}, clear=True):
            message = claw.add_guests_from_request("add mom and dad to the invite")

        self.assertIn("Please configure calendar guest email contacts", message)
        self.assertEqual(provider.updated, [])

    def test_create_event_with_guest_line_requires_configured_contacts(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 11, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", {}, clear=True):
            message = claw.create_event_from_request(
                "Add dentist Saturday at 10 AM\nAdd guest: family",
                reference_time=reference,
            )

        self.assertIn("Please configure calendar guest email contacts", message)
        self.assertEqual(provider.created, [])

    def test_pending_create_keeps_missing_guest_contacts_after_followup(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 11, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", {}, clear=True):
            first_message = claw.create_event_from_request(
                "Add dentist\nAdd guest: family",
                reference_time=reference,
            )
            handled = claw.handle_pending_response(
                "Saturday at 10 AM",
                reference_time=reference,
            )

        self.assertIn("Please configure calendar guest email contacts", first_message)
        self.assertTrue(handled)
        self.assertIsNotNone(claw.pending_action)
        self.assertEqual(claw.pending_action.payload["missing_fields"], ["guest_contacts"])
        self.assertEqual(provider.created, [])

    def test_pending_create_keeps_guest_contacts_added_in_followup(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 11, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", {}, clear=True):
            first_message = claw.create_event_from_request(
                "Add dentist Saturday",
                reference_time=reference,
            )
            handled = claw.handle_pending_response(
                "10 AM\nAdd guest: family",
                reference_time=reference,
            )

        self.assertEqual(first_message, "Please provide a time for Dentist on Saturday, August 15.")
        self.assertTrue(handled)
        self.assertIsNotNone(claw.pending_action)
        self.assertEqual(claw.pending_action.payload["missing_fields"], ["guest_contacts"])
        self.assertEqual(
            claw.pending_action.payload["missing_guest_contacts"],
            ["dad", "mom"],
        )
        self.assertEqual(provider.created, [])

    def test_assign_owner_followup_updates_last_created_event(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add calendar event for Tuesday 8 PM to cancel Fox 1",
                reference_time=datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE)),
            )
            message = claw.assign_owner_from_request("assign it to nimesh")

        self.assertIn("Assigned event to dad", message)
        _, metadata = read_metadata_from_event(provider.updated[-1])
        self.assertEqual(metadata["owner"], "dad")

    def test_assign_owner_preserves_attendees_without_notifications(self):
        provider = FakeProvider()
        event = _fake_event(
            "Dentist",
            "2026-08-29T12:20:00-07:00",
            "2026-08-29T13:20:00-07:00",
        )
        event["attendees"] = [{"email": "friend@example.com", "displayName": "Friend"}]
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.last_created_event = event

        with redirect_stdout(StringIO()):
            claw.assign_owner_from_request("assign it to nimesh")

        self.assertEqual(provider.updated[0]["attendees"], event["attendees"])
        self.assertFalse(provider.updated[0]["notify_attendees"])

    def test_assign_owner_by_title_updates_matching_event(self):
        provider = FakeProvider()
        provider.events = [
            _fake_event(
                "Cancel Fox 1",
                "2026-07-07T20:00:00-07:00",
                "2026-07-07T21:00:00-07:00",
            ),
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            message = claw.assign_owner_from_request(
                "Assign Cancel Fox 1 event to nimesh",
                reference_time=datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE)),
            )

        self.assertIn("Assigned event to dad", message)
        _, metadata = read_metadata_from_event(provider.updated[-1])
        self.assertEqual(metadata["owner"], "dad")

    def test_undo_reverts_created_event(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add dinner Friday at 7 with Rahul",
                reference_time=reference,
            )
            message = claw.undo_last_action()

        self.assertIn("Undid calendar event creation", message)
        self.assertEqual(provider.deleted, ["event-123"])

    def test_undo_reverts_updated_event(self):
        provider = FakeProvider()
        provider.events = [
            _fake_event(
                "Dinner with Rahul",
                "2026-07-03T19:00:00-07:00",
                "2026-07-03T20:00:00-07:00",
                location="Fremont",
            )
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.update_event_from_request(
                "Move dinner with Rahul to Friday at 8pm",
                reference_time=reference,
            )
            claw.handle_pending_response("yes")
            message = claw.undo_last_action()

        self.assertIn("Undid calendar event update", message)
        self.assertEqual(provider.updated[-1]["start"]["dateTime"], "2026-07-03T19:00:00-07:00")

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
        notes, metadata = read_metadata_from_event(provider.created[0])
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
        notes, metadata = read_metadata_from_event(provider.created[0])
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
        notes, metadata = read_metadata_from_event(provider.created[0])
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

    def test_bulk_date_event_request_asks_for_one_time(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "Add events to pay driver for below days\n10/1\n12/1\n2/1\n4/1",
                reference_time=reference,
            )

        self.assertEqual(
            message,
            "Please provide a time for Pay driver on 4 dates: Oct 1, Dec 1, Feb 1, Apr 1.",
        )
        self.assertIsNotNone(claw.pending_action)
        self.assertEqual(provider.created, [])

    def test_bulk_date_event_request_time_followup_requires_confirmation(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add events to pay driver for below days\n10/1\n12/1\n2/1\n4/1",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("9 AM")

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertEqual(provider.created, [])

        with redirect_stdout(StringIO()):
            confirmed = claw.handle_pending_response("yes")

        self.assertTrue(confirmed)
        self.assertIsNone(claw.pending_action)
        self.assertEqual(
            [event["summary"] for event in provider.created],
            ["Pay driver", "Pay driver", "Pay driver", "Pay driver"],
        )
        self.assertEqual(
            [event["start"]["dateTime"] for event in provider.created],
            [
                "2026-10-01T09:00:00-07:00",
                "2026-12-01T09:00:00-08:00",
                "2027-02-01T09:00:00-08:00",
                "2027-04-01T09:00:00-07:00",
            ],
        )

    def test_bulk_date_event_request_full_day_followup_requires_confirmation(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add events to pay driver for below days\n10/1\n12/1\n2/1\n4/1",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("Whenever time is missing make them full day")

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertEqual(provider.created, [])

        with redirect_stdout(StringIO()):
            confirmed = claw.handle_pending_response("yes")

        self.assertTrue(confirmed)
        self.assertIsNone(claw.pending_action)
        self.assertEqual(len(provider.created), 4)
        self.assertEqual(
            [event["start"]["date"] for event in provider.created],
            ["2026-10-01", "2026-12-01", "2027-02-01", "2027-04-01"],
        )
        self.assertTrue(all(event["all_day"] for event in provider.created))

    def test_bulk_date_event_request_with_time_requires_confirmation(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 9, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "Add events at 9 AM to pay driver for below days\n10/1\n12/1\n2/1\n4/1",
                reference_time=reference,
            )

        self.assertIn("4 dates", message)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertEqual(provider.created, [])

        with redirect_stdout(StringIO()):
            handled = claw.handle_pending_response("yes", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(len(provider.created), 4)

    def test_confirmation_accepts_named_calendar_correction(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add swim class tomorrow at 6 PM",
                reference_time=reference,
                require_confirmation=True,
            )
            handled = claw.handle_pending_response(
                "Nysha school calendar instead",
                reference_time=reference,
            )

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "confirm_create")
        self.assertEqual(
            claw.pending_action.payload["target_calendar"],
            "Nysha school calendar",
        )
        self.assertEqual(provider.created, [])

    def test_bulk_confirmation_accepts_named_calendar_correction(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        semantic_intent = {
            "intent": "create_event",
            "title": "Swim class",
            "start_time": "18:00",
            "target_calendar": "Family calendar",
            "missing_fields": [],
        }

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add swim class for these dates\n9/1\n9/8",
                reference_time=reference,
                semantic_intent=semantic_intent,
            )
            handled = claw.handle_pending_response(
                "Nysha school calendar instead",
                reference_time=reference,
            )

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertEqual(
            [
                item["target_calendar"]
                for item in claw.pending_action.payload["intents"]
            ],
            ["Nysha school calendar", "Nysha school calendar"],
        )
        self.assertEqual(provider.created, [])

    def test_bulk_date_preview_keeps_semantic_shared_fields(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        semantic_intent = {
            "intent": "create_event",
            "title": "Driver payment",
            "start_time": "10:00",
            "duration_minutes": 30,
            "target_calendar": "Family calendar",
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=TH"],
            "missing_fields": [],
        }

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add events to pay driver for below days\n10/1\n12/1",
                reference_time=datetime(2026, 8, 9, 12, tzinfo=ZoneInfo(DEFAULT_TIMEZONE)),
                require_confirmation=True,
                semantic_intent=semantic_intent,
            )

        intents = claw.pending_action.payload["intents"]
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertEqual([item["title"] for item in intents], ["Driver payment", "Driver payment"])
        self.assertEqual([item["target_calendar"] for item in intents], ["Family calendar"] * 2)
        self.assertTrue(all(not item.get("recurrence") for item in intents))

    def test_bulk_semantic_intent_preserves_missing_guest_contacts(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        semantic_intent = {
            "intent": "create_event",
            "title": "Swim class",
            "start_time": "18:00",
            "attendees": [],
            "missing_guest_contacts": ["dad"],
            "missing_fields": ["guest_contacts"],
        }

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "Add swim class for these dates\n9/1\n9/8",
                reference_time=datetime(
                    2026,
                    8,
                    21,
                    12,
                    tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
                ),
                semantic_intent=semantic_intent,
            )

        self.assertIn("guest", message.lower())
        self.assertEqual(claw.pending_action.action, "create_bulk")
        self.assertEqual(
            [item["missing_guest_contacts"] for item in claw.pending_action.payload["intents"]],
            [["dad"], ["dad"]],
        )
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
        notes, metadata = read_metadata_from_event(provider.created[0])
        self.assertEqual(notes, "Niyati picks up Navya from school")
        self.assertEqual(metadata["owner"], "mom")
        self.assertEqual(metadata["person"], "Navya")
        self.assertEqual(metadata["category"], "school")

    def test_missing_time_noon_followup_creates_original_event(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "Add appointment Friday",
                reference_time=reference,
            )

        self.assertEqual(message, "Please provide a time for Appointment on Friday, July 3.")
        self.assertIsNotNone(claw.pending_action)

        with redirect_stdout(StringIO()):
            handled = claw.handle_pending_response("noon")

        self.assertTrue(handled)
        self.assertIsNone(claw.pending_action)
        self.assertEqual(provider.created[0]["summary"], "Appointment")
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-03T12:00:00-07:00")
        self.assertEqual(provider.created[0]["end"]["dateTime"], "2026-07-03T13:00:00-07:00")

    def test_missing_time_full_day_followup_creates_all_day_event(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 7, 2, 12, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "Add appointment Friday",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("Whenever time is missing make it full day")

        self.assertTrue(handled)
        self.assertIsNone(claw.pending_action)
        self.assertEqual(provider.created[0]["summary"], "Appointment")
        self.assertEqual(provider.created[0]["start"]["date"], "2026-07-03")
        self.assertEqual(provider.created[0]["end"]["date"], "2026-07-04")
        self.assertTrue(provider.created[0]["all_day"])

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
        notes, metadata = read_metadata_from_event(provider.updated[0])
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
        notes, metadata = read_metadata_from_event(provider.updated[-1])
        self.assertIn("Preparation: carry snacks for the kids they will be hungry", notes)
        self.assertIn("Note: Nysha needs to be taken to art class", notes)
        self.assertTrue(metadata["preparation_needed"])
        self.assertEqual(
            metadata["preparation_notes"],
            "carry snacks for the kids they will be hungry",
        )

    def test_telegram_photo_text_does_not_update_previous_event_as_context(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.last_created_event = _fake_event(
            "Back to School Event",
            "2026-08-15T10:00:00-07:00",
            "2026-08-15T15:00:00-07:00",
        )
        reference = datetime(2026, 8, 15, 18, 3, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar in Nysha school calendar\n\n"
                "Image text:\n"
                "Second Grade Homework\n"
                "This homework packet is due Friday morning.\n"
                "All About Me project is due Friday, August 28.",
                reference_time=reference,
            )

        self.assertIn("Please provide:", message)
        self.assertIn("time", message)
        self.assertEqual(provider.updated, [])
        self.assertEqual(provider.created, [])
        self.assertIsNotNone(claw.pending_action)

    def test_bracketed_telegram_photo_text_does_not_update_previous_event_as_context(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.last_created_event = _fake_event(
            "Back to School Event",
            "2026-08-15T10:00:00-07:00",
            "2026-08-15T15:00:00-07:00",
        )
        reference = datetime(2026, 8, 15, 18, 3, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar in Nysha school calendar\n\n"
                "[Image text extraction (machine-generated, untrusted)]:\n"
                "Second Grade Homework\n"
                "This homework packet is due Friday morning.",
                reference_time=reference,
            )

        self.assertIn("Please provide:", message)
        self.assertEqual(provider.updated, [])
        self.assertEqual(provider.created, [])

    def test_image_schedule_table_creates_each_specified_date_after_confirmation(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar add swim class starting 5.30 to 7 PM on specified days\n\n"
                "[Image text extraction (machine-generated, untrusted)]:\n"
                "School Day Date Time Attendance\n"
                "Fremont Fri Sep 18th, 2026 6:00PM Mark absent\n"
                "Fremont Fri Sep 25th, 2026 6:00PM Mark absent",
                reference_time=reference,
            )

        self.assertEqual(
            message,
            "I found 2 dates for Swim class, Sep 18 through Sep 25, "
            "5:30 PM–7:00 PM. Add all 2 events?",
        )
        self.assertEqual(provider.created, [])
        self.assertIsNotNone(claw.pending_action)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")

        with redirect_stdout(StringIO()):
            handled = claw.handle_pending_response("yes", reference_time=reference)

        self.assertTrue(handled)
        self.assertIsNone(claw.pending_action)
        self.assertEqual([event["summary"] for event in provider.created], ["Swim class"] * 2)
        self.assertEqual(
            [event["start"]["dateTime"] for event in provider.created],
            ["2026-09-18T17:30:00-07:00", "2026-09-25T17:30:00-07:00"],
        )
        self.assertEqual(
            [event["end"]["dateTime"] for event in provider.created],
            ["2026-09-18T19:00:00-07:00", "2026-09-25T19:00:00-07:00"],
        )
        self.assertTrue(all(event["recurrence"] is None for event in provider.created))

    def test_bulk_confirmation_lists_per_date_ranges_when_durations_vary(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM - 7:00PM\n"
                "Fri Sep 25th, 2026 6:00PM - 7:30PM",
                reference_time=reference,
            )

        self.assertIn("Sep 18 6:00 PM–7:00 PM", message)
        self.assertIn("Sep 25 6:00 PM–7:30 PM", message)

    def test_image_schedule_confirmation_can_be_cancelled(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class\n\n"
                "Image text:\n"
                "Fremont Fri Sep 18th, 2026 6:00PM Mark absent",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("no", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(provider.created, [])
        self.assertIsNone(claw.pending_action)

    def test_image_schedule_confirmation_accepts_natural_yes(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class\n\n"
                "Image text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("yes please.", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(len(provider.created), 1)

    def test_image_schedule_confirmation_accepts_time_correction(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class\n\n"
                "Image text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("5:30 PM", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(provider.created, [])
        self.assertEqual(claw.pending_action.payload["start_time"], "17:30")

    def test_image_schedule_start_only_correction_preserves_duration(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class 5:30 to 7 PM\n\n"
                "Image text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            claw.handle_pending_response("5 PM", reference_time=reference)

        self.assertEqual(claw.pending_action.payload["start_time"], "17:00")
        self.assertEqual(claw.pending_action.payload["duration_minutes"], 90)

    def test_image_recurring_confirmation_time_correction_advances_past_occurrence(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 20, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class\n\n"
                "Image text:\n"
                "Fri Aug 21st, 2026 9:00PM\n"
                "Fri Aug 28th, 2026 9:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("5 PM", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["date"], "2026-08-28")

    def test_image_schedule_confirmation_accepts_duration_only_correction(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class\n\n"
                "Image text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("for 90 minutes", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["duration_minutes"], 90)

    def test_image_schedule_time_correction_clears_all_day(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim camp all day\n\n"
                "Image text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("5 PM", reference_time=reference)

        self.assertTrue(handled)
        self.assertFalse(claw.pending_action.payload["all_day"])
        self.assertEqual(claw.pending_action.payload["start_time"], "17:00")

    def test_image_bulk_confirmation_accepts_time_correction(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM\n"
                "Fri Sep 25th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("5:30 PM", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(
            [item["start_time"] for item in claw.pending_action.payload["intents"]],
            ["17:30", "17:30"],
        )

    def test_image_bulk_confirmation_accepts_time_range_correction(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM\n"
                "Fri Sep 25th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("5:30 to 7 PM", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(
            [item["duration_minutes"] for item in claw.pending_action.payload["intents"]],
            [90, 90],
        )

    def test_image_bulk_confirmation_accepts_hour_only_time_range(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM\n"
                "Fri Sep 25th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("5 to 7 PM", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(
            [item["duration_minutes"] for item in claw.pending_action.payload["intents"]],
            [120, 120],
        )

    def test_image_bulk_confirmation_accepts_single_corrected_date(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM\n"
                "Fri Sep 25th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("Sep 26", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["dates"], ["2026-09-26"])

    def test_image_bulk_existing_date_correction_keeps_that_rows_time(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM\n"
                "Fri Sep 25th, 2026 7:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("Sep 25", reference_time=reference)

        self.assertTrue(handled)
        selected = claw.pending_action.payload["intents"][0]
        self.assertEqual(selected["date"], "2026-09-25")
        self.assertEqual(selected["start_time"], "19:00")

    def test_image_bulk_new_date_correction_asks_for_time_when_row_times_vary(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM\n"
                "Fri Sep 25th, 2026 7:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("Sep 26", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "create_bulk")
        selected = claw.pending_action.payload["intents"][0]
        self.assertEqual(selected["date"], "2026-09-26")
        self.assertIsNone(selected["start_time"])
        self.assertIn("time", selected["missing_fields"])

    def test_image_bulk_new_date_and_time_correction_moves_to_confirmation(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM\n"
                "Fri Sep 25th, 2026 7:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response(
                "Sep 26 at 5:30 PM",
                reference_time=reference,
            )

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        selected = claw.pending_action.payload["intents"][0]
        self.assertEqual(selected["date"], "2026-09-26")
        self.assertEqual(selected["start_time"], "17:30")
        self.assertNotIn("time", selected["missing_fields"])

    def test_image_bulk_new_date_asks_for_duration_when_row_durations_vary(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM - 7:00PM\n"
                "Fri Sep 25th, 2026 6:00PM - 7:30PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("Sep 26", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "create_bulk")
        selected = claw.pending_action.payload["intents"][0]
        self.assertIsNone(selected["duration_minutes"])
        self.assertIn("duration", selected["missing_fields"])
        self.assertEqual(provider.created, [])

        with redirect_stdout(StringIO()):
            corrected = claw.handle_pending_response(
                "for 90 minutes",
                reference_time=reference,
            )

        self.assertTrue(corrected)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        selected = claw.pending_action.payload["intents"][0]
        self.assertEqual(selected["duration_minutes"], 90)
        self.assertNotIn("duration", selected["missing_fields"])

    def test_image_schedule_batch_ignores_past_dates(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fri Aug 14th, 2026 6:00PM\n"
                "Fri Aug 21st, 2026 6:00PM\n"
                "Fri Aug 28th, 2026 6:00PM",
                reference_time=reference,
            )

        self.assertNotIn("Aug 14", message)
        self.assertNotIn("2026-08-14", repr(claw.pending_action.payload))

    def test_image_schedule_everyday_typo_still_creates_finite_date_batch(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        request = (
            "/calendar add event everyday day mentioned from 5.30 pm to 7 pm. "
            "Title: Swim Class at American swim academy\n\n"
            "[Image text extraction (machine-generated, untrusted)]:\n"
            "School Day Date Time Attendance\n"
            "Fremont Fri Sep 18th, 2026 6:00PM Mark absent\n"
            "Fremont Fri Sep 25th, 2026 6:00PM Mark absent\n"
            "Fremont Fri Oct 2nd, 2026 6:00PM Mark absent"
        )

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(request, reference_time=reference)

        self.assertIn("I found 3 dates", message)
        self.assertIn("Add all 3 events?", message)
        self.assertIsNotNone(claw.pending_action)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertEqual(
            [intent["date"] for intent in claw.pending_action.payload["intents"]],
            ["2026-09-18", "2026-09-25", "2026-10-02"],
        )
        self.assertTrue(
            all(intent["recurrence"] is None for intent in claw.pending_action.payload["intents"])
        )
        self.assertTrue(
            all(not intent["attendees"] for intent in claw.pending_action.payload["intents"])
        )

    def test_image_schedule_batch_uses_row_time_when_caption_omits_time(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fremont Fri Sep 18th, 2026 6:00PM Mark absent\n"
                "Fremont Fri Sep 25th, 2026 6:00PM Mark absent",
                reference_time=reference,
            )

        self.assertIn("6:00 PM–7:00 PM", message)
        self.assertEqual(
            [intent["start_time"] for intent in claw.pending_action.payload["intents"]],
            ["18:00", "18:00"],
        )

    def test_image_schedule_batch_confirmation_reports_varying_row_times(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Fremont Fri Sep 18th, 2026 6:00PM Mark absent\n"
                "Fremont Fri Sep 25th, 2026 7:00PM Mark absent",
                reference_time=reference,
            )

        self.assertIn(
            "times vary: Sep 18 6:00 PM–7:00 PM; Sep 25 7:00 PM–8:00 PM",
            message,
        )

    def test_image_schedule_batch_does_not_use_dates_from_other_activities(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar add swim class on specified days\n\n"
                "Image text:\n"
                "Swim Fri Sep 18th, 2026 6:00PM\n"
                "Chess Fri Sep 25th, 2026 6:00PM",
                reference_time=reference,
            )

        self.assertNotIn("2 dates", message)
        self.assertEqual(claw.pending_action.action, "confirm_create")
        self.assertEqual(claw.pending_action.payload["date"], "2026-09-18")

    def test_dates_in_image_request_creates_finite_batch_not_recurrence(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar add 5.30 to 7 PM for the dates in the image. "
                "Title: Swim class at 6 for Nysha and Navya\n\n"
                "Image text:\n"
                "Fremont Fri Sep 18th, 2026 6:00PM Mark absent\n"
                "Fremont Fri Sep 25th, 2026 6:00PM Mark absent\n"
                "Fremont Fri Oct 2nd, 2026 6:00PM Mark absent",
                reference_time=reference,
            )

        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertEqual(
            claw.pending_action.payload["dates"],
            ["2026-09-18", "2026-09-25", "2026-10-02"],
        )
        self.assertTrue(
            all(item["recurrence"] is None for item in claw.pending_action.payload["intents"])
        )
        self.assertTrue(
            all(
                item["title"] == "Swim class at 6 for Nysha and Navya"
                and item["start_time"] == "17:30"
                and item["duration_minutes"] == 90
                for item in claw.pending_action.payload["intents"]
            )
        )
        self.assertIn("3 dates", message)

    def test_image_schedule_all_day_batch_does_not_require_time(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar add swim camp all day on specified days\n\n"
                "Image text:\n"
                "Fremont Fri Sep 18th, 2026 6:00PM Mark absent\n"
                "Fremont Fri Sep 25th, 2026 6:00PM Mark absent",
                reference_time=reference,
            )

        self.assertIn("all day", message)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertTrue(
            all(intent["all_day"] for intent in claw.pending_action.payload["intents"])
        )

    def test_image_schedule_single_followup_still_requires_confirmation(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            first = claw.create_event_from_request(
                "/calendar add\n\nImage text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("swim class", reference_time=reference)

        self.assertEqual(first, "Please provide: title.")
        self.assertTrue(handled)
        self.assertEqual(provider.created, [])
        self.assertEqual(claw.pending_action.action, "confirm_create")

    def test_image_title_followup_date_and_time_override_ocr(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add\n\nImage text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response(
                "swim class Sep 25 at 5 PM",
                reference_time=reference,
            )

        self.assertTrue(handled)
        self.assertEqual(provider.created, [])
        self.assertEqual(claw.pending_action.payload["date"], "2026-09-25")
        self.assertEqual(claw.pending_action.payload["start_time"], "17:00")

    def test_image_date_correction_clears_inferred_recurrence(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM\n"
                "Fri Sep 25th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("Sep 26", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["date"], "2026-09-26")
        self.assertIsNone(claw.pending_action.payload["recurrence"])

    def test_image_date_correction_clears_incompatible_typed_recurrence(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class every Friday\n\n"
                "Image text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("Sep 19", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["date"], "2026-09-19")
        self.assertIsNone(claw.pending_action.payload["recurrence"])

    def test_image_confirmation_accepts_configured_guest_addition(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", GUEST_EMAIL_ENV, clear=True), redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class\n\nImage text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("invite dad", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "confirm_create")
        self.assertEqual(claw.pending_action.payload["attendees"], [FAMILY_ATTENDEES[0]])

    def test_image_confirmation_accepts_yes_with_guest_without_replacing_title(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", GUEST_EMAIL_ENV, clear=True), redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class\n\nImage text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("yes, invite dad", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["title"], "Swim class")
        self.assertEqual(claw.pending_action.payload["attendees"], [FAMILY_ATTENDEES[0]])

    def test_image_confirmation_accepts_exclamation_before_guest(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", GUEST_EMAIL_ENV, clear=True), redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class\n\nImage text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("yes!invite dad", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["title"], "Swim class")
        self.assertEqual(claw.pending_action.payload["attendees"], [FAMILY_ATTENDEES[0]])

    def test_image_confirmation_strips_yes_before_weekday_correction(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class\n\nImage text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response(
                "yes Friday at 5 PM",
                reference_time=reference,
            )

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["title"], "Swim class")
        self.assertEqual(claw.pending_action.payload["start_time"], "17:00")

    def test_image_missing_contact_followup_does_not_replace_title_with_yes(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", {}, clear=True), redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class and invite dad\n\n"
                "Image text:\nFri Sep 18th, 2026 6:00PM",
                reference_time=reference,
            )
            original_title = claw.pending_action.payload["title"]
            handled = claw.handle_pending_response("yes, invite dad", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "create")
        self.assertEqual(claw.pending_action.payload["title"], original_title)

    def test_missing_time_followup_uses_compatible_explicit_recurrence_date(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 22, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class every Friday",
                reference_time=reference,
            )
            handled = claw.handle_pending_response(
                "Sep 11 at 5 PM",
                reference_time=reference,
            )

        self.assertTrue(handled)
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-09-11T17:00:00-07:00")
        self.assertEqual(provider.created[0]["recurrence"], ["RRULE:FREQ=WEEKLY;BYDAY=FR"])

    def test_image_date_correction_clears_incompatible_monthly_ordinal_recurrence(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add swim class every first Thursday\n\n"
                "Image text:\nThu Sep 3rd, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("Sep 10", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["date"], "2026-09-10")
        self.assertIsNone(claw.pending_action.payload["recurrence"])

    def test_bulk_image_confirmation_requests_missing_guest_contact(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", {}, clear=True), redirect_stdout(StringIO()) as output:
            claw.create_event_from_request(
                "/calendar add swim class for all dates\n\n"
                "Image text:\nFri Sep 18th, 2026 6:00PM\nFri Sep 25th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("invite dad", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "create_bulk")
        self.assertIn("Please configure calendar guest email contacts for: dad.", output.getvalue())

    def test_recurring_time_followup_advances_past_same_day_occurrence(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request("Add swim class every Friday", reference_time=reference)
            handled = claw.handle_pending_response("5 PM", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-08-28T17:00:00-07:00")

    def test_recurring_time_followup_accepts_naive_reference_time(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18)

        with redirect_stdout(StringIO()):
            claw.create_event_from_request("Add swim class every Friday", reference_time=reference)
            handled = claw.handle_pending_response("5 PM", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-08-28T17:00:00-07:00")

    def test_mixed_image_rows_recover_after_title_followup(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            first = claw.create_event_from_request(
                "/calendar add\n\n"
                "Image text:\n"
                "Chess Fri Aug 28th, 2026 6:00PM\n"
                "Swim Fri Sep 4th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("swim class", reference_time=reference)

        self.assertIn("title", first)
        self.assertTrue(handled)
        self.assertEqual(provider.created, [])
        self.assertEqual(claw.pending_action.action, "confirm_create")
        self.assertEqual(claw.pending_action.payload["date"], "2026-09-04")

    def test_mixed_image_bulk_recovers_matching_dates_after_title_followup(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add on specified days\n\n"
                "Image text:\n"
                "Chess Fri Aug 28th, 2026 6:00PM\n"
                "Swim Fri Sep 4th, 2026 6:00PM\n"
                "Swim Fri Sep 11th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response("swim class", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(provider.created, [])
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertEqual(
            claw.pending_action.payload["dates"],
            ["2026-09-04", "2026-09-11"],
        )

    def test_image_bulk_title_followup_time_overrides_ocr_time(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add on specified days\n\n"
                "Image text:\n"
                "Chess Fri Aug 28th, 2026 6:00PM\n"
                "Swim Fri Sep 4th, 2026 6:00PM\n"
                "Swim Fri Sep 11th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response(
                "swim class at 5:30 PM for 90 minutes",
                reference_time=reference,
            )

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertEqual(
            [item["start_time"] for item in claw.pending_action.payload["intents"]],
            ["17:30", "17:30"],
        )
        self.assertEqual(
            [item["duration_minutes"] for item in claw.pending_action.payload["intents"]],
            [90, 90],
        )
        self.assertNotIn("time", claw.pending_action.payload["missing_fields"])
        self.assertNotIn("duration", claw.pending_action.payload["missing_fields"])

    def test_image_bulk_title_followup_single_date_replaces_ocr_batch(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 18, 18, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            claw.create_event_from_request(
                "/calendar add on specified days\n\n"
                "Image text:\n"
                "Chess Fri Aug 28th, 2026 6:00PM\n"
                "Swim Fri Sep 18th, 2026 6:00PM\n"
                "Swim Fri Sep 25th, 2026 6:00PM",
                reference_time=reference,
            )
            handled = claw.handle_pending_response(
                "swim class Sep 26",
                reference_time=reference,
            )

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["dates"], ["2026-09-26"])

    def test_image_schedule_bulk_followup_still_requires_confirmation(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        base_intent = extract_intent("add", now=reference)
        intents = []
        for event_date in ("2026-09-18", "2026-09-25"):
            intent = dict(base_intent)
            intent.update(
                date=event_date,
                start_time="18:00",
                confirmation_required=False,
                missing_fields=["title"],
            )
            intents.append(intent)
        claw.pending_action = PendingAction(
            action="create_bulk",
            payload={
                "intent": "create_events",
                "intents": intents,
                "dates": ["2026-09-18", "2026-09-25"],
                "confirmation_required": True,
                "missing_fields": ["title"],
            },
        )

        with redirect_stdout(StringIO()):
            handled = claw.handle_pending_response("swim class", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(provider.created, [])
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")

    def test_image_schedule_bulk_missing_title_strips_leading_yes(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        base_intent = extract_intent("add", now=reference)
        intents = []
        for event_date in ("2026-09-18", "2026-09-25"):
            intent = dict(base_intent)
            intent.update(date=event_date, start_time="18:00", missing_fields=["title"])
            intents.append(intent)
        claw.pending_action = PendingAction(
            action="create_bulk",
            payload={
                "intent": "create_events",
                "intents": intents,
                "dates": ["2026-09-18", "2026-09-25"],
                "confirmation_required": True,
                "missing_fields": ["title"],
            },
        )

        with redirect_stdout(StringIO()):
            handled = claw.handle_pending_response("yes, swim class", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.action, "confirm_create_bulk")
        self.assertEqual(
            [item["title"] for item in claw.pending_action.payload["intents"]],
            ["Swim class", "Swim class"],
        )

    def test_image_schedule_bulk_missing_title_preserves_title_starting_with_yes(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        base_intent = extract_intent("add", now=reference)
        intent = {
            **base_intent,
            "date": "2026-09-18",
            "start_time": "18:00",
            "missing_fields": ["title"],
        }
        claw.pending_action = PendingAction(
            action="create_bulk",
            payload={
                "intent": "create_events",
                "intents": [intent],
                "dates": ["2026-09-18"],
                "missing_fields": ["title"],
            },
        )

        with redirect_stdout(StringIO()):
            handled = claw.handle_pending_response("Yes Day", reference_time=reference)

        self.assertTrue(handled)
        self.assertEqual(provider.created[0]["summary"], "Yes Day")

    def test_image_schedule_bulk_missing_field_correction_replaces_date_and_duration(self):
        claw = FamilyCalendarClaw.from_provider(FakeProvider())
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        intents = [
            {
                **extract_intent("add swim class", now=reference),
                "date": event_date,
                "start_time": "18:00",
                "missing_guest_contacts": ["dad"],
                "missing_fields": ["guest_contacts"],
            }
            for event_date in ("2026-09-18", "2026-09-25")
        ]
        claw.pending_action = PendingAction(
            action="create_bulk",
            payload={
                "intent": "create_events",
                "intents": intents,
                "dates": ["2026-09-18", "2026-09-25"],
                "confirmation_required": True,
                "missing_fields": ["guest_contacts"],
            },
        )

        with patch.dict("os.environ", {}, clear=True), redirect_stdout(StringIO()):
            handled = claw.handle_pending_response(
                "Sep 26 at 5:30 to 7 PM",
                reference_time=reference,
            )

        self.assertTrue(handled)
        self.assertEqual(claw.pending_action.payload["dates"], ["2026-09-26"])
        corrected = claw.pending_action.payload["intents"][0]
        self.assertEqual(corrected["start_time"], "17:30")
        self.assertEqual(corrected["duration_minutes"], 90)

    def test_image_schedule_bulk_preserves_missing_guest_contacts(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 21, 20, 14, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with patch.dict("os.environ", {}, clear=True), redirect_stdout(StringIO()):
            message = claw.create_event_from_request(
                "/calendar add swim class for all dates and invite dad\n\n"
                "Image text:\n"
                "Fri Sep 18th, 2026 6:00PM\n"
                "Fri Sep 25th, 2026 6:00PM",
                reference_time=reference,
            )

        self.assertEqual(message, "Please configure calendar guest email contacts for: dad.")
        self.assertEqual(provider.created, [])
        self.assertEqual(claw.pending_action.action, "create_bulk")
        self.assertIn("guest_contacts", claw.pending_action.payload["missing_fields"])

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
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-07-03T08:00:00-07:00")
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

    def test_when_named_school_event_filters_by_text_query(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "first-day",
                "summary": "First day of school",
                "start": {"dateTime": "2026-08-11T00:00:00-07:00"},
                "end": {"dateTime": "2026-08-11T23:59:00-07:00"},
            },
            {
                "id": "minimum-day",
                "summary": "Minimum Day - 1:30 PM dismissal (Grade 1-3)",
                "description": "Recurring Wednesday dismissal excluding no-school/break Wednesdays.",
                "start": {"dateTime": "2026-08-12T13:30:00-07:00"},
                "end": {"dateTime": "2026-08-12T13:45:00-07:00"},
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 9, 0, 39, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.list_events_from_request(
                "When is Nysha's first day of school?",
                reference_time=reference,
            )

        self.assertEqual(provider.list_calls[0]["time_min"], "2026-08-09T00:39:00-07:00")
        self.assertEqual(provider.list_calls[0]["time_max"], "2027-08-09T00:39:00-07:00")
        self.assertIn("First day of school", message)
        self.assertNotIn("Minimum Day", message)

    def test_when_holidays_filters_no_school_related_events(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "holiday",
                "summary": "Holiday - no school",
                "start": {"dateTime": "2026-09-07T00:00:00-07:00"},
                "end": {"dateTime": "2026-09-07T23:59:00-07:00"},
            },
            {
                "id": "spring-break",
                "summary": "Spring Break - no school",
                "start": {"dateTime": "2027-04-05T00:00:00-07:00"},
                "end": {"dateTime": "2027-04-05T23:59:00-07:00"},
            },
            {
                "id": "minimum-day",
                "summary": "Minimum Day - 1:30 PM dismissal (Grade 1-3)",
                "start": {"dateTime": "2026-08-12T13:30:00-07:00"},
                "end": {"dateTime": "2026-08-12T13:45:00-07:00"},
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 9, 0, 39, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.list_events_from_request(
                "When are Nysha's holidays?",
                reference_time=reference,
            )

        self.assertIn("Holiday - no school", message)
        self.assertIn("Spring Break - no school", message)
        self.assertNotIn("Minimum Day", message)

    def test_when_spring_break_filters_exact_break(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "fall-break",
                "summary": "Fall Break - no school",
                "start": {"dateTime": "2026-11-23T00:00:00-08:00"},
                "end": {"dateTime": "2026-11-23T23:59:00-08:00"},
            },
            {
                "id": "spring-break",
                "summary": "Spring Break - no school",
                "start": {"dateTime": "2027-04-05T00:00:00-07:00"},
                "end": {"dateTime": "2027-04-05T23:59:00-07:00"},
            },
        ]
        claw = FamilyCalendarClaw.from_provider(provider)
        reference = datetime(2026, 8, 9, 0, 39, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))

        with redirect_stdout(StringIO()):
            message = claw.list_events_from_request(
                "When is Nysha's spring break?",
                reference_time=reference,
            )

        self.assertIn("Spring Break - no school", message)
        self.assertNotIn("Fall Break", message)

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

    def test_list_events_for_owner_alias_without_date(self):
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
                        "owner": "mom",
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
                        "owner": "dad",
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

    def test_delete_event_searches_named_calendar(self):
        provider = FakeProvider()
        provider.events = [
            {
                "id": "swim-1",
                "summary": "Swim class",
                "calendarId": "nysha-school-id",
                "start": {"dateTime": "2026-07-03T17:00:00-07:00"},
                "end": {"dateTime": "2026-07-03T18:00:00-07:00"},
            }
        ]
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.delete_event_from_request("Delete swim class from Nysha school calendar")

        self.assertEqual(provider.list_calendar_names, ["Nysha school calendar"])
        self.assertTrue(provider.list_calls[0]["writable"])

    def test_preparation_searches_named_calendar(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.preparation_from_request(
                "What should we prepare for from Nysha school calendar next week?",
                reference_time=datetime(
                    2026,
                    8,
                    21,
                    12,
                    tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
                ),
            )

        self.assertEqual(provider.list_calendar_names, ["Nysha school calendar"])

    def test_pronoun_delete_with_named_calendar_does_not_reuse_other_calendar_context(self):
        provider = FakeProvider()
        provider.events = []
        claw = FamilyCalendarClaw.from_provider(provider)
        claw.last_created_event = {
            "id": "family-event",
            "summary": "Family dinner",
            "calendarId": "family-id",
            "start": {"dateTime": "2026-07-03T18:00:00-07:00"},
            "end": {"dateTime": "2026-07-03T19:00:00-07:00"},
        }

        with redirect_stdout(StringIO()):
            claw.delete_event_from_request("Delete it from Nysha school calendar")

        self.assertEqual(provider.list_calendar_names, ["Nysha school calendar"])
        self.assertIsNone(claw.pending_action)

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

    def test_pending_create_accepts_absolute_date_followup(self):
        now = datetime(2026, 8, 9, 10, 4, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()) as output:
            first_message = claw.create_event_from_request(
                "Add event back to school night in Nysha school calendar "
                "Time: 4:30-5:00 PM Location: Chadbourne MUR",
                reference_time=now,
            )
            handled = claw.handle_pending_response("August 11", reference_time=now)

        self.assertEqual(first_message, "Please provide: date.")
        self.assertTrue(handled)
        self.assertIsNone(claw.pending_action)
        self.assertEqual(provider.created[0]["summary"], "Back to school night")
        self.assertEqual(provider.created_calendar_names, ["Nysha school calendar"])
        self.assertEqual(provider.created[0]["start"]["dateTime"], "2026-08-11T16:30:00-07:00")
        self.assertIn("Created calendar event: Back to school night", output.getvalue())

    def test_create_event_reports_expired_google_auth_without_raising(self):
        provider = FailingProvider(
            Exception("invalid_grant: Token has been expired or revoked."),
        )
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()) as output:
            message = claw.create_event_from_request(
                "Nysha and Navya go to the library at 1 PM today",
                reference_time=self.now,
            )

        self.assertIn("Google Calendar needs to be reconnected", message)
        self.assertIn("python3 get_google_token.py", message)
        self.assertIn(message, output.getvalue())

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

    def test_briefing_searches_named_calendar(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)

        with redirect_stdout(StringIO()):
            claw.briefing_from_request(
                "Give me next week's briefing from Nysha school calendar",
                reference_time=datetime(
                    2026,
                    8,
                    21,
                    12,
                    tzinfo=ZoneInfo(DEFAULT_TIMEZONE),
                ),
            )

        self.assertEqual(provider.list_calendar_names, ["Nysha school calendar"])

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
