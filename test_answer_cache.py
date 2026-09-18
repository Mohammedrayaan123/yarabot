"""Regressions for distinct school-hour questions sharing a cache entry."""

import unittest
from unittest.mock import patch

import gemini_rag


class AnswerCacheTests(unittest.TestCase):
    def setUp(self):
        gemini_rag._cache.clear()
        self.addCleanup(gemini_rag._cache.clear)

    def test_distinct_question_calls_model_and_exact_repeat_uses_cache(self):
        def answer(question, _context):
            return "office answer" if "office" in question else "school answer"

        with patch.object(gemini_rag, "search_almanac", return_value="hours context"), \
             patch.object(gemini_rag, "ask_gemini", side_effect=answer) as model:
            self.assertEqual(gemini_rag.gemini_answer("school hours"), "school answer")
            self.assertEqual(gemini_rag.gemini_answer("school office hours"), "office answer")
            self.assertEqual(gemini_rag.gemini_answer("School office hours?"), "office answer")
            self.assertEqual(model.call_count, 2)

    def test_stream_uses_same_exact_cache_rule(self):
        def stream(question, _context):
            yield "office answer" if "office" in question else "school answer"

        with patch.object(gemini_rag, "search_almanac", return_value="hours context"), \
             patch.object(gemini_rag, "ask_gemini_stream", side_effect=stream) as model:
            self.assertEqual("".join(gemini_rag.gemini_answer_stream("school hours")), "school answer")
            self.assertEqual("".join(gemini_rag.gemini_answer_stream("school office hours")), "office answer")
            self.assertEqual("".join(gemini_rag.gemini_answer_stream("school hours")), "school answer")
            self.assertEqual(model.call_count, 2)


if __name__ == "__main__":
    unittest.main()
