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

    def test_adult_read_aloud_is_recorded_with_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            message = claw.record_from_request(
                "Dad read Frog and Toad to Nysha.",
                reference_time=REFERENCE_TIME,
            )

            events = provider.list_events(child="Nysha")
            summary = claw.tools.status(now=REFERENCE_TIME)["data"]["summary"]

        self.assertEqual(message, "Saved family read-aloud for Nysha.")
        self.assertEqual(events[0]["book"], "Frog and Toad")
        self.assertEqual(events[0]["reading_mode"], "read_aloud")
        self.assertEqual(summary["by_child"]["Nysha"]["week"]["reading_moments"], 0)
        self.assertEqual(summary["by_child"]["Nysha"]["weekly_goal"]["reading_days"], 0)
        self.assertEqual(summary["by_child"]["Nysha"]["streaks"]["current"], 0)
        self.assertEqual(summary["by_child"]["Nysha"]["current_book"], "unknown book")
        self.assertEqual(summary["by_child"]["Nysha"]["recent_events"], [])
        self.assertEqual(summary["family"]["week"]["reading_moments"], 1)
        self.assertEqual(summary["family"]["current_book"], "Frog and Toad")

    def test_unclear_reading_asks_one_clarification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            message = claw.record_from_request(
                "We read Frog and Toad.",
                reference_time=REFERENCE_TIME,
            )

            events = provider.list_events(child="Nysha")
        self.assertEqual(message, "Was this reading for Nysha, Navya, or both?")
        self.assertEqual(events, [])

    def test_status_summarizes_today_and_week(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)
            claw.record_from_request("Nysha read 5 pages of Magic Tree House herself.", reference_time=REFERENCE_TIME)
            claw.record_from_request("Nysha read for 12 minutes independently.", reference_time=REFERENCE_TIME)

            status = claw.tools.status(now=REFERENCE_TIME)

        self.assertEqual(status["data"]["summary"]["today"]["label"], "I read today")
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

    def test_records_navya_yesterday_and_family_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            message = claw.record_from_request(
                "Navya finished Brown Bear yesterday.",
                reference_time=REFERENCE_TIME,
                source="telegram_voice",
            )

            events = provider.list_events(child="Navya")
            summary = claw.tools.status(now=REFERENCE_TIME)["data"]["summary"]

        self.assertIn("Navya", message)
        self.assertEqual(events[0]["date"], "2026-07-06")
        self.assertEqual(events[0]["source"], "telegram_voice")
        self.assertEqual(summary["by_child"]["Navya"]["finished"]["count"], 1)
        self.assertEqual(summary["family"]["week"]["reading_moments"], 1)

    def test_records_both_children_from_shared_reading(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            message = claw.record_from_request(
                "Both kids read Frog and Toad together on Monday.",
                reference_time=REFERENCE_TIME,
            )

            nysha_events = provider.list_events(child="Nysha")
            navya_events = provider.list_events(child="Navya")
            summary = claw.tools.status(now=REFERENCE_TIME)["data"]["summary"]

        self.assertIn("Saved 2 reading moments", message)
        self.assertEqual(nysha_events[0]["book"], "Frog and Toad")
        self.assertEqual(navya_events[0]["book"], "Frog and Toad")
        self.assertEqual(nysha_events[0]["reading_mode"], "read_together")
        self.assertEqual(summary["by_child"]["Nysha"]["week"]["reading_moments"], 0)
        self.assertEqual(summary["by_child"]["Navya"]["week"]["reading_moments"], 0)
        self.assertEqual(summary["family"]["week"]["reading_moments"], 2)

    def test_photo_caption_uses_image_text_title_and_photo_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            claw.record_from_request(
                "Nysha read this\nImage text:\nBook title: Mercy Watson",
                reference_time=REFERENCE_TIME,
                source="telegram_photo",
                photo_path="/static/dashboard/uploads/reading/cover.jpg",
            )

            summary = claw.tools.status(now=REFERENCE_TIME)["data"]["summary"]

        self.assertEqual(summary["current_book"], "Mercy Watson")
        self.assertEqual(
            summary["recent_photos"],
            [{"path": "/static/dashboard/uploads/reading/cover.jpg", "book": "Mercy Watson"}],
        )
        self.assertEqual(summary["weekly_goal"]["reading_days"], 1)
        self.assertTrue(summary["history"]["heatmap"])

    def test_sender_tagged_photo_source_records_child_reading(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            claw.record_from_request(
                "Nysha read the book partially today\nImage text:\nBook title: Hello Kitty Graduation Day",
                reference_time=REFERENCE_TIME,
                source="telegram_photo:niyati",
                photo_path="/static/dashboard/uploads/reading/hello-kitty.jpg",
            )

            events = provider.list_events(child="Nysha")

        self.assertEqual(events[0]["book"], "Hello Kitty Graduation Day")
        self.assertEqual(events[0]["source"], "telegram_photo")

    def test_parent_read_photo_source_records_read_aloud(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            claw.record_from_request(
                "Dad read the book to Nysha today\nImage text:\nBook title: Earl & Worm: The Big Mess",
                reference_time=REFERENCE_TIME,
                source="telegram_photo:dad",
                photo_path="/static/dashboard/uploads/reading/earl-worm.jpg",
            )

            events = provider.list_events(child="Nysha")

        self.assertEqual(events[0]["book"], "Earl & Worm: The Big Mess")
        self.assertEqual(events[0]["reading_mode"], "read_aloud")
        self.assertEqual(events[0]["source"], "telegram_photo")

    def test_library_prefixed_photo_reading_is_not_checkout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            message = claw.record_from_request(
                "Library Nysha read 2 series\nImage text:\nBook title: Peppa's Storybook Collection",
                reference_time=REFERENCE_TIME,
                source="telegram_photo",
                photo_path="/static/dashboard/uploads/reading/peppa.jpg",
            )

            events = provider.list_events(child="Nysha")
            summary = claw.tools.status(now=REFERENCE_TIME)["data"]["summary"]

        self.assertIn("reading moment", message)
        self.assertEqual(events[0]["book"], "Peppa's Storybook Collection")
        self.assertEqual(events[0]["photo_path"], "/static/dashboard/uploads/reading/peppa.jpg")
        self.assertEqual(summary["current_bag"]["count"], 0)
        self.assertEqual(summary["current_book"], "Peppa's Storybook Collection")

    def test_family_parent_read_library_photo_saves_bag_without_child_credit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            message = claw.record_from_request(
                "Add to library family reading read by Dad\n"
                "Image text:\n"
                "Book title: Earl & Worm: The Big Mess and Other Stories\n"
                "Author: Greg Pizzoli",
                reference_time=REFERENCE_TIME,
                source="telegram_photo:dad",
                photo_path="/static/dashboard/uploads/reading/earl-worm.jpg",
            )

            events = provider.list_events()
            latest_visit = provider.latest_visit()
            summary = claw.tools.status(now=REFERENCE_TIME)["data"]["summary"]

        self.assertEqual(message, "Saved this library bag with 1 book at home.")
        self.assertEqual(events, [])
        self.assertEqual(latest_visit["source"], "telegram_photo")
        self.assertEqual(summary["current_bag"]["titles"], ["Earl & Worm: The Big Mess and Other Stories"])
        self.assertEqual(summary["weekly_goal"]["reading_days"], 0)
        self.assertEqual(summary["streaks"]["current"], 0)
        self.assertFalse(summary["badges"][0]["earned"])

    def test_photo_ocr_failure_text_is_not_used_as_book_title(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)

            claw.record_from_request(
                "Nysha read this today\nImage text:\nNo visible checklist entries",
                reference_time=REFERENCE_TIME,
                source="telegram_photo",
                photo_path="/static/dashboard/uploads/reading/cover.jpg",
            )

            summary = claw.tools.status(now=REFERENCE_TIME)["data"]["summary"]

        self.assertEqual(summary["current_book"], "unknown book")
        self.assertEqual(
            summary["recent_photos"],
            [{"path": "/static/dashboard/uploads/reading/cover.jpg", "book": "Book snap"}],
        )
        self.assertEqual(summary["week"]["reading_days"], 1)

    def test_updates_latest_reading_from_chat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)
            claw.record_from_request("Nysha read Mercy Watson today.", reference_time=REFERENCE_TIME)

            message = claw.record_from_request(
                "Change Nysha latest reading book to Frog and Toad",
                reference_time=REFERENCE_TIME,
            )

            events = provider.list_events(child="Nysha")

        self.assertIn("Updated reading moment", message)
        self.assertEqual(events[0]["book"], "Frog and Toad")

    def test_deletes_latest_reading_from_chat(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteLibraryProvider(Path(temp_dir) / "library.db")
            claw = LibraryClaw.from_provider(provider)
            claw.record_from_request("Nysha read Mercy Watson today.", reference_time=REFERENCE_TIME)

            message = claw.record_from_request(
                "Delete Nysha latest reading entry",
                reference_time=REFERENCE_TIME,
            )

            events = provider.list_events(child="Nysha")

        self.assertIn("Deleted reading moment", message)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
