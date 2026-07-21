from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parent / "scripts" / "n4os-memory-inbox.py"


class N4OSMemoryInboxScriptTest(unittest.TestCase):
    def test_script_uses_capture_layer_by_default(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            input_text = "2026-07-21 Nysha was nervous about new classmates\nI felt unsure"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "-",
                    "--n4os-root",
                    str(n4os_root),
                    "--source",
                    "Google Docs",
                ],
                input=input_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            observation_text = (
                n4os_root / "family" / "observations" / "2026-07.md"
            ).read_text(encoding="utf-8")
            journal_text = (n4os_root / "journal" / "2026-07-21.md").read_text(
                encoding="utf-8",
            )

        self.assertIn("Captured.", result.stdout)
        self.assertIn("[[family/Nysha|Nysha]]", observation_text)
        self.assertIn("[[School Transition|new classmates]]", observation_text)
        self.assertIn("[[playbooks/Fear|unsure]]", journal_text)

    def test_script_preserves_custom_observations_root_legacy_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            observations_root = Path(tmpdir) / "observations"

            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "-",
                    "--observations-root",
                    str(observations_root),
                    "--default-date",
                    "2026-07-21",
                ],
                input="Nysha liked teaching",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

            observation_text = (observations_root / "2026-07.md").read_text(
                encoding="utf-8",
            )

        self.assertIn("N4OS memory inbox processed.", result.stdout)
        self.assertIn("[[family/Nysha|Nysha]]", observation_text)


if __name__ == "__main__":
    unittest.main()
