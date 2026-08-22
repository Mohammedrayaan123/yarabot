# ============================================================
# PRINCIPAL NLP EXPANSION — reference code for Claude Code to adapt
# ============================================================
# Gives the principal real operational awareness: live teacher
# locations, classroom occupancy, free-teacher lookup, school-wide
# subject/teacher lookups, attendance/fee risk flags.
#
# Reuses estimate_current_period_number() and extract_day_from_
# question() already built for the teacher NLP expansion - do not
# duplicate, reference the same functions.
# ============================================================

# ---------------------------------------------------------
# ADD to nlp_helpers.py INTENT_DATA dictionary:
# ---------------------------------------------------------
NEW_PRINCIPAL_INTENTS = {
    "teacher_location": {
        "phrases": ["where is", "which class is teaching", "what is teaching right now"],
        "keywords": ["where", "location"],
    },
    "classroom_occupant": {
        "phrases": ["who is teaching class", "who is in class", "which teacher is in"],
        "keywords": ["teaching", "occupant"],
    },
    "free_teachers": {
        "phrases": ["which teachers are free", "free teachers right now",
                    "who is available"],
        "keywords": ["free", "available"],
    },
    "teacher_schedule_lookup": {
        "phrases": ["schedule for", "timetable for teacher"],
        "keywords": ["schedule"],
    },
    "school_wide_subject_teacher": {
        "phrases": ["who teaches", "teacher for subject"],
        "keywords": ["teaches"],
    },
    "low_attendance_count": {
        "phrases": ["low attendance", "below 75", "attendance risk",
                    "students with poor attendance"],
        "keywords": ["attendance"],
    },
    "pending_fees_count": {
        "phrases": ["pending fees", "unpaid fees", "fee defaulters"],
        "keywords": ["pending", "unpaid"],
    },
    "teacher_count_by_subject": {
        "phrases": ["how many teachers teach", "teachers for subject"],
        "keywords": ["teachers"],
    },
}


# ---------------------------------------------------------
# ADD to app.py — name/class extraction helpers
# (mirrors extract_subject_from_question already built)
# ---------------------------------------------------------

def extract_teacher_name_from_question(question, known_teachers):
    """known_teachers: list of (teacher_id, name) tuples fetched from DB."""
    q = question.lower()
    for tid, name in known_teachers:
        # Match on first name or full name for flexibility
        first_name = name.split()[0].lower()
        if name.lower() in q or first_name in q:
            return tid, name
    return None, None


def extract_class_from_question(question):
    """Finds a class code like '10-A' mentioned anywhere in the question."""
    import re
    match = re.search(r'\b(\d{1,2})[\s-]?([A-Za-z])\b', question)
    if match:
        return f"{match.group(1)}-{match.group(2).upper()}"
    return None


# ---------------------------------------------------------
# ADD to app.py — principal answer handlers
# ---------------------------------------------------------

def handle_teacher_location(question):
    """Where is a specific teacher right now (which class, if any)."""
    import datetime
    teachers = query("SELECT teacher_id, name FROM teachers", fetch=True, many=True)
    tid, name = extract_teacher_name_from_question(question, teachers)

    if not tid:
        return "Which teacher would you like to locate? Please include their name."

    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()  # reuse from teacher expansion

    result = query("""
        SELECT class FROM timetable
        WHERE teacher_id = %s AND day = %s AND period_no = %s
    """, (tid, today, current_period), fetch=True)

    if result:
        return f"**{name}** is currently teaching **{result[0]}**."
    return f"**{name}** doesn't have a class right now — likely free or unavailable."


def handle_classroom_occupant(question):
    """Who's teaching a specific class right now."""
    import datetime
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
    import datetime
    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()

    all_teachers = query("SELECT teacher_id, name FROM teachers", fetch=True, many=True)
    busy = query("""
        SELECT DISTINCT teacher_id FROM timetable
        WHERE day = %s AND period_no = %s
    """, (today, current_period), fetch=True, many=True)

    busy_ids = {row[0] for row in busy} if busy else set()
    free = [name for tid, name in all_teachers if tid not in busy_ids]

    if free:
        return f"Free teachers right now: **{', '.join(free)}**."
    return "All teachers are currently in class."


def handle_teacher_schedule_lookup(question):
    """Full schedule (or day-filtered) for a specific named teacher."""
    teachers = query("SELECT teacher_id, name FROM teachers", fetch=True, many=True)
    tid, name = extract_teacher_name_from_question(question, teachers)

    if not tid:
        return "Which teacher's schedule would you like to see?"

    day = extract_day_from_question(question)  # reuse existing function

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
    subjects = query("SELECT DISTINCT subject_name FROM subjects", fetch=True, many=True)
    subject_names = [s[0] for s in subjects] if subjects else []
    subject = extract_subject_from_question(question, subject_names)  # reuse existing
    cls = extract_class_from_question(question)

    if not subject:
        return "Which subject would you like to know the teacher for?"

    query_str = """
        SELECT DISTINCT te.name, t.class
        FROM timetable t
        JOIN teachers te ON t.teacher_id = te.teacher_id
        JOIN subjects s ON t.subject_id = s.subject_id
        WHERE s.subject_name = %s
    """
    params = [subject]
    if cls:
        query_str += " AND t.class = %s"
        params.append(cls)

    results = query(query_str, tuple(params), fetch=True, many=True)
    if results:
        lines = [f"- **{cls}**: {name}" for name, cls in results]
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
    result = query("SELECT DISTINCT subject FROM teachers", fetch=True, many=True)
    known_subjects = [r[0] for r in result] if result else []
    subject = extract_subject_from_question(question, known_subjects)

    if not subject:
        return "Which subject would you like the teacher count for?"

    count_result = query(
        "SELECT COUNT(*) FROM teachers WHERE subject = %s",
        (subject,), fetch=True
    )
    count = count_result[0] if count_result else 0
    return f"There are **{count} {subject} teacher(s)** at the school."


# ---------------------------------------------------------
# Update the detect_intent() call inside answer_principal() to:
# intent = detect_intent(question, [
#     "greeting", "thanks", "help", "total_students", "total_teachers",
#     "class_wise_count", "teacher_location", "classroom_occupant",
#     "free_teachers", "teacher_schedule_lookup",
#     "school_wide_subject_teacher", "low_attendance_count",
#     "pending_fees_count", "teacher_count_by_subject"
# ])
# Then wire each new intent to its handler above, same elif pattern
# as the existing principal answer function.
# ---------------------------------------------------------
