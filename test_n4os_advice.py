from __future__ import annotations

from pathlib import Path
import tempfile
import json
from n4os_advice import (
    _task_matches_target,
    format_n4os_advice,
    is_n4os_advice_message,
)
import unittest
from unittest.mock import patch


class N4OSAdviceTest(unittest.TestCase):
    def test_detects_advice_triggers(self):
        self.assertTrue(is_n4os_advice_message("/ask How should we approach reading?"))
        self.assertTrue(is_n4os_advice_message("/n4os How should we approach reading?"))
        self.assertTrue(is_n4os_advice_message("How should we approach Nysha's reading?"))
        self.assertTrue(is_n4os_advice_message("what should I focus on this week?"))
        self.assertFalse(is_n4os_advice_message("add home board item buy milk"))

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
                        "output_text": (
                            "**Decision:** try a small school script.\n\n"
                            "**Next action:** practice one hello."
                        )
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


if __name__ == "__main__":
    unittest.main()
