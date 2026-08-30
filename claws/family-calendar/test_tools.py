import unittest

from google.auth.exceptions import TimeoutError as GoogleAuthTimeoutError
from google.auth.exceptions import TransportError

from tools import DEFAULT_TIMEZONE, CalendarTools


class FakeProvider:
    def __init__(self):
        self.created = []
        self.deleted = []
        self.updated = []
        self.events = [{"id": "event-1", "summary": "Dinner"}]

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
        start = {"date": start_time} if all_day else {"dateTime": start_time, "timeZone": timezone}
        end = {"date": end_time} if all_day else {"dateTime": end_time, "timeZone": timezone}
        event = {
            "id": "created-1",
            "summary": title,
            "start": start,
            "end": end,
            "description": description,
            "location": location,
            "recurrence": recurrence,
            "attendees": attendees,
            "calendarName": calendar_name,
            "notify_attendees": notify_attendees,
            "all_day": all_day,
            "event_label_background_color": event_label_background_color,
        }
        if private_extended_properties:
            event["extendedProperties"] = {
                "private": private_extended_properties,
            }
        self.created.append(event)
        return event

    def list_events(self, time_min, time_max, max_results=10):
        return self.events[:max_results]

    def get_event(self, event_id, calendar_id=None):
        return next(event for event in self.events if event["id"] == event_id)

    def delete_event(self, event_id, calendar_id=None):
        self.deleted.append(event_id)

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
        self.updated.append(
            {
                "event_id": event_id,
                "attendees": attendees,
                "calendar_id": calendar_id,
                "notify_attendees": notify_attendees,
            }
        )
        return {
            "id": event_id,
            "summary": title,
            "attendees": attendees,
            "calendarId": calendar_id,
            "notify_attendees": notify_attendees,
        }


class FailingProvider(FakeProvider):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def create_event(self, *args, **kwargs):
        raise self.error

    def list_events(self, *args, **kwargs):
        raise self.error

    def get_event(self, *args, **kwargs):
        raise self.error

    def delete_event(self, *args, **kwargs):
        raise self.error

    def update_event(self, *args, **kwargs):
        raise self.error


class CalendarToolsTest(unittest.TestCase):
    def test_create_calendar_event_requires_core_fields(self):
        tools = CalendarTools(FakeProvider())

        response = tools.create_calendar_event(title="Dentist")

        self.assertEqual(response["status"], "needs_information")
        self.assertEqual(response["data"]["missing_fields"], ["start_time", "end_time"])

    def test_create_calendar_event_uses_default_timezone(self):
        provider = FakeProvider()
        tools = CalendarTools(provider)

        response = tools.create_calendar_event(
            title="Dentist",
            start_time="2026-07-02T09:00:00",
            end_time="2026-07-02T09:30:00",
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(provider.created[0]["start"]["timeZone"], DEFAULT_TIMEZONE)

    def test_create_calendar_event_passes_attendees(self):
        provider = FakeProvider()

        response = CalendarTools(provider).create_calendar_event(
            title="Dentist",
            start_time="2026-08-29T12:20:00-07:00",
            end_time="2026-08-29T13:20:00-07:00",
            attendees=[{"email": "dad@example.test", "displayName": "Dad"}],
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(
            provider.created[0]["attendees"],
            [{"email": "dad@example.test", "displayName": "Dad"}],
        )

    def test_create_calendar_event_passes_target_calendar_name(self):
        provider = FakeProvider()

        response = CalendarTools(provider).create_calendar_event(
            title="Art class",
            start_time="2026-08-15T10:00:00-07:00",
            end_time="2026-08-15T11:00:00-07:00",
            calendar_name="Nysha school calendar",
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(provider.created[0]["calendarName"], "Nysha school calendar")

    def test_create_calendar_event_passes_event_label_color(self):
        provider = FakeProvider()

        response = CalendarTools(provider).create_calendar_event(
            title="Homework due",
            start_time="2026-08-28T07:00:00-07:00",
            end_time="2026-08-28T07:30:00-07:00",
            event_label_background_color="#d81b60",
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(provider.created[0]["event_label_background_color"], "#d81b60")

    def test_list_calendar_events_returns_provider_events(self):
        tools = CalendarTools(FakeProvider())

        response = tools.list_calendar_events(
            time_min="2026-07-02T00:00:00Z",
            time_max="2026-07-03T00:00:00Z",
        )

        self.assertEqual(response["status"], "ok")
        self.assertEqual(
            response["data"]["events"],
            [{"id": "event-1", "summary": "Dinner"}],
        )

    def test_get_calendar_event_returns_exact_provider_event(self):
        response = CalendarTools(FakeProvider()).get_calendar_event("event-1")

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["event"]["id"], "event-1")

    def test_update_calendar_event_does_not_notify_attendees_by_default(self):
        provider = FakeProvider()

        response = CalendarTools(provider).update_calendar_event(
            event_id="event-1",
            title="Dinner",
            start_time="2026-07-02T19:00:00-07:00",
            end_time="2026-07-02T20:00:00-07:00",
            attendees=[{"email": "friend@example.com"}],
        )

        self.assertEqual(response["status"], "ok")
        self.assertFalse(provider.updated[0]["notify_attendees"])

    def test_delete_calendar_event_requires_event_id(self):
        tools = CalendarTools(FakeProvider())

        response = tools.delete_calendar_event()

        self.assertEqual(response["status"], "needs_information")
        self.assertEqual(response["data"]["missing_fields"], ["event_id"])

    def test_create_calendar_event_reports_expired_google_auth(self):
        tools = CalendarTools(
            FailingProvider(
                Exception("invalid_grant: Token has been expired or revoked."),
            ),
        )

        response = tools.create_calendar_event(
            title="Library",
            start_time="2026-08-08T13:00:00-07:00",
            end_time="2026-08-08T14:00:00-07:00",
        )

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["data"]["error"], "google_calendar_auth_expired")
        self.assertIn("Google Calendar needs to be reconnected", response["message"])
        self.assertIn("python3 get_google_token.py", response["message"])

    def test_list_calendar_events_reports_provider_failure(self):
        tools = CalendarTools(FailingProvider(RuntimeError("backend timeout")))

        response = tools.list_calendar_events(
            time_min="2026-08-08T00:00:00-07:00",
            time_max="2026-08-09T00:00:00-07:00",
        )

        self.assertEqual(response["status"], "error")
        self.assertEqual(response["data"]["error"], "google_calendar_request_failed")

    def test_google_network_failures_report_operation_outcome(self):
        cases = [
            (
                "create transport failure",
                TransportError("network unavailable"),
                lambda tools: tools.create_calendar_event(
                    title="Dentist appointment",
                    start_time="2026-12-04T15:30:00-08:00",
                    end_time="2026-12-04T16:30:00-08:00",
                ),
                "the event was not added",
            ),
            (
                "read timeout",
                GoogleAuthTimeoutError("request timed out"),
                lambda tools: tools.list_calendar_events(
                    time_min="2026-12-04T00:00:00-08:00",
                    time_max="2026-12-05T00:00:00-08:00",
                ),
                "I couldn't check your calendar",
            ),
            (
                "update transport failure",
                TransportError("network unavailable"),
                lambda tools: tools.update_calendar_event(
                    event_id="event-1",
                    title="Dentist appointment",
                    start_time="2026-12-04T15:30:00-08:00",
                    end_time="2026-12-04T16:30:00-08:00",
                ),
                "the event was not changed",
            ),
            (
                "delete timeout",
                GoogleAuthTimeoutError("request timed out"),
                lambda tools: tools.delete_calendar_event("event-1"),
                "the event was not deleted",
            ),
        ]

        for name, error, operation, outcome in cases:
            with self.subTest(name):
                response = operation(CalendarTools(FailingProvider(error)))

                self.assertEqual(response["status"], "error")
                self.assertEqual(response["data"]["error"], "google_calendar_unreachable")
                self.assertEqual(
                    response["message"],
                    f"I couldn't reach Google Calendar, so {outcome}. "
                    "Please try again in a minute. If it still fails, "
                    "check the N4OS Mac's internet connection.",
                )


if __name__ == "__main__":
    unittest.main()
