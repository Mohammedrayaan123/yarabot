# ============================================================
# NLP EXPANSION — reference code for Claude Code to adapt
# ============================================================
# Adds to nlp_helpers.py: new intents for day-specific timetable,
# identity questions, roll number, class, next period, specific
# subject teacher/exam lookups.
#
# Adds to app.py: the new answer logic for each intent, using
# the existing MySQL query pattern already in answer_student()
# and answer_teacher().
# ============================================================

# ---------------------------------------------------------
# ADD to nlp_helpers.py INTENT_DATA dictionary:
# ---------------------------------------------------------
NEW_INTENTS = {
    "identity": {
        "phrases": ["what is my name", "who am i", "my details", "my info"],
        "keywords": ["name", "who"],
    },
    "roll_number": {
        "phrases": ["my roll number", "what is my roll", "roll no"],
        "keywords": ["roll"],
    },
    "my_class": {
        "phrases": ["what class am i in", "which class am i", "my class"],
        "keywords": ["class"],
    },
    "next_period": {
        "phrases": ["next period", "next class", "what's next"],
        "keywords": ["next"],
    },
    "subject_teacher": {
        "phrases": ["who teaches me", "who is my teacher for", "teacher for"],
        "keywords": ["teaches", "teacher"],
    },
}

# Note: "timetable" intent already exists — we extend its HANDLING
# in app.py to detect a day name mentioned in the question, not
# add a new intent. Same for exam — we extend it to detect a
# specific subject name mentioned, not add a new intent.


# ---------------------------------------------------------
# ADD to app.py — helper to extract a day name from a question
# ---------------------------------------------------------
DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

def extract_day_from_question(question):
    """Returns the day name if mentioned in the question, else None.
    Also recognizes 'today' and converts it to the actual current day name."""
    q = question.lower()
    if "today" in q:
        import datetime
        return datetime.datetime.now().strftime("%A").lower()
    for day in DAY_NAMES:
        if day in q:
            return day
    return None


def extract_subject_from_question(question, known_subjects):
    """
    Returns a subject name if one is mentioned in the question.
    known_subjects: list of actual subject names from the DB
    (fetch once, pass in) so we're not guessing/hardcoding subjects.
    """
    q = question.lower()
    for subject in known_subjects:
        if subject.lower() in q:
            return subject
    return None


# ---------------------------------------------------------
# UPDATED answer_student() — new intent handling
# Add these as new elif branches, and update the intent list
# passed to detect_intent() to include the new intents.
# ---------------------------------------------------------

# Update the detect_intent call to include new intents:
# intent = detect_intent(question, [
#     "greeting", "thanks", "help", "attendance", "exam", "timetable",
#     "fee", "identity", "roll_number", "my_class", "next_period",
#     "subject_teacher"
# ])

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
    import datetime
    now = datetime.datetime.now()
    current_day = now.strftime("%A")

    results = query("""
        SELECT t.period_no, s.subject_name, te.name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.subject_id
        JOIN teachers te ON t.teacher_id = te.teacher_id
        JOIN students st ON st.class = t.class
        WHERE st.student_id = %s AND t.day = %s
        ORDER BY t.period_no
    """, (student_id, current_day), fetch=True, many=True)

    if not results:
        return f"No timetable found for {current_day}."

    # Assume periods roughly map to hours starting from school start time
    # This is approximate - for a precise version we'd need period start
    # times in the database, which we can add later if needed.
    current_hour = now.hour
    # Simple heuristic: period 1 starts around 8am, each period ~1 hour
    estimated_current_period = max(1, current_hour - 7)

    upcoming = [p for p in results if p[0] > estimated_current_period]
    if upcoming:
        period_no, subject, teacher = upcoming[0]
        return f"Your next class is **{subject}** (Period {period_no}) with {teacher}."
    return "Looks like you're done for the day! No more periods scheduled."


def handle_timetable_with_day(question, student_id, known_subjects=None):
    """Extended version of timetable handling - filters by day if mentioned."""
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
    else:
        lines = [f"- **{d}**, Period {p}: **{subj}** with {teacher}" for d, p, subj, teacher in results]
        return "Your full timetable:\n" + "\n".join(lines)


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
