import tempfile
import unittest
from pathlib import Path
import sqlite3

from provider import SQLiteFamilyDecisionProvider
from tools import FamilyDecisionTools, build_decision_brief


class FamilyDecisionToolsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.provider = SQLiteFamilyDecisionProvider(Path(self.tmp.name) / "decisions.sqlite")
        self.tools = FamilyDecisionTools(self.provider)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_decision_reports_ai_action_gaps(self):
        response = self.tools.create_decision("Summer camp plan", urgency="high")

        self.assertEqual(response["status"], "ok")
        decision = response["data"]["decision"]
        self.assertEqual(decision["title"], "Summer camp plan")
        self.assertIn("owner", response["data"]["gaps"])
        self.assertIn("next_step", response["data"]["gaps"])

    def test_adds_option_evidence_next_step_and_brief(self):
        decision = self.tools.create_decision(
            "Birthday party",
            owner="both",
            due="2026-07-08",
        )["data"]["decision"]

        self.tools.add_option(decision["id"], "Go to the party")
        self.tools.add_evidence(decision["id"], "It overlaps nap time")
        updated = self.tools.add_next_step(
            decision["id"],
            "Ask host about end time",
            owner="mom",
            due="2026-07-05",
        )["data"]["decision"]

        brief = build_decision_brief(updated)
        self.assertIn("Go to the party", brief)
        self.assertIn("It overlaps nap time", brief)
        self.assertIn("Ask host about end time", brief)

    def test_brief_without_id_uses_latest_open_decision(self):
        self.tools.create_decision("Older decision")
        latest = self.tools.create_decision("Summer camp plan")["data"]["decision"]

        response = self.tools.decision_brief(None)

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["data"]["decision"]["id"], latest["id"])
        self.assertIn("Summer camp plan", response["message"])

    def test_decide_closes_decision_with_rationale(self):
        decision = self.tools.create_decision("School choice")["data"]["decision"]

        response = self.tools.decide(
            decision["id"],
            "Choose Mission Valley Montessori",
            rationale="Best fit for friendship and commute.",
        )

        self.assertEqual(response["status"], "ok")
        decided = response["data"]["decision"]
        self.assertEqual(decided["status"], "decided")
        self.assertEqual(decided["outcome"], "Choose Mission Valley Montessori")
        self.assertEqual(decided["rationale"], "Best fit for friendship and commute.")

    def test_restore_decision_reverts_related_details(self):
        decision = self.tools.create_decision("School choice")["data"]["decision"]
        before = self.tools.read_decision(decision["id"])["data"]["decision"]
        self.tools.add_option(decision["id"], "Choose Mission Valley Montessori")

        response = self.tools.restore_decision(before)
        restored = response["data"]["decision"]

        self.assertEqual(response["status"], "ok")
        self.assertEqual(restored["options"], [])
        self.assertEqual(restored["status"], "inbox")

    def test_backlog_move_requires_confirmation_and_preserves_history(self):
        created = self.tools.create_backlog_item(
            "Camping trip",
            kind="discussion",
            actor="Niyati",
        )["data"]["item"]
        self.tools.add_backlog_note(created["id"], "September works", actor="Niyati")

        pending = self.tools.move_backlog_item(created["id"], "planning", actor="Niyati")
        unchanged = self.tools.read_backlog_item(created["id"])["data"]["item"]
        moved = self.tools.move_backlog_item(
            created["id"],
            "planning",
            confirmed=True,
            actor="Niyati",
        )["data"]["item"]

        self.assertEqual(pending["status"], "needs_confirmation")
        self.assertEqual(unchanged["kind"], "discussion")
        self.assertEqual(moved["id"], created["id"])
        self.assertEqual(moved["kind"], "planning")
        self.assertEqual(moved["notes"][0]["actor"], "Niyati")
        self.assertIn("moved", [activity["kind"] for activity in moved["activity"]])

    def test_backlog_links_are_explicit_and_unique(self):
        first = self.tools.create_backlog_item("Camping", kind="planning")["data"]["item"]
        second = self.tools.create_backlog_item("Packing", kind="planning")["data"]["item"]

        linked = self.tools.link_backlog_item(
            first["id"],
            source_type="google_task",
            external_id="task-1",
            container_id="@default",
        )
        duplicate = self.tools.link_backlog_item(
            second["id"],
            source_type="google_task",
            external_id="task-1",
            container_id="@default",
        )

        self.assertEqual(linked["status"], "ok")
        self.assertEqual(duplicate["status"], "error")

    def test_backlog_close_requires_confirmation(self):
        item = self.tools.create_backlog_item("Birthday", kind="discussion")["data"]["item"]

        pending = self.tools.close_backlog_item(item["id"], "Not attending")
        before = self.tools.read_backlog_item(item["id"])["data"]["item"]
        closed = self.tools.close_backlog_item(
            item["id"],
            "Not attending",
            confirmed=True,
        )["data"]["item"]

        self.assertEqual(pending["status"], "needs_confirmation")
        self.assertEqual(before["status"], "open")
        self.assertEqual(closed["status"], "closed")

    def test_legacy_decision_migration_is_idempotent(self):
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "legacy.sqlite"
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                """
                CREATE TABLE family_decisions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, context TEXT,
                    status TEXT NOT NULL, owner TEXT NOT NULL, urgency TEXT NOT NULL,
                    size TEXT NOT NULL, due TEXT, outcome TEXT, rationale TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, decided_at TEXT
                )
                """,
            )
            connection.execute(
                """
                INSERT INTO family_decisions VALUES (
                    'decision-1', 'Choose school', 'Next year', 'inbox', 'both',
                    'high', 'large', '2027-02-01', NULL, NULL,
                    '2026-07-01T10:00:00+00:00', '2026-07-02T10:00:00+00:00', NULL
                )
                """,
            )

        first_provider = SQLiteFamilyDecisionProvider(db_path)
        second_provider = SQLiteFamilyDecisionProvider(db_path)
        migrated = second_provider.get_item("decision-1")
        with sqlite3.connect(db_path) as connection:
            legacy_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='family_decisions'",
            ).fetchone()
            migration_count = connection.execute(
                "SELECT COUNT(*) FROM n4os_schema_migrations WHERE name = ?",
                ("family_decisions_to_backlog_v1",),
            ).fetchone()[0]

        self.assertIsNotNone(first_provider.get_item("decision-1"))
        self.assertEqual(migrated["id"], "decision-1")
        self.assertEqual(migrated["kind"], "decision")
        self.assertIsNone(legacy_table)
        self.assertEqual(migration_count, 1)


if __name__ == "__main__":
    unittest.main()
