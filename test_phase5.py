"""Grounding and almanac-version regressions from the verified audit."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        old_state = gemini_rag._almanac_cache.copy()
        gemini_rag._cache.clear()
        self.addCleanup(gemini_rag._almanac_cache.update, old_state)
        self.addCleanup(gemini_rag._cache.clear)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "almanac.txt"
            path.write_text("SCHOOL HOURS\nOpening time: 7 AM.", encoding="utf-8")
            with patch.object(gemini_rag, "ALMANAC_PATH", str(path)), \
                 patch.object(gemini_rag, "ask_gemini", side_effect=lambda _q, context: context) as model:
                first = gemini_rag.gemini_answer("school hours")
                self.assertEqual(gemini_rag.gemini_answer("school hours"), first)
                self.assertEqual(model.call_count, 1)
                old_mtime = path.stat().st_mtime_ns
                path.write_text("SCHOOL HOURS\nOpening time: 8 AM.", encoding="utf-8")
                os.utime(path, ns=(old_mtime + 1_000_000, old_mtime + 1_000_000))
                second = gemini_rag.gemini_answer("school hours")
                self.assertIn("8 AM", second)
                self.assertNotEqual(first, second)
                self.assertEqual(model.call_count, 2)


if __name__ == "__main__":
    unittest.main()
