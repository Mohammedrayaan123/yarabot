"""Focused regressions for the five Phase 3 routing and cache fixes."""

import datetime
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import app
import nlp_helpers


class Phase3Tests(unittest.TestCase):
    def test_complete_question_switches_without_question_mark_but_slot_does_not(self):
        self.assertTrue(app._is_topic_switch("what is my attendance", "student", "subject_teacher"))
        self.assertFalse(app._is_topic_switch("math", "student", "subject_teacher"))

    def test_tomorrow_resolves_to_next_calendar_day(self):
        expected = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%A").lower()
        self.assertEqual(app.extract_day_from_question("my timetable tomorrow"), expected)

    def test_punctuation_ending_attendance_phrases_match_as_whole_phrases(self):
        for phrase in ("attendance %", "my attendance %", "my current attendance %"):
            with self.subTest(phrase=phrase):
                self.assertEqual(nlp_helpers.score_intent(phrase, [], "attendance", False,
                                                          phrase_override=[phrase]), 3)
        self.assertEqual(nlp_helpers.score_intent("attendance %age", [], "attendance", False,
                                                  phrase_override=["attendance %"]), 0)

    def test_overlapping_cache_refreshes_keep_newest_snapshot_and_outage_keeps_it(self):
        original = nlp_helpers._phrase_cache.copy()
        self.addCleanup(nlp_helpers._phrase_cache.update, original)
        nlp_helpers._phrase_cache.update(data={"attendance": ["seed"]}, loaded_at=0)
        first_started = threading.Event()
        release_first = threading.Event()
        calls = []

        def fetch():
            calls.append(len(calls))
            if len(calls) == 1:
                first_started.set()
                self.assertTrue(release_first.wait(2))
                return {"attendance": ["old"]}
            return {"attendance": ["new"]}

        with patch.object(nlp_helpers, "_fetch_phrases_from_db", side_effect=fetch):
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(nlp_helpers.refresh_phrase_cache, True)
                self.assertTrue(first_started.wait(2))
                second = pool.submit(nlp_helpers.refresh_phrase_cache, True)
                release_first.set()
                first.result(2)
                second.result(2)
        self.assertEqual(nlp_helpers._phrase_cache["data"]["attendance"], ["new"])
        with patch.object(nlp_helpers, "_fetch_phrases_from_db", return_value=None):
            nlp_helpers.refresh_phrase_cache(force=True)
        self.assertEqual(nlp_helpers._phrase_cache["data"]["attendance"], ["new"])

    def test_wellbeing_greeting_ignores_punctuation_for_each_role(self):
        for answer in (lambda q: app.answer_student(q, 1),
                       lambda q: app.answer_teacher(q, 1),
                       app.answer_principal):
            self.assertEqual(answer("how are u"), answer("how are u?"))
            self.assertEqual(answer("how are u"), "I'm doing well, thanks.")


if __name__ == "__main__":
    unittest.main()
