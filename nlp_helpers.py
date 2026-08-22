
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
        "phrases": ["how many days present", "how many days absent", "attendance percentage"],
        "keywords": ["attendance", "present", "presence", "absent", "absentee", "bunk", "bunked"],
    },
    "exam": {
        "phrases": ["exam date", "next exam", "when is my exam", "test date"],
        "keywords": ["exam", "exams", "test", "tests", "examination", "examinations", "quiz"],
    },
    "timetable": {
        "phrases": ["my timetable", "my schedule", "class schedule", "today's classes"],
        "keywords": ["timetable", "schedule", "periods", "classes", "routine"],
    },
    "fee": {
        "phrases": ["fee status", "fees paid", "school fees"],
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
        # "periods" added after live testing caught a real collision:
        # "free periods today" tied 4-4 with period_count (its own phrase
        # "periods today" is a literal substring match too), and list order
        # would have silently picked period_count - answering with the
        # WEEKLY total instead of what today's free periods actually are.
        "keywords": ["free", "periods"],
    },
    "periods_remaining": {
        # Several phrasings here beyond the terse "periods left", added
        # after live testing: "how many periods do i have left" (arguably
        # the more natural way to ask this) scored ZERO phrase match here
        # (the inserted "do i have" breaks a literal substring match)
        # while period_count's shorter "how many periods" phrase still hit
        # as a prefix, letting the wrong intent win (4 vs 2).
        "phrases": ["periods left", "how many periods left", "periods remaining today",
                    "how many periods do i have left", "how many periods left today",
                    "how many more periods"],
        # "periods" is deliberately included here, not just "remaining"/"left":
        # without it, "how many periods left" ties 4-4 with the existing
        # period_count intent (both score a phrase match + one keyword hit),
        # and detect_intent's tie-break falls back to list order - fragile,
        # and would silently answer with the WEEKLY total instead of TODAY'S
        # remaining count. Adding "periods" gives this intent its own extra
        # keyword point so it wins outright (5 vs 4) regardless of list
        # order. Verified against real inputs before wiring this in.
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


def clean_question(question):
    """Lowercase, expand contractions, strip punctuation.

    Uses word-boundary regex, not plain .replace(): a naive substring
    replace of "im" -> "i am" corrupted any word CONTAINING "im" as a
    substring - "timetable" became "ti ametable", along with "time",
    "similar", "significant", "primary", "climate", etc. This had been
    silently surviving only because the mangled "ametable" token still
    happened to fuzzy-match the "timetable" keyword (0.82 similarity,
    above the 0.75 cutoff) - a lucky accident, not something to rely on.
    It broke for real once a new intent's phrase needed an exact substring
    match instead of a keyword fuzzy-match. Found via testing, not
    theoretical - reproduced and fixed before it could ship broken.
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


def score_intent(cleaned_question, words, intent_name):
    """Return a score for how well the question matches one intent.
    Higher score = stronger match. Phrase matches count more than
    single-word matches, since phrases are more specific/reliable."""
    data = INTENT_DATA[intent_name]
    score = 0

    # Phrase matches (checked against the whole cleaned question, not
    # word by word) - these are strong signals, worth more points
    for phrase in data["phrases"]:
        if phrase in cleaned_question:
            score += 3

    # Single word matches, with fuzzy typo tolerance
    for word in words:
        close = difflib.get_close_matches(word, data["keywords"], n=1, cutoff=0.75)
        if close:
            score += 1

    return score


def detect_intent(question, possible_intents, confidence_threshold=1):
    """
    Given a raw question and a list of intent names to consider,
    return the best-matching intent name, or None if nothing scores
    high enough to be confident about.

    confidence_threshold: minimum score needed to accept a match.
    Raise this if the bot seems to guess wrong too often; lower it
    if it seems too hesitant to answer.
    """
    cleaned = clean_question(question)
    words = tokenize(cleaned)

    best_intent = None
    best_score = 0

    for intent_name in possible_intents:
        score = score_intent(cleaned, words, intent_name)
        if score > best_score:
            best_score = score
            best_intent = intent_name

    if best_score >= confidence_threshold:
        return best_intent
    return None
