
import difflib
import re

# ---- Filler / stopwords - words that don't tell us the TOPIC ----
STOPWORDS = {
    "is", "my", "the", "a", "an", "please", "can", "you", "i",
    "me", "to", "for", "of", "on", "in", "at", "do", "does",
    "what", "when", "how", "will", "be", "am", "are", "have",
    "has", "give", "tell", "know", "want", "would", "could",
    "show", "let", "and", "or", "with", "about", "there", "it",
    "this", "that", "get", "got", "need", "any", "some", "today",
    "please", "kindly", "just", "so", "up", "down"
}

# ---- Common contractions, expanded before processing ----
CONTRACTIONS = {
    "what's": "what is", "whats": "what is",
    "when's": "when is", "whens": "when is",
    "how's": "how is", "hows": "how is",
    "i'm": "i am", "im": "i am",
    "don't": "do not", "dont": "do not",
    "can't": "cannot", "cant": "cannot",
    "won't": "will not", "wont": "will not",
}

# ---- Each intent's keyword/phrase list. Bigger + more varied = smarter bot.
# Multi-word phrases are checked FIRST (more reliable), single words checked
# after with fuzzy typo-matching.
INTENT_DATA = {
    "attendance": {
        # "attendance %" / "check attendance" etc. cover terse phone-typed
        # phrasing with no verb or pronoun.
        "phrases": ["how many days present", "how many days absent", "attendance percentage",
                    "attendance %", "my attendance", "attendance status", "attendance record",
                    "check attendance", "how's my attendance"],
        "keywords": ["attendance", "present", "presence", "absent", "absentee", "bunk", "bunked"],
    },
    "exam": {
        "phrases": ["exam date", "next exam", "when is my exam", "test date",
                    "exam dates", "upcoming exam", "upcoming exams", "next test",
                    "my exam date", "check exam date"],
        "keywords": ["exam", "exams", "test", "tests", "examination", "examinations", "quiz"],
    },
    "timetable": {
        # "time table" as two words doesn't contain "timetable" as one
        # token, so it scored nothing until added explicitly.
        #
        # Both "todays x" and "today's x" are listed: clean_question()
        # doesn't normalize apostrophes and "todays" isn't a CONTRACTIONS
        # entry, so they're different substrings after cleaning. Without
        # both, "whats todays timetable" scored a genuine zero - "timetable"/
        # "classes" are AMBIGUOUS_KEYWORDS, and "todays" isn't a personal
        # pronoun, so the bare keyword match got silently discarded.
        "phrases": ["my timetable", "my schedule", "class schedule", "today's classes",
                    "todays classes", "time table", "class routine", "today's schedule",
                    "todays schedule", "today's timetable", "todays timetable",
                    "weekly timetable", "my periods today", "what's my schedule",
                    "what classes today"],
        "keywords": ["timetable", "schedule", "periods", "classes", "routine"],
    },
    "fee": {
        "phrases": ["fee status", "fees paid", "school fees",
                    "fees status", "fee due", "fees due", "is my fee paid",
                    "check fees", "outstanding fees", "fee balance"],
        "keywords": ["fee", "fees", "payment", "paid", "dues", "due"],
    },
    "period_count": {
        "phrases": ["how many periods", "number of periods", "periods today", "periods this week"],
        "keywords": ["periods", "period"],
    },
    "classes_assigned": {
        "phrases": ["which classes", "my classes", "classes assigned", "classes i teach"],
        "keywords": ["classes", "class", "assigned", "teach"],
    },
    "total_students": {
        "phrases": ["how many students", "total students", "number of students"],
        "keywords": ["students", "student"],
    },
    "total_teachers": {
        "phrases": ["how many teachers", "total teachers", "number of teachers"],
        "keywords": ["teachers", "teacher", "staff"],
    },
    "class_wise_count": {
        "phrases": ["students per class", "class wise", "class-wise", "breakdown by class"],
        "keywords": ["breakdown", "classwise"],
    },
    "greeting": {
        "phrases": ["good morning", "good afternoon", "good evening"],
        "keywords": ["hi", "hello", "hey", "yo"],
    },
    "thanks": {
        "phrases": ["thank you", "thanks a lot"],
        "keywords": ["thanks", "thank", "thx", "ty"],
    },
    "help": {
        "phrases": ["what can you do", "help me", "what do you do"],
        "keywords": ["help", "options", "commands"],
    },

    # ---- Student expansion ----
    "identity": {
        "phrases": ["what is my name", "who am i", "my details", "my info"],
        "keywords": ["name", "who"],
    },
    "roll_number": {
        "phrases": ["my roll number", "what is my roll", "roll no", "roll number"],
        "keywords": ["roll"],
    },
    "my_class": {
        "phrases": ["what class am i in", "which class am i", "my class","which grade am i in"],
        "keywords": ["class","grade"],
    },
    "next_period": {
        "phrases": ["next period", "next class", "what's next"],
        "keywords": ["next"],
    },
    "subject_teacher": {
        "phrases": ["who teaches me", "who is my teacher for", "teacher for"],
        "keywords": ["teaches", "teacher"],
    },

    # ---- Teacher expansion ----
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
        # "periods" keyword needed: "free periods today" was tying 4-4 with
        # period_count (its "periods today" phrase also matches), and list
        # order would silently pick period_count's weekly total instead.
        "keywords": ["free", "periods"],
    },
    "periods_remaining": {
        # "how many periods do i have left" scored zero phrase match here
        # (the inserted "do i have" breaks the substring), while
        # period_count's shorter "how many periods" still matched as a
        # prefix - letting period_count win with the wrong (weekly) answer.
        # More phrasings added to close that gap.
        "phrases": ["periods left", "how many periods left", "periods remaining today",
                    "how many periods do i have left", "how many periods left today",
                    "how many more periods"],
        # "periods" keyword needed for the same reason as free_periods above -
        # without it this ties 4-4 with period_count and falls back to list
        # order.
        "keywords": ["remaining", "left", "periods"],
    },
    "teacher_identity": {
        "phrases": ["what is my name", "who am i", "my details", "my subject"],
        "keywords": ["name", "who"],
    },

    # ---- Principal expansion ----
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
    # A class's general timetable - distinct from classroom_occupant (who's
    # in THIS class right now) and teacher_schedule_lookup (a named
    # teacher's schedule). "schedule for 10a" can't be told apart from
    # teacher_schedule_lookup's own "schedule for" phrase by substring
    # matching alone - app.py's answer_principal() redirects there when a
    # class code is present but no named teacher was found in the question.
    "class_timetable_lookup": {
        "phrases": ["timetable for class", "schedule for class", "class timetable",
                    "class schedule", "class routine"],
        "keywords": ["timetable", "schedule", "class", "classes"],
        "class_code_bypass": True,
    },
    "school_wide_subject_teacher": {
        "phrases": ["who teaches", "teacher for subject"],
        "keywords": ["teaches"],
    },
    # All subject-teacher pairs for a class - distinct from classroom_occupant
    # (who's in THIS class right now) and school_wide_subject_teacher (which
    # teacher teaches a SUBJECT school-wide). Shares "who teaches" with
    # school_wide_subject_teacher with nothing to distinguish a class code
    # from a subject name by substring alone - same redirect pattern as
    # class_timetable_lookup above.
    "class_teacher_lookup": {
        "phrases": ["who teaches class", "teacher for class", "teachers for class",
                    "who is the teacher for class", "class teachers", "class teacher",
                    "teachers assigned to class"],
        "keywords": ["teacher", "teachers", "teach", "teaches", "class", "classes"],
        "class_code_bypass": True,
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

    # ---- Shared across all three roles ----
    "notices": {
        "phrases": ["latest notices", "any announcements", "school notices",
                    "any updates", "recent announcements", "any notices",
                    "any notice", "new notices"],
        "keywords": ["notice", "notices", "announcement", "announcements"],
    },
}


# Words that legitimately belong to a personal-data intent's keyword list
# but also show up in general almanac content ("teachers day", "exam week
# dates", "which classes are on holiday"). A bare match on one of these
# with no phrase match and no personal pronoun doesn't score - see
# score_intent() below. One-time audit, not a growing patch list: a new
# almanac event doesn't need an entry here, app.py's almanac-overlap check
# catches that automatically.
AMBIGUOUS_KEYWORDS = {
    # collide with almanac event names ("Teacher's Day"), PTM content, staff lists
    "teacher", "teachers", "teach", "teaches", "teaching", "staff",
    # collide with the almanac's own exam calendar content
    "exam", "exams", "test", "tests", "examination", "examinations",
    # collide with exam/PTM schedules, academic calendar content
    "schedule", "timetable", "routine",
    # "class"/"classes" appears constantly as a GRADE reference ("Classes
    # I-III"), not a personal "my class" reference
    "class", "classes",
    # "period"/"periods" considered but left off - zero occurrences in
    # school_almanac.txt, and tightening it broke a real working case
    # ("mondays periods" for a student). App.py's almanac-overlap check is
    # the safety net if a future almanac addition ever does collide.
    #
    # appears constantly as a generic reference ("students of Grade IV-V...")
    "student", "students",
    # "attendance policy"/"attendance requirement" is genuine almanac content
    "attendance", "present", "presence", "absent", "absentee",
    # "fee structure"/"fees" appears in general almanac policy content
    "fee", "fees", "due", "dues",
    # extremely common words that say nothing about personal vs. general
    "name", "who", "where",
    # show up in countless general sentences ("next holiday", "free dress day")
    "next", "now", "current", "free", "available",
    "remaining", "left", "pending",
}
# "notice"/"notices"/"announcement"/"announcements" are NOT in the set
# above - zero occurrences in school_almanac.txt, and notices content lives
# in the `notices` MySQL table, not the almanac file.

# Ambiguous in isolation but not flagged - existing phrase lists already
# make a bare match safe:
#   - "roll" (roll_number) - school-record-specific, no almanac use
#   - "bunk"/"bunked" (attendance) - informal slang, not almanac phrasing
#   - "breakdown"/"classwise" (class_wise_count) - not almanac phrasing
#   - "occupant" (classroom_occupant) - not a word almanac content would use
#   - "assigned" (classes_assigned) - not almanac phrasing
#   - "location" (teacher_location) - almanac has no building/room content
#   - "unpaid" (pending_fees_count) - specific to fee defaulting


# Bare personal-reference signal words - deliberately small and separate
# from app.py's PERSONAL_SIGNALS (which also carries bespoke ROUTING
# phrases like "roll no" that don't belong in a generic "is this worded in
# the first person" check). tokenize() already strips "my"/"i"/"me"/"mine"
# as stopwords before scoring, so this checks the CLEANED question string
# directly, as whole words - not the token list.
_PERSONAL_SIGNAL_RE = re.compile(r"\b(my|i|me|mine)\b")


def has_personal_signal(cleaned_question):
    """True if the question refers to the asker in the first person."""
    return bool(_PERSONAL_SIGNAL_RE.search(cleaned_question))


# Mirrors app.py's extract_class_from_question() pattern - kept as a
# separate regex here rather than imported, since app.py already imports
# FROM this module (importing back would be circular). Update both together
# if the class-code format this school uses ever changes. cleaned_question
# is already lowercased by clean_question(), so [a-z] (not [A-Za-z]) is
# enough here.
_CLASS_CODE_RE = re.compile(r'\b\d{1,2}[\s-]?[a-z]\b')


def has_class_code(cleaned_question):
    """True if a class code (e.g. "10-a"/"10a"/"10 a") is mentioned anywhere in the question."""
    return bool(_CLASS_CODE_RE.search(cleaned_question))


def clean_question(question):
    """Lowercase, expand contractions, strip punctuation.

    Word-boundary regex, not plain .replace(): a naive "im" -> "i am"
    replace corrupted any word containing "im" as a substring - "timetable"
    became "ti ametable". Was silently surviving because the mangled token
    still fuzzy-matched "timetable" by luck, until a phrase needed an exact
    substring match instead.
    """
    question = question.lower().strip()
    for contraction, expanded in CONTRACTIONS.items():
        question = re.sub(r"\b" + re.escape(contraction) + r"\b", expanded, question)
    question = re.sub(r"[?!.,]", "", question)
    return question


def tokenize(question):
    """Split into meaningful words (filler words removed)."""
    words = question.split()
    return [w for w in words if w not in STOPWORDS]


def score_intent(cleaned_question, words, intent_name, personal_signal, class_code_present=False):
    """
    Score a question against one intent. Phrase matches outweigh single
    keywords, since phrases are more specific.

    A bare AMBIGUOUS_KEYWORDS match with no phrase and no personal_signal
    scores nothing - that's the "teachers day"/"exam schedule" failure
    mode, a common word that also happens to belong to a personal intent.
    class_code_present is the same kind of bypass, but opt-in per intent
    (INTENT_DATA's class_code_bypass) so a stray class code elsewhere
    doesn't loosen every intent's protection.
    """
    data = INTENT_DATA[intent_name]
    score = 0

    phrase_matched = False
    for phrase in data["phrases"]:
        if phrase in cleaned_question:
            score += 3
            phrase_matched = True

    bypass_signal = personal_signal or (class_code_present and data.get("class_code_bypass", False))

    # Single word matches, with fuzzy typo tolerance
    for word in words:
        close = difflib.get_close_matches(word, data["keywords"], n=1, cutoff=0.75)
        if not close:
            continue
        matched_keyword = close[0]
        if matched_keyword in AMBIGUOUS_KEYWORDS and not phrase_matched and not bypass_signal:
            continue
        score += 1

    return score


def detect_intent_with_score(question, possible_intents, confidence_threshold=1):
    """
    Same matching as detect_intent(), but also returns the winning score -
    used by app.py's routing to tell a phrase-backed match (score >= 3) apart
    from a weak, keyword-only match (score 1-2) when deciding whether to
    double-check against the almanac before trusting NLP over Gemini.

    Returns (intent_name_or_None, best_score).
    """
    cleaned = clean_question(question)
    words = tokenize(cleaned)
    personal_signal = has_personal_signal(cleaned)
    class_code_present = has_class_code(cleaned)

    best_intent = None
    best_score = 0

    for intent_name in possible_intents:
        score = score_intent(cleaned, words, intent_name, personal_signal, class_code_present)
        if score > best_score:
            best_score = score
            best_intent = intent_name

    if best_score >= confidence_threshold:
        return best_intent, best_score
    return None, best_score


def detect_intent(question, possible_intents, confidence_threshold=1):
    """
    Given a raw question and a list of intent names to consider,
    return the best-matching intent name, or None if nothing scores
    high enough to be confident about.

    confidence_threshold: minimum score needed to accept a match.
    Raise this if the bot seems to guess wrong too often; lower it
    if it seems too hesitant to answer.
    """
    intent, _ = detect_intent_with_score(question, possible_intents, confidence_threshold)
    return intent
