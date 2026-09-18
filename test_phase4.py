"""Exact routing regressions from the verified Phase 4 audit."""

import unittest
from unittest.mock import patch

import app


class Phase4RoutingTests(unittest.TestCase):
    def test_remaining_and_free_qualifiers_keep_specific_intents(self):
        self.assertEqual(app.get_routing_decision(
            "how many periods do i have left today", "teacher")[2], "periods_remaining")
        self.assertEqual(app.get_routing_decision(
            "how many teachers are free right now", "principal")[2], "free_teachers")
        self.assertEqual(app.get_routing_decision("how many teachers", "principal")[2],
                         "total_teachers")

    def test_class_code_and_teaching_now_produce_occupant_candidate(self):
        result = app.get_routing_decision("what teachers are teaching 10a now", "principal")
        self.assertTrue(result[0])
        self.assertEqual(result[2], "classroom_occupant")

    def test_generic_identity_and_location_phrases_do_not_commit(self):
        with patch.object(app, "_teachers_with_subjects", return_value=[(1, "Mr Arjun Chopra", "Math")]):
            self.assertFalse(app.get_routing_decision("where is the library", "principal")[0])
            self.assertEqual(app.get_routing_decision("where is Arjun Chopra", "principal")[2],
                             "teacher_location")
        self.assertFalse(app.get_routing_decision("who is my department head", "teacher")[0])
        self.assertFalse(app.get_routing_decision("who is the head of my class", "student")[0])
        self.assertEqual(app.get_routing_decision("my department", "teacher")[2],
                         "teacher_identity")
        self.assertEqual(app.get_routing_decision("my class", "student")[2], "my_class")


if __name__ == "__main__":
    unittest.main()
