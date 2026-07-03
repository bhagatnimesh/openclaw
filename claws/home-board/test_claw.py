import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from io import StringIO
from pathlib import Path
from zoneinfo import ZoneInfo

from claw import HomeBoardClaw
from provider import SQLiteHomeBoardProvider


REFERENCE_TIME = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class HomeBoardClawTest(unittest.TestCase):
    def test_add_item_from_request_persists_notice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claw = HomeBoardClaw.from_provider(
                SQLiteHomeBoardProvider(Path(tmpdir) / "n4os.db"),
            )

            with redirect_stdout(StringIO()):
                message = claw.add_item_from_request(
                    "Nysha, take your journal today",
                    reference_time=REFERENCE_TIME,
                )
            listed = claw.tools.list_items(date="2026-07-03", now=REFERENCE_TIME)

        self.assertIn("Added to Today at Home", message)
        self.assertEqual(listed["data"]["items"][0]["person_or_group"], "Nysha")
        self.assertEqual(listed["data"]["items"][0]["message"], "Take your journal")

    def test_add_items_from_request_persists_bulk_notice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            claw = HomeBoardClaw.from_provider(
                SQLiteHomeBoardProvider(Path(tmpdir) / "n4os.db"),
            )

            with redirect_stdout(StringIO()):
                message = claw.add_item_from_request(
                    "Today at home: Nysha take journal, Helper put food in fridge, Dad take passport",
                    reference_time=REFERENCE_TIME,
                )
            listed = claw.tools.list_items(date="2026-07-03", now=REFERENCE_TIME)

        self.assertIn("Added 3 items", message)
        self.assertEqual(
            sorted(item["person_or_group"] for item in listed["data"]["items"]),
            ["Dad", "Helper", "Nysha"],
        )


if __name__ == "__main__":
    unittest.main()
