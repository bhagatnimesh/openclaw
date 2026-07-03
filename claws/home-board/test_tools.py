import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from provider import SQLiteHomeBoardProvider
from tools import HomeBoardTools


NOW = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo("America/Los_Angeles"))


class HomeBoardToolsTest(unittest.TestCase):
    def _tools(self, tmpdir):
        db_path = Path(tmpdir) / "n4os.db"
        return HomeBoardTools(SQLiteHomeBoardProvider(db_path))

    def test_add_and_list_pending_item(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = self._tools(tmpdir)

            response = tools.add_item(
                person_or_group="Nysha",
                message="Take journal",
                date="2026-07-03",
                context="school",
                priority="high",
                expires_at="2026-07-04T00:00:00-07:00",
            )
            listed = tools.list_items(date="2026-07-03", now=NOW)

        self.assertEqual(response["status"], "ok")
        self.assertEqual(listed["status"], "ok")
        item = listed["data"]["items"][0]
        self.assertEqual(item["person_or_group"], "Nysha")
        self.assertEqual(item["message"], "Take journal")
        self.assertEqual(item["context"], "school")
        self.assertEqual(item["priority"], "high")

    def test_mark_done_removes_item_from_pending_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = self._tools(tmpdir)
            created = tools.add_item(
                person_or_group="Helper",
                message="Put food in fridge",
                date="2026-07-03",
                context="kitchen",
                expires_at="2026-07-04T00:00:00-07:00",
            )
            item_id = created["data"]["item"]["id"]

            done = tools.mark_done(item_id)
            pending = tools.list_items(date="2026-07-03", now=NOW)

        self.assertEqual(done["status"], "ok")
        self.assertEqual(done["data"]["item"]["status"], "done")
        self.assertEqual(pending["data"]["items"], [])

    def test_expired_items_are_hidden_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tools = self._tools(tmpdir)
            tools.add_item(
                person_or_group="Family",
                message="Carry old form",
                date="2026-07-02",
                context="general",
                expires_at="2026-07-03T00:00:00-07:00",
            )

            active = tools.list_items(date="2026-07-02", now=NOW)
            with_expired = tools.list_items(
                date="2026-07-02",
                include_expired=True,
                now=NOW,
            )

        self.assertEqual(active["data"]["items"], [])
        self.assertEqual(len(with_expired["data"]["items"]), 1)


if __name__ == "__main__":
    unittest.main()
