import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from claw import ScienceLabClaw
from provider import SQLiteScienceLabProvider


class ScienceLabClawTest(unittest.TestCase):
    def test_plan_requests_import_when_library_empty(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            claw = ScienceLabClaw.from_provider(
                SQLiteScienceLabProvider(Path(temp_dir) / "science.db")
            )

            output = StringIO()
            with redirect_stdout(output):
                message = claw.plan_from_request("Plan the next 4 science lab experiments")

        self.assertIn("do not have experiment records yet", message)
        self.assertIn("experiment records", output.getvalue())

    def test_plan_lists_next_experiments_and_materials(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            provider = SQLiteScienceLabProvider(Path(temp_dir) / "science.db")
            provider.upsert_experiment(
                experiment_id="ice-cream-bag",
                title="Ice Cream in a Bag",
                concepts=["freezing point"],
                materials=["milk", "rock salt", "zip bags"],
                waiting_time="low",
                visual_excitement="high",
                library_order=2,
            )
            provider.upsert_experiment(
                experiment_id="lava-lamp",
                title="Lava Lamp",
                concepts=["density"],
                materials=["oil", "food coloring"],
                waiting_time="low",
                visual_excitement="high",
                library_order=1,
            )
            provider.upsert_inventory(
                material_id="zip-bags",
                display_name="Zip bags",
                status="have",
            )
            provider.upsert_inventory(
                material_id="rock-salt",
                display_name="Rock salt",
                status="missing",
            )
            claw = ScienceLabClaw.from_provider(provider)

            output = StringIO()
            with redirect_stdout(output):
                message = claw.plan_from_request("Plan the next 2 science lab experiments")

        self.assertIn("Science Lab plan:", message)
        self.assertIn("Lava Lamp", message)
        self.assertIn("Ice Cream in a Bag", message)
        self.assertIn("Already in Home Inventory: zip bags", message)
        self.assertIn("Recommended Amazon Order: rock salt", message)


if __name__ == "__main__":
    unittest.main()
