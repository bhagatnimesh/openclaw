import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from claw import ShoppingClaw
from provider import SQLiteShoppingProvider, SQLiteShoppingStore


class ShoppingClawTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = SQLiteShoppingStore(Path(self.temp_dir.name) / "n4os.db")
        self.claw = ShoppingClaw.from_provider(SQLiteShoppingProvider(self.store), self.store)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_cart_add_and_list(self):
        output = StringIO()
        with redirect_stdout(output):
            self.claw.handle_request("/cart add milk to Costco")
            self.claw.handle_request("/shop Costco")

        text = output.getvalue()
        self.assertIn("Added milk to Costco.", text)
        self.assertIn("Costco:\n- milk", text)

    def test_undo_cart_add_deletes_item(self):
        with redirect_stdout(StringIO()):
            self.claw.handle_request("/cart add milk to Costco")
            self.claw.undo_last_action()

        response = self.claw.tools.list_items("costco")
        self.assertEqual(response["data"]["items"], [])

    def test_cart_add_without_list_uses_history_when_available(self):
        output = StringIO()
        with redirect_stdout(output):
            self.claw.handle_request("/cart add paneer to Indian")
            self.claw.handle_request("/cart add paneer")

        text = output.getvalue()
        self.assertIn("Added paneer to Indian.", text)
        self.assertIn("Added paneer to Indian based on shopping history.", text)

    def test_cart_clothing_voice_text_adds_clean_items_to_others(self):
        output = StringIO()
        with redirect_stdout(output):
            message = self.claw.handle_request(
                "Add to cart, do other shopping list, need to find shorts to wear, "
                "add another item to it, need to find night pants which are a bit "
                "more breathable. Third, need to find full sleeve breathable "
                "t-shirts for night.",
            )

        self.assertIn("Added 3 items to Others:", message)
        self.assertIn("- shorts to wear", message)
        self.assertIn("- night pants which are a bit more breathable", message)
        self.assertIn("- full sleeve breathable t-shirts for night", message)
        response = self.claw.tools.list_items("others")
        self.assertCountEqual(
            [item["title"] for item in response["data"]["items"]],
            [
                "shorts to wear",
                "night pants which are a bit more breathable",
                "full sleeve breathable t-shirts for night",
            ],
        )

    def test_done_clears_pending_list_items(self):
        output = StringIO()
        with redirect_stdout(output):
            self.claw.handle_request("/cart add paneer to Indian")
            self.claw.handle_request("/cart add curry leaves to Indian")
            self.claw.handle_request("Indian grocery done")

        text = output.getvalue()
        self.assertIn("Cleared 2 pending item(s) from Indian.", text)
        pending = self.claw.tools.list_items("indian")
        self.assertEqual(pending["data"]["items"], [])


if __name__ == "__main__":
    unittest.main()
