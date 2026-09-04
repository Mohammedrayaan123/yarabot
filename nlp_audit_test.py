"""Read-only regression harness for the current NLP router.

Run with:
    python nlp_audit_test.py

It imports the production routing code and executes its deterministic path:
  get_routing_decision() -> answer_<role>()

Database access is replaced only for the duration of the process with fixed,
in-memory rows.  Final handlers are replaced with labelled return values so the
script can observe which handler app.py dispatches *after* its redirect logic.
It does not write to MySQL, Flask sessions, source files, or external APIs.

Rows routed to Groq/Gemini are deliberately not sent to live providers.  They
are reported as such; a provider response is not deterministic routing logic.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Callable

import app
import nlp_helpers


ROLE_INTENTS = app.ROLE_PERSONAL_INTENTS


@dataclass
class Result:
    case: str
    role: str
    input: str
    predicted_intent: str | None
    score: int
    ranked_candidates: list[tuple[str, int]]
    lane: str
    executed_intent: str | None
    redirect_fired: bool
    response: str
    clarification: str | None = None


def ranked_candidates(question: str, role: str) -> list[tuple[str, int]]:
    """ALL non-zero candidates via the production ranker itself
    (nlp_helpers.rank_intents) - no separate reimplementation here to drift
    out of sync with it. Uncapped top_n (production's own routing call uses
    the default top_n=3, since only the top 2 ever matter for the margin
    check) - this harness is for audit visibility, so a 4-way tie shouldn't
    have its 4th place silently invisible here."""
    return nlp_helpers.rank_intents(question, ROLE_INTENTS[role], top_n=len(ROLE_INTENTS[role]))


def fake_query(sql, params=None, fetch=False, many=False):
    """Minimal read-only fixture for redirect/entity-extraction paths only."""
    normalized = " ".join(sql.lower().split())
    # app._teachers_with_subjects() (teacher_subjects join, a teacher can
    # teach more than one subject now) replaced the old single-column
    # "select teacher_id, name, subject from teachers" - matched here by
    # its distinctive join target rather than the whole query text, which
    # is now a multi-line aggregate query. Same fixture shape either way:
    # (teacher_id, name, subjects-as-one-string).
    if "left join teacher_subjects" in normalized:
        return [(1, "Mr Ann", "Mathematics"), (2, "Ms Smith", "Science")]
    if "select distinct subject_name from subjects" in normalized:
        return [("Mathematics",), ("Science",), ("Computer Science",)]
    if "select distinct subject from teachers" in normalized:
        return [("Mathematics",), ("Science",)]
    if "group by class" in normalized:
        return []
    # Direct DB branches only need a harmless shape to complete execution.
    if "count(" in normalized:
        return (0,)
    return [] if many else None


HANDLER_NAMES = [
    "handle_identity", "handle_roll_number", "handle_my_class", "handle_next_period",
    "handle_subject_teacher", "handle_student_exam", "handle_student_timetable",
    "handle_teacher_next_class", "handle_teacher_current_class", "handle_teacher_free_periods",
    "handle_teacher_periods_remaining", "handle_teacher_identity", "handle_teacher_timetable",
    "handle_notices", "handle_subjects_offered", "handle_teacher_location",
    "handle_classroom_occupant", "handle_free_teachers", "handle_teacher_schedule_lookup",
    "handle_class_timetable_lookup", "handle_school_wide_subject_teacher",
    "handle_class_teacher_lookup", "handle_low_attendance_count", "handle_pending_fees_count",
    "handle_teacher_count_by_subject", "handle_department_free_teachers",
    "handle_department_schedule_today", "handle_department_teacher_count",
]

# Handler names are not always the intent spelling (for example,
# handle_teacher_periods_remaining serves periods_remaining).  This lets the
# redirect flag mean an actual app.py reclassification rather than a naming
# difference in the implementation.
HANDLER_TO_INTENT = {
    "handle_identity": "identity", "handle_roll_number": "roll_number",
    "handle_my_class": "my_class", "handle_next_period": "next_period",
    "handle_subject_teacher": "subject_teacher", "handle_student_exam": "exam",
    "handle_student_timetable": "timetable", "handle_teacher_next_class": "next_class",
    "handle_teacher_current_class": "current_class",
    "handle_teacher_free_periods": "free_periods",
    "handle_teacher_periods_remaining": "periods_remaining",
    "handle_teacher_identity": "teacher_identity",
    "handle_teacher_timetable": "timetable", "handle_notices": "notices",
    "handle_subjects_offered": "subjects_offered",
    "handle_teacher_location": "teacher_location",
    "handle_classroom_occupant": "classroom_occupant",
    "handle_free_teachers": "free_teachers",
    "handle_teacher_schedule_lookup": "teacher_schedule_lookup",
    "handle_class_timetable_lookup": "class_timetable_lookup",
    "handle_school_wide_subject_teacher": "school_wide_subject_teacher",
    "handle_class_teacher_lookup": "class_teacher_lookup",
    "handle_low_attendance_count": "low_attendance_count",
    "handle_pending_fees_count": "pending_fees_count",
    "handle_teacher_count_by_subject": "teacher_count_by_subject",
    "handle_department_free_teachers": "department_free_teachers",
    "handle_department_schedule_today": "department_schedule_today",
    "handle_department_teacher_count": "department_teacher_count",
}


class RouterProbe:
    """Temporarily instruments app.py without altering its source or persistent state."""

    def __init__(self):
        self.original_query = app.query
        self.original_handlers = {name: getattr(app, name) for name in HANDLER_NAMES}

    def __enter__(self):
        app.query = fake_query
        for name in HANDLER_NAMES:
            setattr(app, name, self._marker(name))
        return self

    def __exit__(self, exc_type, exc, traceback):
        app.query = self.original_query
        for name, handler in self.original_handlers.items():
            setattr(app, name, handler)

    @staticmethod
    def _marker(name: str) -> Callable:
        def marker(*_args, **_kwargs):
            return f"__HANDLER__:{name}"
        return marker

    def run(self, case: str, role: str, question: str) -> Result:
        predicted, score = nlp_helpers.detect_intent_with_score(question, ROLE_INTENTS[role])
        candidates = ranked_candidates(question, role)
        use_nlp, try_classifier, _routing_intent, _routing_score, clarification = \
            app.get_routing_decision(question, role)

        if clarification is not None:
            return Result(case, role, question, predicted, score, candidates,
                           "ambiguity-clarification (not called)", None, False, "", clarification)

        if not use_nlp:
            lane = "classifier (not called)" if try_classifier else "gemini/almanac (not called)"
            return Result(case, role, question, predicted, score, candidates, lane, None, False, "")

        if role == "student":
            response = app.answer_student(question, student_id=1)
        elif role == "teacher":
            response = app.answer_teacher(question, teacher_id=1)
        elif role == "hod":
            # Mirrors app._dispatch_to_role_handler()'s hod/vice_principal
            # branch - everything a teacher sees, plus the department-
            # scoped intents appended.
            response = app.answer_teacher(question, teacher_id=1, extra_intents=app.HOD_DEPARTMENT_INTENTS)
        else:
            response = app.answer_principal(question)

        if response.startswith("__HANDLER__:"):
            executed = HANDLER_TO_INTENT[response.split(":", 1)[1]]
        else:
            # The remaining branches perform their response directly. There are no
            # post-detection redirects after those branches, so the original intent
            # is the executed intent.
            executed = predicted
        return Result(
            case, role, question, predicted, score, candidates, "nlp/mysql",
            executed, executed != predicted, response,
        )


def collision_cases():
    """Generate one execution case for every shared keyword and phrase relation."""
    keyword_owners = defaultdict(list)
    phrase_owners = defaultdict(list)
    for intent, data in nlp_helpers.INTENT_DATA.items():
        for keyword in data["keywords"]:
            keyword_owners[keyword].append(intent)
        for phrase in data["phrases"]:
            phrase_owners[phrase].append(intent)

    for keyword, owners in keyword_owners.items():
        if len(owners) > 1:
            yield f"shared-keyword:{keyword}:{','.join(owners)}", keyword, owners
    for phrase, owners in phrase_owners.items():
        if len(owners) > 1:
            yield f"duplicate-phrase:{phrase}:{','.join(owners)}", phrase, owners

    seen = set()
    for left, left_data in nlp_helpers.INTENT_DATA.items():
        for right, right_data in nlp_helpers.INTENT_DATA.items():
            if left >= right:
                continue
            for a in left_data["phrases"]:
                for b in right_data["phrases"]:
                    if a in b or b in a:
                        long_phrase = a if len(a) >= len(b) else b
                        key = (left, right, long_phrase)
                        if key not in seen:
                            seen.add(key)
                            yield f"phrase-substring:{left}:{right}", long_phrase, [left, right]


BREAKING_INPUTS = [
    ("prior:time-substring", "student", "What is the time schedule?"),
    ("prior:teacher-vs-identity", "student", "Who is my teacher?"),
    ("prior:attendance-policy", "student", "What is my attendance policy?"),
    ("prior:fee-policy", "student", "What is my school fee structure?"),
    ("prior:class-occupant-natural", "principal", "Where is 10A now?"),
    ("prior:free-teachers-natural", "principal", "Are any teachers free?"),
    ("prior:subject-calc", "student", "Who teaches calc?"),
    ("prior:subject-it", "student", "Who teaches IT?"),
    ("prior:subject-sci", "student", "Who teaches sci?"),
    ("prior:name-substring", "principal", "Where is the annual meeting?"),
    ("prior:teacher-count-missing-subject", "principal", "How many teachers teach?"),
    ("prior:teacher-count-followup", "principal", "math"),
    # These are the two explicit app.py redirects discussed in the audit.
    ("redirect:schedule-for-class-code", "principal", "Schedule for 10A"),
    ("redirect:teacher-for-class-code", "principal", "Who teaches 10A?"),
    ("redirect:subject-in-curriculum", "student", "What subjects does the school offer math?"),
    # HOD department-scoped intents, empirically checked for collisions
    # against the full hod bucket (teacher intents + these) before shipping.
    ("hod:department-free-teachers", "hod", "which teachers in my department are free"),
    ("hod:department-schedule-today", "hod", "my department's schedule today"),
    ("hod:department-teacher-count", "hod", "how many teachers are in my department"),
    # A plain teacher-style question must still resolve normally for an hod
    # login - HOD_DEPARTMENT_INTENTS is additive, not a replacement.
    ("hod:still-sees-teacher-intents", "hod", "what is my timetable"),
]


def main():
    results = []
    cross_role_only = []
    with RouterProbe() as probe:
        for case, role, question in BREAKING_INPUTS:
            results.append(probe.run(case, role, question))

        # A relation only matters in a role in which both intents are eligible.
        # Execute every generated collision phrase/keyword for each relevant role.
        for case, question, mentioned in collision_cases():
            exercised = False
            for role, possible in ROLE_INTENTS.items():
                if len(set(mentioned) & set(possible)) >= 2:
                    results.append(probe.run(case, role, question))
                    exercised = True
            if not exercised:
                cross_role_only.append((case, mentioned))

    for result in results:
        print(
            f"[{result.case}]\n"
            f"  role={result.role}\n"
            f"  input={result.input!r}\n"
            f"  predicted_intent={result.predicted_intent!r} score={result.score}\n"
            f"  ranked_candidates={result.ranked_candidates}\n"
            f"  lane={result.lane}\n"
            f"  executed_intent={result.executed_intent!r} redirect_fired={result.redirect_fired}\n"
            f"  response={result.response!r}"
            + (f"\n  clarification={result.clarification!r}" if result.clarification else "")
        )

    for case, intents in cross_role_only:
        print(f"[cross-role-only:{case}] intents={intents} (no role can select both; not a runtime collision)")

    # These functions are called inside the final production handlers.  Print
    # their real results explicitly because the dispatcher probe replaces final
    # handler bodies only after answer_* has chosen one.
    teachers = [(1, "Mr Ann", "Mathematics"), (2, "Ms Smith", "Science")]
    print("[entity-probe:teacher-name-substring]")
    print("  input='Where is the annual meeting?'")
    print(f"  extract_teacher_name_from_question={app.extract_teacher_name_from_question('Where is the annual meeting?', teachers)!r}")
    saved_query = app.query
    try:
        app.query = fake_query
        print(f"  real_teacher_location_handler={app.handle_teacher_location('Where is the annual meeting?')!r}")
    finally:
        app.query = saved_query
    for question in ("Who teaches calc?", "Who teaches IT?", "Who teaches sci?"):
        print(f"[entity-probe:subject] input={question!r} extracted="
              f"{app.extract_subject_from_question(question, ['Mathematics', 'Science', 'Computer Science'])!r}")
    print("[clarification-probe:teacher_count_by_subject]")
    print(f"  persisted={ 'teacher_count_by_subject' in app.CLARIFICATION_CONFIG }")


if __name__ == "__main__":
    main()
