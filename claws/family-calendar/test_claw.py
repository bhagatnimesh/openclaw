import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

from claw import FamilyCalendarClaw, run_cli
from tools import DEFAULT_TIMEZONE


class FakeProvider:
    def __init__(self):
        self.created = []

    def create_event(
        self,
        title,
        start_time,
        end_time,
        timezone=DEFAULT_TIMEZONE,
        description=None,
        location=None,
    ):
        event = {
            "id": "event-123",
            "summary": title,
            "start": {"dateTime": start_time, "timeZone": timezone},
            "end": {"dateTime": end_time, "timeZone": timezone},
            "description": description,
            "location": location,
        }
        self.created.append(event)
        return event

    def list_events(self, time_min, time_max, max_results=10):
        return []

    def delete_event(self, event_id):
        return None


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

    def test_create_event_from_request_asks_for_missing_date(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        output = StringIO()

        with redirect_stdout(output):
            message = claw.create_event_from_request("Add dinner at 7 with Rahul")

        self.assertEqual(message, "Please provide: date.")
        self.assertEqual(provider.created, [])

    def test_create_event_from_request_asks_for_ambiguous_time(self):
        provider = FakeProvider()
        claw = FamilyCalendarClaw.from_provider(provider)
        output = StringIO()

        with redirect_stdout(output):
            message = claw.create_event_from_request("Add appointment Friday at 7")

        self.assertEqual(message, "Please provide: AM or PM.")
        self.assertEqual(provider.created, [])

    def test_run_cli_processes_request_until_exit(self):
        class FakeClaw:
            def __init__(self):
                self.requests = []

            def create_event_from_request(self, request):
                self.requests.append(request)
                print("Created calendar event: Dinner.")

        fake_claw = FakeClaw()
        inputs = iter(["Add dinner Friday at 7 with Rahul", "exit"])
        output = StringIO()

        def fake_input(prompt):
            print(prompt, end="")
            return next(inputs)

        with redirect_stdout(output), patch("builtins.input", fake_input):
            run_cli(fake_claw)

        self.assertEqual(fake_claw.requests, ["Add dinner Friday at 7 with Rahul"])
        self.assertIn("Family Calendar Claw", output.getvalue())
        self.assertIn("> Created calendar event: Dinner.", output.getvalue())


if __name__ == "__main__":
    unittest.main()
