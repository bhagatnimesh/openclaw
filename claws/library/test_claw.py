import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path

from claw import LibraryClaw
from provider import SQLiteLibraryProvider


REFERENCE_TIME = datetime.fromisoformat("2026-07-07T18:00:00-07:00")


class LibraryClawTest(unittest.TestCase):
    def test_records_independent_pages_as_leaf(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            output = StringIO()
            with redirect_stdout(output):
                message = claw.record_from_request(
                    "Nysha read 8 pages of Mercy Watson by herself.",
                    reference_time=REFERENCE_TIME,
                )

            events = provider.list_events(child="Nysha")
        self.assertIn("grew a new leaf", message)
        self.assertIn("grew a new leaf", output.getvalue())
        self.assertEqual(events[0]["book"], "Mercy Watson")
        self.assertEqual(events[0]["pages"], 8)
        self.assertEqual(events[0]["date"], "2026-07-07")

    def test_finished_book_counts_as_flower(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            message = claw.record_from_request(
                "Nysha finished Elephant and Piggie herself.",
                reference_time=REFERENCE_TIME,
            )

            summary = claw.tools.status(now=REFERENCE_TIME)["data"]["summary"]
        self.assertIn("flower bloomed", message)
        self.assertEqual(summary["finished"]["count"], 1)
        self.assertEqual(summary["finished"]["recent_books"], ["Elephant and Piggie"])

    def test_adult_read_aloud_is_not_counted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            message = claw.record_from_request(
                "Dad read Frog and Toad to Nysha.",
                reference_time=REFERENCE_TIME,
            )

            events = provider.list_events(child="Nysha")
        self.assertIn("Not counted", message)
        self.assertEqual(events, [])

    def test_unclear_reading_asks_one_clarification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            message = claw.record_from_request(
                "We read Frog and Toad.",
                reference_time=REFERENCE_TIME,
            )

            events = provider.list_events(child="Nysha")
        self.assertEqual(message, "Did Nysha read this herself?")
        self.assertEqual(events, [])

    def test_status_summarizes_today_and_week(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)
            claw.record_from_request("Nysha read 5 pages of Magic Tree House herself.", reference_time=REFERENCE_TIME)
            claw.record_from_request("Nysha read for 12 minutes independently.", reference_time=REFERENCE_TIME)

            status = claw.tools.status(now=REFERENCE_TIME)

        self.assertEqual(status["data"]["summary"]["today"]["label"], "I read myself today")
        self.assertEqual(status["data"]["summary"]["current_book"], "Magic Tree House")
        self.assertEqual(status["data"]["summary"]["week"]["reading_moments"], 2)
        self.assertEqual(status["data"]["summary"]["week"]["pages"], 5)
        self.assertEqual(status["data"]["summary"]["week"]["minutes"], 12)

    def test_checkout_email_saves_lightweight_library_bag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)
            request = "\n".join(
                [
                    "Library checkout receipt",
                    "Due date: July 28, 2026",
                    "Items checked out",
                    "- Mercy Watson to the Rescue",
                    "Frog and Toad Together",
                    "- Narwhal Unicorn of the Sea",
                ],
            )

            message = claw.checkout_from_request(request, reference_time=REFERENCE_TIME)
            summary = claw.tools.status(now=REFERENCE_TIME)["data"]["summary"]

        self.assertEqual(message, "Saved this library bag with 3 books at home.")
        self.assertEqual(summary["current_bag"]["count"], 3)
        self.assertEqual(
            summary["current_bag"]["titles"],
            [
                "Mercy Watson to the Rescue",
                "Frog and Toad Together",
                "Narwhal Unicorn of the Sea",
            ],
        )
        self.assertEqual(summary["current_bag"]["due_date"], "2026-07-28")
        self.assertEqual(summary["library_visit"]["label"], "Enjoy this library bag.")

    def test_library_visit_cadence_uses_gentle_two_to_three_week_messages(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)
            claw.checkout_from_request(
                "\n".join(["Library checkout", "- Poppleton", "- Mercy Watson"]),
                reference_time=REFERENCE_TIME,
            )

            day_15 = claw.tools.status(now=datetime.fromisoformat("2026-07-22T09:00:00-07:00"))["data"]["summary"]
            day_22 = claw.tools.status(now=datetime.fromisoformat("2026-07-29T09:00:00-07:00"))["data"]["summary"]

        self.assertEqual(day_15["library_visit"]["days_since_visit"], 15)
        self.assertEqual(day_15["library_visit"]["label"], "Good week for a library visit.")
        self.assertEqual(day_22["library_visit"]["days_since_visit"], 22)
        self.assertEqual(day_22["library_visit"]["label"], "Ready for the next library adventure.")

    def test_finished_reading_does_not_match_or_update_library_bag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)
            claw.checkout_from_request(
                "\n".join(["Library checkout", "- Elephant and Piggie"]),
                reference_time=REFERENCE_TIME,
            )
            claw.record_from_request(
                "Nysha finished Elephant and Piggie herself.",
                reference_time=REFERENCE_TIME,
            )

            summary = claw.tools.status(now=REFERENCE_TIME)["data"]["summary"]

        self.assertEqual(summary["finished"]["count"], 1)
        self.assertEqual(summary["current_bag"]["count"], 1)
        self.assertEqual(summary["current_bag"]["titles"], ["Elephant and Piggie"])


if __name__ == "__main__":
    unittest.main()
