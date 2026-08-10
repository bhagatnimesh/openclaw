import unittest

from intent import extract_intent, normalize_list_slug


class ShoppingIntentTest(unittest.TestCase):
    def test_normalizes_list_aliases(self):
        cases = {
            "Indian grocery": "indian",
            "Costco": "costco",
            "WholeFoods": "whole-foods",
            "whole foods": "whole-foods",
            "Amazon": "amazon",
            "Other": "others",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_list_slug(value), expected)

    def test_cart_add_is_explicit_shopping_add(self):
        intent = extract_intent("/cart add milk to Costco")

        self.assertEqual(intent["intent"], "add_item")
        self.assertEqual(intent["list_slug"], "costco")
        self.assertEqual(intent["item"], "milk")

    def test_cart_prefix_with_item_and_store_adds_item(self):
        intent = extract_intent("/cart tofu costco")

        self.assertEqual(intent["intent"], "add_item")
        self.assertEqual(intent["list_slug"], "costco")
        self.assertEqual(intent["item"], "tofu")

    def test_add_to_cart_store_item_adds_clean_item(self):
        intent = extract_intent("Add to cart Costco tofu")

        self.assertEqual(intent["intent"], "add_item")
        self.assertEqual(intent["list_slug"], "costco")
        self.assertEqual(intent["item"], "tofu")

    def test_shop_bare_list_reads_items(self):
        intent = extract_intent("/shop Indian")

        self.assertEqual(intent["intent"], "list_items")
        self.assertEqual(intent["list_slug"], "indian")

    def test_bulk_add_splits_items(self):
        intent = extract_intent("add dosa batter, curry leaves and paneer to Indian")

        self.assertEqual(intent["intent"], "add_items")
        self.assertEqual(intent["list_slug"], "indian")
        self.assertEqual(intent["items"], ["dosa batter", "curry leaves", "paneer"])

    def test_cart_clothing_list_voice_text_adds_clean_items_to_others(self):
        intent = extract_intent(
            "Add to cart, do other shopping list, need to find shorts to wear, "
            "add another item to it, need to find night pants which are a bit "
            "more breathable. Third, need to find full sleeve breathable "
            "t-shirts for night.",
        )

        self.assertEqual(intent["intent"], "add_items")
        self.assertEqual(intent["list_slug"], "others")
        self.assertEqual(
            intent["items"],
            [
                "shorts to wear",
                "night pants which are a bit more breathable",
                "full sleeve breathable t-shirts for night",
            ],
        )

    def test_cart_clothing_list_slash_command_adds_clean_items_to_others(self):
        intent = extract_intent(
            "/cart Add to other shopping list, need to find shorts to wear, "
            "add another item to it, need to find night pants which are a bit "
            "more breathable. Third, need to find full sleeve breathable "
            "t-shirts for night.",
        )

        self.assertEqual(intent["intent"], "add_items")
        self.assertEqual(intent["list_slug"], "others")
        self.assertEqual(
            intent["items"],
            [
                "shorts to wear",
                "night pants which are a bit more breathable",
                "full sleeve breathable t-shirts for night",
            ],
        )

    def test_move_extracts_source_and_target_lists(self):
        intent = extract_intent("/shop move coconut milk from Costco to Indian")

        self.assertEqual(intent["intent"], "move_item")
        self.assertEqual(intent["item"], "coconut milk")
        self.assertEqual(intent["list_slug"], "costco")
        self.assertEqual(intent["target_list_slug"], "indian")

    def test_ambiguous_buy_needs_list_name(self):
        intent = extract_intent("buy milk")

        self.assertEqual(intent["intent"], "add_item")
        self.assertIn("list_name", intent["missing_fields"])

    def test_list_done_clears_list(self):
        intent = extract_intent("Indian grocery done")

        self.assertEqual(intent["intent"], "clear_list")
        self.assertEqual(intent["list_slug"], "indian")

    def test_clear_grocery_list_alias_clears_list(self):
        intent = extract_intent("clear grocery Indian")

        self.assertEqual(intent["intent"], "clear_list")
        self.assertEqual(intent["list_slug"], "indian")


if __name__ == "__main__":
    unittest.main()
