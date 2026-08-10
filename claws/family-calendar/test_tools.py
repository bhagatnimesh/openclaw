import unittest

from tools import DEFAULT_TIMEZONE, CalendarTools


class FakeProvider:
    def __init__(self):
        self.created = []
        self.deleted = []
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
        private_extended_properties=None,
    ):
        event = {
            "id": "created-1",
            "summary": title,
            "start": {"dateTime": start_time, "timeZone": timezone},
            "end": {"dateTime": end_time, "timeZone": timezone},
            "description": description,
            "location": location,
            "recurrence": recurrence,
        }
        if private_extended_properties:
            event["extendedProperties"] = {
                "private": private_extended_properties,
            }
        self.created.append(event)
        return event

    def list_events(self, time_min, time_max, max_results=10):
        return self.events[:max_results]

    def delete_event(self, event_id):
        self.deleted.append(event_id)


class FailingProvider(FakeProvider):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def create_event(self, *args, **kwargs):
        raise self.error

    def list_events(self, *args, **kwargs):
        raise self.error

    def delete_event(self, *args, **kwargs):
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


if __name__ == "__main__":
    unittest.main()
