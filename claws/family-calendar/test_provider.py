import os
import unittest
from datetime import datetime, timedelta


class FakeExecute:
    def __init__(self, response=None):
        self.response = response or {}

    def execute(self):
        return self.response


class FakeCalendarList:
    def __init__(self, service):
        self.service = service

    def list(self, **kwargs):
        self.service.calendar_list_calls.append(kwargs)
        return FakeExecute(
            {
                "items": [
                    {"id": "primary", "summary": "Family"},
                    {"id": "nysha-school-short-id", "summary": "Nysha School"},
                    {"id": "nysha-school-id", "summary": "Nysha School Calendar"},
                ],
            },
        )


class FakeEvents:
    def __init__(self, service):
        self.service = service

    def insert(self, **kwargs):
        self.service.insert_calls.append(kwargs)
        return FakeExecute({"id": "event-1", "summary": kwargs["body"]["summary"]})

    def update(self, **kwargs):
        self.service.update_calls.append(kwargs)
        return FakeExecute({"id": kwargs["eventId"], "summary": kwargs["body"]["summary"]})

    def list(self, **kwargs):
        self.service.list_calls.append(kwargs)
        return FakeExecute({"items": []})


class FakeCalendarService:
    def __init__(self):
        self.calendar_list_calls = []
        self.insert_calls = []
        self.update_calls = []
        self.list_calls = []

    def calendarList(self):
        return FakeCalendarList(self)

    def events(self):
        return FakeEvents(self)


class GoogleCalendarProviderUnitTest(unittest.TestCase):
    def test_list_events_resolves_named_calendar(self):
        from provider import GoogleCalendarProvider

        calendar = GoogleCalendarProvider.__new__(GoogleCalendarProvider)
        calendar.calendar_id = "primary"
        calendar.service = FakeCalendarService()

        calendar.list_events(
            time_min="2026-08-21T00:00:00-07:00",
            time_max="2026-08-28T00:00:00-07:00",
            calendar_name="Nysha School Calendar",
        )

        self.assertEqual(calendar.service.list_calls[0]["calendarId"], "nysha-school-id")
        self.assertEqual(
            calendar.service.calendar_list_calls[0],
            {"maxResults": 250, "minAccessRole": "reader"},
        )

    def test_list_events_can_require_writable_named_calendar(self):
        from provider import GoogleCalendarProvider

        calendar = GoogleCalendarProvider.__new__(GoogleCalendarProvider)
        calendar.calendar_id = "primary"
        calendar.service = FakeCalendarService()

        calendar.list_events(
            time_min="2026-08-21T00:00:00-07:00",
            time_max="2026-08-28T00:00:00-07:00",
            calendar_name="Nysha School Calendar",
            writable=True,
        )

        self.assertEqual(
            calendar.service.calendar_list_calls[0],
            {"maxResults": 250, "minAccessRole": "writer"},
        )

    def test_list_events_forwards_free_text_query(self):
        from provider import GoogleCalendarProvider

        calendar = GoogleCalendarProvider.__new__(GoogleCalendarProvider)
        calendar.calendar_id = "primary"
        calendar.service = FakeCalendarService()

        calendar.list_events(
            time_min="2026-08-21T00:00:00-07:00",
            time_max="2026-08-28T00:00:00-07:00",
            query="dentist appointment",
        )

        self.assertEqual(calendar.service.list_calls[0]["q"], "dentist appointment")

    def test_create_event_resolves_named_calendar(self):
        from provider import GoogleCalendarProvider

        calendar = GoogleCalendarProvider.__new__(GoogleCalendarProvider)
        calendar.calendar_id = "primary"
        calendar.service = FakeCalendarService()

        created = calendar.create_event(
            title="Art class",
            start_time="2026-08-15T10:00:00-07:00",
            end_time="2026-08-15T11:00:00-07:00",
            calendar_name="Nysha school calendar",
        )

        self.assertEqual(calendar.service.insert_calls[0]["calendarId"], "nysha-school-id")
        self.assertEqual(created["calendarId"], "nysha-school-id")
        self.assertEqual(
            calendar.service.calendar_list_calls[0],
            {"maxResults": 250, "minAccessRole": "writer"},
        )

    def test_create_event_only_notifies_attendees_when_requested(self):
        from provider import GoogleCalendarProvider

        calendar = GoogleCalendarProvider.__new__(GoogleCalendarProvider)
        calendar.calendar_id = "primary"
        calendar.service = FakeCalendarService()

        calendar.create_event(
            title="Restored event",
            start_time="2026-08-15T10:00:00-07:00",
            end_time="2026-08-15T11:00:00-07:00",
            attendees=[{"email": "friend@example.test"}],
        )
        calendar.create_event(
            title="New invite",
            start_time="2026-08-15T12:00:00-07:00",
            end_time="2026-08-15T13:00:00-07:00",
            attendees=[{"email": "friend@example.test"}],
            notify_attendees=True,
        )

        self.assertNotIn("sendUpdates", calendar.service.insert_calls[0])
        self.assertEqual(calendar.service.insert_calls[1]["sendUpdates"], "all")

    def test_update_event_only_notifies_attendees_when_requested(self):
        from provider import GoogleCalendarProvider

        calendar = GoogleCalendarProvider.__new__(GoogleCalendarProvider)
        calendar.calendar_id = "primary"
        calendar.service = FakeCalendarService()

        calendar.update_event(
            event_id="event-1",
            title="Metadata edit",
            start_time="2026-08-15T10:00:00-07:00",
            end_time="2026-08-15T11:00:00-07:00",
            attendees=[{"email": "friend@example.test"}],
        )
        calendar.update_event(
            event_id="event-1",
            title="Guest edit",
            start_time="2026-08-15T10:00:00-07:00",
            end_time="2026-08-15T11:00:00-07:00",
            attendees=[{"email": "friend@example.test"}],
            notify_attendees=True,
        )

        self.assertNotIn("sendUpdates", calendar.service.update_calls[0])
        self.assertEqual(calendar.service.update_calls[1]["sendUpdates"], "all")


@unittest.skipUnless(
    os.environ.get("OPENCLAW_LIVE_TEST") == "1",
    "set OPENCLAW_LIVE_TEST=1 to run the live Google Calendar smoke test",
)
class GoogleCalendarProviderLiveTest(unittest.TestCase):
    def test_create_list_delete_event(self):
        from provider import GoogleCalendarProvider

        calendar = GoogleCalendarProvider(calendar_id="primary")
        start = datetime.now() + timedelta(hours=1)
        end = start + timedelta(minutes=30)

        created = calendar.create_event(
            title="N4OS Provider Test",
            start_time=start.isoformat(),
            end_time=end.isoformat(),
        )

        events = calendar.list_events(
            time_min=datetime.now().isoformat() + "Z",
            time_max=(datetime.now() + timedelta(days=7)).isoformat() + "Z",
        )

        self.assertIn(created["id"], {event.get("id") for event in events})
        self.assertEqual(calendar.get_event(created["id"])["id"], created["id"])
        calendar.delete_event(created["id"])


if __name__ == "__main__":
    unittest.main()
