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

# Add parent directory to path so we can import our existing helper files
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from auth_helpers import verify_password
from nlp_helpers import detect_intent
from gemini_rag import gemini_answer_stream
from config import DB_CONFIG
import mysql.connector


# =========================================================
# TWO-LANE ROUTING
# Lane 1 (personal)  -> NLP + MySQL. Private data never leaves the server.
# Lane 2 (general)   -> Gemini + almanac. Only school-wide info is sent out.
# =========================================================

# Words that strongly signal a PERSONAL question (needs MySQL)
PERSONAL_SIGNALS = [
    'my ', 'my ', ' i ', 'am i', 'do i', 'have i', 'i have',
    'mine', 'me ', ' me,', 'i am', "i'm", 'show me my',
    'what is my', 'what are my', "what's my",
    # Bare/telegraphic forms of the new student intents, added after live
    # testing: "roll no" and "next period" (no "my"/"I") don't match any
    # signal above, so a student asking exactly that got routed to Gemini
    # instead of NLP - a real feature gap, not just a theoretical one. Each
    # is specific enough to a personal record that it's very unlikely to
    # appear in a genuine general-knowledge almanac question instead.
    'roll no', 'roll number', 'next period', 'next class',
]


def is_personal_question(question):
    """
    Returns True if the question is about the user's own data
    (attendance, exam, timetable, fees) — routes to NLP + MySQL.
    Returns False if it's a general school knowledge question
    — routes to Gemini + almanac.
    """
    q = question.lower()
    return any(signal in q for signal in PERSONAL_SIGNALS)


# Roles whose questions are nearly always about school records in MySQL.
# A principal asks "how many students are there" and a teacher asks "which
# classes are assigned" - both are database questions, but neither is worded
# in the first person, so the PERSONAL_SIGNALS check alone would wrongly send
# them to Gemini (which has no student counts in the almanac).
DB_FIRST_ROLES = {"teacher", "principal"}

# Phrases that clearly point at school-wide almanac content (holidays, PTM,
# admissions, policies, exam calendar, ...) rather than a specific person's
# own records. Checked BEFORE the DB_FIRST_ROLES default below.
#
# Why this is needed: nlp_helpers.py's timetable intent treats "schedule" and
# "classes" as keywords, so a teacher/principal asking "when is the exam
# schedule for grade 9" (a school-wide almanac question) would otherwise
# match that intent and get back THEIR OWN timetable instead - wrong data,
# returned confidently. These phrases are deliberately multi-word/specific
# (not bare "exam", "schedule", "class", "fee") so they don't collide with
# genuine personal questions like "what's my exam schedule" or "what classes
# am I in", which are still correctly caught by PERSONAL_SIGNALS for students.
GENERAL_KNOWLEDGE_SIGNALS = [
    "exam schedule", "exam date", "exam dates", "board exam",
    "half yearly", "half-yearly", "pre-board", "unit test schedule",
    "holiday", "holidays", "vacation", "hajj", "eid",
    "ptm", "parent teacher", "parent-teacher",
    "admission", "admissions",
    "fee structure", "fees structure", "school fee",
    "uniform", "dress code",
    "policy", "policies", "circular", "notice board", "announcement",
    "academic calendar", "academic year",
    "cbse",
    "school reopen", "school reopens", "school start", "school begins",
    "school hours", "school timing", "office hours",
    "transport", "bus route",
]


def is_general_knowledge_question(question):
    """
    Returns True if the question is clearly about school-wide information
    that lives in the almanac, not a specific person's own records.
    """
    q = question.lower()
    return any(signal in q for signal in GENERAL_KNOWLEDGE_SIGNALS)


# Exact allowlist of pure greeting/thanks/help pleasantries - checked as a
# WHOLE-MESSAGE match, never a substring or fuzzy match.
#
# This used to call nlp_helpers.detect_intent(), which uses typo-tolerant
# fuzzy matching (cutoff 0.75) so real typos like "helo" still work. That
# caused a real production bug: a student asked "Shark Tank?" and got
# "You're welcome!" back, because "tank" fuzzy-matches "thank" (a
# thanks-intent keyword) at ~0.89 similarity - comfortably past the cutoff -
# so the question never reached Gemini at all. An exact allowlist can't have
# that failure mode: "tank" is not "thanks", full stop. The tradeoff is a
# genuine typo'd greeting ("helo") won't be caught here and goes to Gemini
# instead - a harmless, cheap miss, versus silently swallowing a real
# question, which is not.
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


def use_nlp_lane(question, role):
    """
    Pick which lane answers this question.

    True  -> Lane 1: NLP + MySQL (personal data / school records)
    False -> Lane 2: Gemini + almanac (general school knowledge)

    Pure greetings/thanks/help-requests always go to NLP first, regardless
    of role, since those cost nothing and nlp_helpers.py already handles
    them (see is_pure_greeting).

    Teachers and principals then go to the MySQL lane first because that is
    what almost all of their questions need - EXCEPT questions that clearly
    match GENERAL_KNOWLEDGE_SIGNALS, which go straight to Gemini so wording
    overlap with NLP's timetable/classes keywords can't hijack an almanac
    question. If the NLP engine doesn't recognize a question, the existing
    fallback in the /api/chat route still forwards it to Gemini as a last
    resort.

    Students ask a genuine mix of personal and general questions, so their
    routing stays based on first-person wording (PERSONAL_SIGNALS already
    correctly separates "what's my exam schedule" from "when is the exam
    schedule for grade 9" via the word "my", so no extra check is needed
    here for students).
    """
    if is_pure_greeting(question):
        return True
    if role in DB_FIRST_ROLES:
        if is_general_knowledge_question(question):
            return False
        return True
    return is_personal_question(question)

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

    elif role == "teacher":
        info = query(
            "SELECT name, subject, classes_assigned FROM teachers WHERE teacher_id=%s",
            (linked_id,), fetch=True
        )
        if not info:
            return None
        return info[0], {
            "name": info[0], "subject": info[1],
            "classes": info[2]
        }

    else:  # principal
        return "Principal", {"name": "Principal"}


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
        return jsonify({
            "success": False,
            "error": "Too many failed attempts. Please try again in a few minutes."
        }), 429

    result = query(
        "SELECT user_id, password_hash, role, linked_id FROM users WHERE username=%s",
        (username,), fetch=True
    )

    # Same generic error whether the username doesn't exist or the password is
    # wrong - distinguishing them lets an attacker enumerate valid usernames.
    if not result or not verify_password(password, result[1]):
        _record_failed_login(rate_key)
        return jsonify({"success": False, "error": "Invalid username or password."})

    _clear_failed_logins(rate_key)
    user_id, stored_hash, role, linked_id = result

    built = _build_profile(role, linked_id)
    if not built:
        return jsonify({"success": False, "error": "Could not load profile."})
    display_name, profile = built

    # Store in session
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


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


# =========================================================
# CHAT ROUTE — the NLP brain
# =========================================================
@app.route("/api/chat", methods=["POST"])
def chat():
    if "user_id" not in session:
        return jsonify({"error": "Not logged in."}), 401

    data = request.get_json()
    question = data.get("message", "").strip()
    role = session.get("role")
    linked_id = session.get("linked_id")

    if not question:
        return jsonify({"reply": "Please type a question first."})

    question_lower = question.lower()

    # TWO-LANE ROUTING:
    # Lane 1: Personal question → NLP + MySQL (private, personal data).
    #         Already instant (a single DB lookup), so it stays a normal
    #         JSON response - streaming would add complexity for no benefit
    #         on an answer that arrives in one piece anyway.
    # Lane 2: General question → Gemini + almanac (school-wide info).
    #         Gemini calls take a few seconds; this lane streams so the
    #         reply appears incrementally instead of the user staring at
    #         the typing indicator for the whole round trip.

    if use_nlp_lane(question_lower, role):
        # Personal lane — use NLP + MySQL
        print(f'[NLP LANE] Question: {question_lower}')
        if role == "student":
            reply = answer_student(question_lower, linked_id)
        elif role == "teacher":
            reply = answer_teacher(question_lower, linked_id)
        else:  # principal
            reply = answer_principal(question_lower)

        # If NLP couldn't recognize it even with personal words, fall back
        # to Gemini as a last resort. The answer is now genuinely coming
        # from Gemini, so it streams too, same as the general lane below.
        if "didn't quite get" in reply or "didn't understand" in reply:
            # Plain ASCII "->" deliberately, not "→": this print() crashes
            # with UnicodeEncodeError on Windows whenever stdout isn't
            # forced to UTF-8 (the OS default is cp1252), which would take
            # down every NLP-miss request with an unhandled 500. Found via
            # real testing, not theoretical - reproduced it while verifying
            # streaming.
            print(f'[NLP MISS -> GEMINI FALLBACK] Question: {question_lower}')
            return stream_gemini_reply(question_lower)

        return jsonify({"reply": reply})

    # General lane — use Gemini + almanac, streamed
    return stream_gemini_reply(question_lower)


def stream_gemini_reply(question):
    """
    Wraps gemini_answer_stream() as a Server-Sent-Events HTTP response, so
    static/app.js can read chunks incrementally via a ReadableStream reader
    instead of waiting for the whole reply the way the NLP lane's plain
    JSON response does.

    Each chunk is sent as its own "data: {...}\\n\\n" line (SSE framing);
    the chunk text is JSON-encoded so a chunk containing a literal newline
    can't be mistaken for the blank-line message separator. A final
    "data: [DONE]\\n\\n" marks the end of the stream so app.js knows to stop
    reading (it also ends naturally when the connection closes, but this
    makes that explicit rather than relying on it).
    """
    def generate():
        for chunk in gemini_answer_stream(question):
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


def extract_subject_from_question(question, known_subjects):
    """
    Returns a subject name if one is mentioned in the question.
    known_subjects: list of actual subject names from the DB (fetch once,
    pass in) so we're not guessing/hardcoding subjects.

    Two tiers, found via live testing:

    1. Full subject name present in the question -> prefer the LONGEST
       match. This school's data has both "Science" and "Computer Science"
       as distinct subjects, and "Science" is a literal substring of
       "Computer Science" - without preferring the longer match, "how many
       teachers teach computer science" silently answered about plain
       Science instead, depending on arbitrary DB row order.
    2. Only if NO full name matched: fall back to a shortened/informal form
       the user typed (e.g. "math" for "Mathematics", "chem" for
       "Chemistry") - any question word of at least 4 characters that's a
       substring of the subject name. Gated strictly behind tier 1 finding
       nothing: an earlier version checked both tiers unconditionally, and
       a plain "science" question got hijacked into "Computer Science"
       because the word "science" is also a substring of THAT longer name.
    """
    q = question.lower()

    full_matches = [s for s in known_subjects if s.lower().strip() in q]
    if full_matches:
        return max(full_matches, key=len)

    q_words = q.split()
    partial_matches = [
        s for s in known_subjects
        if any(len(w) >= 4 and w in s.lower() for w in q_words)
    ]
    if partial_matches:
        return max(partial_matches, key=len)

    return None


def extract_teacher_name_from_question(question, known_teachers):
    """
    known_teachers: list of (teacher_id, name) tuples fetched from DB.

    Checks every word in the stored name, not just the first one, skipping
    common titles. Found via live testing: this school's teacher names are
    stored with a leading title ("Mr Imdadullah"), so the original
    first-word-only check was matching "Mr" as the "first name" and never
    finding the teacher when a user just said their actual name.
    """
    q = question.lower()
    for tid, name in known_teachers:
        name_lower = name.lower().strip()
        if name_lower and name_lower in q:
            return tid, name
        for word in name_lower.split():
            word = word.strip(".")
            if word and word not in TEACHER_NAME_TITLES and word in q:
                return tid, name
    return None, None


def extract_class_from_question(question):
    """Finds a class code like '10-A' mentioned anywhere in the question."""
    match = re.search(r'\b(\d{1,2})[\s-]?([A-Za-z])\b', question)
    if match:
        return f"{match.group(1)}-{match.group(2).upper()}"
    return None


def _known_subject_names():
    """Fetches the school-wide list of subject names, for
    extract_subject_from_question() to match against. Shared by the
    student exam/subject-teacher lookups."""
    rows = query("SELECT DISTINCT subject_name FROM subjects", fetch=True, many=True) or []
    return [r[0] for r in rows]


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
    """Finds who teaches a specific subject to this student's class."""
    subject = extract_subject_from_question(question, known_subjects)

    if not subject:
        return "Which subject would you like to know the teacher for?"

    result = query("""
        SELECT DISTINCT te.name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.subject_id
        JOIN teachers te ON t.teacher_id = te.teacher_id
        JOIN students st ON st.class = t.class
        WHERE st.student_id = %s AND s.subject_name = %s
    """, (student_id, subject), fetch=True)

    if result:
        return f"**{subject}** is taught by **{result[0]}**."
    return f"I couldn't find a teacher for {subject} in your class."


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
        "SELECT name, subject FROM teachers WHERE teacher_id=%s",
        (teacher_id,), fetch=True
    )
    if result:
        name, subject = result
        return f"You're **{name}**, {subject} teacher."
    return "I couldn't find your details."


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
    teachers = query("SELECT teacher_id, name FROM teachers", fetch=True, many=True) or []
    tid, name = extract_teacher_name_from_question(question, teachers)

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
        return "Which class would you like to check? Please include the class (e.g. 10-A)."

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
    teachers = query("SELECT teacher_id, name FROM teachers", fetch=True, many=True) or []
    tid, name = extract_teacher_name_from_question(question, teachers)

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
    """How many teachers teach a given subject, school-wide."""
    result = query("SELECT DISTINCT subject FROM teachers", fetch=True, many=True) or []
    known_subjects = [r[0] for r in result]
    subject = extract_subject_from_question(question, known_subjects)

    if not subject:
        return "Which subject would you like the teacher count for?"

    # TRIM() on both sides, not a plain "=": found via live testing that
    # teachers.subject has whitespace-duplicated rows for the same real
    # subject ('Computer Science' and 'Computer Science ' as DISTINCT
    # values - 3 teachers vs 1). An exact match against whichever variant
    # extract_subject_from_question happened to pick undercounted (1
    # instead of the real 4). TRIM() treats both as the same subject.
    count_result = query(
        "SELECT COUNT(*) FROM teachers WHERE TRIM(subject) = TRIM(%s)",
        (subject,), fetch=True
    )
    count = count_result[0] if count_result else 0
    return f"There are **{count} {subject.strip()} teacher(s)** at the school."


# =========================================================
# NLP ANSWER FUNCTIONS
# =========================================================
def answer_student(question, student_id):
    intent = detect_intent(
        question,
        ["greeting", "thanks", "help", "attendance", "exam", "timetable", "fee",
         "identity", "roll_number", "my_class", "next_period", "subject_teacher"]
    )

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
                "👩‍🏫 **Subject teacher** — *'who teaches me math'*")

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

    return ("I didn't quite get that. Try asking about:\n"
            "**attendance**, **exams**, **timetable**, **fees**, or your **details**.")


def answer_teacher(question, teacher_id):
    intent = detect_intent(
        question,
        ["greeting", "thanks", "help", "period_count", "timetable", "classes_assigned",
         "next_class", "current_class", "free_periods", "periods_remaining", "teacher_identity"]
    )

    if intent == "greeting":
        return "Hi, I'm Nova! Ask me about your schedule, periods, or classes. 😊"
    elif intent == "thanks":
        return "You're welcome! 👍"
    elif intent == "help":
        return ("Hi, I'm Nova! Here's what I can help with:\n"
                "🕐 **Schedule** — *'show my timetable'* (add a day, e.g. 'Monday' or 'today')\n"
                "📊 **Periods** — *'how many periods do I have'*\n"
                "🏫 **Classes** — *'which classes do I teach'*\n"
                "⏭ **Next/current class** — *'what am I teaching next'*, *'what am I teaching now'*\n"
                "🆓 **Free periods** — *'am I free right now'*, *'free periods today'*\n"
                "⏳ **Periods left today** — *'how many periods do I have left'*\n"
                "🙋 **My details** — *'who am i'*")

    if intent == "period_count":
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

    return "I didn't understand that. Try asking about your **schedule**, **periods**, or **classes**."


def answer_principal(question):
    intent = detect_intent(
        question,
        ["greeting", "thanks", "help",
         # Newer/more specific intents listed before their more generic
         # existing counterparts: detect_intent() keeps whichever intent it
         # checks FIRST on an exact score tie, and "how many teachers teach
         # math" ties 4-4 between total_teachers and teacher_count_by_subject
         # (both get a phrase match + one keyword hit). Checking the
         # subject-specific one first makes it win that tie, while a plain
         # "how many teachers" still resolves to total_teachers on its own
         # merits regardless of order (it scores 4 there vs 1) - verified
         # both cases against real input before wiring this in.
         "teacher_count_by_subject", "total_students", "total_teachers", "class_wise_count",
         "teacher_location", "classroom_occupant", "free_teachers",
         "teacher_schedule_lookup", "school_wide_subject_teacher",
         "low_attendance_count", "pending_fees_count"]
    )

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
                "👩‍🏫 **Subject teachers** — *'who teaches math'*\n"
                "⚠️ **Attendance risk** — *'students with low attendance'*\n"
                "💰 **Pending fees** — *'pending fees'*")

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

    elif intent == "school_wide_subject_teacher":
        return handle_school_wide_subject_teacher(question)

    elif intent == "low_attendance_count":
        return handle_low_attendance_count()

    elif intent == "pending_fees_count":
        return handle_pending_fees_count()

    elif intent == "teacher_count_by_subject":
        return handle_teacher_count_by_subject(question)

    return "I didn't understand that. Try asking about **students**, **teachers**, or **class breakdown**."


# =========================================================
# RUN
# =========================================================
if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug_mode, port=5000)
