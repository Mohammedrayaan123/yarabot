
import ast
import difflib
import re

from validators import GRADE_SECTION_PATTERN, EARLY_YEARS_CLASSES

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
                    "check attendance", "how's my attendance",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "attendence", "check my attendence", "my attendence status", "how many days have i missed", "did i bunk too much", "how many days was i absent", "how many days was i present", "days present count", "days absent count", "how many absences do i have", "total days absent", "total days present"],
        "keywords": ["attendance", "present", "presence", "absent", "absentee", "bunk", "bunked"],
    },
    "exam": {
        "phrases": ["exam date", "next exam", "when is my exam", "test date",
                    "exam dates", "upcoming exam", "upcoming exams", "next test",
                    "my exam date", "check exam date",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "wen is my exam", "xam date", "test dates pls", "when do i have exams", "tell me my exam dates", "gimme exam dates", "half yearly date", "pre board date", "prelims date", "when is half yearly", "when is pre board", "do i have an exam soon", "exam kab hai", "mera exam kab hai", "quickly tell me exam date", "just tell me when my exam is", "i need to know my exam date", "i wanted to check my exam dates", "upcoming test date", "my upcoming exams", "check my test date", "check upcoming exams"],
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
                    "what classes today",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "scedule", "my scedule", "timetabel", "my timetabel", "what do i have today", "what do i have tomorrow"],
        "keywords": ["timetable", "schedule", "periods", "classes", "routine"],
    },
    "fee": {
        "phrases": ["fee status", "fees paid", "school fees",
                    "fees status", "fee due", "fees due", "is my fee paid",
                    "check fees", "outstanding fees", "fee balance",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "fee status pls", "fees status pls", "is my fee cleared", "is my fees cleared", "have i paid my fees", "did i pay fees", "fee payment status", "fees payment status", "do i owe fees", "do i owe any fees", "fee ka status", "meri fees paid hai kya", "check my fees", "check fee status", "my fee details", "tuition status", "tuition fee status", "term fee status", "fees due date", "last date for fees", "fee balance check", "outstanding balance", "how much fee do i owe", "how much do i owe", "fee cleared or not", "fees cleared or not", "show my fee status", "gimme fee status", "tell me if my fees are paid", "i want to know my fee status", "quickly check fees", "just tell me fee status", "bro is my fee paid", "my fee history"],
        "keywords": ["fee", "fees", "payment", "paid", "dues", "due"],
    },
    "period_count": {
        "phrases": ["how many periods", "number of periods", "periods today", "periods this week",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "period count today", "show my period count", "just tell me period count", "how many lectures today", "how many lectures this week", "weekly period count", "daily period count", "total lectures i have", "my period load today", "workload today", "how heavy is my day"],
        "keywords": ["periods", "period"],
    },
    "classes_assigned": {
        "phrases": ["which classes", "my classes", "classes assigned", "classes i teach",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "which sections am i assigned", "sections i handle", "grades assigned to me", "sections covered by me"],
        "keywords": ["classes", "class", "assigned", "teach"],
    },
    "total_students": {
        "phrases": ["how many students", "total students", "number of students",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "total student count", "how many students in school", "how many students enrolled", "total strength", "school strength", "overall student count", "how many kids in school", "how many students do we have", "enrollment numbers", "total enrollment", "give me student count", "gimme student total", "quickly tell me student count", "just give me student total", "school student total", "total pupils", "how many pupils", "number of pupils", "students enrolled total", "overall enrollment", "school population", "total kids enrolled"],
        "keywords": ["students", "student"],
    },
    "total_teachers": {
        "phrases": ["how many teachers", "total teachers", "number of teachers",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "total staff count", "how many staff members", "how many faculty", "total faculty", "number of faculty members", "faculty size", "how many educators"],
        "keywords": ["teachers", "teacher", "staff"],
    },
    "class_wise_count": {
        "phrases": ["students per class", "class wise", "class-wise", "breakdown by class",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "section wise count", "breakdown by section", "gimme classwise breakdown", "class-wise breakdown pls", "how many per section", "just give me the breakdown", "section distribution"],
        "keywords": ["breakdown", "classwise"],
    },
    "greeting": {
        "phrases": ["good morning", "good afternoon", "good evening",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "hii", "hiii", "heyy", "heya", "yo yo", "sup", "wassup", "good day", "morning", "hey there", "hi there", "hello there", "salaam", "assalam o alaikum", "namaste", "hi bot", "hey bot", "hi nova", "hey nova"],
        "keywords": ["hi", "hello", "hey", "yo"],
    },
    "thanks": {
        "phrases": ["thank you", "thanks a lot",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "thanks bro", "thank u", "thnx", "tysm", "much appreciated", "appreciate it", "thanks a ton", "cheers", "thanks so much", "thankyou", "thank you so much", "great thanks", "ok thanks", "perfect thanks", "thanks for that", "thanks nova", "thank you nova", "thx a lot", "many thanks", "thanks a bunch"],
        "keywords": ["thanks", "thank", "thx", "ty"],
    },
    "help": {
        "phrases": ["what can you do", "help me", "what do you do",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "what can u do", "what do u do", "how do i use this", "how does this work", "what can this bot do", "options pls", "show me options", "show commands", "list commands", "i need help", "help pls", "help plz", "can u help me", "what should i ask", "what can i ask you", "guide me", "how to use this bot", "what are my options", "menu", "show menu"],
        "keywords": ["help", "options", "commands"],
    },

    # ---- Student expansion ----
    "identity": {
        "phrases": ["what is my name", "who am i", "my details", "my info", "share my identity card info",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "show my info", "show my profile", "my profile", "my id card info", "id card details", "gimme my info", "identity card details", "show identity card", "my info pls", "my personal info", "personal details pls", "my record", "show my record", "verify my identity", "my basic info", "profile details", "account details", "my account info"],
        "keywords": ["name", "who"],
    },
    "roll_number": {
        "phrases": ["my roll number", "what is my roll", "roll no", "roll number", "my enrollment number please",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "tell me my roll no", "roll number pls", "my roll no pls", "give me my roll number", "gimme roll no", "show my roll number", "quickly tell me roll no", "just tell me roll number", "roll no kya hai", "mera roll number kya hai", "my enrollment no", "enrollment number pls", "my admission number", "admission no", "my seat number", "roll no check", "check my roll number", "confirm my roll no", "roll id", "my roll id", "id number pls"],
        "keywords": ["roll"],
    },
    "my_class": {
        "phrases": ["what class am i in", "which class am i", "my class","which grade am i in",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "what grade am i in", "my grade", "my section", "which section am i in", "which grade and section", "what section am i", "which standard am i in", "my standard", "standard and section", "which grade do i study in", "am i in grade 10"],
        "keywords": ["class","grade"],
    },
    "next_period": {
        "phrases": ["next period", "next class", "what's next",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "after this what do i have", "right after this"],
        "keywords": ["next"],
    },
    "subject_teacher": {
        "phrases": ["who teaches me", "who is my teacher for", "teacher for",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "my math sir", "my science maam", "my english maam"],
        "keywords": ["teaches", "teacher"],
    },

    # ---- Teacher expansion ----
    "next_class": {
        "phrases": ["next class", "what am i teaching next", "which class next",
                    "next period", "what's next"],
        "keywords": ["next"],
    },
    "current_class": {
        "phrases": ["what am i teaching now", "current class", "right now",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "what am i doing right now"],
        "keywords": ["now", "current"],
    },
    "free_periods": {
        "phrases": ["free periods", "am i free", "do i have a free period",
                    "any free time",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "any spare time today", "do i have a gap today", "when do i get a break", "any breaks today"],
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
                    "how many more periods",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "am i done for the day", "am i almost done", "quickly how many left", "how much longer today", "how many more today", "am i finished for today"],
        # "periods" keyword needed for the same reason as free_periods above -
        # without it this ties 4-4 with period_count and falls back to list
        # order.
        "keywords": ["remaining", "left", "periods"],
    },
    "teacher_identity": {
        "phrases": ["what is my name", "who am i", "my details", "my subject",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "show my profile", "my profile", "my employee details", "employee id", "my contact details", "my registered contact", "my personal info", "my record", "show my record", "my basic info"],
        "keywords": ["name", "who"],
    },

    # ---- Principal expansion ----
    "teacher_location": {
        "phrases": ["where is", "which class is teaching", "what is teaching right now",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "wheres mr ahmed", "wheres ms priya", "which room is he in", "which room is she in", "where can i find", "gimme location of", "just tell me where he is", "which classroom is he in", "which classroom is she in"],
        "keywords": ["where", "location"],
    },
    "classroom_occupant": {
        "phrases": ["who is teaching class", "who is in class", "which teacher is in",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "whos teaching 10a", "who's occupying that room", "who's in room 10a", "occupant check"],
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
    # (who's in THIS class right now), school_wide_subject_teacher (which
    # teacher teaches a SUBJECT school-wide), and class_teacher (the ONE
    # designated homeroom teacher, below). Shares "who teaches" with
    # school_wide_subject_teacher with nothing to distinguish a class code
    # from a subject name by substring alone - same redirect pattern as
    # class_timetable_lookup above. Deliberately does NOT include "class
    # teacher" (singular) - that phrase means the ONE homeroom teacher, not
    # this intent's full subject roster; see class_teacher's own phrases.
    "class_teacher_lookup": {
        "phrases": ["who teaches class", "teacher for class", "teachers for class",
                    "who is the teacher for class", "class teachers",
                    "teachers assigned to class"],
        "keywords": ["teacher", "teachers", "teach", "teaches", "class", "classes"],
        "class_code_bypass": True,
    },
    # The single designated homeroom "class teacher" for a class - the
    # standard term at Indian-curriculum schools like Yara. Distinct from
    # class_teacher_lookup above (ALL subject teachers for a class). The
    # dividing line is genuinely just singular "class teacher" here vs.
    # plural "class teachers"/"who teaches class" there - "class teacher"
    # was deliberately removed from class_teacher_lookup's own phrase list
    # so the two can never both phrase-match the same question.
    "class_teacher": {
        # Bare "class teacher" is listed alongside the longer phrases it's
        # already a substring of (not redundant - score_intent() awards +3
        # per DISTINCT phrase match, so a longer phrase also matching this
        # one stacks an extra +3). Needed to keep a safe margin over
        # subject_teacher ("teacher for" is itself a substring of "class
        # teacher for") and over my_class's bare "my class" - confirmed via
        # nlp_audit_test.py's auto-generated phrase-substring cases, which
        # caught both as real (if narrow) ties without this.
        "phrases": ["class teacher", "my class teacher", "who is my class teacher",
                    "class teacher name", "class teacher for", "who is the class teacher",
                    "class teacher of", "tell me my class teacher"],
        "keywords": ["teacher", "class"],
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
        "phrases": ["how many teachers teach", "teachers for subject",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "faculty count for a subject"],
        "keywords": ["teachers"],
    },

    # ---- HOD expansion (department-scoped versions of the principal
    # intents above) - "department" is unique vocabulary nowhere else in
    # this file, so these can't collide with a plain teacher's own
    # questions; empirically checked against the full HOD bucket (teacher
    # intents + these three) before shipping - see the app.py role-
    # hierarchy task this came from.
    "department_free_teachers": {
        "phrases": ["teachers in my department are free", "free teachers in my department",
                    "who is free in my department", "which teachers in my department are free",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "who's not busy in my dept", "who's around in my dept"],
        "keywords": ["department", "free", "teachers"],
    },
    "department_schedule_today": {
        "phrases": ["my department's schedule today", "department schedule today",
                    "my department schedule", "department's schedule today"],
        "keywords": ["department", "schedule"],
    },
    "department_teacher_count": {
        "phrases": ["how many teachers are in my department", "how many teachers in my department",
                    "department teacher count", "teacher count for my department",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "how many faculty in my dept", "dept headcount", "total faculty in my dept", "dept strength"],
        "keywords": ["department", "teachers"],
    },

    # ---- Shared across all three roles ----
    # "urgent"/"old"/"older" aren't separate intents (a real "notices"
    # vs. "notices, but urgent-only" vs. "notices, but older" fragmentation
    # would just be the same underlying query with different filters,
    # competing with each other for zero benefit) - one intent,
    # app.py's handle_notices() extracts the urgency/recency filter from
    # the question text directly, same "optional filter extracted from the
    # question" pattern already used for exam/timetable's subject/day
    # filters elsewhere in this file.
    "notices": {
        "phrases": ["latest notices", "any announcements", "school notices",
                    "any updates", "recent announcements", "any notices",
                    "any notice", "new notices", "any new announcements",
                    "old announcements", "older announcements", "old notices",
                    "older notices", "urgent notices", "any urgent notices",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "anything new", "any news", "any updates today", "any circulars", "new circular", "latest circular", "any memo", "any important notice", "check notices", "check announcements", "gimme notices", "gimme announcements", "show notices", "show announcements", "quickly any notices", "just tell me any updates", "bro any announcements", "anything posted recently", "any school updates", "any admin updates", "recent updates", "notices for me", "notices for today", "todays notices", "this weeks notices", "any notice board updates", "notice board pls", "koi notice hai kya", "koi announcement hai", "any fresh notices", "check notice board"],
        "keywords": ["notice", "notices", "announcement", "announcements", "urgent"],
    },
    # Deliberately NOT "what subjects" alone - that also matches a teacher
    # asking about their OWN subjects ("what subjects do i teach"), which
    # this intent shouldn't answer for. Every phrase here names the SCHOOL
    # as the subject of the question, not the asker.
    "subjects_offered": {
        "phrases": ["subjects does the school", "subjects does yara",
                    "subjects are offered", "subjects offered", "school subjects",
                    "list of subjects", "subjects available", "subjects does this school",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "what subjects can i take", "curriculum list", "list of subjects offered", "which subjects exist here", "subjects taught here", "gimme subject list", "quickly list all subjects", "just tell me the subjects offered", "bro what subjects does the school have", "curriculum offered", "what streams are offered", "science stream subjects", "commerce stream subjects", "arts stream subjects", "optional subjects list", "full subject list", "school curriculum subjects", "subjects taught at this school", "does the school offer computer science", "does the school offer economics", "is commerce offered", "is arts stream offered", "check subjects offered", "find subject list"],
        "keywords": ["subjects", "curriculum"],
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
    # I-III"), not a personal "my class" reference. Same for "grade" -
    # "grade 5 fees"/"grade 5 tuition" is almanac content, not my_class's
    # "which grade am i in" (that's a phrase match, unaffected by this).
    "class", "classes", "grade", "grades",
    # collides with the almanac's ENTRANCE TEST section ("Grades I & II:
    # English, Mathematics, Hindi") - "what subjects are tested in the
    # entrance exam" is real almanac FAQ content, not subject_teacher's
    # "who teaches me" territory
    "subject", "subjects",
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


# Shares GRADE_SECTION_PATTERN/EARLY_YEARS_CLASSES with app.py's
# extract_class_from_question() and validators.py's validate_class() - see
# validators.py for why these three used to drift as independent copies.
# No circularity importing from validators.py: unlike app.py (which already
# imports FROM this module), validators.py imports nothing from either file.
_CLASS_CODE_RE = re.compile(GRADE_SECTION_PATTERN)
_EARLY_YEARS_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(c) for c in EARLY_YEARS_CLASSES) + r')\b',
    re.IGNORECASE
)


def has_class_code(cleaned_question):
    """True if a class code - grade-section ("10-a"/"10a"/"10 a") or an
    early-years standalone code ("nursery"/"lkg"/"ukg") - is mentioned
    anywhere in the question."""
    return bool(_CLASS_CODE_RE.search(cleaned_question) or _EARLY_YEARS_RE.search(cleaned_question))


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
        # \b-anchored, not a bare substring `in` check - "next period" was
        # matching inside "next periodical" (word-glued at the tail), same
        # bug class as extract_teacher_name_from_question()'s "Ann"/"annual".
        if re.search(r'\b' + re.escape(phrase) + r'\b', cleaned_question):
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


def rank_intents(question, possible_intents, top_n=3):
    """
    Scores `question` against every intent in `possible_intents` and returns
    the top `top_n` non-zero results, highest first: [(intent, score), ...].

    Ties keep `possible_intents`' own list order (Python's sort() is
    stable) - this is intentional: app.py's routing needs to see BOTH the
    winner and the runner-up (to check the margin between them isn't a
    coin-flip tie), not just a single collapsed "best" intent the way
    detect_intent_with_score() below returns.
    """
    cleaned = clean_question(question)
    words = tokenize(cleaned)
    personal_signal = has_personal_signal(cleaned)
    class_code_present = has_class_code(cleaned)

    scored = [
        (intent_name, score_intent(cleaned, words, intent_name, personal_signal, class_code_present))
        for intent_name in possible_intents
    ]
    scored = [(name, score) for name, score in scored if score > 0]
    scored.sort(key=lambda pair: -pair[1])
    return scored[:top_n]


def detect_intent_with_score(question, possible_intents, confidence_threshold=1):
    """
    Same matching as detect_intent(), but also returns the winning score -
    used by app.py's routing to tell a phrase-backed match (score >= 3) apart
    from a weak, keyword-only match (score 1-2) when deciding whether to
    double-check against the almanac before trusting NLP over Gemini.

    Built on rank_intents() - just keeps its top result, so both stay in
    sync automatically.

    Returns (intent_name_or_None, best_score).
    """
    ranked = rank_intents(question, possible_intents, top_n=1)
    if not ranked:
        return None, 0
    best_intent, best_score = ranked[0]
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


# =========================================================
# LEARNED PHRASES — safety check
# Backs the dashboard's "Learned Phrases" review queue (candidate phrases
# the AI classifier resolved but score_intent() itself missed - see
# app.py's classifier lane and gemini_rag.log_learned_phrase()). Before a
# candidate can be marked safe to promote into INTENT_DATA, this runs it
# through the SAME score_intent() this file already uses at request time -
# not a separate pattern-matching heuristic that could drift from how
# scoring actually behaves.
# =========================================================

# "Scores meaningfully" bar for the score_intent() collision check below -
# mirrors ALMANAC_STRONG_MATCH_SCORE (app.py), this project's existing
# precedent for "confident enough to matter", not a new number invented
# for this feature. Not imported from app.py: app.py already imports FROM
# this module, so importing back would be circular (same reasoning as
# extract_class_from_question's duplicated regex there).
_COLLISION_SCORE_THRESHOLD = 2


def check_phrase_safety(phrase, target_intent, same_role_intents=None):
    """
    Simulates adding `phrase` to target_intent's phrases list and checks it
    for collisions against every OTHER intent already in INTENT_DATA.

    same_role_intents: optional set of intent names that share at least
    one role with target_intent (from app.py's ROLE_PERSONAL_INTENTS - not
    imported here, app.py already imports FROM this module, so dashboard.py
    passes it in instead). When given, each conflict is labeled same-role
    (a real routing risk - that other intent is actually scored alongside
    this one) or cross-role (informational only - found via live testing:
    "who's free right now" (principal, free_teachers) collides with
    "current_class"'s "right now" phrase, but current_class only exists
    for the teacher role and is never scored against a principal's
    question, so that particular collision can't actually misroute
    anything). Without it (the default), every collision is reported
    as-is with no role context, since there's nothing to compare against.

    Three checks, most concrete first:
    1. The phrase overlaps another intent's own phrase text, or shares an
       exact word with another intent's keyword list - a literal reuse of
       wording that's already claimed elsewhere.
    2. score_intent() run against every OTHER intent with this exact
       phrase, using its own real personal_signal/class_code_present
       state - if some other intent would score >= _COLLISION_SCORE_
       THRESHOLD on this wording as-is, adding it here means two intents
       fight over the same question.
    3. The phrase's words include an AMBIGUOUS_KEYWORDS entry, and the
       phrase itself doesn't establish a personal signal ("my"/"i"/"me")
       or a distinctive 3+ word structure - the same conditions
       score_intent() already relies on to stop a bare ambiguous keyword
       from scoring elsewhere. Flagged even with no direct collision found
       today, since this is exactly the failure mode AMBIGUOUS_KEYWORDS
       exists to catch before a FUTURE almanac addition creates one
       silently ("teachers day"/"exam schedule" both started this way).

    Returns (status, reason): status is "safe" or "needs_review"; reason
    is "" for "safe", otherwise names the specific conflict(s) found.
    """
    cleaned = clean_question(phrase)
    words = tokenize(cleaned)
    personal = has_personal_signal(cleaned)
    code_present = has_class_code(cleaned)

    reasons = []
    for other_intent, data in INTENT_DATA.items():
        if other_intent == target_intent:
            continue

        if same_role_intents is None:
            role_note = ""
        elif other_intent in same_role_intents:
            role_note = " [same role - real routing risk]"
        else:
            role_note = " [different role - not actually scored together, informational only]"

        for existing_phrase in data.get("phrases", []):
            if existing_phrase and (existing_phrase in cleaned or cleaned in existing_phrase):
                reasons.append(
                    f'overlaps existing phrase "{existing_phrase}" already registered under '
                    f"'{other_intent}'{role_note}"
                )

        shared_keywords = set(words) & set(data.get("keywords", []))
        if shared_keywords:
            reasons.append(
                f"shares keyword(s) {sorted(shared_keywords)} with '{other_intent}'{role_note}"
            )

        score = score_intent(cleaned, words, other_intent, personal, code_present)
        if score >= _COLLISION_SCORE_THRESHOLD:
            reasons.append(
                f"scores {score} against '{other_intent}' under score_intent() as-is - "
                f"would be ambiguous{role_note}"
            )

    if reasons:
        # de-duplicate while keeping order - checks 1 and 2 above can both
        # legitimately fire for the same intent (a phrase substring hit
        # naturally also scores high), no need to say it twice
        seen = set()
        unique_reasons = [r for r in reasons if not (r in seen or seen.add(r))]
        return "needs_review", "; ".join(unique_reasons)

    ambiguous_words = [w for w in words if w in AMBIGUOUS_KEYWORDS]
    if ambiguous_words and not personal and len(words) < 3:
        return "needs_review", (
            f"core word(s) {ambiguous_words} are in AMBIGUOUS_KEYWORDS and this phrase has no "
            "personal signal ('my'/'i'/'me') or distinctive 3+ word structure - could silently "
            "collide with future almanac content the same way 'teachers day'/'exam schedule' did"
        )

    return "safe", ""


def _quote_literal(text):
    """Double-quoted, matching this file's own string style - falls back to
    repr() only if the text itself contains a double quote, which would
    otherwise break out of the literal."""
    if '"' not in text:
        return f'"{text}"'
    return repr(text)


def apply_phrase_to_intent_data(phrase, target_intent, file_path=None):
    """
    Appends `phrase` to target_intent's "phrases" list, editing THIS FILE'S
    OWN SOURCE on disk - the dashboard's Learned Phrases "Approve" action.

    Uses ast.parse() to find the exact source position of the last element
    in target_intent's phrases list (or the list's own position, if empty),
    then inserts as plain text at that exact line/column - regardless of
    whether the list is written on one line or wrapped across several (see
    the file's own phrases lists for both styles). A blind regex/string
    replace can't reliably tell "the end of THIS intent's phrases list"
    from a similar-looking line elsewhere; AST position info can.

    Does NOT reformat/re-wrap the edited line afterward - it may end up
    longer than this file's usual style. Left for an optional manual
    cleanup pass rather than risking a naive line-wrapping heuristic
    corrupting the surrounding formatting.

    Re-parses the edited source before writing anything - if the result
    wouldn't itself be valid Python, the file on disk is left untouched
    and this raises instead.

    Option A (see gemini_rag.py's LEARNED PHRASES section): this edits the
    source file, but the nlp_helpers module already loaded in the running
    Flask process keeps its OLD INTENT_DATA in memory until restarted -
    dashboard.py must say so next to the Approve button, not imply this is
    instant like the Almanac editor.

    Returns True if the phrase was inserted, False if it was already
    present (no-op, not an error). Raises ValueError if target_intent
    doesn't exist in INTENT_DATA, or if it has no "phrases" list.
    """
    path = file_path or __file__
    with open(path, "r", encoding="utf-8") as f:
        source = f.read()
    lines = source.splitlines(keepends=True)

    tree = ast.parse(source)
    intent_data_node = next(
        (node.value for node in ast.walk(tree)
         if isinstance(node, ast.Assign)
         and any(isinstance(t, ast.Name) and t.id == "INTENT_DATA" for t in node.targets)),
        None
    )
    if intent_data_node is None or not isinstance(intent_data_node, ast.Dict):
        raise ValueError(f"Could not locate INTENT_DATA dict in {path}")

    target_dict_node = next(
        (v for k, v in zip(intent_data_node.keys, intent_data_node.values)
         if isinstance(k, ast.Constant) and k.value == target_intent),
        None
    )
    if target_dict_node is None:
        raise ValueError(f"Intent '{target_intent}' not found in INTENT_DATA")

    phrases_list_node = next(
        (v for k, v in zip(target_dict_node.keys, target_dict_node.values)
         if isinstance(k, ast.Constant) and k.value == "phrases"),
        None
    )
    if phrases_list_node is None or not isinstance(phrases_list_node, ast.List):
        raise ValueError(f"Intent '{target_intent}' has no \"phrases\" list to append to")

    existing = [el.value for el in phrases_list_node.elts if isinstance(el, ast.Constant)]
    if phrase in existing:
        return False

    if phrases_list_node.elts:
        last = phrases_list_node.elts[-1]
        insert_line, insert_col = last.end_lineno, last.end_col_offset
        insertion = f", {_quote_literal(phrase)}"
    else:
        insert_line = phrases_list_node.lineno
        insert_col = phrases_list_node.col_offset + 1  # just past the opening "["
        insertion = _quote_literal(phrase)

    target_line = lines[insert_line - 1]
    lines[insert_line - 1] = target_line[:insert_col] + insertion + target_line[insert_col:]

    new_source = "".join(lines)
    ast.parse(new_source)  # fail loudly rather than write a broken file

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_source)

    return True
