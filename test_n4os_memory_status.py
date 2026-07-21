from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from n4os_memory_status import (
    format_memory_status,
    is_memory_status_message,
    parse_memory_status_target,
)


class N4OSMemoryStatusTest(unittest.TestCase):
    def test_memory_status_command_detection(self):
        self.assertTrue(is_memory_status_message("/memory-status family"))
        self.assertTrue(is_memory_status_message("/memory-status Nysha"))
        self.assertFalse(is_memory_status_message("/memory family"))
        self.assertEqual(parse_memory_status_target("/memory-status Nysha"), "nysha")
        self.assertEqual(parse_memory_status_target("/memory-status nonsense"), "family")

    def test_format_family_status_includes_stable_and_recent_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            family_root = Path(tmpdir)
            (family_root / "observations").mkdir()
            (family_root / "FamilyValues.md").write_text(
                "\n".join(
                    [
                        "# Family Values",
                        "## Values",
                        "- Love before achievement.",
                        "- Presence before productivity.",
                        "## Family Practices",
                        "- Read together.",
                        "- Play games.",
                    ]
                ),
                encoding="utf-8",
            )
            (family_root / "Nysha.md").write_text(
                "# Nysha\n## 2026 Focus\nHelp Nysha build curiosity.\n",
                encoding="utf-8",
            )
            (family_root / "Navya.md").write_text(
                "# Navya\n## Focus\nHelp Navya grow with confidence.\n",
                encoding="utf-8",
            )
            (family_root / "observations" / "2026-07.md").write_text(
                "\n".join(
                    [
                        "# Family Observations - 2026-07",
                        "## 2026-07-21",
                        "### Nysha",
                        "- Observation: likes teaching",
                        "  Source: Google Docs",
                        "## 2026-07-21",
                        "### Navya",
                        "- Observation: has identity I love Maths",
                        "  Source: Google Docs",
                    ]
                ),
                encoding="utf-8",
            )

            status = format_memory_status("family", family_root=family_root)

        self.assertIn("Stable family memory", status)
        self.assertIn("Values: Love before achievement; Presence before productivity.", status)
        self.assertIn("2026-07-21 Nysha: likes teaching", status)
        self.assertIn("2026-07-21 Navya: has identity I love Maths", status)
        self.assertIn("- Nysha: 1", status)
        self.assertIn("- Navya: 1", status)
        self.assertIn("No raw observations are promoted", status)


if __name__ == "__main__":
    unittest.main()
