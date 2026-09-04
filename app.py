"""
app.py
------
Flask backend for the Yara School Chatbot.
Handles login, session management, and NLP queries.
Runs separately from the Streamlit dashboard.

To run:
    python app.py

Then open: http://localhost:5000
"""

from dotenv import load_dotenv
load_dotenv()

from flask import (
    Flask, render_template, request, jsonify, session, redirect, url_for,
    Response, stream_with_context
)
from datetime import date
import datetime
import sys
import os
import re
import secrets
import time
import json
import math

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from auth_helpers import verify_password
from nlp_helpers import detect_intent, detect_intent_with_score, rank_intents
from gemini_rag import gemini_answer_stream, almanac_top_score, classify_personal_intent, log_learned_phrase
from config import DB_CONFIG
from validators import GRADE_SECTION_PATTERN, EARLY_YEARS_CLASSES
import mysql.connector


# =========================================================
# ROUTING
# Lane 1 (personal)   -> NLP + MySQL. Private data never leaves the server.
# Lane 2 (classifier) -> Groq picks an intent when neither lane above is confident.
# Lane 3 (general)    -> Gemini + almanac. Only school-wide info is sent out.
# =========================================================

# Words that strongly signal a PERSONAL question (needs MySQL)
# Bare pronoun words used to be padded with manual spaces (' i ', 'me ') as
# a crude stand-in for word-boundary matching - still wrong both ways: 'me '
# matched inside "extreme thing", 'mine' (no padding at all) matched inside
# "determine"/"mineral", and ' i ' missed "who am i?" (no trailing space
# before the '?'). Now matched via real \b-anchored regex - see
# _PERSONAL_SIGNAL_PATTERNS below - which is also what makes this
# consistent with nlp_helpers.has_personal_signal()'s own \b(my|i|me|mine)\b,
# instead of two different mechanisms for the same concept.
PERSONAL_SIGNALS = [
    'my', 'i', 'am i', 'do i', 'have i', 'i have',
    'mine', 'me', 'i am', "i'm", 'show me my',
    'what is my', 'what are my', "what's my",
    # "roll no"/"next period" etc. have no "my"/"I" at all, so a student
    # asking exactly that was routed to Gemini instead of NLP.
    'roll no', 'roll number', 'next period', 'next class',
]
_PERSONAL_SIGNAL_PATTERNS = [re.compile(r'\b' + re.escape(s) + r'\b') for s in PERSONAL_SIGNALS]


def is_personal_question(question):
    """
    Returns True if the question is about the user's own data
    (attendance, exam, timetable, fees) — routes to NLP + MySQL.
    Returns False if it's a general school knowledge question
    — routes to Gemini + almanac.
    """
    q = question.lower()
    return any(pattern.search(q) for pattern in _PERSONAL_SIGNAL_PATTERNS)


# Words/phrases that mark a question as asking about the SCHOOL'S rule, not
# the asker's own record - "what is my attendance policy" is asking about
# the POLICY (school-wide, lives in the almanac), not the asker's own
# attendance percentage, even though it's grammatically possessive ("my").
# Checked unconditionally in _nlp_lane_decision(), ahead of the personal-
# pronoun protection - that protection exists specifically to stop pronoun'd
# wording being misrouted to general knowledge, and this is the deliberate,
# narrow exception to it: a pronoun must not force the personal-data lane
# when policy-frame language is also present.
POLICY_FRAME_SIGNALS = [
    "policy", "policies", "structure", "rules", "regulations", "regulation",
    "procedure", "procedures", "guideline", "guidelines",
    "how does the school", "what is the school's", "what is the schools",
]
_POLICY_FRAME_PATTERNS = [re.compile(r'\b' + re.escape(s) + r'\b') for s in POLICY_FRAME_SIGNALS]


def is_policy_framed(question):
    """True if the question is asking about a school policy/rule rather than
    a personal record - see POLICY_FRAME_SIGNALS above."""
    q = question.lower()
    return any(pattern.search(q) for pattern in _POLICY_FRAME_PATTERNS)


# Role hierarchy: assistant_principal gets identical access to principal
# (full school-wide data, no scoping). hod and vice_principal both get
# everything a teacher sees plus their own department-scoped queries -
# vice_principal is an explicit placeholder at hod-level access for now
# (requirements TBD, to be upgraded later - see the task this came from).
# Centralized here so a role's actual behavior is decided once, not
# re-derived at every dispatch site below.
PRINCIPAL_LIKE_ROLES = {"principal", "assistant_principal"}
HOD_LIKE_ROLES = {"hod", "vice_principal"}


def _effective_role(role):
    """Maps a login role to the routing/intent bucket it behaves as -
    'principal' or 'hod' for the roles those groups cover, otherwise the
    role itself unchanged (student/teacher)."""
    if role in PRINCIPAL_LIKE_ROLES:
        return "principal"
    if role in HOD_LIKE_ROLES:
        return "hod"
    return role


def _notice_visible_roles(role):
    """Which notices.target_roles tokens this login role should see -
    additive, same "sees everything X sees, plus more" shape as
    _effective_role() above, but a SET (not a single bucket): hod/
    vice_principal see 'teacher'-targeted notices in addition to their own
    'hod'-targeted ones, not INSTEAD of them (_effective_role() alone would
    collapse them to just 'hod' and miss teacher-wide notices entirely).
    target_roles='all' is checked separately by the caller, not folded in
    here."""
    if role in PRINCIPAL_LIKE_ROLES:
        return {"principal"}
    if role in HOD_LIKE_ROLES:
        return {"teacher", "hod"}
    return {role}  # student, teacher


# Teacher/principal-tier questions are nearly always DB questions ("how many
# students", "which classes are assigned") but rarely first-person, so
# PERSONAL_SIGNALS alone would wrongly send them to Gemini.
DB_FIRST_ROLES = {"teacher", "principal"} | HOD_LIKE_ROLES | PRINCIPAL_LIKE_ROLES

# Phrases that clearly point at school-wide almanac content, not a
# person's own records. Needed because nlp_helpers.py's timetable intent
# treats "schedule"/"classes" as keywords - without this, "when is the
# exam schedule for grade 9" would match that intent and return the
# ASKER's own timetable instead. Deliberately multi-word/specific so they
# don't collide with genuine personal questions like "what's my exam
# schedule", which PERSONAL_SIGNALS still catches correctly.
GENERAL_KNOWLEDGE_SIGNALS = [
    "exam schedule", "exam date", "exam dates", "board exam",
    "half yearly", "half-yearly", "pre-board", "unit test schedule",
    "holiday", "holidays", "vacation", "hajj", "eid",
    "ptm", "parent teacher", "parent-teacher",
    "admission", "admissions",
    "fee structure", "fees structure", "school fee",
    "uniform", "dress code",
    "policy", "policies", "circular", "notice board",
    "academic calendar", "academic year",
    "cbse",
    "school reopen", "school reopens", "school start", "school begins",
    "school hours", "school timing", "office hours",
    "transport", "bus route",
]
# "announcement" used to be in this list, but it shadowed the real
# `notices` NLP intent (zero occurrences in school_almanac.txt anyway) -
# "any announcements" was being forced to Gemini instead of the actual
# notices. Removed; the almanac_top_score() tie-break further down covers
# it if a future almanac edit ever legitimately uses the word.


def is_general_knowledge_question(question):
    """
    Returns True if the question is clearly about school-wide information
    that lives in the almanac, not a specific person's own records.
    """
    q = question.lower()
    return any(signal in q for signal in GENERAL_KNOWLEDGE_SIGNALS)


# Exact allowlist, checked as a WHOLE-MESSAGE match, never substring/fuzzy.
# Used to go through nlp_helpers.detect_intent()'s fuzzy matching, which
# caused a real bug: "Shark Tank?" fuzzy-matched "thank" at ~0.89 and got
# "You're welcome!" instead of reaching Gemini. A typo'd greeting like
# "helo" now misses here and goes to Gemini instead - a harmless miss,
# unlike silently swallowing a real question.
GREETING_ONLY_PHRASES = {
    "hi", "hello", "hey", "yo",
    "good morning", "good afternoon", "good evening",
    "thanks", "thank you", "thanks a lot", "thx", "ty",
    "help", "what can you do", "what do you do",
}


def is_pure_greeting(question):
    """
    True only when the ENTIRE message (after stripping punctuation) is one
    of GREETING_ONLY_PHRASES above - nlp_helpers.py already answers these
    for free, with no DB query and no API call. Checked before everything
    else in use_nlp_lane() so a bare "hi" from a student can't fall through
    to Gemini and burn a quota-limited API call on a message NLP was
    already fully equipped to answer.
    """
    cleaned = question.strip().lower()
    for ch in '?!.,':
        cleaned = cleaned.replace(ch, '')
    return cleaned in GREETING_ONLY_PHRASES


# The intent names each role's answer_*() actually recognizes (mirrors the
# possible_intents lists in answer_student/teacher/principal below), minus
# greeting/thanks/help - those are already handled by is_pure_greeting().
# Named separately (not just inline in ROLE_PERSONAL_INTENTS) so "hod" can
# be built as "everything a teacher sees, plus its own department-scoped
# intents" in code, not just in prose - see HOD_LIKE_ROLES above.
TEACHER_INTENTS = ["period_count", "timetable", "classes_assigned", "next_class",
                    "current_class", "free_periods", "periods_remaining", "teacher_identity",
                    "notices", "subjects_offered"]
HOD_DEPARTMENT_INTENTS = ["department_free_teachers", "department_schedule_today",
                           "department_teacher_count"]

ROLE_PERSONAL_INTENTS = {
    "student": ["attendance", "exam", "timetable", "fee", "identity",
                "roll_number", "my_class", "next_period", "subject_teacher",
                "notices", "subjects_offered"],
    "teacher": TEACHER_INTENTS,
    "hod": TEACHER_INTENTS + HOD_DEPARTMENT_INTENTS,
    "principal": ["teacher_count_by_subject", "total_students", "total_teachers",
                  "class_wise_count", "teacher_location", "classroom_occupant",
                  "free_teachers", "teacher_schedule_lookup", "class_timetable_lookup",
                  "school_wide_subject_teacher", "class_teacher_lookup",
                  "low_attendance_count", "pending_fees_count", "notices", "subjects_offered"],
}


# Thresholds for the almanac-overlap tie-break (see _nlp_lane_decision).
# ALMANAC_STRONG_MATCH_SCORE=2 was picked against real almanac content:
# "teachers day" scores 2 against the "Teacher's Day" section (both
# "teachers" and "day" match) - a single stray word (score 1) is too weak
# to override a genuine NLP match, but 2 isn't. NLP_PHRASE_MATCH_SCORE=3
# matches nlp_helpers.score_intent()'s +3-per-phrase scoring, so anything
# under 3 is keyword-only by construction.
ALMANAC_STRONG_MATCH_SCORE = 2
NLP_PHRASE_MATCH_SCORE = 3

# Ambiguity guard thresholds (see _nlp_lane_decision). A single top score is
# only trustworthy if it's (a) not too weak on its own, and (b) clearly
# ahead of the runner-up - ties used to be broken silently by list order
# ("who is my teacher?" ties identity/subject_teacher at 1 each; "how many
# teachers teach?" ties teacher_count_by_subject/total_teachers at 5 each).
NLP_SCORE_FLOOR = 2
NLP_MARGIN_THRESHOLD = 2

# Short, human phrase per intent, for the ambiguity clarification message
# ("Did you mean X or Y?"). Every intent that can appear in
# ROLE_PERSONAL_INTENTS needs an entry here, or the clarification falls back
# to the raw intent name.
INTENT_DESCRIPTIONS = {
    "attendance": "your attendance percentage",
    "exam": "your upcoming exams",
    "timetable": "your timetable",
    "fee": "your fee status",
    "period_count": "how many periods you have",
    "classes_assigned": "which classes you're assigned to",
    "total_students": "the total student count",
    "total_teachers": "the total teacher count",
    "class_wise_count": "a class-wise student breakdown",
    "identity": "your own details",
    "roll_number": "your roll number",
    "my_class": "which class you're in",
    "next_period": "your next period",
    "subject_teacher": "who teaches you a subject",
    "next_class": "your next class",
    "current_class": "your current class",
    "free_periods": "your free periods",
    "periods_remaining": "how many periods you have left today",
    "teacher_identity": "your own details",
    "teacher_location": "where a teacher is right now",
    "classroom_occupant": "who's teaching a class right now",
    "free_teachers": "which teachers are free right now",
    "teacher_schedule_lookup": "a teacher's schedule",
    "class_timetable_lookup": "a class's timetable",
    "school_wide_subject_teacher": "who teaches a subject school-wide",
    "class_teacher_lookup": "a class's subject-teacher list",
    "low_attendance_count": "students below the attendance threshold",
    "pending_fees_count": "students with pending fees",
    "teacher_count_by_subject": "how many teachers teach a subject",
    "department_free_teachers": "which teachers in your department are free",
    "department_schedule_today": "your department's schedule today",
    "department_teacher_count": "how many teachers are in your department",
    "notices": "the latest notices",
    "subjects_offered": "which subjects the school offers",
}


def _ambiguity_clarification(intent_a, intent_b):
    """'Did you mean X or Y?' using INTENT_DESCRIPTIONS, falling back to the
    raw intent name for anything not covered there."""
    desc_a = INTENT_DESCRIPTIONS.get(intent_a, intent_a)
    desc_b = INTENT_DESCRIPTIONS.get(intent_b, intent_b)
    return f"Did you mean {desc_a}, or {desc_b}?"


def use_nlp_lane(question, role):
    """
    True routes to the NLP lane, False means Gemini, the classifier, or a
    clarification gets a turn. Plain-bool wrapper around
    _nlp_lane_decision() - see its docstring for the full routing rationale
    (the AMBIGUOUS_KEYWORDS collision fix, the almanac tie-break, the
    classifier's trigger rule, the ambiguity-margin guard).
    """
    return _nlp_lane_decision(question, role)[0]


def get_routing_decision(question, role):
    """
    Public entry point for /api/chat's routing split (NLP lane / AI
    classifier lane / ambiguity clarification / Gemini+almanac lane). Thin
    wrapper around _nlp_lane_decision() - see its docstring for the full
    trigger rule.

    Returns (use_nlp: bool, try_classifier: bool, nlp_intent: str|None,
    nlp_score: int, clarification: str|None). At most one of use_nlp /
    try_classifier / (clarification is not None) is ever truthy; all falsy
    means go straight to Gemini. nlp_intent/nlp_score are for logging only -
    whichever weak (or absent) NLP match, if any, existed when the decision
    was made. clarification, when not None, is the "Did you mean X or Y?"
    text /api/chat should return directly instead of dispatching to any lane.
    """
    return _nlp_lane_decision(question, role)


# Post-scoring adjustment, NOT a change to score_intent()'s general scoring
# (that function has no DB access and stays subject-blind, as it should).
# "how many teachers teach math" ties teacher_count_by_subject/total_teachers
# 5-5 in rank_intents() even though "math" resolves to a real subject in the
# DB - total_teachers has no subject slot to use that evidence with, so it
# shouldn't be able to tie (or win) against an intent that does once a real
# subject was actually named. Deliberately narrow: only the intents whose
# handler actually looks up a subject via extract_subject_from_question()
# are "subject slot" intents; everything else in a tie gets penalized.
SUBJECT_SLOT_INTENTS = {"teacher_count_by_subject", "subject_teacher", "school_wide_subject_teacher"}
SUBJECT_MISMATCH_PENALTY = 3


def _apply_subject_scoring_adjustment(ranked, question):
    """Re-scores `ranked` (rank_intents() output) when a real subject is
    named in `question`: any intent without a subject slot is penalized so
    a subject-slot intent already in the running wins outright instead of
    tying. A no-op whenever `ranked` has no subject-slot/subject-blind mix
    to disambiguate, or no real subject is actually named (a bare "how many
    teachers teach?" must still fall through to its existing ambiguity
    clarification - this only fires once a subject was genuinely resolved
    against the DB, not on every principal question)."""
    subject_slot_present = any(name in SUBJECT_SLOT_INTENTS for name, _ in ranked)
    subject_blind_present = any(name not in SUBJECT_SLOT_INTENTS for name, _ in ranked)
    if not (subject_slot_present and subject_blind_present):
        return ranked
    if not extract_subject_from_question(question, _known_subject_names()):
        return ranked

    adjusted = [
        (name, score - SUBJECT_MISMATCH_PENALTY if name not in SUBJECT_SLOT_INTENTS else score)
        for name, score in ranked
    ]
    adjusted.sort(key=lambda pair: -pair[1])
    return adjusted


def _nlp_lane_decision(question, role):
    """
    Core routing decision behind use_nlp_lane() (plain bool) and
    get_routing_decision() (the full tuple) - one implementation so they
    can't drift apart.

    Returns (use_nlp: bool, try_classifier: bool, nlp_intent: str|None,
    nlp_score: int, clarification: str|None).

    AMBIGUITY GUARD: a raw top score from rank_intents() isn't trusted on
    its own anymore. It must clear NLP_SCORE_FLOOR (too weak otherwise -
    treated as no match, same as a genuine zero) AND beat the runner-up by
    NLP_MARGIN_THRESHOLD (otherwise it's a coin-flip tie, not a confident
    winner - "who is my teacher?" ties identity/subject_teacher at 1 each,
    "how many teachers teach?" ties teacher_count_by_subject/total_teachers
    at 5 each; list order used to silently decide both). A margin failure
    returns a "Did you mean X or Y?" clarification instead of guessing -
    checked BEFORE the phrase-match/pronoun/almanac logic below, since even
    a phrase-backed score-3+ match must still clear this first.

    Asks NLP directly whether it recognizes the question as one of its own
    intents for this role, rather than gating on PERSONAL_SIGNALS pronouns
    first - that missed pronoun-free personal questions like "mondays
    periods". The general-knowledge check is skipped for personal-pronoun'd
    wording, so "what's my exam schedule" isn't misclassified as general
    knowledge just because it contains the phrase "exam schedule".

    Two-part fix for a recurring collision ("exam schedule" vs. timetable,
    "teachers day" vs. subject_teacher - an ambiguous word shared between a
    personal intent's keywords and real almanac content): nlp_helpers.py's
    score_intent() won't award a point for a bare AMBIGUOUS_KEYWORDS match
    with no phrase and no personal signal. Here, even when NLP still finds
    a weak (keyword-only) match, a STRONG competing almanac match (>=
    ALMANAC_STRONG_MATCH_SCORE, >= the NLP score) wins the tie - checked
    against the almanac's actual current content, so a future addition
    ("Founders' Day") is automatically protected with no code change.

    CLASSIFIER TRIGGER: try_classifier is True only when NEITHER lane is
    confident - NLP's score is zero or weak AND the almanac has no strong
    competing match. Real gap fixed here: the original version only set
    try_classifier on a weak-but-NONZERO score, skipping genuine zeros by
    design - but testing showed the most common real failure mode is
    exactly a genuine zero. "whats todays timetable" scores a TRUE ZERO,
    not weak-nonzero: "timetable" is AMBIGUOUS_KEYWORDS-blocked with no
    phrase and no personal-pronoun wording, so score_intent() discards it
    entirely. This revision treats zero and weak the same way for
    eligibility, while still requiring the almanac to also not be
    confident - keeps the cost-conscious "only genuinely uncertain
    questions get an extra Groq call" design.

    Personal-pronoun'd wording with any real (even weak) NLP match is still
    trusted outright, unchanged (e.g. "how many days was I absent" -> weak
    attendance match, answered correctly today). A pronoun'd question that
    finds NOTHING now goes through the same "neither lane confident" check
    as pronoun-free ones, instead of a doomed detour through the NLP lane
    first that would just fail again and reach Gemini one step later anyway.
    """
    if is_pure_greeting(question):
        return True, False, None, 0, None

    # Policy-frame language wins outright, even over a possessive pronoun -
    # see POLICY_FRAME_SIGNALS/is_policy_framed() above. Checked before the
    # personal-pronoun protection below on purpose: "what is my attendance
    # policy" must not reach the personal attendance-lookup handler just
    # because it says "my".
    if is_policy_framed(question):
        return False, False, None, 0, None

    has_personal_pronoun = role not in DB_FIRST_ROLES and is_personal_question(question)

    if not has_personal_pronoun and is_general_knowledge_question(question):
        return False, False, None, 0, None

    ranked = rank_intents(question, ROLE_PERSONAL_INTENTS.get(_effective_role(role), []))
    ranked = _apply_subject_scoring_adjustment(ranked, question)
    intent, nlp_score = ranked[0] if ranked else (None, 0)

    if intent is not None:
        if nlp_score < NLP_SCORE_FLOOR:
            # Too weak to trust at all - fall through exactly as a genuine
            # zero score would (the classifier/almanac logic below still
            # gets its normal chance), rather than NLP guessing on it.
            intent, nlp_score = None, 0
        elif len(ranked) > 1 and (nlp_score - ranked[1][1]) < NLP_MARGIN_THRESHOLD:
            return False, False, None, 0, _ambiguity_clarification(ranked[0][0], ranked[1][0])

    # Phrase-backed match (score >= 3): trust it outright, no need to even
    # check the almanac - a real phrase match is strong enough evidence on
    # its own (and nlp_helpers.py's AMBIGUOUS_KEYWORDS fix already keeps
    # bare-keyword matches honest).
    if intent is not None and nlp_score >= NLP_PHRASE_MATCH_SCORE:
        return True, False, intent, nlp_score, None

    if has_personal_pronoun and intent is not None:
        return True, False, intent, nlp_score, None

    almanac_score = almanac_top_score(question)
    almanac_confident = almanac_score >= ALMANAC_STRONG_MATCH_SCORE and almanac_score >= nlp_score

    if intent is not None:
        # Weak (keyword-only) match, no personal-pronoun protection. If the
        # almanac ALSO isn't confidently ahead, neither lane is sure -
        # classifier's turn. Otherwise the weak match wins on its own
        # merits, same as before.
        if almanac_confident:
            return False, True, intent, nlp_score, None
        return True, False, intent, nlp_score, None

    # intent is None: a genuine zero, with or without personal-pronoun
    # wording. A confident almanac match still wins outright with no need
    # for the classifier; otherwise this is exactly the "neither lane
    # confident" gap the classifier exists to catch.
    if almanac_score >= ALMANAC_STRONG_MATCH_SCORE:
        return False, False, None, 0, None

    return False, True, None, 0, None

app = Flask(__name__)

# Secret key signs the session cookie - a hardcoded value here would let anyone
# who reads the source forge a session for any user/role. Set FLASK_SECRET_KEY
# in the environment before deploying; falls back to a random key for local dev
# (sessions won't survive a restart without the env var set, which is fine locally).
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
if not os.environ.get("FLASK_SECRET_KEY"):
    print("WARNING: FLASK_SECRET_KEY not set - using a random key for this run only. "
          "Set FLASK_SECRET_KEY before deploying so sessions survive restarts.")

_is_production = os.environ.get("FLASK_ENV") == "production"

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_is_production,
)

# Outside production, don't let the browser cache static files. Flask defaults
# to 12 hours, which means edits to app.js silently don't show up until the
# cache expires or you hard-refresh. Production keeps the default caching.
#
# TEMPLATES_AUTO_RELOAD matters just as much: Jinja normally only re-reads
# templates when debug is on, and debug is off by default here for security.
# Without this, editing index.html does nothing until the server is restarted.
if not _is_production:
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.config["TEMPLATES_AUTO_RELOAD"] = True

# =========================================================
# LOGIN RATE LIMITING
# Tracks failed attempts per (IP, username) so a script can't brute-force
# passwords. In-memory only - resets on server restart, which is acceptable
# for this scale (single school, small user base).
# =========================================================
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW = 300  # seconds
_failed_logins = {}


def _login_rate_limited(key):
    entry = _failed_logins.get(key)
    if not entry:
        return False
    count, window_start = entry
    if time.time() - window_start > LOGIN_ATTEMPT_WINDOW:
        del _failed_logins[key]
        return False
    return count >= LOGIN_ATTEMPT_LIMIT


def _record_failed_login(key):
    count, window_start = _failed_logins.get(key, (0, time.time()))
    if time.time() - window_start > LOGIN_ATTEMPT_WINDOW:
        count, window_start = 0, time.time()
    _failed_logins[key] = (count + 1, window_start)


def _clear_failed_logins(key):
    _failed_logins.pop(key, None)


def _login_retry_after(key):
    """Seconds left until this key's lockout window expires. Rounded up
    (not down) so the frontend countdown never hits 0 while still locked."""
    entry = _failed_logins.get(key)
    if not entry:
        return 0
    _, window_start = entry
    remaining = LOGIN_ATTEMPT_WINDOW - (time.time() - window_start)
    return max(0, math.ceil(remaining))


# =========================================================
# DATABASE HELPER
# =========================================================
def get_db():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as e:
        return None


def query(sql, params=None, fetch=False, many=False):
    """Run a SQL query safely. Returns result or None on error."""
    conn = get_db()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params or ())
        if fetch:
            result = cursor.fetchall() if many else cursor.fetchone()
        else:
            conn.commit()
            result = cursor.lastrowid
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        print(f"DB error: {e}")
        return None


def _chatbot_enabled():
    """Reads the system_settings.chatbot_enabled flag (stored as the
    string 'true'/'false', not a real boolean column - see
    setup_database.py). Missing row or a DB error both default to
    enabled: a query failure here means the DB is unreachable, which
    already breaks every other feature regardless of this flag, so there's
    no real "fail safe by disabling" benefit to defaulting the other way -
    and it matches the seeded default (generate_dummy_data.py inserts
    'true')."""
    result = query("SELECT value FROM system_settings WHERE `key`='chatbot_enabled'", fetch=True)
    if result is None:
        return True
    return result[0] == "true"


# =========================================================
# HEALTH CHECK
# Deliberately outside AUTH ROUTES and doesn't touch session/login at all -
# an external uptime monitor needs to reach this with no credentials. Uses
# get_db() directly (not the query() helper) so a bad SELECT 1 can't be
# confused with query()'s normal "no matching row" None return - here, ANY
# failure (can't connect, or the query itself errors) means unhealthy.
# Also deliberately never touches the NLP or Gemini/Groq lanes, so pinging
# this can't burn a quota-limited AI API call or run any DB write.
# =========================================================
@app.route("/healthz")
def healthz():
    conn = get_db()
    if conn is None:
        return jsonify({"status": "error", "detail": "database unreachable"}), 503

    try:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        cursor.close()
        conn.close()
    except mysql.connector.Error as e:
        print(f"[HEALTHZ] DB error: {e}")
        return jsonify({"status": "error", "detail": "database unreachable"}), 503

    return jsonify({"status": "ok"}), 200


# =========================================================
# AUTH ROUTES
# =========================================================
@app.route("/")
def index():
    if "user_id" not in session:
        return render_template("index.html", logged_in=False)
    return render_template("index.html", logged_in=True)


def _build_profile(role, linked_id):
    """Fetch display name + role-specific profile fields for the session.
    Returns (display_name, profile_dict), or None if the linked record is gone."""
    if role == "student":
        info = query(
            "SELECT name, class, roll_no, attendance_pct, fees_status FROM students WHERE student_id=%s",
            (linked_id,), fetch=True
        )
        if not info:
            return None
        return info[0], {
            "name": info[0], "class": info[1],
            "roll_no": info[2], "attendance": float(info[3]),
            "fees": info[4]
        }

    elif role == "teacher" or role in HOD_LIKE_ROLES:
        # hod/vice_principal log in AS a teacher record (their own linked_id
        # is their own teacher_id, same mechanism as a plain teacher login) -
        # same profile shape, plus their department name.
        info = query(
            """SELECT te.name,
                      COALESCE(GROUP_CONCAT(DISTINCT s.subject_name ORDER BY s.subject_name SEPARATOR ', '), ''),
                      te.classes_assigned, d.name
               FROM teachers te
               LEFT JOIN teacher_subjects ts ON te.teacher_id = ts.teacher_id
               LEFT JOIN subjects s ON ts.subject_id = s.subject_id
               LEFT JOIN departments d ON te.department_id = d.department_id
               WHERE te.teacher_id=%s
               GROUP BY te.teacher_id, te.name, te.classes_assigned, d.name""",
            (linked_id,), fetch=True
        )
        if not info:
            return None
        return info[0], {
            "name": info[0], "subject": info[1],
            "classes": info[2], "department": info[3]
        }

    else:  # principal, assistant_principal - identical access, distinct label
        label = "Assistant Principal" if role == "assistant_principal" else "Principal"
        return label, {"name": label}


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "")

    if not username or not password:
        return jsonify({"success": False, "error": "Please enter username and password."})

    # Rate-limit by IP+username so a script can't brute-force passwords
    rate_key = f"{request.remote_addr}:{username.lower()}"
    if _login_rate_limited(rate_key):
        retry_after = _login_retry_after(rate_key)
        return jsonify({
            "success": False,
            "error": "Too many failed attempts. Please try again later.",
            "retry_after": retry_after
        }), 429

    # Looked up directly here (not via the shared query() helper) so a DB
    # connection failure can be told apart from "no matching user" - query()
    # returns None for both, which is exactly the ambiguity that caused a
    # dead database to be shown to users as "wrong password".
    conn = get_db()
    if conn is None:
        # DB itself is unreachable - this is not a failed credential attempt,
        # so it must NOT count toward the lockout, and it gets its own honest
        # message rather than the generic invalid-credentials one.
        return jsonify({
            "success": False,
            "error": "Unable to connect right now, please try again in a moment."
        }), 503

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, password_hash, role, linked_id FROM users WHERE username=%s",
            (username,)
        )
        result = cursor.fetchone()
        cursor.close()
        conn.close()
    except mysql.connector.Error as e:
        print(f"DB error during login: {e}")
        return jsonify({
            "success": False,
            "error": "Unable to connect right now, please try again in a moment."
        }), 503

    # Same generic error whether the username doesn't exist or the password is
    # wrong - distinguishing them lets an attacker enumerate valid usernames.
    # This still applies here: only an actual DB failure (above) gets a
    # different message, not "username exists but password is wrong" vs.
    # "username doesn't exist" - those two remain identical.
    if not result or not verify_password(password, result[1]):
        _record_failed_login(rate_key)
        return jsonify({"success": False, "error": "Invalid username or password."})

    _clear_failed_logins(rate_key)
    user_id, stored_hash, role, linked_id = result

    built = _build_profile(role, linked_id)
    if not built:
        return jsonify({"success": False, "error": "Could not load profile."})
    display_name, profile = built

    session["user_id"] = user_id
    session["role"] = role
    session["linked_id"] = linked_id
    session["username"] = username
    session["display_name"] = display_name

    return jsonify({"success": True, "role": role, "profile": profile})


@app.route("/api/me")
def me():
    """Lets the frontend restore the chat page after a refresh instead of
    always showing the login screen, since the session cookie itself
    persists fine across refreshes - the old frontend just never checked it."""
    if "user_id" not in session:
        return jsonify({"logged_in": False})

    built = _build_profile(session.get("role"), session.get("linked_id"))
    if not built:
        session.clear()
        return jsonify({"logged_in": False})

    display_name, profile = built
    return jsonify({"logged_in": True, "role": session.get("role"), "profile": profile})


# =========================================================
# NOTIFICATIONS BADGE — visual only, not an NLP feature (see
# handle_notices() for the actual chat-facing notices intent). Counts
# recent, role-visible notices the user hasn't checked yet; "seen" is a
# single per-user timestamp (users.last_seen_notices_at), not per-notice
# read tracking - all this badge needs is a count, not which ones.
# =========================================================
@app.route("/api/notices-count")
def notices_count():
    if "user_id" not in session:
        return jsonify({"count": 0})

    role = session.get("role")
    visible_roles = _notice_visible_roles(role)
    conditions = ["target_roles='all'"] + ["FIND_IN_SET(%s, target_roles)"] * len(visible_roles)
    result = query(
        f"""SELECT COUNT(*) FROM notices n
            WHERE n.date_posted >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            AND n.notice_id > (SELECT last_seen_notice_id FROM users WHERE user_id=%s)
            AND ({' OR '.join(conditions)})""",
        tuple([session.get("user_id")] + list(visible_roles)),
        fetch=True
    )
    return jsonify({"count": result[0] if result else 0})


@app.route("/api/notices-seen", methods=["POST"])
def notices_seen():
    """Marks every notice posted so far as seen (bumps last_seen_notice_id
    up to the current max) - called by the frontend when the user engages
    with the notifications badge (see static/app.js), not tied to the NLP
    notices intent firing internally. MAX(notice_id) defaults to 0 via
    COALESCE when the table is empty, matching the column's own default -
    an empty table already means "nothing to have seen"."""
    if "user_id" not in session:
        return jsonify({"success": False}), 401

    query(
        """UPDATE users SET last_seen_notice_id = COALESCE((SELECT MAX(notice_id) FROM notices), 0)
           WHERE user_id=%s""",
        (session.get("user_id"),)
    )
    return jsonify({"success": True})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# =========================================================
# CLARIFICATION MEMORY
# A handler that can't extract what it needs (no subject/class/teacher
# named) asks a clarifying question instead of answering. Without this,
# the student's next message - "umm math" - was routed as a brand new,
# unrelated question and got a wrong answer instead of completing the one
# they were actually answering.
#
# Generalized from an earlier 4-intent version: registers every handler
# that can ask a clarifying question (all 8 - grep app.py for
# `return "Which` to reconfirm the full set if a new one is ever added),
# and stores richer state (resolved_slots/original_message/expires_at, not
# just intent+role) so the resume path can do more than blindly retry the
# bare follow-up text - see _resume_clarification_reply()'s merge-fallback
# and _is_topic_switch() below.
#
# Still fundamentally single-turn by design: pending_clarification is
# popped from the session the moment the next message arrives, whether or
# not it resolves anything, so a stale clarification can never linger into
# a later, unrelated conversation. expires_at is a second, time-based
# backstop on top of that (a follow-up minutes late shouldn't resume
# either), not a replacement for it.
# =========================================================
CLARIFICATION_TTL_SECONDS = 300

CLARIFICATION_CONFIG = {
    "subject_teacher": {
        "role": "student",
        "prompt": "Which subject would you like to know the teacher for?",
    },
    "school_wide_subject_teacher": {
        "role": "principal",
        "prompt": "Which subject would you like to know the teacher for?",
    },
    "class_teacher_lookup": {
        "role": "principal",
        "prompt": "Which class would you like to check? Please include the class (e.g. 10-A).",
    },
    "teacher_schedule_lookup": {
        "role": "principal",
        "prompt": "Which teacher's schedule would you like to see?",
    },
    "teacher_count_by_subject": {
        "role": "principal",
        "prompt": "Which subject would you like the teacher count for?",
    },
    "teacher_location": {
        "role": "principal",
        "prompt": "Which teacher would you like to locate? Please include their name.",
    },
    "classroom_occupant": {
        "role": "principal",
        "prompt": "Which class would you like me to check who's in right now? Please include the class (e.g. 10-A).",
    },
    "class_timetable_lookup": {
        "role": "principal",
        "prompt": "Which class's timetable would you like to see? Please include the class (e.g. 10-A).",
    },
}
# (role, exact clarifying text) -> intent. subject_teacher and school_wide_
# subject_teacher share the same prompt text, but never the same role
# (student-only vs. principal-only), so the pair stays unambiguous. Every
# OTHER prompt text here must be unique within a role, or this reverse
# lookup can't tell two pending intents apart - see handle_classroom_
# occupant()'s comment for the one collision that was actually found
# (against class_teacher_lookup) and reworded to fix.
_CLARIFICATION_BY_PROMPT = {
    (cfg["role"], cfg["prompt"]): intent for intent, cfg in CLARIFICATION_CONFIG.items()
}
# Bare set of the prompt text alone (role-independent) - used by the
# classifier lane below to tell "the classifier picked an intent AND the
# handler actually answered" apart from "the classifier picked an intent
# but the handler still had nothing to work with", so Learned Phrases only
# logs genuine deliveries, not a clarifying question in disguise.
_CLARIFICATION_PROMPTS = {cfg["prompt"] for cfg in CLARIFICATION_CONFIG.values()}


def _resume_clarification_reply(intent, question, linked_id, original_message):
    """
    Re-runs the ORIGINAL handler on the follow-up message first, so it
    re-extracts its slot the exact same way it did the first time - no
    separate extraction logic here to keep in sync with each handler's own.

    If that alone doesn't resolve it (the same clarifying prompt comes back
    unchanged), retries once more with original_message merged in ahead of
    the follow-up ("umm the science one" alone might not read as a subject
    to extract_subject_from_question(), but "who teaches me umm the
    science one" - the original question plus the follow-up - might).
    """
    def dispatch(q):
        if intent == "subject_teacher":
            return handle_subject_teacher(q, linked_id, _known_subject_names())
        if intent == "school_wide_subject_teacher":
            return handle_school_wide_subject_teacher(q)
        if intent == "class_teacher_lookup":
            return handle_class_teacher_lookup(q)
        if intent == "teacher_schedule_lookup":
            return handle_teacher_schedule_lookup(q)
        if intent == "teacher_count_by_subject":
            return handle_teacher_count_by_subject(q)
        if intent == "teacher_location":
            return handle_teacher_location(q)
        if intent == "classroom_occupant":
            return handle_classroom_occupant(q)
        if intent == "class_timetable_lookup":
            return handle_class_timetable_lookup(q)
        return None

    resumed = dispatch(question)
    config = CLARIFICATION_CONFIG.get(intent)
    if config and resumed == config["prompt"] and original_message:
        resumed = dispatch(f"{original_message} {question}")
    return resumed


def _is_topic_switch(question, role, pending_intent):
    """
    True if the follow-up looks like its own fresh question rather than a
    bare answer to a pending clarification - a '?' together with a
    confident (phrase-backed, score >= NLP_PHRASE_MATCH_SCORE) match for
    some OTHER intent is strong evidence the user moved on to something
    else, not just answering what was asked.

    Checked BEFORE attempting to resume, so e.g. "what's my exam schedule
    for science?" (a fresh, self-contained exam question) doesn't get
    wrongly swallowed by a pending subject_teacher clarification just
    because "science" also happens to be a valid subject name that
    handler's own extraction would have matched.
    """
    if "?" not in question:
        return False
    intent, score = detect_intent_with_score(question, ROLE_PERSONAL_INTENTS.get(_effective_role(role), []))
    return intent is not None and intent != pending_intent and score >= NLP_PHRASE_MATCH_SCORE


def _maybe_set_pending_clarification(role, reply, original_message):
    """Called after every NLP-lane/classifier-lane reply - if it's one of
    the exact clarifying prompts above, remember what was asked (plus the
    ORIGINAL message, for _resume_clarification_reply()'s merge-fallback)
    so the NEXT message can try to complete it instead of starting fresh."""
    intent = _CLARIFICATION_BY_PROMPT.get((role, reply))
    if intent:
        session["pending_clarification"] = {
            "intent": intent,
            "role": role,
            "resolved_slots": {},
            "original_message": original_message,
            "expires_at": time.time() + CLARIFICATION_TTL_SECONDS,
        }
        print(f'[CLARIFICATION SET] intent={intent} role={role}')


def _dispatch_to_role_handler(role, question, linked_id, forced_intent=None):
    """Routes to the right answer_*() for this role, applying the role-
    hierarchy rules once instead of re-deciding them at both /api/chat call
    sites: assistant_principal is identical to principal; hod and
    vice_principal both get everything a teacher sees plus their own
    department-scoped intents (see HOD_LIKE_ROLES/HOD_DEPARTMENT_INTENTS)."""
    if role == "student":
        return answer_student(question, linked_id, forced_intent=forced_intent)
    if role == "teacher" or role in HOD_LIKE_ROLES:
        extra = HOD_DEPARTMENT_INTENTS if role in HOD_LIKE_ROLES else None
        return answer_teacher(question, linked_id, forced_intent=forced_intent, extra_intents=extra, role=role)
    return answer_principal(question, forced_intent=forced_intent)  # principal, assistant_principal


# =========================================================
# PRINCIPAL-ONLY KILL SWITCH
# A hold-to-confirm button in the chatbot UI, principal-only, that takes
# the whole bot offline for every user at once - an emergency stop, not a
# per-role access control (deliberately checks the LITERAL "principal"
# role here, not _effective_role()/PRINCIPAL_LIKE_ROLES - assistant_principal
# has identical DATA access elsewhere in this file, but this is a distinct,
# narrower authority the task explicitly scoped to principal only).
# Re-enabling is dashboard-only (see dashboard.py's System Status page) -
# this endpoint only ever turns it off.
# =========================================================
@app.route("/api/system-status")
def system_status():
    """No auth - the frontend needs to check this before a user has even
    logged in (on every page load) to decide whether to show the disabled
    banner."""
    return jsonify({"enabled": _chatbot_enabled()})


@app.route("/api/kill-switch", methods=["POST"])
def kill_switch():
    if session.get("role") != "principal":
        return jsonify({"error": "Forbidden."}), 403

    data = request.get_json(silent=True) or {}
    if data.get("action") != "disable":
        return jsonify({"error": "Invalid action."}), 400

    query("UPDATE system_settings SET value='false' WHERE `key`='chatbot_enabled'")
    query(
        "INSERT INTO system_logs (action, performed_by) VALUES (%s, %s)",
        ("disable", session.get("user_id"))
    )
    return jsonify({"success": True, "enabled": False})


# =========================================================
# CHAT ROUTE — the NLP brain
# =========================================================
@app.route("/api/chat", methods=["POST"])
def chat():
    # Absolute first check, before session/auth, NLP, DB lookups, or any
    # LLM call - a globally disabled bot answers nothing for anyone,
    # regardless of who's asking or whether they're even logged in.
    if not _chatbot_enabled():
        return jsonify({
            "reply": "YaraBot is temporarily unavailable. Please contact the school administration.",
            "disabled": True
        })

    if "user_id" not in session:
        return jsonify({"error": "Not logged in."}), 401

    data = request.get_json()
    question = data.get("message", "").strip()
    role = session.get("role")
    linked_id = session.get("linked_id")

    if not question:
        return jsonify({"reply": "Please type a question first."})

    question_lower = question.lower()

    # Check for a pending clarification BEFORE normal routing - popped
    # immediately either way (success or failure), so it can only ever
    # affect this one follow-up message. On failure, deliberately don't
    # return here - falls through to normal routing below so an unrelated
    # message right after a clarifying question still gets answered.
    pending = session.pop("pending_clarification", None)
    if pending and pending.get("role") == role:
        pending_intent = pending.get("intent")
        if time.time() > pending.get("expires_at", 0):
            print(f'[CLARIFICATION SKIPPED] intent={pending_intent} role={role} '
                  f'follow_up={question_lower!r} reason=expired -> falling through to normal routing')
        elif _is_topic_switch(question_lower, role, pending_intent):
            print(f'[CLARIFICATION SKIPPED] intent={pending_intent} role={role} '
                  f'follow_up={question_lower!r} reason=topic-switch -> falling through to normal routing')
        else:
            config = CLARIFICATION_CONFIG.get(pending_intent)
            resumed = (_resume_clarification_reply(
                           pending_intent, question_lower, linked_id, pending.get("original_message"))
                       if config else None)
            if resumed is not None and resumed != config["prompt"]:
                print(f'[CLARIFICATION RESUMED] intent={pending_intent} role={role} '
                      f'follow_up={question_lower!r}')
                return jsonify({"reply": resumed})
            print(f'[CLARIFICATION EXPIRED] intent={pending_intent} role={role} '
                  f'follow_up={question_lower!r} -> falling through to normal routing')

    # ROUTING:
    # Lane 0: Ambiguity clarification - two (or more) intents scored too
    #         close together to trust a silent winner (see
    #         _nlp_lane_decision()'s NLP_SCORE_FLOOR/NLP_MARGIN_THRESHOLD).
    #         Returns the "Did you mean X or Y?" text directly, no DB call,
    #         no AI call.
    # Lane 1: Personal question → NLP + MySQL (private, personal data).
    #         Already instant (a single DB lookup), so it stays a normal
    #         JSON response - streaming would add complexity for no benefit
    #         on an answer that arrives in one piece anyway.
    # Lane 2: AI classifier - the "neither lane is confident" last line of
    #         defense (see get_routing_decision()/_nlp_lane_decision()'s
    #         docstring) before giving up to the generic fallback. Also a
    #         plain JSON response, same reasoning as Lane 1.
    # Lane 3: General question → Gemini + almanac (school-wide info).
    #         Gemini calls take a few seconds; this lane streams so the
    #         reply appears incrementally instead of the user staring at
    #         the typing indicator for the whole round trip.

    use_nlp, try_classifier, weak_intent, weak_score, clarification = get_routing_decision(question_lower, role)

    if clarification is not None:
        print(f'[AMBIGUITY CLARIFICATION] Question: {question_lower} -> {clarification!r}')
        return jsonify({"reply": clarification})

    if use_nlp:
        # Personal lane — use NLP + MySQL
        print(f'[NLP LANE] Question: {question_lower}')
        reply = _dispatch_to_role_handler(role, question_lower, linked_id)

        # If NLP couldn't recognize it even with personal words, fall back
        # to Gemini as a last resort. The answer is now genuinely coming
        # from Gemini, so it streams too, same as the general lane below.
        if "didn't quite get" in reply or "didn't understand" in reply:
            # Plain ASCII "->", not "→" - the unicode arrow crashes this
            # print() with UnicodeEncodeError on Windows (stdout defaults
            # to cp1252), which took down every NLP-miss request with a 500.
            print(f'[NLP MISS -> GEMINI FALLBACK] Question: {question_lower}')
            return stream_gemini_reply(question_lower, role)

        _maybe_set_pending_clarification(role, reply, question_lower)
        return jsonify({"reply": reply})

    # CLASSIFIER LANE: fires whenever get_routing_decision() found NEITHER
    # lane confident - NLP's best score was zero or weak AND the almanac
    # had no strong competing match either. This is the genuine last line
    # of defense before the generic Gemini "I don't have that information"
    # fallback, not a narrow edge case - a question either lane already
    # confidently resolved never reaches here at all.
    if try_classifier:
        # classify_personal_intent() already catches its own errors and
        # returns None on any failure - this try/except is a second,
        # belt-and-suspenders layer at the call site itself. Real bug found
        # via live testing: a monkeypatched classifier that raised directly
        # (simulating a failure mode outside that function's own try/except -
        # e.g. a future bug in it, or an exception type genuinely missed)
        # took down the whole /api/chat request with an unhandled 500
        # instead of degrading to Gemini, which is exactly the "must fail
        # open, never break the existing flow" guarantee this lane is
        # required to hold regardless of what goes wrong inside the call.
        try:
            classified_intent = classify_personal_intent(
                question_lower, role, ROLE_PERSONAL_INTENTS.get(_effective_role(role), [])
            )
        except Exception as e:
            print(f'[CLASSIFIER LANE ERROR] Question: {question_lower} -> {e}')
            classified_intent = None

        if classified_intent is not None:
            print(f'[CLASSIFIER LANE] Question: {question_lower} -> {classified_intent} '
                  f'(NLP: {weak_intent!r} score {weak_score}, almanac not confident)')
            reply = _dispatch_to_role_handler(role, question_lower, linked_id, forced_intent=classified_intent)
            _maybe_set_pending_clarification(role, reply, question_lower)

            # Learned Phrases: log only a genuine delivery - the classifier
            # picked an intent AND the handler actually answered, not a
            # clarifying question the handler still had to ask right back.
            if reply not in _CLARIFICATION_PROMPTS:
                log_learned_phrase(question_lower, classified_intent, role)

            return jsonify({"reply": reply})
        # Classifier picked NONE, or the call failed/errored - fail open,
        # fall through to the exact same Gemini/almanac lane as today.

    # General lane — use Gemini + almanac, streamed
    return stream_gemini_reply(question_lower, role)


def stream_gemini_reply(question, role):
    """
    Wraps gemini_answer_stream() as an SSE response so app.js can read
    chunks incrementally instead of waiting for the whole reply.

    Each chunk ships as its own "data: {...}\\n\\n" line, JSON-encoded so a
    literal newline in the text can't be mistaken for the blank-line
    separator. A final "data: [DONE]\\n\\n" makes the end explicit rather
    than relying on the connection closing.

    role -> _notice_visible_roles(role) before crossing into gemini_rag.py,
    not the raw role itself - that module has no concept of this app's
    role hierarchy (hod/vice_principal/assistant_principal) and shouldn't
    need one just to know which notices are groundable for this question;
    it only ever sees a plain set of target_roles tokens to match against.
    """
    visible_roles = _notice_visible_roles(role)

    def generate():
        for chunk in gemini_answer_stream(question, visible_roles):
            yield f"data: {json.dumps({'chunk': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    # Prevent a reverse proxy (e.g. nginx, relevant once this is deployed)
    # from buffering the whole response before sending it - that would
    # silently turn "streaming" back into "wait for everything, then show
    # it all at once".
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


# =========================================================
# NLP EXPANSION — shared extraction helpers
# extract_day_from_question() and estimate_current_period_number() are used
# across all three roles (student/teacher/principal) - implemented ONCE
# here rather than duplicated per role.
# =========================================================
DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Common name-prefix titles to skip when matching a teacher's name against a
# question - this school's teacher names are stored WITH the title baked in
# (e.g. "Mr Imdadullah"), so treating the title as a real "first name" word
# would never match anything a user actually types.
TEACHER_NAME_TITLES = {"mr", "mrs", "ms", "miss", "dr", "mx"}


def extract_day_from_question(question):
    """Returns the day name if mentioned in the question, else None.
    Also recognizes 'today' and converts it to the actual current day name."""
    q = question.lower()
    if "today" in q:
        return datetime.datetime.now().strftime("%A").lower()
    for day in DAY_NAMES:
        if day in q:
            return day
    return None


def estimate_current_period_number():
    """
    Rough estimate of which period number is happening right now, based on
    the current time (period 1 starts ~8am, each period ~1 hour).

    Known limitation, intentional for now: the database doesn't store real
    period start/end times, so this is a heuristic, not a fix. Inventing
    more precise logic without real timing data in the schema would just be
    guessing more elaborately - if exact period times are needed later,
    that means adding start/end columns to the timetable table, not
    tightening this estimate further.
    """
    now = datetime.datetime.now()
    return max(1, now.hour - 7)


# Casual shorthand a student might type ("math", "bio", "cs") that the
# tier-3 substring check below can't reach on its own - either the word's
# under its 4-character floor ("bio", "cs", "eng", "soc") or it just isn't
# a literal substring of the real name at all ("maths" isn't a substring
# of "mathematics" - the trailing "s" breaks it). Multiple possible
# targets per real-world alias are listed on purpose; _subject_alias_map()
# below keeps only the ones this school's DB actually has, so an alias for
# a subject not currently taught here (no "Physical Education" today)
# stays inert instead of getting hardcoded as if it existed.
_SUBJECT_ALIAS_HINTS = {
    "math": "Mathematics", "maths": "Mathematics",
    "bio": "Biology",
    "chem": "Chemistry",
    "phy": "Physics",
    "cs": "Computer Science", "comp sci": "Computer Science", "compsci": "Computer Science",
    "soc": "Social Studies", "socials": "Social Studies", "sst": "Social Studies",
    "eng": "English",
    "pe": "Physical Education", "gym": "Physical Education", "phys ed": "Physical Education",
    "geo": "Geography",
    "hist": "History",
}


def _subject_alias_map(known_subjects):
    """_SUBJECT_ALIAS_HINTS filtered down to aliases whose target subject is
    actually in known_subjects, and remapped to the DB's real casing."""
    lower_lookup = {s.lower(): s for s in known_subjects}
    return {
        alias: lower_lookup[canonical.lower()]
        for alias, canonical in _SUBJECT_ALIAS_HINTS.items()
        if canonical.lower() in lower_lookup
    }


def extract_subject_from_question(question, known_subjects):
    """
    Returns a subject name if one is mentioned in the question.
    known_subjects: list of actual subject names from the DB (fetch once,
    pass in) so we're not guessing/hardcoding subjects.

    Three tiers, found via live testing:

    1. Full subject name present in the question -> prefer the LONGEST
       match. This school's data has both "Science" and "Computer Science"
       as distinct subjects, and "Science" is a literal substring of
       "Computer Science" - without preferring the longer match, "how many
       teachers teach computer science" silently answered about plain
       Science instead, depending on arbitrary DB row order.
    2. Only if NO full name matched: the curated alias map (_SUBJECT_ALIAS_
       HINTS) - "math"/"cs"/"bio"/"soc"/"eng" and friends. Matched with
       word boundaries so a short alias like "cs" can't fire off a
       substring inside an unrelated word ("process", "cost").
    3. Only if NEITHER of the above matched: fall back to a shortened/
       informal form the user typed that isn't in the alias map either -
       any question word of at least 4 characters that's a substring of
       the subject name. Gated strictly behind tiers 1-2 finding nothing:
       an earlier version checked this unconditionally alongside tier 1,
       and a plain "science" question got hijacked into "Computer Science"
       because the word "science" is also a substring of THAT longer name.
    """
    q = question.lower()

    full_matches = [s for s in known_subjects if s.lower().strip() in q]
    if full_matches:
        return max(full_matches, key=len)

    alias_map = _subject_alias_map(known_subjects)
    alias_matches = [
        canonical for alias, canonical in alias_map.items()
        if re.search(r'\b' + re.escape(alias) + r'\b', q)
    ]
    if alias_matches:
        return max(set(alias_matches), key=len)

    q_words = q.split()
    partial_matches = [
        s for s in known_subjects
        if any(len(w) >= 4 and w in s.lower() for w in q_words)
    ]
    if partial_matches:
        return max(partial_matches, key=len)

    return None


def _teacher_ambiguity_clarification(headline_name, matches):
    """'There are multiple teachers named X. Which one - X (Subject1,
    Subject2), or Y (Subject3)?' - matches is [(teacher_id, name,
    subjects), ...], subjects a comma-joined string (a teacher can teach
    more than one) used as the disambiguator. Confirmed live and real, not
    hypothetical: with ~130 real staff, two different teachers are both
    actually named "Tariq Al-Rashid" in this school's own data."""
    options = ", or ".join(f"{name} ({subjects})" for _, name, subjects in matches)
    return f"There are multiple teachers named {headline_name}. Which one — {options}?"


def extract_teacher_name_from_question(question, known_teachers):
    """
    known_teachers: list of (teacher_id, name, subjects) tuples fetched
    from DB - subjects a comma-joined string of everything that teacher
    teaches, only ever used as opaque disambiguator text here (see
    _teacher_ambiguity_clarification()).

    Checks every word in the stored name, not just the first one, skipping
    common titles. Found via live testing: this school's teacher names are
    stored with a leading title ("Mr Imdadullah"), so the original
    first-word-only check was matching "Mr" as the "first name" and never
    finding the teacher when a user just said their actual name.

    Token-boundary matched, not a bare substring `in` check - a teacher
    named "Ann" was matching inside "annual" ("Where is the annual
    meeting?"), wrongly resolving a location question about a meeting into
    one about a specific teacher.

    Two-tier match, full name checked across ALL teachers before any
    per-word fallback: a specific full-name match ("Krishna Gupta") is
    confident evidence on its own and shouldn't be second-guessed just
    because some OTHER teacher's first name also happens to be "Krishna" -
    only genuine duplicates (two+ teachers matching at the SAME tier) count
    as ambiguous.

    Returns (teacher_id, name, clarification). Exactly one of teacher_id or
    clarification is ever set (both None/None/None on no match at all) -
    silently returning whichever teacher matched first, on a name that
    isn't actually unique, is exactly the kind of guess this project keeps
    finding bugs from.
    """
    q = question.lower()

    full_name_matches = [
        (tid, name, subjects) for tid, name, subjects in known_teachers
        if name.lower().strip() and re.search(r'\b' + re.escape(name.lower().strip()) + r'\b', q)
    ]
    if full_name_matches:
        if len(full_name_matches) == 1:
            tid, name, _ = full_name_matches[0]
            return tid, name, None
        return None, None, _teacher_ambiguity_clarification(full_name_matches[0][1], full_name_matches)

    word_matches = []
    seen_ids = set()
    for tid, name, subjects in known_teachers:
        for word in name.lower().strip().split():
            word = word.strip(".")
            if word and word not in TEACHER_NAME_TITLES and re.search(r'\b' + re.escape(word) + r'\b', q):
                if tid not in seen_ids:
                    word_matches.append((tid, name, subjects))
                    seen_ids.add(tid)
                break

    if not word_matches:
        return None, None, None
    if len(word_matches) == 1:
        tid, name, _ = word_matches[0]
        return tid, name, None
    return None, None, _teacher_ambiguity_clarification(word_matches[0][1], word_matches)


def extract_class_from_question(question):
    """
    Finds a class code mentioned anywhere in the question - an early-years
    standalone code ('Nursery'/'LKG'/'UKG') or a grade-section like '10-A'.
    Shares its pattern with validate_class() (validators.py) and
    has_class_code() (nlp_helpers.py) via GRADE_SECTION_PATTERN/
    EARLY_YEARS_CLASSES - see validators.py for why these three are no
    longer defined independently.
    """
    q_lower = question.lower()
    for code in EARLY_YEARS_CLASSES:
        if re.search(r'\b' + re.escape(code.lower()) + r'\b', q_lower):
            return code

    match = re.search(GRADE_SECTION_PATTERN, question)
    if match:
        return f"{match.group(1)}-{match.group(2).upper()}"
    return None


def _known_subject_names():
    """Fetches the school-wide list of subject names, for
    extract_subject_from_question() to match against. Shared by the
    student exam/subject-teacher lookups."""
    rows = query("SELECT DISTINCT subject_name FROM subjects", fetch=True, many=True) or []
    return [r[0] for r in rows]


def _teachers_with_subjects():
    """(teacher_id, name, subjects) for every teacher - subjects a
    comma-joined string ("English, History") via teacher_subjects/subjects,
    '' if a teacher has none on record. Feeds
    extract_teacher_name_from_question()'s disambiguation (a teacher can
    now teach more than one subject, see teacher_subjects in
    setup_database.py) - shared by every handler that needs to match a
    named teacher in a question."""
    return query("""
        SELECT te.teacher_id, te.name,
               COALESCE(GROUP_CONCAT(DISTINCT s.subject_name ORDER BY s.subject_name SEPARATOR ', '), '')
        FROM teachers te
        LEFT JOIN teacher_subjects ts ON te.teacher_id = ts.teacher_id
        LEFT JOIN subjects s ON ts.subject_id = s.subject_id
        GROUP BY te.teacher_id, te.name
    """, fetch=True, many=True) or []


# =========================================================
# NLP EXPANSION — shared across all three roles
# Notices/subjects_offered aren't personal to any one student/teacher, and
# they're not principal-only school stats either - every role sees the
# same answer, so unlike every other handler in this file there's no
# role-specific ID to branch on. These are the only handlers called
# verbatim from all three of answer_student()/answer_teacher()/
# answer_principal() below.
# =========================================================
_NOTICE_URGENT_RE = re.compile(r'\burgent\b')
_NOTICE_OLD_RE = re.compile(r'\bold(er)?\b')


def _extract_notice_filters(question):
    """(urgent_only, offset) from the question text - "any urgent notices"
    filters to priority='urgent'; "old"/"older announcements" shows the
    NEXT batch after the latest 5 (rows 6-10) instead of the most recent
    ones. A one-shot text signal, not a stateful pagination cursor - "old
    announcements" always means rows 6-10, asked fresh or as a follow-up,
    which is simpler and doesn't need any session-state tracking to satisfy
    "the option to ask for older ones"."""
    q = question.lower()
    urgent_only = bool(_NOTICE_URGENT_RE.search(q))
    offset = 5 if _NOTICE_OLD_RE.search(q) else 0
    return urgent_only, offset


PRIORITY_ICONS = {"urgent": "🔴 ", "important": "🟡 ", "normal": ""}


def handle_notices(role, question):
    """Notices visible to `role` (see _notice_visible_roles()), newest
    first, capped at 5 per batch so a long history doesn't flood the chat -
    "old"/"older" in the question shows the next batch instead of the
    first, "urgent" filters to priority='urgent' only (see
    _extract_notice_filters())."""
    urgent_only, offset = _extract_notice_filters(question)
    visible_roles = _notice_visible_roles(role)

    conditions = ["target_roles='all'"] + ["FIND_IN_SET(%s, target_roles)"] * len(visible_roles)
    sql = f"SELECT title, body, date_posted, priority FROM notices WHERE ({' OR '.join(conditions)})"
    params = list(visible_roles)
    if urgent_only:
        sql += " AND priority='urgent'"
    sql += " ORDER BY date_posted DESC, notice_id DESC LIMIT 5 OFFSET %s"
    params.append(offset)

    results = query(sql, tuple(params), fetch=True, many=True)

    if not results:
        if urgent_only:
            return "No urgent notices right now." if offset == 0 else "No older urgent notices found."
        return "No notices posted at the moment." if offset == 0 else "No older notices found."

    lines = [
        f"{PRIORITY_ICONS.get(priority, '')}**{title}** ({date_posted})\n{body}"
        for title, body, date_posted, priority in results
    ]
    header = "Urgent notices" if urgent_only else "Older notices" if offset else "Latest notices"
    return f"📢 {header}:\n\n" + "\n\n".join(lines)


def handle_subjects_offered(question):
    """The school's curriculum - distinct subjects taught, optionally
    narrowed to one class if a class code is mentioned ("what subjects
    does 10-A study"). Instant DB lookup, no Gemini call needed - this
    data lives in the subjects/timetable tables, not the almanac file
    Gemini reads from, so Gemini has no way to answer this on its own."""
    cls = extract_class_from_question(question)

    if cls:
        results = query("""
            SELECT DISTINCT s.subject_name
            FROM timetable t
            JOIN subjects s ON t.subject_id = s.subject_id
            WHERE t.class = %s
            ORDER BY s.subject_name
        """, (cls,), fetch=True, many=True)
        if not results:
            return f"No subjects found for **{cls}** yet."
        return f"**{cls}** studies: " + ", ".join(r[0] for r in results) + "."

    subjects = _known_subject_names()
    if not subjects:
        return "I couldn't find the subject list right now."
    return "Yara International School teaches: " + ", ".join(sorted(subjects)) + "."


# =========================================================
# NLP EXPANSION — student handlers
# =========================================================
def handle_identity(student_id):
    result = query(
        "SELECT name, class, roll_no FROM students WHERE student_id=%s",
        (student_id,), fetch=True
    )
    if result:
        name, cls, roll = result
        return f"You're **{name}**, Class {cls}, Roll No. {roll}."
    return "I couldn't find your details."


def handle_roll_number(student_id):
    result = query(
        "SELECT roll_no FROM students WHERE student_id=%s",
        (student_id,), fetch=True
    )
    if result:
        return f"Your roll number is **{result[0]}**."
    return "I couldn't find your roll number."


def handle_my_class(student_id):
    result = query(
        "SELECT class FROM students WHERE student_id=%s",
        (student_id,), fetch=True
    )
    if result:
        return f"You're in class **{result[0]}**."
    return "I couldn't find your class."


def handle_next_period(student_id):
    """Finds the next period today, based on current time."""
    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()

    results = query("""
        SELECT t.period_no, s.subject_name, te.name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.subject_id
        JOIN teachers te ON t.teacher_id = te.teacher_id
        JOIN students st ON st.class = t.class
        WHERE st.student_id = %s AND t.day = %s
        ORDER BY t.period_no
    """, (student_id, today), fetch=True, many=True)

    if not results:
        # "No periods scheduled", not "no timetable found" - the latter
        # reads like the student's timetable data is missing rather than
        # "you just don't have class today", which is what's actually true.
        return f"You have no periods scheduled for {today}."

    upcoming = [p for p in results if p[0] > current_period]
    if upcoming:
        period_no, subject, teacher = upcoming[0]
        return f"Your next class is **{subject}** (Period {period_no}) with {teacher}."
    return "Looks like you're done for the day! No more periods scheduled."


def handle_subject_teacher(question, student_id, known_subjects):
    """Finds who teaches a specific subject to this student's class.

    A subject can legitimately be taught by more than one teacher across
    the week (confirmed via testing: one student's class had 5 different
    Math teachers across 5 periods) - fetchall(), not fetchone(), and both
    the single- and multiple-teacher cases are handled explicitly. The
    previous fetchone() version left the query's other rows unread, which
    mysql-connector raises as an "Unread result found" error on the next
    query - silently swallowed by query()'s except block and surfaced to
    the student as a wrong "couldn't find a teacher" answer despite a real
    match existing.
    """
    subject = extract_subject_from_question(question, known_subjects)

    if not subject:
        return "Which subject would you like to know the teacher for?"

    results = query("""
        SELECT DISTINCT te.name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.subject_id
        JOIN teachers te ON t.teacher_id = te.teacher_id
        JOIN students st ON st.class = t.class
        WHERE st.student_id = %s AND s.subject_name = %s
    """, (student_id, subject), fetch=True, many=True)

    if not results:
        return f"I couldn't find a teacher for {subject} in your class."

    names = sorted(name for (name,) in results)

    if len(names) == 1:
        return f"**{subject}** is taught by **{names[0]}**."

    teacher_list = ", ".join(f"**{n}**" for n in names)
    return f"**{subject}** is taught by multiple teachers this week: {teacher_list}."


def handle_student_timetable(question, student_id):
    """Timetable, optionally filtered to a specific day if one is mentioned.
    Extends the existing 'timetable' intent's handling rather than being a
    separate intent - the unfiltered case keeps the exact same output
    format as before this expansion."""
    day = extract_day_from_question(question)

    base_query = """
        SELECT t.day, t.period_no, s.subject_name, te.name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.subject_id
        JOIN teachers te ON t.teacher_id = te.teacher_id
        JOIN students st ON st.class = t.class
        WHERE st.student_id = %s
    """
    params = [student_id]

    if day:
        base_query += " AND LOWER(t.day) = %s"
        params.append(day)

    base_query += """
        ORDER BY FIELD(t.day,'Monday','Tuesday','Wednesday',
                       'Thursday','Friday','Saturday'), t.period_no
    """

    results = query(base_query, tuple(params), fetch=True, many=True)

    if not results:
        if day:
            return f"No classes scheduled for {day.capitalize()}."
        return "No timetable found for your class yet."

    if day:
        lines = [f"- Period {p}: **{subj}** with {teacher}" for _, p, subj, teacher in results]
        return f"Your timetable for **{day.capitalize()}**:\n" + "\n".join(lines)

    # Unfiltered case - identical format to the pre-expansion behavior.
    lines = [f"- **{d}**, Period {p}: {subj} *(with {teacher})*"
             for d, p, subj, teacher in results]
    return "Your timetable:\n" + "\n".join(lines)


def handle_student_exam(question, student_id, known_subjects):
    """Upcoming exams, optionally filtered to a specific subject if one is
    mentioned. Extends the existing 'exam' intent's handling rather than
    being a separate intent - the unfiltered case keeps the exact same
    output format as before this expansion."""
    subject = extract_subject_from_question(question, known_subjects)

    base_query = """
        SELECT s.subject_name, e.exam_date, e.exam_type
        FROM exams e
        JOIN subjects s ON e.subject_id = s.subject_id
        JOIN students st ON st.class = e.class
        WHERE st.student_id = %s AND e.exam_date >= %s
    """
    params = [student_id, date.today()]

    if subject:
        base_query += " AND s.subject_name = %s"
        params.append(subject)

    base_query += " ORDER BY e.exam_date"

    results = query(base_query, tuple(params), fetch=True, many=True)

    if results:
        lines = []
        for subj, edate, etype in results:
            days_left = (edate - date.today()).days
            icon = "🔴" if days_left <= 7 else "🟡" if days_left <= 14 else "🟢"
            lines.append(f"{icon} **{subj}** ({etype}) — {edate} *(in {days_left} days)*")
        header = f"Your upcoming **{subject}** exams:\n" if subject else "Your upcoming exams:\n"
        return header + "\n".join(lines)

    if subject:
        return f"You have no upcoming {subject} exams on record."
    return "You have no upcoming exams on record. 🎉"


# =========================================================
# NLP EXPANSION — teacher handlers
# =========================================================
def handle_teacher_next_class(teacher_id):
    """What class is this teacher teaching next, today."""
    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()

    result = query("""
        SELECT period_no, class
        FROM timetable
        WHERE teacher_id = %s AND day = %s AND period_no > %s
        ORDER BY period_no
        LIMIT 1
    """, (teacher_id, today, current_period), fetch=True)

    if result:
        period_no, cls = result
        return f"Your next class is **{cls}** at Period {period_no}."
    return "You have no more classes scheduled for today. 🎉"


def handle_teacher_current_class(teacher_id):
    """What class is this teacher teaching right now, if any."""
    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()

    result = query("""
        SELECT class
        FROM timetable
        WHERE teacher_id = %s AND day = %s AND period_no = %s
    """, (teacher_id, today, current_period), fetch=True)

    if result:
        return f"You're currently teaching **{result[0]}**."
    return "You don't have a class right now — this looks like a free period."


def handle_teacher_free_periods(teacher_id):
    """Lists which periods today the teacher has NO class scheduled."""
    today = datetime.datetime.now().strftime("%A")

    results = query("""
        SELECT period_no FROM timetable
        WHERE teacher_id = %s AND day = %s
        ORDER BY period_no
    """, (teacher_id, today), fetch=True, many=True)

    if not results:
        return f"No classes scheduled for you today ({today}) — fully free!"

    occupied_periods = {r[0] for r in results}
    # Periods 1-10: matches the actual range used for period_no elsewhere
    # in this project (dashboard.py's timetable form caps period_no at 10),
    # not the reference snippet's assumed 1-8.
    all_periods = set(range(1, 11))
    free = sorted(all_periods - occupied_periods)

    if free:
        free_list = ", ".join(f"Period {p}" for p in free)
        return f"You're free during: **{free_list}** today."
    return "You're booked solid today — no free periods!"


def handle_teacher_periods_remaining(teacher_id):
    """How many periods does this teacher have left today, from now."""
    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()

    result = query("""
        SELECT COUNT(*) FROM timetable
        WHERE teacher_id = %s AND day = %s AND period_no > %s
    """, (teacher_id, today, current_period), fetch=True)

    count = result[0] if result else 0
    if count == 0:
        return "You're done for the day! No more periods left. 🎉"
    return f"You have **{count} period(s)** left today."


def handle_teacher_identity(teacher_id):
    result = query(
        """SELECT te.name,
                  COALESCE(GROUP_CONCAT(DISTINCT s.subject_name ORDER BY s.subject_name SEPARATOR ', '), '')
           FROM teachers te
           LEFT JOIN teacher_subjects ts ON te.teacher_id = ts.teacher_id
           LEFT JOIN subjects s ON ts.subject_id = s.subject_id
           WHERE te.teacher_id=%s
           GROUP BY te.teacher_id, te.name""",
        (teacher_id,), fetch=True
    )
    if result:
        name, subjects = result
        if not subjects:
            return f"You're **{name}**. No subject is on record for you yet."
        return f"You're **{name}**, teaching **{subjects}**."
    return "I couldn't find your details."


# =========================================================
# HOD EXPANSION — department-scoped versions of the principal-tier
# free_teachers/total_teachers/schedule-lookup handlers, filtered down to
# the asking HOD's own department. Dispatched from answer_teacher() (see
# HOD_DEPARTMENT_INTENTS) - vice_principal reuses these unchanged too
# (hod-level access placeholder, see _effective_role()).
# =========================================================
def _hod_department_id(teacher_id):
    """The department_id on this HOD's own teacher record, or None if
    they don't have one assigned yet. Shared by all three handlers below."""
    result = query("SELECT department_id FROM teachers WHERE teacher_id=%s", (teacher_id,), fetch=True)
    return result[0] if result else None


def handle_department_free_teachers(teacher_id):
    """Department-scoped version of handle_free_teachers() - every teacher
    in the HOD's own department with no class scheduled this period."""
    department_id = _hod_department_id(teacher_id)
    if not department_id:
        return "You don't have a department on record yet."

    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()

    dept_teachers = query(
        "SELECT teacher_id, name FROM teachers WHERE department_id=%s",
        (department_id,), fetch=True, many=True
    ) or []
    if not dept_teachers:
        return "No teachers are assigned to your department yet."

    busy = query("""
        SELECT DISTINCT teacher_id FROM timetable
        WHERE day = %s AND period_no = %s
    """, (today, current_period), fetch=True, many=True) or []
    busy_ids = {row[0] for row in busy}
    free = [name for tid, name in dept_teachers if tid not in busy_ids]

    if free:
        return f"Free teachers in your department right now: **{', '.join(free)}**."
    return "Every teacher in your department is currently in class."


def handle_department_schedule_today(teacher_id):
    """Every teacher in the HOD's department, scheduled periods for today -
    department-scoped equivalent of a class timetable lookup."""
    department_id = _hod_department_id(teacher_id)
    if not department_id:
        return "You don't have a department on record yet."

    today = datetime.datetime.now().strftime("%A")
    results = query("""
        SELECT te.name, t.period_no, s.subject_name, t.class
        FROM timetable t
        JOIN teachers te ON t.teacher_id = te.teacher_id
        JOIN subjects s ON t.subject_id = s.subject_id
        WHERE te.department_id = %s AND t.day = %s
        ORDER BY te.name, t.period_no
    """, (department_id, today), fetch=True, many=True)

    if not results:
        return f"No classes scheduled for your department on **{today}**."

    lines = [f"- **{name}**, Period {p}: {subj} *({cls})*" for name, p, subj, cls in results]
    return f"Your department's schedule for **{today}**:\n" + "\n".join(lines)


def handle_department_teacher_count(teacher_id):
    """How many teachers are in the HOD's own department - department-
    scoped equivalent of total_teachers."""
    department_id = _hod_department_id(teacher_id)
    if not department_id:
        return "You don't have a department on record yet."

    result = query("SELECT COUNT(*) FROM teachers WHERE department_id=%s", (department_id,), fetch=True)
    count = result[0] if result else 0
    return f"There are **{count} teacher(s)** in your department."


def handle_teacher_timetable(question, teacher_id):
    """Schedule, optionally filtered to a specific day if one is mentioned.
    Extends the existing 'timetable' intent's handling - unfiltered case
    keeps the exact same output format as before this expansion."""
    day = extract_day_from_question(question)

    base_query = "SELECT day, period_no, class FROM timetable WHERE teacher_id = %s"
    params = [teacher_id]

    if day:
        base_query += " AND LOWER(day) = %s"
        params.append(day)

    base_query += """
        ORDER BY FIELD(day,'Monday','Tuesday','Wednesday',
                       'Thursday','Friday','Saturday'), period_no
    """

    results = query(base_query, tuple(params), fetch=True, many=True)

    if not results:
        if day:
            return f"No classes scheduled for {day.capitalize()}."
        return "No timetable entries found for you yet."

    if day:
        lines = [f"- Period {p}: Class **{cls}**" for _, p, cls in results]
        return f"Your schedule for **{day.capitalize()}**:\n" + "\n".join(lines)

    # Unfiltered case - identical format to the pre-expansion behavior.
    lines = [f"- **{d}**, Period {p}: Class {cls}" for d, p, cls in results]
    return "Your schedule:\n" + "\n".join(lines)


# =========================================================
# NLP EXPANSION — principal handlers
# =========================================================
def handle_teacher_location(question):
    """Where is a specific teacher right now (which class, if any)."""
    teachers = _teachers_with_subjects()
    tid, name, clarification = extract_teacher_name_from_question(question, teachers)

    if clarification:
        return clarification
    if not tid:
        return "Which teacher would you like to locate? Please include their name."

    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()

    result = query("""
        SELECT class FROM timetable
        WHERE teacher_id = %s AND day = %s AND period_no = %s
    """, (tid, today, current_period), fetch=True)

    if result:
        return f"**{name}** is currently teaching **{result[0]}**."
    return f"**{name}** doesn't have a class right now — likely free or unavailable."


def handle_classroom_occupant(question):
    """Who's teaching a specific class right now."""
    cls = extract_class_from_question(question)

    if not cls:
        # Deliberately worded differently from class_teacher_lookup's own
        # "which class" prompt below - the two used to be byte-identical,
        # which would have made them indistinguishable once both got
        # registered in CLARIFICATION_CONFIG (that dict resolves a pending
        # follow-up by matching the exact prompt text back to an intent).
        return "Which class would you like me to check who's in right now? Please include the class (e.g. 10-A)."

    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()

    result = query("""
        SELECT te.name, s.subject_name
        FROM timetable t
        JOIN teachers te ON t.teacher_id = te.teacher_id
        JOIN subjects s ON t.subject_id = s.subject_id
        WHERE t.class = %s AND t.day = %s AND t.period_no = %s
    """, (cls, today, current_period), fetch=True)

    if result:
        teacher, subject = result
        return f"**{cls}** is currently having **{subject}** with **{teacher}**."
    return f"No class scheduled for **{cls}** right now."


def handle_free_teachers():
    """Lists every teacher with no class scheduled this period."""
    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()

    all_teachers = query("SELECT teacher_id, name FROM teachers", fetch=True, many=True) or []
    busy = query("""
        SELECT DISTINCT teacher_id FROM timetable
        WHERE day = %s AND period_no = %s
    """, (today, current_period), fetch=True, many=True) or []

    busy_ids = {row[0] for row in busy}
    free = [name for tid, name in all_teachers if tid not in busy_ids]

    if free:
        return f"Free teachers right now: **{', '.join(free)}**."
    return "All teachers are currently in class."


def handle_teacher_schedule_lookup(question):
    """Full schedule (or day-filtered) for a specific named teacher."""
    teachers = _teachers_with_subjects()
    tid, name, clarification = extract_teacher_name_from_question(question, teachers)

    if clarification:
        return clarification
    if not tid:
        return "Which teacher's schedule would you like to see?"

    day = extract_day_from_question(question)

    base_query = "SELECT day, period_no, class FROM timetable WHERE teacher_id = %s"
    params = [tid]
    if day:
        base_query += " AND LOWER(day) = %s"
        params.append(day)
    base_query += """
        ORDER BY FIELD(day,'Monday','Tuesday','Wednesday',
                       'Thursday','Friday','Saturday'), period_no
    """

    results = query(base_query, tuple(params), fetch=True, many=True)
    if not results:
        return f"No schedule found for {name}" + (f" on {day.capitalize()}." if day else ".")

    lines = [f"- {d}, Period {p}: Class **{cls}**" for d, p, cls in results]
    return f"**{name}**'s schedule:\n" + "\n".join(lines)


def handle_class_timetable_lookup(question):
    """Full (not current-period-only) timetable for a class, day-filterable.
    Distinct from handle_classroom_occupant() (who's teaching THIS class
    right now) - same shape as handle_student_timetable()/
    handle_teacher_timetable(), just keyed by class."""
    cls = extract_class_from_question(question)
    if not cls:
        return "Which class's timetable would you like to see? Please include the class (e.g. 10-A)."

    day = extract_day_from_question(question)

    base_query = """
        SELECT t.day, t.period_no, s.subject_name, te.name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.subject_id
        JOIN teachers te ON t.teacher_id = te.teacher_id
        WHERE t.class = %s
    """
    params = [cls]

    if day:
        base_query += " AND LOWER(t.day) = %s"
        params.append(day)

    base_query += """
        ORDER BY FIELD(t.day,'Monday','Tuesday','Wednesday',
                       'Thursday','Friday','Saturday'), t.period_no
    """

    results = query(base_query, tuple(params), fetch=True, many=True)

    if not results:
        if day:
            return f"No classes scheduled for **{cls}** on {day.capitalize()}."
        return f"No timetable found for **{cls}** yet."

    if day:
        lines = [f"- Period {p}: **{subj}** with {teacher}" for _, p, subj, teacher in results]
        return f"**{cls}**'s timetable for **{day.capitalize()}**:\n" + "\n".join(lines)

    lines = [f"- **{d}**, Period {p}: {subj} *(with {teacher})*"
             for d, p, subj, teacher in results]
    return f"**{cls}**'s timetable:\n" + "\n".join(lines)


def handle_school_wide_subject_teacher(question):
    """Who teaches a specific subject in a specific class, anywhere in school."""
    subjects = query("SELECT DISTINCT subject_name FROM subjects", fetch=True, many=True) or []
    subject_names = [s[0] for s in subjects]
    subject = extract_subject_from_question(question, subject_names)
    cls = extract_class_from_question(question)

    if not subject:
        return "Which subject would you like to know the teacher for?"

    sql = """
        SELECT DISTINCT te.name, t.class
        FROM timetable t
        JOIN teachers te ON t.teacher_id = te.teacher_id
        JOIN subjects s ON t.subject_id = s.subject_id
        WHERE s.subject_name = %s
    """
    params = [subject]
    if cls:
        sql += " AND t.class = %s"
        params.append(cls)

    results = query(sql, tuple(params), fetch=True, many=True)
    if results:
        lines = [f"- **{c}**: {name}" for name, c in results]
        return f"Teachers for **{subject}**:\n" + "\n".join(lines)
    return f"No teacher found for {subject}" + (f" in {cls}." if cls else ".")


def handle_class_teacher_lookup(question):
    """Every subject-teacher pair for a class - "Class 10-A: Mathematics
    — Mr. X, Science — Ms. Y". Distinct from handle_classroom_occupant()
    (who's in THIS class right now) and handle_school_wide_subject_teacher()
    (which teacher teaches a SUBJECT school-wide, not a class's roster)."""
    cls = extract_class_from_question(question)
    if not cls:
        return "Which class would you like to check? Please include the class (e.g. 10-A)."

    results = query("""
        SELECT DISTINCT s.subject_name, te.name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.subject_id
        JOIN teachers te ON t.teacher_id = te.teacher_id
        WHERE t.class = %s
        ORDER BY s.subject_name
    """, (cls,), fetch=True, many=True)

    if not results:
        return f"No teachers found for **{cls}** yet."

    pairs = ", ".join(f"{subj} — {teacher}" for subj, teacher in results)
    return f"**Class {cls}**: {pairs}"


def handle_low_attendance_count():
    """Flags students below the 75% attendance threshold, count + list."""
    results = query("""
        SELECT name, class, attendance_pct FROM students
        WHERE attendance_pct < 75
        ORDER BY attendance_pct ASC
    """, fetch=True, many=True)

    if not results:
        return "Great news — no students are currently below 75% attendance."

    count = len(results)
    lines = [f"- {name} ({cls}): {att}%" for name, cls, att in results[:10]]
    more = f"\n...and {count - 10} more." if count > 10 else ""
    return f"**{count} students** are below 75% attendance:\n" + "\n".join(lines) + more


def handle_pending_fees_count():
    """Count and list of students with pending fee status."""
    results = query("""
        SELECT name, class FROM students WHERE fees_status = 'pending'
    """, fetch=True, many=True)

    if not results:
        return "All student fees are currently paid. ✅"

    count = len(results)
    lines = [f"- {name} ({cls})" for name, cls in results[:10]]
    more = f"\n...and {count - 10} more." if count > 10 else ""
    return f"**{count} students** have pending fees:\n" + "\n".join(lines) + more


def handle_teacher_count_by_subject(question):
    """How many teachers teach a given subject, school-wide. A teacher can
    now teach more than one subject (teacher_subjects join table, see
    setup_database.py) - COUNT(DISTINCT teacher_id) so a teacher who also
    teaches something else isn't double-counted against this one subject."""
    subject = extract_subject_from_question(question, _known_subject_names())

    if not subject:
        return "Which subject would you like the teacher count for?"

    # TRIM() on both sides, not a plain "=": subjects.subject_name has had
    # whitespace-duplicated rows for the same real subject in the past
    # ('Computer Science' vs 'Computer Science ' as DISTINCT values) - an
    # exact match against whichever variant extract_subject_from_question
    # happened to pick would undercount.
    count_result = query(
        """SELECT COUNT(DISTINCT ts.teacher_id)
           FROM teacher_subjects ts
           JOIN subjects s ON ts.subject_id = s.subject_id
           WHERE TRIM(s.subject_name) = TRIM(%s)""",
        (subject,), fetch=True
    )
    count = count_result[0] if count_result else 0
    return f"There are **{count} {subject.strip()} teacher(s)** at the school."


# =========================================================
# NLP ANSWER FUNCTIONS
# =========================================================
def answer_student(question, student_id, forced_intent=None):
    # forced_intent: set by the classifier lane when it already picked the
    # intent (see classify_personal_intent() in gemini_rag.py) - skips
    # detect_intent() and goes straight into the same dispatch below.
    intent = forced_intent if forced_intent is not None else detect_intent(
        question,
        ["greeting", "thanks", "help", "attendance", "exam", "timetable", "fee",
         "identity", "roll_number", "my_class", "next_period", "subject_teacher",
         "notices", "subjects_offered"]
    )

    # subject_teacher's "who teaches me"/"teacher for" phrases are about a
    # SPECIFIC subject; subjects_offered's are about the curriculum in
    # general. Phrase sets don't overlap, but a subject name mentioned
    # alongside otherwise-generic curriculum wording ("does the school
    # offer computer science") can still score subjects_offered - redirect
    # to the specific lookup when a real subject is actually named, same
    # class-code-vs-named-entity redirect used in answer_principal() below.
    if forced_intent is None and intent == "subjects_offered" \
            and extract_subject_from_question(question, _known_subject_names()):
        intent = "subject_teacher"

    if intent == "greeting":
        return "Hi, I'm Nova! Ask me about your attendance, exams, timetable, or fees. 😊"
    elif intent == "thanks":
        return "You're welcome! Let me know if you need anything else. 👍"
    elif intent == "help":
        return ("Hi, I'm Nova! Here's what I can help with:\n"
                "📊 **Attendance** — *'what's my attendance'*\n"
                "📅 **Exams** — *'when is my next exam'* (add a subject to filter)\n"
                "🕐 **Timetable** — *'show my timetable'* (add a day, e.g. 'Monday' or 'today')\n"
                "💰 **Fees** — *'is my fee paid'*\n"
                "🙋 **My details** — *'who am i'*, *'my roll number'*, *'what class am i in'*\n"
                "⏭ **Next period** — *'what's my next period'*\n"
                "👩‍🏫 **Subject teacher** — *'who teaches me math'*\n"
                "📢 **Notices** — *'any announcements'*")

    if intent == "attendance":
        result = query(
            "SELECT attendance_pct FROM students WHERE student_id=%s",
            (student_id,), fetch=True
        )
        if result:
            att = float(result[0])
            status = "✅ Great standing!" if att >= 75 else "⚠️ Below required 75% — please improve."
            return f"Your current attendance is **{att}%**. {status}"
        return "I couldn't find your attendance record."

    elif intent == "exam":
        known_subjects = _known_subject_names()
        return handle_student_exam(question, student_id, known_subjects)

    elif intent == "timetable":
        return handle_student_timetable(question, student_id)

    elif intent == "fee":
        result = query(
            "SELECT fees_status FROM students WHERE student_id=%s",
            (student_id,), fetch=True
        )
        if result:
            status = "✅ Paid" if result[0] == "paid" else "⚠️ Pending — please contact the school office"
            return f"Your fees status: **{status}**"
        return "I couldn't find your fee status."

    elif intent == "identity":
        return handle_identity(student_id)

    elif intent == "roll_number":
        return handle_roll_number(student_id)

    elif intent == "my_class":
        return handle_my_class(student_id)

    elif intent == "next_period":
        return handle_next_period(student_id)

    elif intent == "subject_teacher":
        known_subjects = _known_subject_names()
        return handle_subject_teacher(question, student_id, known_subjects)

    elif intent == "notices":
        return handle_notices("student", question)

    elif intent == "subjects_offered":
        return handle_subjects_offered(question)

    return ("I didn't quite get that. Try asking about:\n"
            "**attendance**, **exams**, **timetable**, **fees**, or your **details**.")


def answer_teacher(question, teacher_id, forced_intent=None, extra_intents=None, role="teacher"):
    # forced_intent: see answer_student()'s matching comment above.
    # extra_intents: HOD_DEPARTMENT_INTENTS when this is really an hod/
    # vice_principal login (see _dispatch_to_role_handler) - a plain
    # teacher is never passed any, so those intents can never be detected
    # for one.
    # role: the REAL login role (teacher/hod/vice_principal), needed only
    # for handle_notices()'s role-scoped visibility - hod/vice_principal
    # see more notices than a plain teacher even though every other branch
    # in this function treats all three identically. Defaults to "teacher"
    # so existing callers (e.g. nlp_audit_test.py) that don't pass it are
    # unaffected.
    intent = forced_intent if forced_intent is not None else detect_intent(
        question,
        ["greeting", "thanks", "help", "period_count", "timetable", "classes_assigned",
         "next_class", "current_class", "free_periods", "periods_remaining", "teacher_identity",
         "notices", "subjects_offered"] + (extra_intents or [])
    )

    if intent == "greeting":
        return "Hi, I'm Nova! Ask me about your schedule, periods, or classes. 😊"
    elif intent == "thanks":
        return "You're welcome! 👍"
    elif intent == "help":
        department_help = (
            "\n🏢 **Department** — *'which teachers in my department are free'*, "
            "*'my department's schedule today'*, *'how many teachers are in my department'*"
            if extra_intents else ""
        )
        return ("Hi, I'm Nova! Here's what I can help with:\n"
                "🕐 **Schedule** — *'show my timetable'* (add a day, e.g. 'Monday' or 'today')\n"
                "📊 **Periods** — *'how many periods do I have'*\n"
                "🏫 **Classes** — *'which classes do I teach'*\n"
                "⏭ **Next/current class** — *'what am I teaching next'*, *'what am I teaching now'*\n"
                "🆓 **Free periods** — *'am I free right now'*, *'free periods today'*\n"
                "⏳ **Periods left today** — *'how many periods do I have left'*\n"
                "🙋 **My details** — *'who am i'*\n"
                "📢 **Notices** — *'any announcements'*" + department_help)

    if intent == "period_count":
        # "periods today" is one of this intent's own phrases, so without
        # a day filter it was silently answering with the WEEKLY total.
        day = extract_day_from_question(question)
        if day:
            result = query(
                "SELECT COUNT(*) FROM timetable WHERE teacher_id=%s AND LOWER(day)=%s",
                (teacher_id, day), fetch=True
            )
            if result:
                return f"You have **{result[0]} periods** scheduled for **{day.capitalize()}**."
            return "I couldn't retrieve your period count right now."

        result = query(
            "SELECT COUNT(*) FROM timetable WHERE teacher_id=%s",
            (teacher_id,), fetch=True
        )
        if result:
            return f"You have **{result[0]} periods** assigned this week."
        return "I couldn't retrieve your period count right now."

    elif intent == "timetable":
        return handle_teacher_timetable(question, teacher_id)

    elif intent == "classes_assigned":
        result = query(
            "SELECT classes_assigned FROM teachers WHERE teacher_id=%s",
            (teacher_id,), fetch=True
        )
        if result:
            return f"You're assigned to: **{result[0]}**"
        return "I couldn't retrieve your assigned classes right now."

    elif intent == "next_class":
        return handle_teacher_next_class(teacher_id)

    elif intent == "current_class":
        return handle_teacher_current_class(teacher_id)

    elif intent == "free_periods":
        return handle_teacher_free_periods(teacher_id)

    elif intent == "periods_remaining":
        return handle_teacher_periods_remaining(teacher_id)

    elif intent == "teacher_identity":
        return handle_teacher_identity(teacher_id)

    elif intent == "notices":
        return handle_notices(role, question)

    elif intent == "subjects_offered":
        return handle_subjects_offered(question)

    elif intent == "department_free_teachers":
        return handle_department_free_teachers(teacher_id)

    elif intent == "department_schedule_today":
        return handle_department_schedule_today(teacher_id)

    elif intent == "department_teacher_count":
        return handle_department_teacher_count(teacher_id)

    return "I didn't understand that. Try asking about your **schedule**, **periods**, or **classes**."


def answer_principal(question, forced_intent=None):
    # forced_intent: see answer_student()'s matching comment above.
    if forced_intent is not None:
        intent = forced_intent
    else:
        # rank_intents() + _apply_subject_scoring_adjustment(), not
        # detect_intent(): "how many teachers teach math" ties
        # teacher_count_by_subject/total_teachers, and the adjustment is
        # what actually resolves that now instead of relying on
        # teacher_count_by_subject happening to be listed first below.
        principal_ranked = rank_intents(question, [
            "greeting", "thanks", "help",
            "teacher_count_by_subject", "total_students", "total_teachers", "class_wise_count",
            "teacher_location", "classroom_occupant", "free_teachers",
            "teacher_schedule_lookup", "class_timetable_lookup",
            "school_wide_subject_teacher", "class_teacher_lookup",
            "low_attendance_count", "pending_fees_count", "notices", "subjects_offered"
        ])
        principal_ranked = _apply_subject_scoring_adjustment(principal_ranked, question)
        intent = principal_ranked[0][0] if principal_ranked else None

    # teacher_schedule_lookup's "schedule for" and school_wide_subject_
    # teacher's "who teaches" phrases also match a class-code question
    # with no named teacher/subject ("schedule for 10a") - phrase matching
    # is a literal substring check, it can't tell "schedule for <TEACHER>"
    # from "schedule for <CLASS CODE>" on its own. Redirect to the
    # class-scoped intent when a class code is present but no real
    # teacher/subject was extracted - the narrower intent's handler would
    # have had nothing to work with anyway. Skipped for forced_intent -
    # the classifier already makes this distinction itself.
    if forced_intent is None and extract_class_from_question(question):
        if intent == "teacher_schedule_lookup":
            teachers = _teachers_with_subjects()
            tid, _, clarification = extract_teacher_name_from_question(question, teachers)
            # A real (if ambiguous) teacher reference was found - let it
            # through to handle_teacher_schedule_lookup(), which will
            # re-extract and surface the same disambiguation itself. Only
            # redirect when NEITHER a teacher NOR an ambiguity was found.
            if not tid and not clarification:
                intent = "class_timetable_lookup"
        elif intent == "school_wide_subject_teacher":
            if not extract_subject_from_question(question, _known_subject_names()):
                intent = "class_teacher_lookup"

    # Same reasoning as the class-code redirect above, other direction:
    # subjects_offered ("what subjects does the school teach") and
    # school_wide_subject_teacher ("who teaches computer science") have
    # disjoint phrase sets, but a subject name mentioned inside otherwise
    # generic curriculum wording can still score subjects_offered. A real
    # subject being named is a stronger signal than that - redirect to the
    # specific lookup instead of answering with the whole subject list.
    if forced_intent is None and intent == "subjects_offered" \
            and extract_subject_from_question(question, _known_subject_names()):
        intent = "school_wide_subject_teacher"

    if intent == "greeting":
        return "Good day! I'm Nova. Ask me about student numbers, teachers, or class breakdowns. 😊"
    elif intent == "thanks":
        return "You're welcome! 👍"
    elif intent == "help":
        return ("I'm Nova — here's what I can show:\n"
                "👥 **Total students**\n"
                "👨‍🏫 **Total teachers** (or *'how many teachers teach math'* for a subject)\n"
                "📊 **Class-wise breakdown**\n"
                "📍 **Where is a teacher** — *'where is <name>'*\n"
                "🚪 **Who's in a class** — *'who is teaching class 10-A'*\n"
                "🆓 **Free teachers** — *'which teachers are free right now'*\n"
                "🗓 **A teacher's schedule** — *'schedule for <name>'*\n"
                "📅 **A class's timetable** — *'timetable for class 10-A'*\n"
                "👩‍🏫 **Subject teachers** — *'who teaches math'*\n"
                "🧑‍🏫 **A class's teachers** — *'who teaches class 10-A'*\n"
                "⚠️ **Attendance risk** — *'students with low attendance'*\n"
                "💰 **Pending fees** — *'pending fees'*\n"
                "📢 **Notices** — *'any announcements'*")

    if intent == "total_students":
        result = query("SELECT COUNT(*) FROM students", fetch=True)
        if result:
            return f"There are **{result[0]} students** enrolled in total. 👥"
        return "I couldn't retrieve the student count right now."

    elif intent == "total_teachers":
        result = query("SELECT COUNT(*) FROM teachers", fetch=True)
        if result:
            return f"There are **{result[0]} teachers** on staff. 👨‍🏫"
        return "I couldn't retrieve the teacher count right now."

    elif intent == "class_wise_count":
        results = query(
            "SELECT class, COUNT(*) FROM students GROUP BY class ORDER BY class",
            fetch=True, many=True
        )
        if results:
            lines = [f"- Class **{cls}**: {count} students" for cls, count in results]
            return "Class-wise breakdown:\n" + "\n".join(lines)
        return "No student records found yet."

    elif intent == "teacher_location":
        return handle_teacher_location(question)

    elif intent == "classroom_occupant":
        return handle_classroom_occupant(question)

    elif intent == "free_teachers":
        return handle_free_teachers()

    elif intent == "teacher_schedule_lookup":
        return handle_teacher_schedule_lookup(question)

    elif intent == "class_timetable_lookup":
        return handle_class_timetable_lookup(question)

    elif intent == "school_wide_subject_teacher":
        return handle_school_wide_subject_teacher(question)

    elif intent == "class_teacher_lookup":
        return handle_class_teacher_lookup(question)

    elif intent == "low_attendance_count":
        return handle_low_attendance_count()

    elif intent == "pending_fees_count":
        return handle_pending_fees_count()

    elif intent == "teacher_count_by_subject":
        return handle_teacher_count_by_subject(question)

    elif intent == "notices":
        return handle_notices("principal", question)

    elif intent == "subjects_offered":
        return handle_subjects_offered(question)

    return "I didn't understand that. Try asking about **students**, **teachers**, or **class breakdown**."


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    # Render (and most hosts) assign the port to listen on via $PORT -
    # default to 5000 so local dev is unaffected.
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=debug_mode, port=port)
