from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import unittest

from n4os_structured_memory import (
    format_structured_memory_query,
    has_structured_memory_mutation_match,
    has_structured_memory_query_match,
    is_structured_memory_mutation_message,
    is_structured_memory_query,
    is_structured_remember_message,
    mutate_structured_memory,
    remember_structured_memory,
)


class N4OSStructuredMemoryTest(unittest.TestCase):
    def test_twenty_family_memory_examples_retrieve_by_natural_language(self):
        cases = [
            (
                "school-gate",
                "/remember Nysha school gate code is 4812",
                "What is Nysha school gate code?",
                "4812",
            ),
            (
                "learning-bee",
                "/remember learning bee code 0816",
                "What is the learning bee code?",
                "0816",
            ),
            (
                "wifi",
                "/remember guest wifi password is mango-2026",
                "What is the guest Wi-Fi password?",
                "mango-2026",
            ),
            (
                "library-card",
                "/remember Navya library card number is 209944",
                "What do you remember about Navya library card?",
                "209944",
            ),
            (
                "locker",
                "/remember Nysha swim locker combination is 22-14-36",
                "What is Nysha swim locker combination?",
                "22-14-36",
            ),
            (
                "teacher",
                "/remember Navya teacher email is ms.lee@example.test",
                "What is Navya teacher email?",
                "ms.lee@example.test",
            ),
            (
                "costco",
                "/remember Costco membership is under Niyati phone ending 7788",
                "What do you remember about Costco membership?",
                "7788",
            ),
            (
                "piano",
                "/remember Piano lesson Zoom passcode is keys9",
                "What is the piano lesson passcode?",
                "keys9",
            ),
            (
                "allergy",
                "/remember Navya is allergic to cashews",
                "What do you remember about Navya allergy?",
                "cashews",
            ),
            (
                "parking",
                "/remember school parking permit number is FUSD-44",
                "What is the school parking permit number?",
                "FUSD-44",
            ),
            (
                "bike",
                "/remember Nysha bike lock code is 7305",
                "What is Nysha bike lock code?",
                "7305",
            ),
            (
                "doctor",
                "/remember pediatrician portal username is navya.parent",
                "What is pediatrician portal login?",
                "navya.parent",
            ),
            (
                "dinner-next",
                "/remember Niyati has the next dinner pickup",
                "whose turn is it to pick up dinner?",
                "Niyati",
            ),
            (
                "dinner-last",
                "/remember Nimesh picked up dinner yesterday",
                "who picked the last 1 dinner?",
                "Nimesh",
            ),
            (
                "medicine",
                "/remember Nysha medicine dose is 5 ml after dinner",
                "What do you remember about Nysha medicine dose?",
                "5 ml",
            ),
            (
                "carpool",
                "/remember Friday carpool pickup spot is blue gate",
                "Find memory Friday carpool pickup spot",
                "blue gate",
            ),
            (
                "passport",
                "/remember Navya passport appointment confirmation is PX92",
                "What is Navya passport appointment confirmation?",
                "PX92",
            ),
            (
                "robotics",
                "/remember robotics team code is bot-17",
                "What is robotics team code?",
                "bot-17",
            ),
            (
                "dentist",
                "/remember dentist insurance group number is DELTA-8",
                "What is dentist insurance group number?",
                "DELTA-8",
            ),
            (
                "soccer",
                "/remember soccer coach phone ends in 4410",
                "Find memory soccer coach phone",
                "4410",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            for _, remember_text, _, _ in cases:
                remember_structured_memory(remember_text, n4os_root=n4os_root)

            passed = 0
            failures: list[str] = []
            for name, _, query, expected in cases:
                should_route = (
                    is_structured_memory_query(query)
                    or has_structured_memory_query_match(query, n4os_root=n4os_root)
                )
                reply = (
                    format_structured_memory_query(query, n4os_root=n4os_root)
                    if should_route
                    else "NOT_ROUTED"
                )
                if expected.lower() in reply.lower():
                    passed += 1
                else:
                    failures.append(f"{name}: expected {expected!r} in {reply!r}")

        self.assertEqual(passed, 20, "\n".join(failures))

    def test_remember_records_last_dinner_pickups_in_date_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati picked up dinner tonight",
                n4os_root=n4os_root,
                today=date(2026, 8, 9),
            )
            remember_structured_memory(
                "/remember Nimesh picked up dinner yesterday",
                n4os_root=n4os_root,
                today=date(2026, 8, 9),
            )
            remember_structured_memory(
                "/remember Niyati picked up dinner today",
                n4os_root=n4os_root,
                today=date(2026, 8, 7),
            )

            reply = format_structured_memory_query(
                "who picked the last 3 dinners?",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "Last 3 dinner pickups:\n"
            "1. 2026-08-09: Niyati\n"
            "2. 2026-08-08: Nimesh\n"
            "3. 2026-08-07: Niyati",
        )

    def test_remember_records_next_dinner_pickup_assignment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            result = remember_structured_memory(
                "remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
                today=date(2026, 8, 9),
            )
            reply = format_structured_memory_query(
                "whose turn is it to pick up dinner?",
                n4os_root=n4os_root,
            )

        self.assertEqual(result.reply, "Remembered. Next dinner pickup: Niyati.")
        self.assertEqual(reply, "Next dinner pickup: Niyati.\nSource: Telegram.")

    def test_unresolved_pickup_note_answers_without_inventing_owner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember dinner pickup is unresolved; do not assume the same person",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "whose turn is it to pick up dinner?",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "I do not have a locked dinner pickup owner yet.\n"
            "\n"
            "Current memory:\n"
            "- dinner pickup is unresolved; do not assume the same person",
        )

    def test_explicit_memory_query_can_find_dinner_assignment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "find memory Niyati dinner pickup",
                n4os_root=n4os_root,
            )

        self.assertEqual(reply, "Remembered memory: Next dinner pickup: Niyati\nSource: Telegram.")

    def test_explicit_dinner_pickup_owner_search_uses_assignment_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "find memory dinner pickup turn",
                n4os_root=n4os_root,
            )

        self.assertEqual(reply, "Remembered memory: Next dinner pickup: Niyati\nSource: Telegram.")

    def test_explicit_memory_query_match_sees_dinner_assignment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )

            self.assertTrue(
                has_structured_memory_query_match(
                    "find memory dinner pickup turn",
                    n4os_root=n4os_root,
                )
            )

    def test_generic_remembered_note_can_be_retrieved_by_natural_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember learning code 0816",
                n4os_root=n4os_root,
                source="Telegram Nimesh",
            )
            reply = format_structured_memory_query(
                "What is the learning code?",
                n4os_root=n4os_root,
            )

        self.assertEqual(reply, "Remembered note: learning code 0816\nSource: Telegram Nimesh.")

    def test_generic_query_does_not_return_dinner_assignment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What is the learning code?",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )

    def test_generic_query_uses_word_matches_not_substrings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember I said to check the mail",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What do you remember about AI?",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )

    def test_forget_structured_memory_removes_matching_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory("/remember learning code 0816", n4os_root=n4os_root)
            result = mutate_structured_memory("forget learning code", n4os_root=n4os_root)
            reply = format_structured_memory_query(
                "What is the learning code?",
                n4os_root=n4os_root,
            )

        self.assertEqual(result.reply, "Forgot structured memory: learning code 0816.")
        self.assertEqual(
            reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )

    def test_update_structured_memory_replaces_matching_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory("/remember learning code 0816", n4os_root=n4os_root)
            result = mutate_structured_memory("update learning code to 9911", n4os_root=n4os_root)
            reply = format_structured_memory_query(
                "What is the learning code?",
                n4os_root=n4os_root,
            )

        self.assertEqual(result.reply, "Updated structured memory: learning code 9911.")
        self.assertIn("9911", reply)
        self.assertNotIn("0816", reply)

    def test_update_structured_memory_preserves_omitted_note_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Nysha school gate code is 4812",
                n4os_root=n4os_root,
            )
            result = mutate_structured_memory("update gate code to 9911", n4os_root=n4os_root)
            reply = format_structured_memory_query(
                "What is Nysha school gate code?",
                n4os_root=n4os_root,
            )

        self.assertEqual(result.reply, "Updated structured memory: Nysha school gate code is 9911.")
        self.assertIn("9911", reply)

    def test_update_structured_memory_preserves_non_lookup_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Navya is allergic to cashews",
                n4os_root=n4os_root,
            )
            result = mutate_structured_memory("update Navya allergy to peanuts", n4os_root=n4os_root)
            reply = format_structured_memory_query(
                "What do you remember about Navya allergy?",
                n4os_root=n4os_root,
            )

        self.assertEqual(result.reply, "Updated structured memory: Navya is allergic to peanuts.")
        self.assertIn("peanuts", reply)
        self.assertNotIn("cashews", reply)

    def test_update_structured_memory_preserves_sentence_phrase(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Nysha medicine dose is 5 ml after dinner",
                n4os_root=n4os_root,
            )
            result = mutate_structured_memory("update Nysha medicine dose to 7 ml", n4os_root=n4os_root)
            reply = format_structured_memory_query(
                "What do you remember about Nysha medicine dose?",
                n4os_root=n4os_root,
            )

        self.assertEqual(result.reply, "Updated structured memory: Nysha medicine dose is 7 ml.")
        self.assertIn("7 ml", reply)
        self.assertNotIn("5 ml", reply)

    def test_update_structured_memory_uses_lookup_alias_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember pediatrician portal username is navya.parent",
                n4os_root=n4os_root,
            )
            result = mutate_structured_memory("update portal login to navya.family", n4os_root=n4os_root)
            reply = format_structured_memory_query(
                "What is pediatrician portal login?",
                n4os_root=n4os_root,
            )

        self.assertEqual(result.reply, "Updated structured memory: pediatrician portal username is navya.family.")
        self.assertIn("navya.family", reply)
        self.assertNotIn("navya.parent", reply)

    def test_update_non_note_structured_memory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )
            result = mutate_structured_memory(
                "update memory dinner pickup to Nimesh",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            result.reply,
            "I can only update structured notes right now. Use forget and remember to replace dinner pickup records.",
        )

    def test_update_dinner_pickup_note_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember dinner pickup is unresolved; do not assume the same person",
                n4os_root=n4os_root,
            )
            result = mutate_structured_memory(
                "update memory dinner pickup to Nimesh",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            result.reply,
            "I can only update structured notes right now. Use forget and remember to replace dinner pickup records.",
        )

    def test_update_dinner_pickup_gate_code_note_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember dinner pickup gate code is 1234",
                n4os_root=n4os_root,
            )
            result = mutate_structured_memory(
                "update gate code to 4321",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What is dinner pickup gate code?",
                n4os_root=n4os_root,
            )

        self.assertEqual(result.reply, "Updated structured memory: dinner pickup gate code is 4321.")
        self.assertIn("4321", reply)

    def test_structured_memory_mutation_detection_does_not_steal_task_commands(self):
        self.assertTrue(is_structured_memory_mutation_message("forget learning code"))
        self.assertTrue(is_structured_memory_mutation_message("update learning code to 9911"))
        self.assertTrue(is_structured_memory_mutation_message("delete memory learning code"))
        self.assertFalse(is_structured_memory_mutation_message("delete task buy milk"))
        self.assertFalse(is_structured_memory_mutation_message("update task buy milk to tomorrow"))
        self.assertFalse(is_structured_memory_mutation_message("update task code to 1234"))
        self.assertFalse(is_structured_memory_mutation_message("change calendar pin to 4321"))
        self.assertFalse(is_structured_memory_mutation_message("remove memory card from Costco"))
        self.assertFalse(is_structured_memory_mutation_message("update memory card to 128GB"))
        self.assertFalse(is_structured_memory_mutation_message("forget that"))
        self.assertTrue(is_structured_memory_mutation_message("forget Navya allergy"))
        self.assertTrue(is_structured_memory_mutation_message("update Navya allergy to peanuts"))
        self.assertFalse(is_structured_memory_mutation_message("forget code"))
        self.assertFalse(is_structured_memory_mutation_message("update password to 1234"))

    def test_specific_natural_mutation_miss_returns_memory_miss(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            result = mutate_structured_memory("forget Navya allergy", n4os_root=n4os_root)

        self.assertEqual(
            result.reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )

    def test_lookup_only_mutations_ask_for_more_detail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory("/remember learning code 0816", n4os_root=n4os_root)
            forget_result = mutate_structured_memory("forget code", n4os_root=n4os_root)
            update_result = mutate_structured_memory("update code to 9911", n4os_root=n4os_root)
            reply = format_structured_memory_query(
                "What is the learning code?",
                n4os_root=n4os_root,
            )

        self.assertIn("Please include which structured memory", forget_result.reply)
        self.assertIn("Please include which structured memory", update_result.reply)
        self.assertIn("0816", reply)

    def test_structured_memory_mutation_probe_requires_existing_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Navya is allergic to cashews",
                n4os_root=n4os_root,
            )

            self.assertTrue(
                has_structured_memory_mutation_match(
                    "forget Navya allergy",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_mutation_match(
                    "delete Navya allergy",
                    n4os_root=n4os_root,
                )
            )
            self.assertTrue(
                has_structured_memory_mutation_match(
                    "delete memory Navya allergy",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_mutation_match(
                    "forget the last idea",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_mutation_match(
                    "forget code",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_mutation_match(
                    "update password to 1234",
                    n4os_root=n4os_root,
                )
            )

    def test_mutation_probe_does_not_hijack_calendar_like_updates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Navya passport appointment confirmation is PX92",
                n4os_root=n4os_root,
            )

            self.assertFalse(is_structured_memory_mutation_message("forget passport appointment"))
            self.assertFalse(
                has_structured_memory_mutation_match(
                    "change passport appointment to Friday",
                    n4os_root=n4os_root,
                )
            )
            self.assertTrue(
                has_structured_memory_mutation_match(
                    "update passport appointment confirmation to PX93",
                    n4os_root=n4os_root,
                )
            )
            self.assertTrue(
                has_structured_memory_mutation_match(
                    "update memory passport appointment confirmation to PX93",
                    n4os_root=n4os_root,
                )
            )

    def test_generic_query_match_requires_existing_memory_for_natural_language(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            self.assertFalse(
                has_structured_memory_query_match(
                    "What is area code 510?",
                    n4os_root=n4os_root,
                )
            )
            remember_structured_memory(
                "/remember learning code 0816",
                n4os_root=n4os_root,
            )

            self.assertTrue(
                has_structured_memory_query_match(
                    "What is the learning code?",
                    n4os_root=n4os_root,
                )
            )

    def test_generic_code_query_requires_specific_term_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember door code 1234",
                n4os_root=n4os_root,
            )

            self.assertFalse(
                has_structured_memory_query_match(
                    "What is the learning code?",
                    n4os_root=n4os_root,
                )
            )
            self.assertEqual(
                format_structured_memory_query(
                    "What is the learning code?",
                    n4os_root=n4os_root,
                ),
                "I do not have a structured memory matching that yet. Use /remember to save it.",
            )

    def test_generic_code_query_does_not_cross_person_entities(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Navya school gate code is 1234",
                n4os_root=n4os_root,
            )

            self.assertFalse(
                has_structured_memory_query_match(
                    "What is Nysha school gate code?",
                    n4os_root=n4os_root,
                )
            )
            self.assertEqual(
                format_structured_memory_query(
                    "What is Nysha school gate code?",
                    n4os_root=n4os_root,
                ),
                "I do not have a structured memory matching that yet. Use /remember to save it.",
            )

    def test_generic_query_requires_more_than_entity_only_overlap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Navya is allergic to cashews",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember Navya passport appointment confirmation is PX92",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What do you remember about Navya allergy?",
                n4os_root=n4os_root,
            )

        self.assertIn("cashews", reply)
        self.assertNotIn("PX92", reply)

    def test_generic_value_query_requires_requested_field_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Navya is allergic to cashews",
                n4os_root=n4os_root,
            )

            self.assertFalse(
                has_structured_memory_query_match(
                    "What is Navya email?",
                    n4os_root=n4os_root,
                )
            )
            self.assertEqual(
                format_structured_memory_query(
                    "What is Navya email?",
                    n4os_root=n4os_root,
                ),
                "I do not have a structured memory matching that yet. Use /remember to save it.",
            )

    def test_number_lookup_does_not_match_confirmation_or_phone_ending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Navya passport appointment confirmation is PX92",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember Costco membership is under Niyati phone ending 7788",
                n4os_root=n4os_root,
            )

            self.assertFalse(
                has_structured_memory_query_match(
                    "What is Navya passport appointment number?",
                    n4os_root=n4os_root,
                )
            )
            self.assertEqual(
                format_structured_memory_query(
                    "What is Navya passport appointment number?",
                    n4os_root=n4os_root,
                ),
                "I do not have a structured memory matching that yet. Use /remember to save it.",
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "What is Costco membership number?",
                    n4os_root=n4os_root,
                )
            )
            remember_structured_memory(
                "/remember Navya library card number is 209944",
                n4os_root=n4os_root,
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "What is Navya library card confirmation?",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "What is Navya library card ending?",
                    n4os_root=n4os_root,
                )
            )

    def test_memory_search_requires_specific_phrase_for_non_lookup_notes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Navya is allergic to cashews",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember Navya passport appointment confirmation is PX92",
                n4os_root=n4os_root,
            )

            self.assertFalse(
                has_structured_memory_query_match(
                    "find memory allergy",
                    n4os_root=n4os_root,
                )
            )
            self.assertTrue(
                has_structured_memory_query_match(
                    "find memory Navya allergy",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "show me memory passport",
                    n4os_root=n4os_root,
                )
            )
            self.assertTrue(
                has_structured_memory_query_match(
                    "show me memory Navya passport",
                    n4os_root=n4os_root,
                )
            )

    def test_generic_value_query_can_mention_dinner_pickup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember dinner pickup gate code is 1234",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What is the dinner pickup gate code?",
                n4os_root=n4os_root,
            )

        self.assertEqual(reply, "Remembered note: dinner pickup gate code is 1234\nSource: Telegram.")

    def test_find_memory_does_not_require_find_token_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember learning code 0816",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "find memory learning code",
                n4os_root=n4os_root,
            )

        self.assertEqual(reply, "Remembered note: learning code 0816\nSource: Telegram.")

    def test_explicit_memory_query_with_dinner_pickup_returns_note_before_owner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember dinner pickup gate code is 1234",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "find memory dinner pickup gate code",
                n4os_root=n4os_root,
            )

        self.assertEqual(reply, "Remembered note: dinner pickup gate code is 1234\nSource: Telegram.")

    def test_explicit_dinner_pickup_search_miss_does_not_return_owner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "find memory dinner pickup spot",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )

    def test_generic_value_query_with_dinner_pickup_does_not_return_owner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What is the dinner pickup gate code?",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )

    def test_dinner_owner_query_ignores_generic_dinner_pickup_note(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember dinner pickup gate code is 1234",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "whose turn is it to pick up dinner?",
                n4os_root=n4os_root,
            )

        self.assertNotIn("1234", reply)
        self.assertEqual(
            reply,
            "I do not have dinner pickup memory yet. Use /remember to save who picked up dinner or who has the next turn.",
        )

    def test_explicit_memory_search_finds_dinner_assignment_and_event_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember Nimesh picked up dinner yesterday",
                n4os_root=n4os_root,
                today=date(2026, 8, 13),
            )

            assignment_reply = format_structured_memory_query(
                "show me memory Niyati dinner pickup",
                n4os_root=n4os_root,
            )
            event_reply = format_structured_memory_query(
                "find memory Nimesh dinner pickup",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            assignment_reply,
            "Remembered memory: Next dinner pickup: Niyati\nSource: Telegram.",
        )
        self.assertEqual(
            event_reply,
            "Remembered memory: Dinner pickup: 2026-08-12: Nimesh\nSource: Telegram.",
        )

    def test_explicit_dinner_pickup_content_miss_does_not_return_owner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What do you remember about dinner pickup spot?",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )

    def test_explicit_dinner_pickup_owner_term_search_miss_does_not_fallback_to_owner(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Niyati has the next dinner pickup",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "find memory Friday dinner pickup turn",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )

    def test_generic_value_query_asks_for_detail_when_multiple_values_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Nysha school gate code is 4812",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember Navya school gate code is 2719",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What is the school gate code?",
                n4os_root=n4os_root,
            )

        self.assertIn("multiple matching structured memories", reply)
        self.assertIn("4812", reply)
        self.assertIn("2719", reply)

    def test_same_second_memory_correction_reports_conflicting_values(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember learning code 0816",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember learning code 9911",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What is the learning code?",
                n4os_root=n4os_root,
            )

        self.assertIn("multiple matching structured memories", reply)
        self.assertIn("9911", reply)
        self.assertIn("0816", reply)

    def test_string_memory_correction_returns_latest_value(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember guest wifi password is mango-2026",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember guest wifi password is kiwi-2026",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What is the guest Wi-Fi password?",
                n4os_root=n4os_root,
            )

        self.assertIn("multiple matching structured memories", reply)
        self.assertIn("kiwi-2026", reply)
        self.assertIn("mango-2026", reply)

    def test_clear_generic_value_lookup_without_match_falls_through_probe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            self.assertFalse(
                has_structured_memory_query_match(
                    "What is the learning code?",
                    n4os_root=n4os_root,
                )
            )
            reply = format_structured_memory_query(
                "What is the learning code?",
                n4os_root=n4os_root,
            )

        self.assertEqual(
            reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )

    def test_generic_memory_probe_does_not_create_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"

            self.assertFalse(
                has_structured_memory_query_match(
                    "What is the learning code?",
                    n4os_root=n4os_root,
                )
            )

            self.assertFalse((Path(tmpdir) / "data").exists())

    def test_format_lookup_query_does_not_create_database_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"

            reply = format_structured_memory_query(
                "find memory learning code",
                n4os_root=n4os_root,
            )

            self.assertEqual(
                reply,
                "I do not have a structured memory matching that yet. Use /remember to save it.",
            )
            self.assertFalse((Path(tmpdir) / "data").exists())

    def test_format_lookup_query_handles_existing_database_without_memory_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir()
            db_path = data_dir / "n4os.db"

            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE other_feature (id TEXT PRIMARY KEY)")
                connection.commit()
            finally:
                connection.close()

            reply = format_structured_memory_query(
                "find memory learning code",
                n4os_root=n4os_root,
            )

            self.assertEqual(
                reply,
                "I do not have a structured memory matching that yet. Use /remember to save it.",
            )

    def test_probe_handles_existing_database_without_memory_table(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir()
            db_path = data_dir / "n4os.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("CREATE TABLE other_feature (id TEXT PRIMARY KEY)")
                connection.commit()
            finally:
                connection.close()

            self.assertFalse(
                has_structured_memory_query_match(
                    "What is the learning code?",
                    n4os_root=n4os_root,
                )
            )

    def test_format_remember_about_query_does_not_create_database_when_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"

            reply = format_structured_memory_query(
                "What do you remember about learning code?",
                n4os_root=n4os_root,
            )

            self.assertEqual(
                reply,
                "I do not have a structured memory matching that yet. Use /remember to save it.",
            )
            self.assertFalse((Path(tmpdir) / "data").exists())

    def test_generic_value_query_accepts_whats_without_apostrophe(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember guest wifi password is mango-2026",
                n4os_root=n4os_root,
            )

            self.assertTrue(
                has_structured_memory_query_match(
                    "whats the guest wifi password?",
                    n4os_root=n4os_root,
                )
            )

    def test_generic_note_search_can_find_older_memories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory("/remember learning code 0816", n4os_root=n4os_root)
            for index in range(101):
                remember_structured_memory(
                    f"/remember unrelated note {index}",
                    n4os_root=n4os_root,
                )
            reply = format_structured_memory_query(
                "What is the learning code?",
                n4os_root=n4os_root,
            )

        self.assertEqual(reply, "Remembered note: learning code 0816\nSource: Telegram.")

    def test_structured_memory_wrapper_word_is_not_required_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory("/remember learning code 0816", n4os_root=n4os_root)
            reply = format_structured_memory_query(
                "find structured memory learning code",
                n4os_root=n4os_root,
            )

        self.assertEqual(reply, "Remembered note: learning code 0816\nSource: Telegram.")

    def test_message_detection(self):
        self.assertTrue(is_structured_remember_message("/remember Niyati picked up dinner"))
        self.assertTrue(is_structured_remember_message("remember Niyati picked up dinner"))
        self.assertFalse(is_structured_remember_message("remember to add milk"))
        self.assertTrue(is_structured_memory_query("who picked the last 3 dinners?"))
        self.assertFalse(is_structured_memory_query("What is code for learning bee?"))
        self.assertTrue(is_structured_memory_query("Find memory learning code"))
        self.assertTrue(is_structured_memory_query("Find memory school gate code"))
        self.assertTrue(is_structured_memory_query("Find memory soccer coach phone"))
        self.assertTrue(is_structured_memory_query("Find remembered memory learning code"))
        self.assertFalse(is_structured_memory_query("Find remembered memory passport"))
        self.assertFalse(is_structured_memory_query("What do you remember about Navya allergy?"))
        self.assertFalse(is_structured_memory_query("Do you remember that?"))
        self.assertFalse(is_structured_memory_query("Do you remember the plan from yesterday?"))
        self.assertFalse(is_structured_memory_query("What is the QR code?"))
        self.assertFalse(is_structured_memory_query("what is the memory usage?"))
        self.assertFalse(is_structured_memory_query("show memory usage"))
        self.assertFalse(is_structured_memory_query("find notes app"))
        self.assertFalse(is_structured_memory_query("find memory code"))
        self.assertFalse(is_structured_memory_query("show memory password"))
        self.assertFalse(is_structured_memory_query("what are my current goals?"))

    def test_remember_about_probe_routes_only_with_matching_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Navya is allergic to cashews",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember learning code 0816",
                n4os_root=n4os_root,
            )

            self.assertTrue(
                has_structured_memory_query_match(
                    "What do you remember about Navya allergy?",
                    n4os_root=n4os_root,
                )
            )
            self.assertTrue(
                has_structured_memory_query_match(
                    "What do you remember about learning code?",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "What do you remember about yesterday?",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "What do you remember about Navya?",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "What do you remember about dinner?",
                    n4os_root=n4os_root,
                )
            )

    def test_temporal_remember_about_probe_does_not_route_matching_date_words(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Nimesh picked up dinner yesterday",
                n4os_root=n4os_root,
            )

            self.assertFalse(
                has_structured_memory_query_match(
                    "What do you remember about yesterday?",
                    n4os_root=n4os_root,
                )
            )

    def test_lookup_only_memory_probe_does_not_route_saved_secrets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember learning code 0816",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember guest wifi password is mango-2026",
                n4os_root=n4os_root,
            )

            self.assertFalse(
                has_structured_memory_query_match(
                    "find memory code",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "show memory password",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "What do you remember about code?",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "What do you remember about password?",
                    n4os_root=n4os_root,
                )
            )

    def test_explicit_alias_memory_search_requires_specific_phrase(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember Navya passport appointment confirmation is PX92",
                n4os_root=n4os_root,
            )

            self.assertFalse(
                has_structured_memory_query_match(
                    "find remembered memory passport",
                    n4os_root=n4os_root,
                )
            )
            self.assertEqual(
                format_structured_memory_query(
                    "find remembered memory passport",
                    n4os_root=n4os_root,
                ),
                "I do not have a structured memory matching that yet. Use /remember to save it.",
            )
            self.assertEqual(
                format_structured_memory_query(
                    "show me memory passport",
                    n4os_root=n4os_root,
                ),
                "I do not have a structured memory matching that yet. Use /remember to save it.",
            )
            self.assertTrue(
                has_structured_memory_query_match(
                    "find remembered memory Navya passport",
                    n4os_root=n4os_root,
                )
            )

    def test_explicit_lookup_only_memory_query_does_not_dump_saved_secrets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember learning code 0816",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember guest wifi password is mango-2026",
                n4os_root=n4os_root,
            )
            code_reply = format_structured_memory_query(
                "What do you remember about code?",
                n4os_root=n4os_root,
            )
            password_reply = format_structured_memory_query(
                "What do you remember about password?",
                n4os_root=n4os_root,
            )

        self.assertNotIn("0816", code_reply)
        self.assertNotIn("mango-2026", password_reply)
        self.assertEqual(
            code_reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )
        self.assertEqual(
            password_reply,
            "I do not have a structured memory matching that yet. Use /remember to save it.",
        )

    def test_single_subject_memory_probe_does_not_hijack_normal_commands(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember phone memory usage was high after photos",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember notes app is on the second home screen",
                n4os_root=n4os_root,
            )

            self.assertFalse(
                has_structured_memory_query_match(
                    "show memory usage",
                    n4os_root=n4os_root,
                )
            )
            self.assertFalse(
                has_structured_memory_query_match(
                    "find notes app",
                    n4os_root=n4os_root,
                )
            )

    def test_generic_value_query_ignores_common_followup_fillers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember learning code 0816",
                n4os_root=n4os_root,
            )
            reply = format_structured_memory_query(
                "What was the current learning code again?",
                n4os_root=n4os_root,
            )

        self.assertEqual(reply, "Remembered note: learning code 0816\nSource: Telegram.")

    def test_generic_value_query_matches_lookup_alias_phrasing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            n4os_root = Path(tmpdir) / "n4os"
            n4os_root.mkdir()

            remember_structured_memory(
                "/remember pediatrician portal username is navya.parent",
                n4os_root=n4os_root,
            )
            remember_structured_memory(
                "/remember passport appointment confirmation is PX92",
                n4os_root=n4os_root,
            )
            login_reply = format_structured_memory_query(
                "What is the pediatrician portal login?",
                n4os_root=n4os_root,
            )
            confirmation_reply = format_structured_memory_query(
                "What is the passport appointment confirmation?",
                n4os_root=n4os_root,
            )

        self.assertIn("navya.parent", login_reply)
        self.assertIn("PX92", confirmation_reply)


if __name__ == "__main__":
    unittest.main()
