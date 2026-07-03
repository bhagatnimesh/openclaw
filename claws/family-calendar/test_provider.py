import os
import unittest
from datetime import datetime, timedelta


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
        calendar.delete_event(created["id"])


if __name__ == "__main__":
    unittest.main()
