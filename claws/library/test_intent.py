import unittest

from intent import extract_intent


class LibraryIntentTest(unittest.TestCase):
    def test_named_child_reading_status(self):
        intent = extract_intent("Show Nysha reading status")

        self.assertEqual(intent, {"intent": "status", "children": ["Nysha"]})


if __name__ == "__main__":
    unittest.main()
