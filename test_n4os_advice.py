from __future__ import annotations

from pathlib import Path
import tempfile
import json
import sqlite3
from n4os_advice import (
    _build_context,
    _task_matches_target,
    format_n4os_knowledge_preview,
    format_n4os_advice,
    generate_n4os_advice,
    is_n4os_advice_message,
)
import unittest
from unittest.mock import patch


class N4OSAdviceTest(unittest.TestCase):
    def test_knowledge_preview_names_every_loaded_source_without_paths(self):
        preview = format_n4os_knowledge_preview(
            {
                "files": [
                    {"path": "n4os/SOUL.md"},
                    {"path": "n4os/IDENTITY.md"},
                    {"path": "n4os/PRIORITIES.md"},
                    {"path": "n4os/PERSONAL_MODEL.md"},
                    {"path": "n4os/playbooks/Parenting.md"},
                ],
                "observations": ["signal"],
                "journal": [],
                "trajectories": [],
            },
            history_turns=1,
        )

        self.assertIn("Sources: SOUL, Identity, Priorities, Personal Model, Parenting", preview)
        self.assertIn("1 observation", preview)
        self.assertIn("Chat history: 1 turn", preview)
        self.assertNotIn("n4os/", preview)

    def test_detects_advice_triggers(self):
        self.assertTrue(is_n4os_advice_message("/ask How should we approach reading?"))
        self.assertTrue(is_n4os_advice_message("/n4os How should we approach reading?"))
        self.assertTrue(is_n4os_advice_message("How should we approach Nysha's reading?"))
        self.assertTrue(is_n4os_advice_message("what should I focus on this week?"))
        self.assertTrue(is_n4os_advice_message("Run morning check-in."))
        self.assertTrue(is_n4os_advice_message("Help me plan tomorrow morning."))
        self.assertFalse(is_n4os_advice_message("add home board item buy milk"))

    def test_morning_checkin_is_deterministic_phone_prompt(self):
        def fail_urlopen(*args, **kwargs):
            raise AssertionError("morning check-in should not call OpenAI")

        reply = format_n4os_advice(
            "Run morning check-in.",
            api_key="test-key",
            urlopen=fail_urlopen,
        )

        self.assertIn("Morning check-in", reply)
        self.assertIn("Energy/body/mind", reply)
        self.assertIn("Commit to 3 things", reply)
        self.assertIn("Decision:", reply)
        self.assertIn("Next action:", reply)
        self.assertNotIn("Loaded:", reply)
        self.assertNotIn("**", reply)
        self.assertLessEqual(len(reply.splitlines()), 16)

    def test_tomorrow_morning_plan_is_deterministic_phone_prompt(self):
        reply = format_n4os_advice(
            "Help me plan tomorrow morning.",
            api_key="",
        )

        self.assertIn("Tomorrow morning plan", reply)
        self.assertIn("write the 3 commitments", reply)
        self.assertNotIn("N4OS Telegram help", reply)

    def test_fallback_advice_uses_nysha_reading_memory(self):
        reply = format_n4os_advice(
            "How should we approach Nysha's reading?",
            api_key="",
        )

        self.assertIn("N4OS advice", reply)
        self.assertIn("Nysha's reading", reply)
        self.assertIn("Memory signals used", reply)
        self.assertIn("n4os/family/Nysha.md", reply)

    def test_fallback_school_transition_is_practice_first_and_captures_review(self):
        reply = format_n4os_advice(
            "/advice Nysha is struggling with school transition. What should I do?",
            api_key="",
        )

        self.assertIn("practice + safety", reply)
        self.assertIn("Do this for 7 days", reply)
        self.assertIn("Hi, I'm Nysha.", reply)
        self.assertIn("one bridge person", reply)
        self.assertIn("Decision:", reply)
        self.assertIn("Next action: do a 10-minute rehearsal", reply)
        self.assertIn("Review/Capture: check in 1 week", reply)
        self.assertRegex(reply, r"family/observations/\d{4}-\d{2}\.md")
        self.assertIn("Used:", reply)
        self.assertIn("SOUL", reply)
        self.assertIn("VISION", reply)
        self.assertIn("School Transition", reply)
        self.assertNotIn("does not talk to new people", reply)
        self.assertNotIn("**", reply)
        self.assertLessEqual(len(reply.splitlines()), 16)

    def test_school_transition_bypasses_openai_for_compact_phone_format(self):
        def fail_urlopen(*args, **kwargs):
            raise AssertionError("school transition should use deterministic phone format")

        reply = format_n4os_advice(
            "/advice Nysha is struggling with school transition. What should I do?",
            api_key="test-key",
            urlopen=fail_urlopen,
        )

        self.assertIn("Nysha likely needs practice + safety", reply)
        self.assertNotIn("###", reply)
        self.assertNotIn("**", reply)
        self.assertLessEqual(len(reply.splitlines()), 16)

    def test_openai_advice_is_plain_text_and_keeps_family_capture_loop(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(
                    {
                        "output_text": json.dumps(
                            {
                                "reasoning_summary": "Used recent reading signals.",
                                "answer": (
                                    "**Decision:** try a small school script.\n\n"
                                    "**Next action:** practice one hello."
                                ),
                            }
                        ),
                        "output": [
                            {
                                "type": "reasoning",
                                "summary": [
                                    {
                                        "type": "summary_text",
                                        "text": "Prioritized the recent family signals.",
                                    }
                                ],
                            }
                        ],
                    }
                ).encode("utf-8")

        seen_body = {}

        def fake_urlopen(request, timeout):
            seen_body["payload"] = json.loads(request.data.decode("utf-8"))
            return Response()

        reply = format_n4os_advice(
            "/ask How should we approach Nysha's reading?",
            api_key="test-key",
            urlopen=fake_urlopen,
        )

        self.assertIn("Decision: try a small school script.", reply)
        self.assertIn("Next action: practice one hello.", reply)
        self.assertIn("Capture loop:", reply)
        self.assertNotIn("**", reply)
        system_prompt = seen_body["payload"]["input"][0]["content"]
        self.assertIn("plain text for Telegram", system_prompt)
        self.assertIn("under 14 lines", system_prompt)
        self.assertIn("currently tends to", system_prompt)
        memory_paths = [
            item["path"]
            for item in json.loads(seen_body["payload"]["input"][1]["content"])["memory"]["files"]
        ]
        self.assertIn("n4os/SOUL.md", memory_paths)
        self.assertIn("n4os/MISSION.md", memory_paths)
        self.assertIn("n4os/VISION.md", memory_paths)
        self.assertEqual(seen_body["payload"]["reasoning"], {"summary": "concise"})
        self.assertEqual(
            seen_body["payload"]["text"]["format"]["type"],
            "json_schema",
        )

        result = generate_n4os_advice(
            "/ask How should we approach Nysha's reading?",
            api_key="test-key",
            urlopen=fake_urlopen,
        )
        self.assertEqual(result.reasoning_summary, "Prioritized the recent family signals.")
        self.assertTrue(result.knowledge_preview.startswith("Knowledge selected"))

    def test_fallback_advice_uses_recent_journal_signals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            (n4os_root / "journal").mkdir(parents=True)
            (n4os_root / "journal" / "2026-07-21.md").write_text(
                "\n".join(
                    [
                        "# Journal - 2026-07-21",
                        "",
                        "## Captures",
                        "",
                        "- I felt [[Attention|scattered]] because I [[playbooks/Health|slept]] badly.",
                        "  Topics: [[playbooks/Health|Health]], [[Attention]]",
                    ]
                ),
                encoding="utf-8",
            )

            reply = format_n4os_advice(
                "/ask what should I do about my health and attention?",
                n4os_root=n4os_root,
                api_key="",
            )

        self.assertIn("Journal signals used", reply)
        self.assertIn("I felt scattered because I slept badly", reply)

    def test_build_context_uses_related_terms_for_observation_recall(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            observations = n4os_root / "family" / "observations"
            observations.mkdir(parents=True)
            (observations / "2026-08.md").write_text(
                "\n".join(
                    [
                        "# Observations",
                        "",
                        "## 2026-08-23",
                        "",
                        "### [[family/Nysha|Nysha]]",
                        "- Observation: enjoys visual patterns and board games when they feel playful",
                    ]
                ),
                encoding="utf-8",
            )

            context = _build_context("How should we use silly puzzles with Nysha?", n4os_root)

        self.assertEqual(
            context["observations"],
            ["2026-08-23 Nysha: enjoys visual patterns and board games when they feel playful"],
        )

    def test_build_context_uses_related_terms_for_confidence_recall(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            observations = n4os_root / "family" / "observations"
            observations.mkdir(parents=True)
            (observations / "2026-08.md").write_text(
                "\n".join(
                    [
                        "# Observations",
                        "",
                        "## 2026-08-23",
                        "",
                        "### [[family/Nysha|Nysha]]",
                        "- Observation: speaks in a low voice around new adults",
                    ]
                ),
                encoding="utf-8",
            )

            context = _build_context("How can Nysha build confidence with public speaking?", n4os_root)

        self.assertEqual(
            context["observations"],
            ["2026-08-23 Nysha: speaks in a low voice around new adults"],
        )

    def test_fallback_advice_uses_recent_trajectory_signals(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir(parents=True)
            (n4os_root / "SOUL.md").write_text("Be warm.\n", encoding="utf-8")
            (n4os_root / "trajectories").mkdir(parents=True)
            (n4os_root / "trajectories" / "2026-08.md").write_text(
                "\n".join(
                    [
                        "# N4OS Trajectories - 2026-08",
                        "",
                        "## 2026-08-08T21:15:00",
                        "",
                        "- Mode: ask",
                        "- Topics: Reading, Parenting",
                        "- Summary: Nysha reading works better when she can teach back ideas.",
                    ]
                ),
                encoding="utf-8",
            )

            reply = format_n4os_advice(
                "/ask how should I approach Nysha reading?",
                n4os_root=n4os_root,
                api_key="",
            )

        self.assertIn("Conversation signals used", reply)
        self.assertIn("teach back", reply)

    def test_week_ahead_request_gets_week_plan_not_generic_memory_dump(self):
        operations = {
            "events": ["- Tue 4 PM-5 PM: Dentist"],
            "prep_events": ["- Dentist: bring insurance card"],
            "tasks": ["- Submit school forms | 2026-07-23 | 30 min"],
            "home_board": ["- 2026-07-22: Family: pack water bottles (leaving home)"],
            "unavailable": [],
        }
        with patch("n4os_advice._load_week_ahead_operations", return_value=operations):
            reply = format_n4os_advice(
                "/ask tell me about week ahead",
                api_key="",
            )

        self.assertIn("N4OS week ahead", reply)
        self.assertIn("This looks like a protect-attention week", reply)
        self.assertIn("Dentist", reply)
        self.assertIn("bring insurance card", reply)
        self.assertIn("Submit school forms", reply)
        self.assertIn("pack water bottles", reply)
        self.assertIn("Focus tomorrow:", reply)
        self.assertIn("Protect sleep and movement", reply)
        self.assertIn("Next action:", reply)
        self.assertIn("Used:", reply)
        self.assertNotIn("Loaded:", reply)
        self.assertNotIn("Memory signals used:", reply)
        self.assertNotIn("Start from health, family, purpose", reply)

    def test_week_ahead_request_does_not_call_openai_before_operations(self):
        def fail_urlopen(*args, **kwargs):
            raise AssertionError("week-ahead should not call OpenAI first")

        with patch("n4os_advice._load_week_ahead_operations", return_value={}):
            reply = format_n4os_advice(
                "/ask what should I focus on this week?",
                api_key="test-key",
                urlopen=fail_urlopen,
            )

        self.assertIn("N4OS week ahead", reply)
        self.assertIn("Focus tomorrow:", reply)

    def test_week_ahead_for_nysha_passes_target_to_operations(self):
        operations = {
            "events": [],
            "prep_events": [],
            "tasks": [],
            "home_board": [],
            "unavailable": [],
        }
        with patch("n4os_advice._load_week_ahead_operations", return_value=operations) as loader:
            reply = format_n4os_advice(
                "/ask tell me about week ahead for Nysha",
                api_key="",
            )

        loader.assert_called_once_with(target="Nysha")
        self.assertIn("N4OS week ahead for Nysha", reply)
        self.assertIn("This looks like a keep-it-calm", reply)
        self.assertIn("Focus tomorrow:", reply)
        self.assertIn("One concrete, low-pressure school-facing step", reply)
        self.assertIn("Family signal: Nysha currently benefits", reply)
        self.assertNotIn("Purpose/AI:", reply)
        self.assertNotIn("Review signals:", reply)
        self.assertIn("Review/Capture:", reply)
        self.assertNotIn("Loaded:", reply)
        self.assertNotIn("**", reply)
        self.assertLessEqual(len(reply.splitlines()), 18)

    def test_week_ahead_target_filter_excludes_unrelated_tasks(self):
        self.assertFalse(
            _task_matches_target(
                {"title": "Return amazon", "notes": ""},
                {"urgency": "high"},
                "Nysha",
            )
        )
        self.assertTrue(
            _task_matches_target(
                {"title": "Call school about Nysha transition", "notes": ""},
                {"urgency": "high"},
                "Nysha",
            )
        )
        self.assertTrue(
            _task_matches_target(
                {"title": "Submit school forms", "notes": ""},
                {"person": "Nysha", "urgency": "high"},
                "Nysha",
            )
        )

    def test_nysha_school_question_loads_imported_school_pack(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            school_root = n4os_root / "school" / "Nysha" / "2026-2027"
            school_root.mkdir(parents=True)
            for name in ("School Knowledge.md", "Room 13.md"):
                (school_root / name).write_text(f"# {name}\nsource-backed school context\n", encoding="utf-8")

            context = _build_context("when does Nysha have spring break", n4os_root)

        loaded = [item["path"] for item in context["files"]]
        self.assertIn("n4os/school/Nysha/2026-2027/School Knowledge.md", loaded)
        self.assertIn("n4os/school/Nysha/2026-2027/Room 13.md", loaded)

    def test_nysha_school_conversation_question_loads_saved_prompts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            school_root = n4os_root / "school" / "Nysha" / "2026-2027"
            school_root.mkdir(parents=True)
            for name in ("School Knowledge.md", "Room 13.md", "Conversation Starters.md"):
                (school_root / name).write_text(f"# {name}\nsource-backed school context\n", encoding="utf-8")

            context = _build_context("what can I ask Nysha about school today", n4os_root)

        loaded = [item["path"] for item in context["files"]]
        self.assertIn("n4os/school/Nysha/2026-2027/Conversation Starters.md", loaded)

    def test_nysha_book_question_adds_reading_garden_context(self):
        reading_garden = {
            "available": True,
            "current_book": "Pete the Cat",
            "book_collection": [{"title": "Pete the Cat", "status": "Reading", "last_read": "2026-08-11"}],
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "n4os_advice._reading_garden_context",
            return_value=reading_garden,
        ):
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir(parents=True)

            context = _build_context("what books is Nysha reading", n4os_root)

        self.assertEqual(context["reading_garden"], reading_garden)

    def test_newsletter_question_loads_all_saved_nysha_newsletter_imports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            n4os_root = root / "n4os"
            n4os_root.mkdir()
            self._write_newsletter_import(
                root / "data" / "n4os.db",
                date="2026-08-14",
                source_id="previous",
                source_url="https://docs.google.com/presentation/d/previous/edit",
                parsed={
                    "books": [
                        "Our Class is a Family",
                        "Chrysanthemum by Kevin Henkes",
                    ],
                    "learning_context": ["Classroom community"],
                },
            )
            self._write_newsletter_import(
                root / "data" / "n4os.db",
                date="2026-08-21",
                source_id="current",
                source_url="https://docs.google.com/presentation/d/current/edit",
                parsed={
                    "knowledge": {
                        "resources": [
                            {"kind": "book", "label": "Lilly's Purple Plastic Purse"},
                            {"kind": "video", "label": "Zen Den"},
                        ],
                        "topics": ["Character and responsibility"],
                    },
                },
            )

            context = _build_context("what books were mentioned in Nysha imported newsletters?", n4os_root)

        newsletters = context["school_newsletters"]
        self.assertEqual([item["date"] for item in newsletters], ["2026-08-14", "2026-08-21"])
        self.assertEqual(
            newsletters[0]["books"],
            ["Our Class is a Family", "Chrysanthemum by Kevin Henkes"],
        )
        self.assertEqual(newsletters[1]["books"], ["Lilly's Purple Plastic Purse"])

    def test_book_lookup_fallback_answers_from_all_newsletter_imports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            n4os_root = root / "n4os"
            n4os_root.mkdir()
            self._write_newsletter_import(
                root / "data" / "n4os.db",
                date="2026-08-14",
                source_id="previous",
                source_url="https://docs.google.com/presentation/d/previous/edit",
                parsed={"books": ["Our Class is a Family", "The Dot"]},
            )
            self._write_newsletter_import(
                root / "data" / "n4os.db",
                date="2026-08-21",
                source_id="current",
                source_url="https://docs.google.com/presentation/d/current/edit",
                parsed={"knowledge": {"resources": [{"kind": "book", "label": "Penny and Her Marble"}]}},
            )

            reply = format_n4os_advice(
                "/ask what books were mentioned in Nysha imported newsletters?",
                n4os_root=n4os_root,
                api_key="",
            )

        self.assertIn("2026-08-14:", reply)
        self.assertIn("Our Class is a Family", reply)
        self.assertIn("The Dot", reply)
        self.assertIn("2026-08-21:", reply)
        self.assertIn("Penny and Her Marble", reply)
        self.assertIn("Used: School Newsletters", reply)

    def test_school_newsletter_book_lookup_defaults_to_nysha_imports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            n4os_root = root / "n4os"
            n4os_root.mkdir()
            self._write_newsletter_import(
                root / "data" / "n4os.db",
                date="2026-08-14",
                source_id="previous",
                source_url="https://docs.google.com/presentation/d/previous/edit",
                parsed={"books": ["Our Class is a Family"]},
            )

            reply = format_n4os_advice(
                "/ask which books are covered in school newsletter",
                n4os_root=n4os_root,
                api_key="",
            )

        self.assertIn("Our Class is a Family", reply)
        self.assertIn("2026-08-14:", reply)

    def _write_newsletter_import(
        self,
        db_path: Path,
        *,
        date: str,
        source_id: str,
        source_url: str,
        parsed: dict,
    ) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS school_newsletter_imports (
                    id TEXT PRIMARY KEY,
                    child TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    title TEXT NOT NULL,
                    teacher TEXT,
                    newsletter_date TEXT NOT NULL,
                    content_fingerprint TEXT NOT NULL,
                    parsed_json TEXT NOT NULL,
                    saved_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(child, source_type, source_id, content_fingerprint)
                )
                """
            )
            payload = {"child": "Nysha", "title": "Room 13 Newsletter", "newsletter_date": date, **parsed}
            connection.execute(
                """
                INSERT INTO school_newsletter_imports (
                    id, child, source_type, source_id, source_url, title, teacher,
                    newsletter_date, content_fingerprint, parsed_json, saved_json,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    "Nysha",
                    "google_slides",
                    source_id,
                    source_url,
                    "Room 13 Newsletter",
                    "Mrs. Thompson",
                    date,
                    source_id,
                    json.dumps(payload, sort_keys=True),
                    "{}",
                    "saved",
                    f"{date}T12:00:00",
                    f"{date}T12:00:00",
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_book_lookup_fallback_answers_from_reading_garden(self):
        reading_garden = {
            "available": True,
            "current_book": "Pete the Cat: Pete at the Beach",
            "book_collection": [
                {
                    "title": "Pete the Cat: Pete at the Beach",
                    "status": "Reading",
                    "last_read": "2026-08-11",
                },
                {
                    "title": "HELLO KITTY Graduation Day",
                    "status": "Reading",
                    "last_read": "2026-08-09",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "n4os_advice._reading_garden_context",
            return_value=reading_garden,
        ):
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir(parents=True)

            reply = format_n4os_advice("/ask what books is Nysha reading", n4os_root=n4os_root)

        self.assertIn("Pete the Cat: Pete at the Beach", reply)
        self.assertIn("HELLO KITTY Graduation Day", reply)
        self.assertIn("Used: Reading Garden, Nysha", reply)


if __name__ == "__main__":
    unittest.main()
