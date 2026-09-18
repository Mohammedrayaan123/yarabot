"""Grounding and almanac-version regressions from the verified audit."""

import unittest
from unittest.mock import patch

import almanac_store
import gemini_rag


class AlmanacTests(unittest.TestCase):
    def test_subsection_heading_stays_with_uniform_and_fee_tables(self):
        boys = gemini_rag._score_almanac_sections("uniform for boys")[0][1]
        payment = gemini_rag._score_almanac_sections("when do i pay fees")[0][1]
        self.assertTrue(boys.startswith("SCHOOL UNIFORM POLICY / BOYS:"))
        self.assertIn("Chocolate brown chequered shirts", boys)
        self.assertTrue(payment.startswith("FEE PAYMENT PROCEDURES & DATES"))
        self.assertIn("15th April", payment)

    def test_whole_words_and_stricter_classifier_bypass(self):
        with patch.object(gemini_rag, "get_almanac", return_value="COFFEE\nHot coffee is available."):
            self.assertEqual(gemini_rag._score_almanac_sections("fee"), [])
        self.assertEqual(gemini_rag.almanac_match_confidence("phone policy"), (0, False))
        self.assertEqual(gemini_rag.almanac_match_confidence("teachers day date"), (0, False))
        self.assertEqual(gemini_rag.almanac_match_confidence("parent teacher meeting date"), (0, False))
        self.assertTrue(gemini_rag._score_almanac_sections("school transport fees")[0][1]
                        .startswith("FEE STRUCTURE"))
        self.assertTrue(gemini_rag.almanac_match_confidence("uniform for boys")[1])
        self.assertTrue(gemini_rag.almanac_match_confidence("when do i pay fees")[1])

    def test_almanac_edit_invalidates_an_answer_for_the_same_question(self):
        old_state = almanac_store._cache.copy()
        gemini_rag._cache.clear()
        self.addCleanup(almanac_store._cache.update, old_state)
        self.addCleanup(gemini_rag._cache.clear)
        record = [("SCHOOL HOURS\nOpening time: 7 AM.", 1)]
        with patch.object(almanac_store, "read_almanac", side_effect=lambda: record[0]), \
             patch.object(gemini_rag, "ask_gemini", side_effect=lambda _q, context: context) as model:
            almanac_store.get_almanac_snapshot(force=True)
            first = gemini_rag.gemini_answer("school hours")
            self.assertEqual(gemini_rag.gemini_answer("school hours"), first)
            self.assertEqual(model.call_count, 1)
            record[0] = ("SCHOOL HOURS\nOpening time: 8 AM.", 2)
            almanac_store.get_almanac_snapshot(force=True)
            second = gemini_rag.gemini_answer("school hours")
            self.assertIn("8 AM", second)
            self.assertNotEqual(first, second)
            self.assertEqual(model.call_count, 2)

    def test_almanac_db_outage_serves_last_good_snapshot(self):
        old_state = almanac_store._cache.copy()
        self.addCleanup(almanac_store._cache.update, old_state)
        with patch.object(almanac_store, "read_almanac", return_value=("SCHOOL HOURS\n7 AM", 3)):
            self.assertEqual(almanac_store.get_almanac_snapshot(force=True)[1], 3)
        with patch.object(almanac_store, "read_almanac", side_effect=OSError("database offline")):
            self.assertEqual(almanac_store.get_almanac_snapshot(force=True),
                             ("SCHOOL HOURS\n7 AM", 3))


if __name__ == "__main__":
    unittest.main()
