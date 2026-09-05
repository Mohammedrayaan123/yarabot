
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
                    "check attendance", "how is my attendance",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "attendence", "check my attendence", "my attendence status", "how many days have i missed", "did i bunk too much", "how many days was i absent", "how many days was i present", "days present count", "days absent count", "how many absences do i have", "total days absent", "total days present",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "my attendance %", "how much attendance do i have", "show my attendance", "how many classes have i missed", "am i short on attendance", "am i below 75", "is my attendance ok", "is my attendance fine", "my attendance record pls", "tell me my attendance", "i need my attendance", "i want to check my attendance", "can u show my attendance", "attendance kitni hai", "meri attendance kya hai", "my attendance sheet", "quickly check my attendance", "just show my attendance", "i wanted to check my attendance", "need to know my attendance", "my current attendance %", "check my attendance status pls", "give me my attendance", "my attendance count"],
        "keywords": ["attendance", "present", "presence", "absent", "absentee", "bunk", "bunked"],
    },
    "exam": {
        "phrases": ["exam date", "next exam", "when is my exam", "test date",
                    "exam dates", "upcoming exam", "upcoming exams", "next test",
                    "my exam date", "check exam date",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "wen is my exam", "xam date", "test dates pls", "when do i have exams", "tell me my exam dates", "gimme exam dates", "half yearly date", "pre board date", "prelims date", "when is half yearly", "when is pre board", "do i have an exam soon", "exam kab hai", "mera exam kab hai", "quickly tell me exam date", "just tell me when my exam is", "i need to know my exam date", "i wanted to check my exam dates", "upcoming test date", "my upcoming exams", "check my test date", "check upcoming exams",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "exam schedule for me", "show my exam schedule", "next unit test", "my test schedule", "next exam subject", "which exam is next"],
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
                    "weekly timetable", "my periods today", "what is my schedule",
                    "what classes today",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "scedule", "my scedule", "timetabel", "my timetabel", "what do i have today", "what do i have tomorrow",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "gimme my timetable", "gimme my timetable for tmrw", "thursday periods", "my periods list", "full week timetable", "my class routine", "send my timetable", "share my timetable", "i need my timetable", "i want to see my schedule", "can u show my timetable", "just show my timetable", "bro send timetable", "mera timetable dikhao", "aaj ka schedule kya hai", "which classes do i have today", "which classes do i have tomorrow", "next weeks timetable", "my daily schedule", "todays periods list", "full schedule pls"],
        "keywords": ["timetable", "schedule", "periods", "classes", "routine"],
    },
    "fee": {
        "phrases": ["fee status", "fees paid", "school fees",
                    "fees status", "fee due", "fees due", "is my fee paid",
                    "check fees", "outstanding fees", "fee balance",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "fee status pls", "fees status pls", "is my fee cleared", "is my fees cleared", "have i paid my fees", "did i pay fees", "fee payment status", "fees payment status", "do i owe fees", "do i owe any fees", "fee ka status", "meri fees paid hai kya", "check my fees", "check fee status", "my fee details", "tuition status", "tuition fee status", "term fee status", "fees due date", "last date for fees", "fee balance check", "outstanding balance", "how much fee do i owe", "how much do i owe", "fee cleared or not", "fees cleared or not", "show my fee status", "gimme fee status", "tell me if my fees are paid", "i want to know my fee status", "quickly check fees", "just tell me fee status", "bro is my fee paid", "my fee history",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "hey is my fee paid", "fees pending or not"],
        "keywords": ["fee", "fees", "payment", "paid", "dues", "due"],
    },
    "period_count": {
        "phrases": ["how many periods", "number of periods", "periods today", "periods this week",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "period count today", "show my period count", "just tell me period count", "how many lectures today", "how many lectures this week", "weekly period count", "daily period count", "total lectures i have", "my period load today", "workload today", "how heavy is my day",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "how many periods do i have", "how many periods today", "how many classes do i have today", "how many periods this week", "total periods today", "total periods this week", "periods count", "how many classes this week", "no of periods today", "tell me periods today", "quickly how many periods", "hey how many periods today", "periods for today", "periods for this week", "how many periods do i teach today", "how many periods do i teach this week", "total classes i have", "how many periods am i teaching", "periods scheduled today"],
        "keywords": ["periods", "period"],
    },
    "classes_assigned": {
        "phrases": ["which classes", "my classes", "classes assigned", "classes i teach",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "which sections am i assigned", "sections i handle", "grades assigned to me", "sections covered by me",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "which classes do i teach", "what classes do i teach", "what classes am i assigned", "which classes am i assigned to", "my assigned classes", "list of classes i teach", "show my classes", "show classes assigned to me", "tell me my classes", "which sections do i teach", "which grades do i teach", "my grade sections", "classes under me", "classes handled by me", "which classes are mine", "my teaching classes", "give me my classes", "gimme my classes", "quickly show my classes", "just tell me my classes", "hey which classes do i teach", "bro what classes are mine", "which classes am i in charge of", "classes i am responsible for", "my class list", "assigned classes list", "which grade am i assigned to teach", "classes i am teaching this year", "my subject classes", "classes for my subject", "which class sections do i cover", "my roster of classes", "classes on my roster", "teaching load classes"],
        "keywords": ["classes", "class", "assigned", "teach"],
    },
    "total_students": {
        "phrases": ["how many students", "total students", "number of students",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "total student count", "how many students in school", "how many students enrolled", "total strength", "school strength", "overall student count", "how many kids in school", "how many students do we have", "enrollment numbers", "total enrollment", "give me student count", "gimme student total", "quickly tell me student count", "just give me student total", "school student total", "total pupils", "how many pupils", "number of pupils", "students enrolled total", "overall enrollment", "school population", "total kids enrolled",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "current student count", "hey how many students"],
        "keywords": ["students", "student"],
    },
    "total_teachers": {
        "phrases": ["how many teachers", "total teachers", "number of teachers",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "total staff count", "how many staff members", "how many faculty", "total faculty", "number of faculty members", "faculty size", "how many educators",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "total teacher count", "how many teachers in school", "how many teachers on staff", "how many teaching staff", "teaching staff count", "give me teacher count", "gimme teacher total", "quickly tell me teacher count", "just give me teacher total", "hey how many teachers", "how many teachers do we have", "how many teachers work here", "school teacher total", "current teacher count", "overall teacher count", "number of teachers employed", "total employed teachers"],
        "keywords": ["teachers", "teacher", "staff"],
    },
    "class_wise_count": {
        "phrases": ["students per class", "class wise", "class-wise", "breakdown by class",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "section wise count", "breakdown by section", "gimme classwise breakdown", "class-wise breakdown pls", "how many per section", "just give me the breakdown", "section distribution",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "students per section", "how many students in each class", "how many students per grade", "class by class breakdown", "grade wise breakdown", "grade wise count", "students count by class", "students count by grade", "breakdown by grade", "give me class wise count", "show class wise count", "show breakdown per class", "class wise numbers", "class wise strength", "strength per class", "strength by class", "students in each section", "how many per class", "per class student count", "per section student count", "quickly show class wise count", "hey show class breakdown", "student distribution by class", "class size breakdown"],
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
                    "show my info", "show my profile", "my profile", "my id card info", "id card details", "gimme my info", "identity card details", "show identity card", "my info pls", "my personal info", "personal details pls", "my record", "show my record", "verify my identity", "my basic info", "profile details", "account details", "my account info",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "tell me my name", "my full name", "show my details", "give me my details", "quickly show my details", "just tell me my name", "my student details", "my student profile", "my details pls", "confirm my details", "my student record", "my name and class"],
        "keywords": ["name", "who"],
    },
    "roll_number": {
        "phrases": ["my roll number", "what is my roll", "roll no", "roll number", "my enrollment number please",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "tell me my roll no", "roll number pls", "my roll no pls", "give me my roll number", "gimme roll no", "show my roll number", "quickly tell me roll no", "just tell me roll number", "roll no kya hai", "mera roll number kya hai", "my enrollment no", "enrollment number pls", "my admission number", "admission no", "my seat number", "roll no check", "check my roll number", "confirm my roll no", "roll id", "my roll id", "id number pls",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "class roll number", "my class roll no", "student roll number", "what number am i in class", "student id number", "my student number"],
        "keywords": ["roll"],
    },
    "my_class": {
        "phrases": ["what class am i in", "which class am i", "my class","which grade am i in",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "what grade am i in", "my grade", "my section", "which section am i in", "which grade and section", "what section am i", "which standard am i in", "my standard", "standard and section", "which grade do i study in", "am i in grade 10",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "which class am i studying in", "tell me my class", "show my class", "gimme my class", "give me my class", "quickly tell me my class", "just tell me what class", "hey what class am i in", "bro which class am i in", "which class do i belong to", "my current class", "my current grade", "class and section pls", "my class section", "mera class kya hai", "meri class konsi hai", "confirm my class", "which class am i enrolled in", "my class info", "what class do i study in", "check my class", "class name pls", "my class name"],
        "keywords": ["class","grade"],
    },
    "next_period": {
        "phrases": ["next period", "next class", "what is next",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "after this what do i have", "right after this",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "what do i have next", "next period pls", "what comes after this period", "gimme next period", "just tell me next period", "next period subject", "which period is next", "my upcoming period", "immediate next class", "what happens after this period"],
        "keywords": ["next"],
    },
    "subject_teacher": {
        "phrases": ["who teaches me", "who is my teacher for", "teacher for",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "my math sir", "my science maam", "my english maam",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "who teaches me math", "who teaches me maths", "who teaches me science", "who teaches me english", "who teaches me hindi", "who teaches me physics", "who teaches me chemistry", "who teaches me biology", "who teaches me cs", "who teaches me computer science", "who teaches me sst", "who teaches me social studies", "math teacher name", "science teacher name", "english teacher name", "whos my math teacher", "whos my science teacher", "which teacher for math", "which teacher for science", "which teacher teaches me english", "my chem teacher", "my bio teacher", "my phy teacher", "my eng teacher", "my cs teacher", "my sst teacher", "who is my maths teacher", "who is my science teacher", "who's my hindi teacher", "gimme my math teacher name", "tell me my science teacher", "quickly tell me who teaches me math", "just tell me my math teacher", "hey whos my science teacher", "bro who teaches me english", "meri math teacher kaun hai", "mera science teacher kaun hai", "who takes my math class", "who takes my science class", "who handles my english class", "which sir teaches me physics", "which maam teaches me chemistry"],
        "keywords": ["teaches", "teacher"],
    },

    # ---- Teacher expansion ----
    "next_class": {
        "phrases": ["next class", "what am i teaching next", "which class next",
                    "next period", "what is next",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "what do i have next", "which class do i teach next", "next period for me", "what am i teaching next period", "gimme my next class", "just tell me next class", "which grade next", "which section next", "next lecture for me", "what class comes next", "upcoming class for me", "what do i teach next", "which class am i teaching after this", "next teaching slot", "next class slot", "immediate next class for me", "class after this one", "next on my schedule", "which class right after this", "my following class", "class following this one", "next up for me"],
        "keywords": ["next"],
    },
    "current_class": {
        "phrases": ["what am i teaching now", "current class", "right now",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "what am i doing right now",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "what am i teaching", "which class am i teaching now", "which class am i in right now", "class right now", "gimme my current class", "just tell me current class", "which grade am i teaching now", "which section am i teaching now", "what subject am i teaching now", "am i in class right now", "which class is on now", "live class right now", "current teaching slot"],
        "keywords": ["now", "current"],
    },
    "free_periods": {
        "phrases": ["free periods", "am i free", "do i have a free period",
                    "any free time",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "any spare time today", "do i have a gap today", "when do i get a break", "any breaks today",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "am i free right now", "do i have any free time", "free periods this week", "when am i free today", "when am i free", "do i have a free hour", "free slots this week", "my free time today", "gimme my free periods", "quickly any free periods", "hey am i free", "bro any free periods", "off periods today", "off periods this week", "break periods today", "empty periods today", "no class periods today", "am i off right now", "am i free this period", "which periods am i free", "list my free periods", "spare periods today", "gaps in my schedule", "am i free next period", "free time this week"],
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
                    "am i done for the day", "am i almost done", "quickly how many left", "how much longer today", "how many more today", "am i finished for today",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "how many classes left today", "how many more classes today", "how many periods to go", "periods to go today", "how many more periods to teach", "classes still left", "periods still left", "how much more teaching today", "remaining classes count", "remaining periods count", "gimme periods left", "just tell me periods left", "hey how many more classes", "bro periods left today", "how many periods still to go"],
        # "periods" keyword needed for the same reason as free_periods above -
        # without it this ties 4-4 with period_count and falls back to list
        # order.
        "keywords": ["remaining", "left", "periods"],
    },
    "teacher_identity": {
        "phrases": ["what is my name", "who am i", "my details", "my subject",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "show my profile", "my profile", "my employee details", "employee id", "my contact details", "my registered contact", "my personal info", "my record", "show my record", "my basic info",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "tell me my name", "my teacher details", "my staff details", "my teacher profile", "show my info", "give me my details", "gimme my info", "quickly show my details", "just tell me my name", "my department", "which department am i in", "my subject i teach", "what subject do i teach", "my staff id", "staff id pls", "confirm my details"],
        "keywords": ["name", "who"],
    },

    # ---- Principal expansion ----
    "teacher_location": {
        "phrases": ["where is", "which class is teaching", "what is teaching right now",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "wheres mr ahmed", "wheres ms priya", "which room is he in", "which room is she in", "where can i find", "gimme location of", "just tell me where he is", "which classroom is he in", "which classroom is she in",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "where's he right now", "where's she right now", "which class is he teaching", "which class is she teaching", "current location of teacher", "hey where is she", "bro wheres the teacher", "find where a teacher is", "teachers current room", "teacher room number", "which class right now for", "where is teacher teaching now", "locate a staff member", "his current class", "her current class", "which class is she in right now"],
        "keywords": ["where", "location"],
    },
    "classroom_occupant": {
        "phrases": ["who is teaching class", "who is in class", "which teacher is in",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "whos teaching 10a", "who's occupying that room", "who's in room 10a", "occupant check",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "whos in 10a right now", "who is in that class now", "which teacher is there now", "who's inside class", "who's currently in class", "who's taking this class", "who's handling this class right now", "whos in class 9b", "class occupant right now", "current occupant of class", "who is present in the classroom", "which teacher is present now", "who's in the room now", "whos there right now", "class 10a occupant", "gimme who's in class", "quickly who's in that class", "just tell me who's teaching that class right now", "hey who's in 10a", "bro who's teaching this class", "currently teaching that class", "who's live in that class", "class in session who", "who's conducting class right now", "which staff is in class now", "whos in charge of that room now", "who's holding class right now", "check who's in class", "who is present in class 10a"],
        "keywords": ["teaching", "occupant"],
    },
    "free_teachers": {
        "phrases": ["which teachers are free", "free teachers right now",
                    "who is available",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "any teacher free right now", "who's free at the moment", "which staff is free", "gimme list of free teachers", "quickly any free teachers", "just tell me who's free", "hey any teachers free", "bro is anyone free right now", "available teachers now", "who's available right now", "list of available teachers", "teachers with free period now", "who has a free period now", "which teachers are off right now", "off duty teachers", "spare teachers right now", "teachers not in class now", "who's not teaching right now", "who's idle right now", "free staff right now", "which teacher is doing nothing right now", "who can i call right now", "who's around and free", "check for free teachers", "free faculty right now", "faculty available now", "who's not busy right now", "which teacher has no class now", "teachers on a break now", "who's on break right now", "list free staff"],
        "keywords": ["free", "available"],
    },
    "teacher_schedule_lookup": {
        "phrases": ["schedule for", "timetable for teacher",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "show me schedule for mr", "show me schedule for ms", "gimme his schedule", "gimme her schedule", "his timetable pls", "her timetable pls", "quickly show his schedule", "just tell me her schedule", "bro show me her schedule", "which periods does he have", "which periods does she have", "his weekly schedule", "her weekly schedule", "check his schedule", "check her schedule", "staff schedule lookup", "look up teacher schedule", "find a teachers timetable", "a teachers full schedule", "his class schedule", "her class schedule", "teacher weekly timetable", "get me his schedule", "get me her schedule", "display his timetable", "display her timetable"],
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
                    "class schedule", "class routine",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "grade 10 section a timetable", "class 10a schedule pls", "gimme timetable for 9b", "gimme schedule for grade 8", "full timetable for 10a", "weekly timetable for a class", "class routine for 10a", "quickly show 10a timetable", "just tell me 9b's schedule", "hey show me 10a schedule", "timetable of grade 11", "schedule of grade 11", "section wise timetable", "look up a class timetable", "find class schedule", "check class routine", "class 10a full week", "full week for a class", "grade section timetable", "class schedule for the week", "which periods does 10a have", "10a's periods list", "class 8c routine", "routine for class 8c", "display class timetable", "display 10a's schedule", "class timing for 10a"],
        "keywords": ["timetable", "schedule", "class", "classes"],
        "class_code_bypass": True,
    },
    "school_wide_subject_teacher": {
        "phrases": ["who teaches", "teacher for subject",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "who teaches physics to grade 12", "who teaches math school wide", "who teaches chemistry", "who teaches biology", "who teaches computer science", "who teaches english", "who teaches hindi", "who teaches sst", "which teacher handles physics", "which teacher handles chemistry", "gimme the physics teacher", "show me the math teacher", "quickly who teaches science", "just tell me who teaches cs", "hey who teaches biology", "bro who teaches english school wide", "subject teacher for math", "subject teacher for physics", "who's the physics teacher here", "who's the math teacher here", "which staff teaches chemistry", "who instructs biology", "who takes physics classes", "who takes chemistry classes", "school wide math teacher", "overall physics teacher", "who is responsible for teaching math", "who handles the science subject", "find the subject teacher", "look up subject teacher", "who teaches physics to seniors", "who teaches math to grade 10", "teacher for chemistry subject", "teacher for biology subject", "who's assigned to teach computer science"],
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
                    "teachers assigned to class",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "who teaches class 10a", "teachers for class 10a", "class 10a teacher list", "all teachers for 10a", "full teacher list for 10a", "give me class 10a's teachers", "gimme teachers for grade 9", "show teachers for class 8", "quickly who teaches 10b", "just tell me the teachers for 9c", "hey who's teaching class 10a", "bro who teaches 9b", "which teachers are assigned to 10a", "who are the subject teachers for 10a", "list of teachers for a class", "class roster of teachers", "teacher roster for 10a", "subject wise teachers for 10a", "who teaches each subject in 10a", "class 10a subject teachers", "10a's teacher list", "teachers of grade 10 section a", "who's teaching 9th grade", "who's teaching 10th grade", "class teachers list for 8c", "check teachers for a class", "find all teachers for 10a", "look up class teacher list", "teachers assigned to 9b", "which staff teach 10a", "who covers 10a for each subject", "full subject teacher list for class", "class wide teacher list"],
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
                    "class teacher of", "tell me my class teacher",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "whos my class teacher", "my class teacher name pls", "who's the class teacher for 10a", "class teacher of 10b", "class teacher name for 9c", "whos the class teacher", "gimme my class teacher name", "show my class teacher", "quickly whos my class teacher", "just tell me my class teacher", "hey whos my class teacher", "bro whos my class teacher", "who is 10a's class teacher", "who is 9b's class teacher", "class teacher assigned to 10a", "class teacher in charge of my class", "my homeroom teacher", "who's my homeroom teacher", "homeroom teacher for 10a", "class in charge teacher", "who's in charge of my class", "which teacher is in charge of my section", "my section teacher", "meri class teacher kaun hai", "mera class teacher kaun hai", "class teacher ka naam", "our class teacher", "who's our class teacher", "class teacher info", "who's responsible for my class", "designated class teacher", "assigned class teacher", "class teacher contact", "my class teacher's name", "the class teacher for my section", "class teacher check", "find class teacher", "look up class teacher", "class teacher for grade 10 section a", "who oversees my class", "class teacher of my section", "class in charge for 10a", "in charge teacher for my class", "who's assigned as class teacher"],
        "keywords": ["teacher", "class"],
        "class_code_bypass": True,
    },
    "low_attendance_count": {
        "phrases": ["low attendance", "below 75", "attendance risk",
                    "students with poor attendance",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "students below 75 attendance", "who has low attendance", "list low attendance students", "students with bad attendance", "which students have poor attendance", "attendance risk list", "students at attendance risk", "gimme low attendance list", "show low attendance students", "quickly who has low attendance", "just tell me the low attendance list", "hey any low attendance students", "bro whos got bad attendance", "students under 75 percent", "attendance below threshold", "who needs attendance warning", "list of attendance defaulters", "students failing attendance", "count of low attendance students", "how many students have low attendance", "attendance shortfall list", "students short on attendance", "check low attendance", "find low attendance students", "students flagged for attendance", "attendance flag list", "who's at risk of detention for attendance", "students needing attendance improvement", "low attendance report", "attendance concern list", "which students are below the attendance limit", "students under attendance minimum", "attendance warning list", "students on attendance watch"],
        "keywords": ["attendance"],
    },
    "pending_fees_count": {
        "phrases": ["pending fees", "unpaid fees", "fee defaulters",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "who has pending fees", "list students with pending fees", "fee defaulters list", "students who havent paid", "which students owe fees", "gimme pending fees list", "show unpaid fees students", "quickly who hasnt paid fees", "just tell me the fee defaulters", "hey any pending fees", "bro whos got unpaid fees", "students with fee dues", "students with outstanding fees", "unpaid dues list", "how many students have pending fees", "count of fee defaulters", "fee shortfall list", "students behind on fees", "check pending fees", "find fee defaulters", "students flagged for fees", "fee flag list", "outstanding fee students", "which students still owe money", "students who need to pay fees", "fees not paid list", "pending dues report", "fee concern list", "list of unpaid students", "students due for payment", "fee payment pending list", "who's yet to pay fees", "students not cleared for fees", "fee clearance pending list", "overdue fee students"],
        "keywords": ["pending", "unpaid"],
    },
    "teacher_count_by_subject": {
        "phrases": ["how many teachers teach", "teachers for subject",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "faculty count for a subject",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "how many teachers teach physics", "how many teach chemistry", "how many teach biology", "how many teach computer science", "how many teach english", "how many teach hindi", "how many teach sst", "teacher count for math", "teacher count for physics", "gimme teacher count for chemistry", "show how many teach science", "quickly how many teach cs", "just tell me teacher count for english", "hey how many teach biology", "bro how many teach physics", "number of teachers for math", "number of math teachers", "number of science teachers", "number of physics teachers", "how many staff teach chemistry", "count of teachers for a subject", "how many faculty teach biology", "subject teacher count", "teacher headcount for math", "how many people teach english", "staff count for cs", "how many teach this subject", "teacher tally for physics", "teacher tally for chemistry", "count teachers per subject", "how many hands teach math", "teachers assigned to math subject", "teachers assigned to science subject", "how many teach social studies"],
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
                    "who's not busy in my dept", "who's around in my dept",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "any teacher free in my department", "who's free in science dept", "free staff in my department", "gimme free teachers in my dept", "show free teachers in department", "quickly who's free in my department", "just tell me free teachers in my dept", "hey any free staff in department", "bro who's free in my dept", "department staff free now", "free faculty in my department", "who's off in my department", "which teachers in my dept have no class", "my department's free staff", "list free teachers in department", "check free teachers dept", "find free staff in my department", "who's available in my department", "available staff in dept", "dept free teacher list", "free teacher count in my department", "science dept who's free", "math dept who's free", "who in my department is idle", "any spare staff in department", "department availability check", "free hands in my department", "department free staff list"],
        "keywords": ["department", "free", "teachers"],
    },
    "department_schedule_today": {
        "phrases": ["my department's schedule today", "department schedule today",
                    "my department schedule", "department's schedule today",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "gimme dept schedule today", "quickly department schedule", "just tell me dept schedule today", "science department today", "math department schedule today", "todays department timetable", "department periods today", "dept periods today", "check department schedule", "find dept schedule today", "my dept's classes today", "todays schedule for my department", "dept schedule check", "department daily schedule", "department activity today", "todays dept routine", "dept routine check", "my department today", "department overview today"],
        "keywords": ["department", "schedule"],
    },
    "department_teacher_count": {
        "phrases": ["how many teachers are in my department", "how many teachers in my department",
                    "department teacher count", "teacher count for my department",
                    # ---- Bulk-generated 2026-09-05, collision-checked via
                    # check_phrase_safety() one at a time (see check_bulk_phrases.py) ----
                    "how many faculty in my dept", "dept headcount", "total faculty in my dept", "dept strength",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "how many staff in my department", "department staff count", "dept teacher tally", "gimme dept teacher count", "show department teacher count", "quickly how many in my department", "just tell me dept teacher count", "hey how many teachers in my dept", "bro how many in my department", "science department teacher count", "math department staff count", "how many people in my department", "department headcount", "check department teacher count", "find dept staff count", "how big is my department", "size of my department", "department size", "total staff in my department", "how many educators in my department", "department strength", "number of teachers in my dept", "how many colleagues in my department", "count of staff in my department", "department team size", "how many on my department team"],
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
                    "anything new", "any news", "any updates today", "any circulars", "new circular", "latest circular", "any memo", "any important notice", "check notices", "check announcements", "gimme notices", "gimme announcements", "show notices", "show announcements", "quickly any notices", "just tell me any updates", "bro any announcements", "anything posted recently", "any school updates", "any admin updates", "recent updates", "notices for me", "notices for today", "todays notices", "this weeks notices", "any notice board updates", "notice board pls", "koi notice hai kya", "koi announcement hai", "any fresh notices", "check notice board",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "hey any notices", "any pending notices for me"],
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
                    "what subjects can i take", "curriculum list", "list of subjects offered", "which subjects exist here", "subjects taught here", "gimme subject list", "quickly list all subjects", "just tell me the subjects offered", "bro what subjects does the school have", "curriculum offered", "what streams are offered", "science stream subjects", "commerce stream subjects", "arts stream subjects", "optional subjects list", "full subject list", "school curriculum subjects", "subjects taught at this school", "does the school offer computer science", "does the school offer economics", "is commerce offered", "is arts stream offered", "check subjects offered", "find subject list",
                    # ---- Round 2: re-evaluated via the routing-simulation fix to
                    # check_phrase_safety() (previously rejected on unverified
                    # keyword-overlap alone; now confirmed to win or fall to a safe
                    # clarification under the real score_intent() margin rules) ----
                    "hey what subjects are offered", "subjects available at yara", "elective subjects available", "which subjects can students choose", "subject choices available"],
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

# "Scores meaningfully" bar for the diagnostic keyword-overlap warning
# below - mirrors ALMANAC_STRONG_MATCH_SCORE (app.py), this project's
# existing precedent for "confident enough to matter". Kept only for the
# non-blocking warning text now (see check_phrase_safety()'s history: this
# used to be a hard blocker on its own, which is exactly what caused the
# 77% false-positive rate an audit found - a shared keyword alone doesn't
# mean a real routing risk, since score_intent() has its own bypasses
# (personal signal, phrase match, class-code) that a bare keyword-overlap
# check can't see).
_COLLISION_SCORE_THRESHOLD = 2

# Mirrors app.py's NLP_SCORE_FLOOR/NLP_MARGIN_THRESHOLD exactly - not
# imported from app.py since app.py already imports FROM this module
# (circular import, same reasoning as _COLLISION_SCORE_THRESHOLD above
# and extract_class_from_question's duplicated regex elsewhere). Keep
# both pairs of constants in sync if either side's thresholds ever change.
_NLP_SCORE_FLOOR = 2
_NLP_MARGIN_THRESHOLD = 2

# Every role's real detect_intent()/rank_intents() call also always
# includes these three (checked ahead of is_pure_greeting()'s own
# full-message shortcut in app.py, but still genuinely scored alongside
# everything else for a non-exact-greeting message) - ROLE_PERSONAL_
# INTENTS itself deliberately excludes them (see app.py's own comment),
# so a routing simulation built only from ROLE_PERSONAL_INTENTS would
# silently miss a real collision against one of these (found live: "yo
# when's my math exam" shares greeting's "yo" keyword).
ALWAYS_SCORED_INTENTS = {"greeting", "thanks", "help"}


def _score_with_candidate(cleaned, words, intent_name, personal, code_present,
                           target_intent, candidate_phrase):
    """score_intent(), but with `candidate_phrase` temporarily spliced into
    target_intent's real phrase list first - so scoring target_intent
    reflects what it would actually score AFTER this phrase is added, not
    just its current phrase list. Mutates INTENT_DATA only for the
    duration of one score_intent() call, always restored via finally -
    the one deliberate, tightly-scoped exception to this module's
    otherwise read-only relationship with its own data during a check."""
    if intent_name != target_intent:
        return score_intent(cleaned, words, intent_name, personal, code_present)

    original = INTENT_DATA[target_intent]["phrases"]
    INTENT_DATA[target_intent]["phrases"] = original + [candidate_phrase]
    try:
        return score_intent(cleaned, words, target_intent, personal, code_present)
    finally:
        INTENT_DATA[target_intent]["phrases"] = original


def _simulate_group_verdict(cleaned, words, personal, code_present, target_intent,
                             candidate_phrase, group):
    """
    Simulates one real role's routing decision (app.py's
    _nlp_lane_decision, minus its almanac-tie-break/policy-framing/
    subject-scoring-adjustment layers - those need DB access or app.py's
    own question-classification helpers, which this DB-free, subject-
    blind module deliberately doesn't have; see app.py's own comment on
    _apply_subject_scoring_adjustment for why that boundary is
    intentional) for `candidate_phrase` as if it were already added to
    target_intent, against every intent in `group` (target_intent
    included - a role's own full candidate list, e.g.
    ROLE_PERSONAL_INTENTS['principal'] | ALWAYS_SCORED_INTENTS).

    Returns (verdict, detail) where verdict is one of:
      "target_wins"     - target_intent wins outright, safe.
      "clarification"   - top-2 near-tie (margin < NLP_MARGIN_THRESHOLD)
                           but target_intent IS one of the two - the live
                           router asks "did you mean X or Y", never
                           silently guesses wrong. Safe by the same logic
                           the router itself already relies on.
      "no_match"        - nothing scored high enough to matter (falls
                           through to the classifier/Gemini lane, exactly
                           as it does today with no risk). Safe.
      "misroute"        - some OTHER intent wins outright, or wins/ties
                           another OTHER intent while target_intent isn't
                           even in the top two - target_intent's phrase
                           addition doesn't help, this question would
                           resolve to (or ambiguously between) unrelated
                           intents. UNSAFE.
    """
    scored = []
    for intent_name in group:
        score = _score_with_candidate(cleaned, words, intent_name, personal,
                                       code_present, target_intent, candidate_phrase)
        if score > 0:
            scored.append((intent_name, score))
    scored.sort(key=lambda pair: -pair[1])

    if not scored or scored[0][1] < _NLP_SCORE_FLOOR:
        return "no_match", None

    top_intent, top_score = scored[0]
    runner_up_score = scored[1][1] if len(scored) > 1 else 0
    margin = top_score - runner_up_score

    if len(scored) > 1 and margin < _NLP_MARGIN_THRESHOLD:
        runner_up_intent = scored[1][0]
        if target_intent in (top_intent, runner_up_intent):
            return "clarification", (top_intent, top_score, runner_up_intent, runner_up_score)
        return "misroute", (
            f"ties between '{top_intent}' and '{runner_up_intent}' "
            f"(scores {top_score}/{runner_up_score}) - neither is the target intent"
        )

    if top_intent == target_intent:
        return "target_wins", (top_intent, top_score, runner_up_score)

    return "misroute", (
        f"'{top_intent}' wins outright (score {top_score} vs target's "
        f"{dict(scored).get(target_intent, 0)}, margin {margin})"
    )


def check_phrase_safety(phrase, target_intent, same_role_intents=None, role_groups=None):
    """
    Checks whether adding `phrase` to target_intent's phrases list is safe.

    same_role_intents: optional set of intent names that share at least
    one role with target_intent (from app.py's ROLE_PERSONAL_INTENTS - not
    imported here, app.py already imports FROM this module, so callers
    pass it in instead). Used only to label the diagnostic warnings below
    as same-role (informational, but worth a human's attention) or
    cross-role (roles never co-occur at runtime, so cannot misroute
    anything). Without it, warnings are reported with no role context.

    role_groups: optional list of sets, each the FULL real intent
    candidate list of one role that actually contains target_intent (e.g.
    passing [ROLE_PERSONAL_INTENTS['principal']] when target_intent is
    principal-only, or one set per role when it's shared, like 'notices')
    - this is what makes the check simulate ACTUAL routing instead of
    approximating it. Falls back to treating `same_role_intents |
    {target_intent}` as a single group when not given (a coarser
    approximation: real per-role separation is lost, but still a routing
    simulation rather than pure keyword-text matching). If neither is
    given, no routing simulation runs at all (matches this function's
    original behavior for any caller that never passes role context).

    THE ACTUAL SAFETY DECISION (see _simulate_group_verdict()): runs
    score_intent() - the same function score_intent() itself uses at
    request time - against every intent in each role_group, as if this
    phrase were already added to target_intent, using the SAME
    NLP_SCORE_FLOOR/NLP_MARGIN_THRESHOLD the live router trusts. A phrase
    is "needs_review" only if some group would actually misroute it (a
    different intent wins outright, or ties/wins narrowly against another
    OTHER intent with target_intent nowhere near the top). target_intent
    winning outright, or landing in a close top-2 WITH target_intent as
    one of the two options (a "did you mean X or Y" clarification - never
    a silent wrong answer), or scoring too weakly to match anything at
    all (falls through exactly as it does today) are all safe outcomes.

    An earlier version of this function blocked on ANY literal keyword-
    list overlap with another intent, regardless of whether score_intent()
    would ever actually let that overlap cause a real collision (it
    ignores personal-signal/phrase-match/class-code bypasses entirely) -
    an audit of 1,320 real candidate phrases found this rejected roughly
    77% of them as unverified false positives. That check still runs, but
    now as a non-blocking diagnostic warning folded into the reason text
    on genuine failures, and included in the returned reason even on a
    "safe" verdict when there's something a human reviewer would still
    want to glance at.

    check #3 (below) is UNCHANGED and still a hard blocker: it protects
    against a bare AMBIGUOUS_KEYWORDS word colliding with FUTURE almanac
    content, which lives entirely outside INTENT_DATA and can never be
    caught by a routing simulation against other intents - this is a
    genuinely different risk category from same-role misrouting, not
    something the false-positive-rate finding was ever about.

    Returns (status, reason): status is "safe" or "needs_review"; reason
    is "" (or a warning-only note) for "safe", otherwise names the
    specific conflict(s) found.
    """
    cleaned = clean_question(phrase)
    words = tokenize(cleaned)
    personal = has_personal_signal(cleaned)
    code_present = has_class_code(cleaned)

    # ---- Diagnostic-only: literal keyword/phrase text overlap. Never a
    # blocker on its own anymore - see docstring above. ----
    warnings = []
    for other_intent, data in INTENT_DATA.items():
        if other_intent == target_intent:
            continue

        if same_role_intents is None:
            role_note = ""
        elif other_intent in same_role_intents:
            role_note = " [same role]"
        else:
            role_note = " [different role - informational only]"

        for existing_phrase in data.get("phrases", []):
            if existing_phrase and (existing_phrase in cleaned or cleaned in existing_phrase):
                warnings.append(
                    f'overlaps existing phrase "{existing_phrase}" already registered under '
                    f"'{other_intent}'{role_note}"
                )

        shared_keywords = set(words) & set(data.get("keywords", []))
        if shared_keywords:
            warnings.append(
                f"shares keyword(s) {sorted(shared_keywords)} with '{other_intent}'{role_note}"
            )

    seen = set()
    warnings = [w for w in warnings if not (w in seen or seen.add(w))]

    # ---- The actual go/no-go: simulated routing, per real role group. ----
    if role_groups is not None:
        groups = [set(g) | {target_intent} for g in role_groups]
    elif same_role_intents is not None:
        groups = [set(same_role_intents) | {target_intent}]
    else:
        groups = []

    misroute_reasons = []
    for group in groups:
        verdict, detail = _simulate_group_verdict(
            cleaned, words, personal, code_present, target_intent, phrase, group
        )
        if verdict == "misroute":
            if isinstance(detail, str):
                misroute_reasons.append(f"{detail} (role group: {sorted(group)})")
            else:
                top_intent, top_score, runner_up_score = detail
                misroute_reasons.append(
                    f"would be misrouted to '{top_intent}' (score {top_score} vs target's "
                    f"score, margin >= {_NLP_MARGIN_THRESHOLD}) in role group {sorted(group)}"
                )

    if misroute_reasons:
        reason = "; ".join(misroute_reasons)
        if warnings:
            reason += " | diagnostic warnings (non-blocking): " + "; ".join(warnings)
        return "needs_review", reason

    # check #3 - UNCHANGED, still a hard blocker (see docstring: different
    # risk category, not what the false-positive-rate finding was about).
    ambiguous_words = [w for w in words if w in AMBIGUOUS_KEYWORDS]
    if ambiguous_words and not personal and len(words) < 3:
        return "needs_review", (
            f"core word(s) {ambiguous_words} are in AMBIGUOUS_KEYWORDS and this phrase has no "
            "personal signal ('my'/'i'/'me') or distinctive 3+ word structure - could silently "
            "collide with future almanac content the same way 'teachers day'/'exam schedule' did"
        )

    if warnings:
        return "safe", "diagnostic warnings (non-blocking): " + "; ".join(warnings)
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
