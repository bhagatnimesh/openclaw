import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from intent import DEFAULT_TIMEZONE, extract_intent


REFERENCE_TIME = datetime(2026, 7, 3, 9, 0, tzinfo=ZoneInfo(DEFAULT_TIMEZONE))


class HomeBoardIntentTest(unittest.TestCase):
    def test_before_leaving_future_date_notice(self):
        intent = extract_intent(
            "Next Wednesday before Nysha leaves remind us to take the payment envelope",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "add_item")
        self.assertEqual(intent["person_or_group"], "Nysha")
        self.assertEqual(intent["message"], "Take the payment envelope")
        self.assertEqual(intent["date"], "2026-07-08")
        self.assertEqual(intent["context"], "before_leave")
        self.assertEqual(intent["trigger"], "leave_home")
        self.assertEqual(intent["priority"], "medium")

    def test_before_leaving_alias_notice(self):
        intent = extract_intent(
            "Next Wednesday before Big N leaves remind us to take the payment envelope",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "add_item")
        self.assertEqual(intent["person_or_group"], "Nysha")
        self.assertEqual(intent["message"], "Take the payment envelope")

    def test_helper_kitchen_notice_defaults_to_today(self):
        intent = extract_intent(
            "Helper should put the food in the fridge today",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["person_or_group"], "Helper")
        self.assertEqual(intent["message"], "Put the food in the fridge")
        self.assertEqual(intent["date"], "2026-07-03")
        self.assertEqual(intent["context"], "kitchen")

    def test_before_airport_notice_targets_family(self):
        intent = extract_intent(
            "Before airport, remind everyone to carry passports",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["person_or_group"], "Family")
        self.assertEqual(intent["message"], "Carry passports")
        self.assertEqual(intent["context"], "airport")
        self.assertEqual(intent["trigger"], "airport")

    def test_thought_like_home_board_item_uses_clean_message(self):
        intent = extract_intent(
            "Add home board item tomorrow to put passports by the door",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "add_item")
        self.assertEqual(intent["person_or_group"], "Family")
        self.assertEqual(intent["message"], "Put passports by the door")
        self.assertEqual(intent["date"], "2026-07-04")

    def test_list_today_at_home_intent(self):
        intent = extract_intent("What's on Today at Home?", now=REFERENCE_TIME)

        self.assertEqual(intent["intent"], "list_items")
        self.assertEqual(intent["date"], "2026-07-03")

    def test_bare_home_board_command_lists_items(self):
        intent = extract_intent("Home board", now=REFERENCE_TIME)

        self.assertEqual(intent["intent"], "list_items")
        self.assertEqual(intent["date"], "2026-07-03")

    def test_unknown_text_does_not_become_notice(self):
        intent = extract_intent("What should I focus on today?", now=REFERENCE_TIME)

        self.assertEqual(intent["intent"], "unknown")

    def test_line_based_bulk_notice(self):
        intent = extract_intent(
            """Today at home:
            Nysha: take journal
            Helper: put food in fridge
            Dad: take passport""",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "add_items")
        self.assertEqual(
            [(item["person_or_group"], item["message"]) for item in intent["items"]],
            [
                ("Nysha", "Take journal"),
                ("Helper", "Put food in fridge"),
                ("Dad", "Take passport"),
            ],
        )
        self.assertEqual({item["date"] for item in intent["items"]}, {"2026-07-03"})

    def test_comma_separated_bulk_notice(self):
        intent = extract_intent(
            "Nysha take journal, Helper put food in fridge, Dad take passport today",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "add_items")
        self.assertEqual(len(intent["items"]), 3)
        self.assertEqual(intent["items"][1]["context"], "kitchen")

    def test_comma_separated_bulk_notice_with_aliases(self):
        intent = extract_intent(
            "Nisha take journal, Naavya bring shoes, Namesh take passport today",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "add_items")
        self.assertEqual(
            [(item["person_or_group"], item["message"]) for item in intent["items"]],
            [
                ("Nysha", "Take journal"),
                ("Navya", "Bring shoes"),
                ("Dad", "Take passport"),
            ],
        )

    def test_shared_future_date_bulk_notice(self):
        intent = extract_intent(
            "Next Wednesday Nysha take payment envelope; Dad take passport",
            now=REFERENCE_TIME,
        )

        self.assertEqual(intent["intent"], "add_items")
        self.assertEqual({item["date"] for item in intent["items"]}, {"2026-07-08"})


if __name__ == "__main__":
    unittest.main()
