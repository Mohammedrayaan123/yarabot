# ============================================================
# TEACHER NLP EXPANSION — reference code for Claude Code to adapt
# ============================================================
# Teachers are the primary daytime users (students can't use
# phones at school). This adds real-time, in-the-moment features:
# what class to teach next, current period, free periods today,
# day-specific schedule, and quick lookups.
#
# Reuses extract_day_from_question() from the student NLP
# expansion (should already exist in app.py from that change) -
# do not duplicate it, reference the same function.
# ============================================================

# ---------------------------------------------------------
# ADD to nlp_helpers.py INTENT_DATA dictionary:
# ---------------------------------------------------------
NEW_TEACHER_INTENTS = {
    "next_class": {
        "phrases": ["next class", "what am i teaching next", "which class next",
                    "next period", "what's next"],
        "keywords": ["next"],
    },
    "current_class": {
        "phrases": ["what am i teaching now", "current class", "right now"],
        "keywords": ["now", "current"],
    },
    "free_periods": {
        "phrases": ["free periods", "am i free", "do i have a free period",
                    "any free time"],
        "keywords": ["free"],
    },
    "periods_remaining": {
        "phrases": ["periods left", "how many periods left", "periods remaining today"],
        "keywords": ["remaining", "left"],
    },
    "teacher_identity": {
        "phrases": ["what is my name", "who am i", "my details", "my subject"],
        "keywords": ["name", "who"],
    },
}

# Note: "timetable" intent already exists and will be extended to
# support day-filtering via extract_day_from_question() - same as
# the student side. No separate intent needed for that.


# ---------------------------------------------------------
# ADD to app.py — teacher answer handlers
# These assume a helper get_period_times() that maps period
# numbers to approximate start times. Since the DB doesn't store
# exact period times, we use the same simple estimate as the
# student "next period" feature: period 1 starts ~8am, ~1 hour each.
# If you want this precise, we'd need to add period start/end
# times to the timetable table - flag this to Rayaan as a
# possible future improvement rather than guessing further.
# ---------------------------------------------------------

def estimate_current_period_number():
    """Rough estimate of which period number is happening right now,
    based on current time. Same heuristic used for student next_period."""
    import datetime
    now = datetime.datetime.now()
    current_hour = now.hour
    return max(1, current_hour - 7)  # period 1 starts ~8am


def handle_teacher_next_class(teacher_id):
    """What class is this teacher teaching next, today."""
    import datetime
    today = datetime.datetime.now().strftime("%A")
    current_period = estimate_current_period_number()

    results = query("""
        SELECT period_no, class
        FROM timetable
        WHERE teacher_id = %s AND day = %s AND period_no > %s
        ORDER BY period_no
        LIMIT 1
    """, (teacher_id, today, current_period), fetch=True)

    if results:
        period_no, cls = results
        return f"Your next class is **{cls}** at Period {period_no}."
    return "You have no more classes scheduled for today. 🎉"


def handle_teacher_current_class(teacher_id):
    """What class is this teacher teaching right now, if any."""
    import datetime
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
    import datetime
    today = datetime.datetime.now().strftime("%A")

    results = query("""
        SELECT period_no FROM timetable
        WHERE teacher_id = %s AND day = %s
        ORDER BY period_no
    """, (teacher_id, today), fetch=True, many=True)

    if not results:
        return f"No classes scheduled for you today ({today}) — fully free!"

    occupied_periods = {r[0] for r in results}
    # Assume a standard school day has periods 1 through 8 - adjust
    # this range if your school's actual period count differs
    all_periods = set(range(1, 9))
    free = sorted(all_periods - occupied_periods)

    if free:
        free_list = ", ".join(f"Period {p}" for p in free)
        return f"You're free during: **{free_list}** today."
    return "You're booked solid today — no free periods!"


def handle_teacher_periods_remaining(teacher_id):
    """How many periods does this teacher have left today, from now."""
    import datetime
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


def handle_teacher_timetable_with_day(question, teacher_id):
    """Extended timetable - filters by day if mentioned (today/Monday/etc)."""
    day = extract_day_from_question(question)  # reuse existing function

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
    else:
        lines = [f"- **{d}**, Period {p}: Class **{cls}**" for d, p, cls in results]
        return "Your full schedule:\n" + "\n".join(lines)


# ---------------------------------------------------------
# Update the detect_intent() call inside answer_teacher() to:
# intent = detect_intent(question, [
#     "greeting", "thanks", "help", "period_count", "timetable",
#     "classes_assigned", "next_class", "current_class",
#     "free_periods", "periods_remaining", "teacher_identity"
# ])
# Then wire each new intent to its handler function above,
# following the same elif pattern as the existing code.
# ---------------------------------------------------------
