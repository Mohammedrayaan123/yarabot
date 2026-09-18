"""
gemini_rag.py
-------------
Handles the RAG layer.
Used for general school knowledge questions — holidays, events,
PTM dates, exam schedules, policies — anything NOT personal to
a specific student or teacher.

Privacy guarantee: Gemini/Groq NEVER see student names, attendance,
grades, or any personal data. They only see the school almanac
text + the user's question.

Provider fallback: Gemini is the primary provider. If Gemini specifically
fails due to rate limiting/quota, Groq (an OpenAI-compatible API) answers
instead, using the same prompt and almanac context so answer quality stays
consistent regardless of which provider actually responds.
"""

import os
import re
import time
import math
from collections import Counter
import mysql.connector
from google import genai
from openai import OpenAI
from config import DB_CONFIG


def load_almanac(path='school_almanac.txt'):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return ''


# The dashboard's Almanac editor writes this file directly and needs
# changes to take effect without a Flask restart. Re-reading on every
# question would work but almanac_top_score() runs on nearly every chat
# message (including the NLP-lane tie-break), so we cache by mtime instead -
# a cheap stat call per question instead of a full read.
ALMANAC_PATH = 'school_almanac.txt'
_almanac_cache = {'content': load_almanac(ALMANAC_PATH), 'mtime_ns': None}
try:
    _almanac_cache['mtime_ns'] = os.stat(ALMANAC_PATH).st_mtime_ns
except OSError:
    pass


def get_almanac():
    try:
        current_mtime = os.stat(ALMANAC_PATH).st_mtime_ns
    except OSError:
        return _almanac_cache['content']

    if current_mtime != _almanac_cache['mtime_ns']:
        _almanac_cache['content'] = load_almanac(ALMANAC_PATH)
        _almanac_cache['mtime_ns'] = current_mtime
        print(f"[ALMANAC RELOADED] {len(_almanac_cache['content'])} chars")

    return _almanac_cache['content']



_ROMAN_GRADES = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
_ROMAN_TO_GRADE = {roman: n + 1 for n, roman in enumerate(_ROMAN_GRADES)}

_GRADE_NUMBER_RE = re.compile(
    r'\b(?:grade|class|std\.?|standard)s?\s*(\d{1,2})\b'
    r'|\b(\d{1,2})(?:st|nd|rd|th)?\s*(?:grade|class)\b'
)


def _question_grade_number(cleaned_question):
    """Pulls a 1-12 grade/class number out of e.g. "grade 5 fees" or "class 8 tuition". None otherwise."""
    m = _GRADE_NUMBER_RE.search(cleaned_question)
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    return n if 1 <= n <= 12 else None


def _section_covers_grade(section, grade_n):
    
    matches = [m for m in re.finditer(r'\b[IVX]+\b', section) if m.group() in _ROMAN_TO_GRADE]
    if not matches:
        return False

    values = [_ROMAN_TO_GRADE[m.group()] for m in matches]
    if grade_n in values:
        return True

    for m1, m2 in zip(matches, matches[1:]):
        between = section[m1.end():m2.start()]
        if re.fullmatch(r'\s*(-|to)\s*', between, re.I):
            lo, hi = sorted((_ROMAN_TO_GRADE[m1.group()], _ROMAN_TO_GRADE[m2.group()]))
            if lo <= grade_n <= hi:
                return True
    return False


_ALMANAC_HEADING = re.compile(r'(?m)^-{20,}\s*\n([^\n]+)\n-{20,}\s*')
_ALMANAC_SUBHEADING = re.compile(r'(?m)^([A-Z][A-Z0-9 /&()\-]{3,}:?)\s*$')
_ALMANAC_STOPWORDS = {'what', 'when', 'where', 'how', 'who', 'is', 'are', 'the',
                      'a', 'an', 'my', 'me', 'i', 'do', 'does', 'please',
                      'can', 'you', 'tell', 'give', 'show', 'for', 'of', 'to',
                      'policy', 'procedure'}
_ALMANAC_GENERIC_TERMS = {'school', 'date', 'day', 'time', 'hour', 'fee', 'parent'}
ALMANAC_CONTEXT_MATCH_SCORE = 1.0
ALMANAC_CLASSIFIER_BYPASS_SCORE = 6.0


def _almanac_sections(almanac):
    """Keep each heading with its body and split named subsections with context."""
    headings = list(_ALMANAC_HEADING.finditer(almanac))
    if not headings:
        return [s.strip() for s in re.split(r'\n\s*\n', almanac) if s.strip()]

    sections = []
    for index, heading in enumerate(headings):
        title = heading.group(1).strip()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(almanac)
        body = almanac[heading.end():end].strip()
        if not body:
            continue
        if title.startswith('FREQUENTLY ASKED QUESTIONS'):
            for part in re.split(r'(?m)(?=^Q: )', body):
                if part.strip().startswith('Q: '):
                    question_line, answer = part.strip().split('\n', 1)
                    sections.append(f"{title} / {question_line}\n{answer}")
            continue
        subheadings = list(_ALMANAC_SUBHEADING.finditer(body))
        if not subheadings:
            sections.append(f"{title}\n{body}")
            continue
        prelude = body[:subheadings[0].start()].strip()
        if prelude:
            sections.append(f"{title}\n{prelude}")
        for subindex, subheading in enumerate(subheadings):
            subend = subheadings[subindex + 1].start() if subindex + 1 < len(subheadings) else len(body)
            subsection = body[subheading.end():subend].strip()
            if subsection:
                sections.append(f"{title} / {subheading.group(1).strip()}\n{subsection}")
    return sections


def _almanac_words(text):
    return {'transport' if word == 'transportation' else _singularize(word)
            for word in re.findall(r'\b\w+\b', text.casefold())}


def _almanac_question_words(question):
    words = _almanac_words(question) - _ALMANAC_STOPWORDS
    grade_n = _question_grade_number(question.lower())
    if grade_n is not None:
        words.discard(str(grade_n))
    return words, grade_n


def _score_almanac_sections(question):
    almanac = get_almanac()
    if not almanac:
        return []

    sections = _almanac_sections(almanac)
    section_words = [(_almanac_words(section.split('\n', 1)[0]),
                      _almanac_words(section.split('\n', 1)[1] if '\n' in section else ''))
                     for section in sections]
    document_frequency = Counter(word for title_words, body_words in section_words
                                 for word in title_words | body_words)
    question_words, grade_n = _almanac_question_words(question)

    scored = []
    for section, (title_words, body_words) in zip(sections, section_words):
        matched = question_words & (title_words | body_words)
        if len(question_words) >= 3 and len(matched) < 2:
            continue
        topic_words = question_words - _ALMANAC_GENERIC_TERMS
        if topic_words and not (topic_words & matched):
            continue
        score = 0.0
        for word in question_words:
            if word not in title_words and word not in body_words:
                continue
            idf = min(2.0, 1.0 + math.log((len(sections) + 1) / (document_frequency[word] + 1)))
            score += idf * (2.5 if word in title_words else 1.0)
        if question_words:
            score *= len(matched) / len(question_words)
        if grade_n and _section_covers_grade(section, grade_n):
            score += 3
        if score >= ALMANAC_CONTEXT_MATCH_SCORE:
            scored.append((score, section))

    scored.sort(reverse=True, key=lambda pair: pair[0])
    return scored


def search_almanac(question):
    scored = _score_almanac_sections(question)
    top = [section for _, section in scored[:3]]

    grade_n = _question_grade_number(question.lower())
    if grade_n is not None:
        for _, section in scored[3:]:
            if section not in top and _section_covers_grade(section, grade_n):
                top.append(section)

    return '\n\n'.join(top)


def almanac_top_score(question):

    scored = _score_almanac_sections(question)
    return scored[0][0] if scored else 0


def almanac_match_confidence(question):
    """Return the top score and whether it can safely bypass classification."""
    scored = _score_almanac_sections(question)
    if not scored:
        return 0, False
    score, section = scored[0]
    query_words, _ = _almanac_question_words(question)
    matched = query_words & _almanac_words(section)
    coverage = len(matched) / len(query_words) if query_words else 0
    return score, score >= ALMANAC_CLASSIFIER_BYPASS_SCORE and coverage >= 0.75


_NOTICE_STOPWORDS = {'what', 'when', 'where', 'how', 'who', 'is', 'are', 'the',
                     'a', 'an', 'my', 'me', 'i', 'do', 'does', 'please',
                     'can', 'you', 'tell', 'give', 'show', 'mean', 'about',
                     'exactly'}


def _score_notices(question, visible_roles):
    if not visible_roles:
        return []

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        conditions = ["target_roles='all'"] + ["FIND_IN_SET(%s, target_roles)"] * len(visible_roles)
        cursor.execute(
            f"SELECT title, body FROM notices WHERE ({' OR '.join(conditions)})",
            tuple(visible_roles)
        )
        rows = cursor.fetchall()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f'[NOTICES CONTEXT ERROR] {e}')
        return []

    cleaned_question = question.lower()
    for ch in '?!.,':
        cleaned_question = cleaned_question.replace(ch, '')
    question_words = set(cleaned_question.split()) - _NOTICE_STOPWORDS

    scored = []
    for title, body in rows:
        text = f"{title} {body}".lower()
        score = sum(1 for word in question_words if word in text)
        if score > 0:
            scored.append((score, title, body))

    scored.sort(reverse=True, key=lambda t: t[0])
    return scored


def search_notice_context(question, visible_roles):
    scored = _score_notices(question, visible_roles)
    if not scored:
        return ''
    _, title, body = scored[0]
    return f"Recent notice — {title}: {body}"



NO_CONTEXT_MESSAGE = (
    "I don't have general school information available yet. "
    "Please contact the school office directly."
)
API_ERROR_MESSAGE = (
    "I'm having trouble connecting to my knowledge base right now. "
    "Please contact the school office for this information."
)

GEMINI_DECLINED_PHRASE = "I don't have that information — please contact the school office directly."

GEMINI_MODEL = 'gemini-3.5-flash-lite'

# Kicks in when Gemini hits a rate limit/quota error. Groq's API is
# OpenAI-compatible, so we reuse the openai SDK pointed at its endpoint.
# 'llama-3.1-8b-instant' (an earlier choice) went dead on Groq's API
# (404 model_not_found) - verify against the live model list, don't trust
# a model string from memory.
GROQ_MODEL = 'openai/gpt-oss-20b'

# Forces both gemini_answer() and gemini_answer_stream() through Groq
# instead of Gemini, for testing the fallback path without a real rate
# limit. Separate early branch, doesn't touch the real fallback logic.
# Set FORCE_GROQ=true in .env - read once at import, so toggling needs a restart.
FORCE_GROQ = os.getenv('FORCE_GROQ', 'false').strip().lower() == 'true'


def _build_prompt(question, context):
    return f"""You are a helpful assistant for Yara International School in Riyadh, Saudi Arabia.
Answer the question using ONLY the school information provided below.
If the answer is not clearly in the provided information, say exactly:
"{GEMINI_DECLINED_PHRASE}"
Never make up dates, events, or policies. Keep your answer concise, friendly, and accurate.
Use bullet points if listing multiple dates or items.

SCHOOL INFORMATION:
{context}

QUESTION: {question}

ANSWER:"""


def is_rate_limit_error(error):
    """Check if an exception looks like a rate limit / quota error, as
    opposed to any other kind of failure (network error, bad key, etc.)."""
    error_str = str(error).lower()
    return any(term in error_str for term in
               ['429', 'quota', 'rate limit', 'resource_exhausted'])


def ask_groq(question, context):
    """
    Fallback answer using Groq, called only when Gemini has already failed
    with a rate-limit error. Same prompt/context as Gemini so answer
    quality stays consistent regardless of which provider actually answers.

    Deliberately swallows its own errors and returns API_ERROR_MESSAGE
    (mirrors ask_gemini()'s old behavior) rather than raising - Groq is
    already the fallback, so there's nowhere further to fall back to if it
    also fails.
    """
    if not context:
        return NO_CONTEXT_MESSAGE

    try:
        # Built fresh per call, not at module level - a client built at
        # import time can bake in a missing GROQ_API_KEY if this module
        # loads before load_dotenv() runs (bit us once already, with
        # GEMINI_API_KEY in a bare test script).
        client = OpenAI(api_key=os.getenv('GROQ_API_KEY'), base_url='https://api.groq.com/openai/v1')
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{'role': 'user', 'content': _build_prompt(question, context)}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f'[GROQ ERROR] {e}')
        return API_ERROR_MESSAGE


# =========================================================
# UNCERTAIN-MATCH CLASSIFIER
# Last line of defense in app.py's routing pipeline - fires when NEITHER
# the NLP lane nor the almanac is confident (see _nlp_lane_decision() in
# app.py), one more chance to catch a personal-data question phrased in a
# way nlp_helpers.py's keyword lists weren't taught, before falling
# through to the generic Gemini fallback.
# =========================================================
def classify_personal_intent(question, role, possible_intents, intent_descriptions=None):
    """
    intent_descriptions: optional {intent_name: description} (app.py's own
    INTENT_DESCRIPTIONS - not imported here, app.py already imports FROM
    this module, so it's passed in instead, same pattern as
    possible_intents itself). When given, each category line in the
    prompt carries its plain-English description alongside the raw name -
    lets the model choose between a SHORT, specific set of real options
    (app.py's tie-break path passes just the 2-3 intents NLP was actually
    torn between) instead of guessing blind from a bare identifier like
    "class_teacher_lookup" vs "class_teacher". Omitted (the original
    behavior) for the full-role-list classifier call, where there's no
    tie-break context driving the choice.
    """
    if intent_descriptions:
        intent_list = "\n".join(
            f"- {name}: {intent_descriptions.get(name, '')}" for name in possible_intents
        )
    else:
        intent_list = "\n".join(f"- {name}" for name in possible_intents)
    prompt = f"""A {role} at a school is using a chatbot. Decide which ONE category their question belongs to.

CATEGORIES:
{intent_list}
- NONE (this is general school information - a holiday, policy, event, or anything not specific to this {role} personally)

QUESTION: "{question}"

Reply with ONLY the category name exactly as written above, or NONE. No explanation, no punctuation, nothing else."""

    try:
        # Built fresh per call, same reasoning as ask_groq()'s client above.
        client = OpenAI(api_key=os.getenv('GROQ_API_KEY'), base_url='https://api.groq.com/openai/v1')
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0,   # classification, not creative writing - same input should always get the same category
            # GROQ_MODEL is a reasoning model - its chain-of-thought counts
            # against max_tokens too. A tight cap here (originally 20) let
            # the reasoning alone exhaust the budget before any visible
            # answer came out, so content was '' on every call and the
            # fail-open path silently swallowed it as "no match" - looked
            # like it worked, never actually classified anything. Confirmed
            # via response.choices[0].message.reasoning. 300 covers the
            # reasoning plus a one-word answer.
            max_tokens=300,
        )
        raw_answer = response.choices[0].message.content.strip()
    except Exception as e:
        print(f'[CLASSIFIER ERROR] {e}')
        return None

    # Exact match against the real intent list (case-insensitive, tolerant
    # of stray punctuation the model might add around it) - anything else,
    # including a literal "NONE", is treated the same as a failed call.
    cleaned = raw_answer.strip().strip('.').strip('"').strip("'").lower()
    for name in possible_intents:
        if cleaned == name.lower():
            return name

    return None


def ask_gemini(question, context):
    if not context:
        return NO_CONTEXT_MESSAGE

    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=_build_prompt(question, context)
    )
    return response.text.strip()


def ask_gemini_stream(question, context):
    if not context:
        yield NO_CONTEXT_MESSAGE
        return

    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    stream = client.models.generate_content_stream(
        model=GEMINI_MODEL,
        contents=_build_prompt(question, context)
    )
    for chunk in stream:
        text = chunk.text or ''
        if text:
            yield text


# =========================================================
# CACHE
# In-memory cache — lives in RAM while Flask is running.
# Resets on server restart (intentional — keeps almanac answers fresh).
# =========================================================
_cache = {}  # {normalized_question: {"answer": str, "timestamp": float}}
CACHE_TTL_SECONDS = 86400  # 24 hours — almanac data doesn't change daily
CACHE_SIMILARITY_THRESHOLD = 0.85  # Unanswered-question grouping only


def normalize_cache_question(question):
    """Keep qualifiers in the cache key so distinct questions stay distinct."""
    return ' '.join(re.findall(r"\b\w+\b", question.casefold()))


def normalize_question(question):
    """Strip punctuation, lowercase, remove common stopwords for better cache matching."""
    stopwords = {'what', 'when', 'where', 'how', 'is', 'are', 'the', 'a', 'an',
                 'please', 'can', 'you', 'tell', 'me', 'i', 'do', 'does', 'about'}
    q = question.lower().strip()
    for ch in '?!.,':
        q = q.replace(ch, '')
    words = [w for w in q.split() if w not in stopwords]
    return ' '.join(words)


def _singularize(word):
    """Naive plural strip (dates -> date, holidays -> holiday) so word-overlap
    matching isn't defeated by a simple singular/plural difference. Guarded
    to length > 3 so short words like 'is', 'as', 'bus' aren't mangled -
    those are filtered out as stopwords or almanac-irrelevant anyway."""
    return word[:-1] if len(word) > 3 and word.endswith('s') else word


def _normalized_word_set(normalized_question):
    """Turns an already-normalize_question()'d string into the singularized
    word set find_cached_answer() (and now log_unanswered_question() below)
    both match against - factored out so the two share one definition of
    "same underlying question" instead of two copies that could drift."""
    return {_singularize(w) for w in normalized_question.split()}


def _overlap_score(words_a, words_b):
    """
    Overlap coefficient between two word sets: how much of the SHORTER
    set's words appear in the other. See find_cached_answer()'s docstring
    for why this (not character-level diffing) is used - short questions
    like "ptm" vs "ptm date" should still score as a strong match.
    """
    if not words_a or not words_b:
        return 0
    return len(words_a & words_b) / min(len(words_a), len(words_b))


def find_cached_answer(normalized_question):
    """Return an unexpired answer only for this exact cache key."""
    get_almanac()  # Refresh the file version before considering an old answer.
    entry = _cache.get(normalized_question)
    if entry is None:
        return None
    if entry.get('almanac_version') != _almanac_cache['mtime_ns']:
        del _cache[normalized_question]
        return None
    if time.time() - entry['timestamp'] > CACHE_TTL_SECONDS:
        del _cache[normalized_question]
        return None
    print(f'[CACHE HIT] Question: {normalized_question}')
    return entry['answer']


UNCACHEABLE_ANSWERS = {NO_CONTEXT_MESSAGE, API_ERROR_MESSAGE, GEMINI_DECLINED_PHRASE}


def log_unanswered_question(question):
    """
    Records (or, for a near-duplicate phrasing, increments) an unanswered
    question in the unanswered_questions table.

    Groups similar unanswered phrasings with normalize_question(),
    _normalized_word_set(), and _overlap_score(). This grouping is separate
    from the answer cache, which requires an exact normalized question.

    Best-effort: a DB hiccup here must never break the chat reply already
    being sent to the user, so failures are logged and swallowed rather
    than raised.
    """
    normalized = normalize_question(question)
    query_words = _normalized_word_set(normalized)
    if not query_words:
        return  # a stopwords-only/empty question has nothing to group on

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute("SELECT id, normalized_question FROM unanswered_questions")
        rows = cursor.fetchall()

        best_id, best_score = None, 0
        for row_id, stored_normalized in rows:
            score = _overlap_score(query_words, _normalized_word_set(stored_normalized))
            if score > best_score:
                best_score = score
                best_id = row_id

        if best_score >= CACHE_SIMILARITY_THRESHOLD:
            cursor.execute(
                "UPDATE unanswered_questions "
                "SET ask_count = ask_count + 1, last_asked = NOW() "
                "WHERE id = %s",
                (best_id,)
            )
            print(f'[UNANSWERED] Grouped into #{best_id} (score {best_score:.2f}): {question}')
        else:
            cursor.execute(
                "INSERT INTO unanswered_questions "
                "(question_text, normalized_question, ask_count, first_asked, last_asked) "
                "VALUES (%s, %s, 1, NOW(), NOW())",
                (question, normalized)
            )
            print(f'[UNANSWERED] New entry logged: {question}')

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f'[UNANSWERED LOG ERROR] {e}')


def log_learned_phrase(question, intent, role):
    normalized = normalize_question(question)
    query_words = _normalized_word_set(normalized)
    if not query_words:
        return

    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, normalized_phrase FROM learned_phrases "
            "WHERE resolved_intent = %s AND applied = 0",
            (intent,)
        )
        rows = cursor.fetchall()

        best_id, best_score = None, 0
        for row_id, stored_normalized in rows:
            score = _overlap_score(query_words, _normalized_word_set(stored_normalized))
            if score > best_score:
                best_score = score
                best_id = row_id

        if best_score >= CACHE_SIMILARITY_THRESHOLD:
            cursor.execute(
                "UPDATE learned_phrases "
                "SET ask_count = ask_count + 1, last_asked = NOW() "
                "WHERE id = %s",
                (best_id,)
            )
            print(f'[LEARNED PHRASE] Grouped into #{best_id} (score {best_score:.2f}): '
                  f'{question} -> {intent}')
        else:
            cursor.execute(
                "INSERT INTO learned_phrases "
                "(phrase_text, normalized_phrase, resolved_intent, role, "
                " ask_count, first_asked, last_asked, applied) "
                "VALUES (%s, %s, %s, %s, 1, NOW(), NOW(), 0)",
                (question, normalized, intent, role)
            )
            print(f'[LEARNED PHRASE] New candidate logged: {question} -> {intent} ({role})')

        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        print(f'[LEARNED PHRASE LOG ERROR] {e}')


def cache_answer(normalized_question, answer, almanac_version):
    """Store a Gemini answer in the cache."""
    _cache[normalized_question] = {
        'answer': answer,
        'timestamp': time.time(),
        'almanac_version': almanac_version,
    }
    print(f'[CACHE STORED] Question: {normalized_question}')
    print(f'[CACHE SIZE] {len(_cache)} entries')


def gemini_answer(question, visible_roles=()):
    normalized = normalize_cache_question(question)

    notice_context = search_notice_context(question, visible_roles) if visible_roles else ''
    used_notice = bool(notice_context)

    if not used_notice:
        cached = find_cached_answer(normalized)
        if cached:
            return cached

    almanac_context = search_almanac(question)
    almanac_version = _almanac_cache['mtime_ns']
    context = f"{almanac_context}\n\n{notice_context}".strip() if notice_context else almanac_context

    if FORCE_GROQ:
        print(f'[FORCE_GROQ ACTIVE] Skipping Gemini, using Groq directly: {question}')
        answer = ask_groq(question, context)
    else:
        if context:
            print(f'[GEMINI LANE] Cache miss - calling API: {question}')
        else:
            # ask_gemini() also checks this and won't call the API either way -
            # this log line just makes it visible in the console that no request
            # was sent, instead of it looking identical to a real API call.
            print(f'[NO ALMANAC MATCH - SKIPPING GEMINI CALL] Question: {question}')

        try:
            answer = ask_gemini(question, context)
        except Exception as e:
            if is_rate_limit_error(e):
                print(f'[GEMINI RATE LIMITED -> GROQ FALLBACK] Question: {question}')
                answer = ask_groq(question, context)
            else:
                print(f'[GEMINI ERROR] {e}')
                answer = API_ERROR_MESSAGE

    if answer in UNCACHEABLE_ANSWERS:
        print(f'[CACHE SKIPPED] Fallback/error answer not cached: {normalized}')
        # Only a genuine "no info" answer gets logged - API_ERROR_MESSAGE is a
        # transient system failure, not a content gap, and logging it would
        # pollute Suggested Additions with questions Gemini may well have
        # been able to answer once the API is reachable again.
        if answer == NO_CONTEXT_MESSAGE or answer == GEMINI_DECLINED_PHRASE:
            log_unanswered_question(question)
        return answer

    if used_notice:
        print(f'[CACHE SKIPPED] Notice-grounded answer not cached: {normalized}')
    else:
        cache_answer(normalized, answer, almanac_version)
    return answer


def gemini_answer_stream(question, visible_roles=()):

    normalized = normalize_cache_question(question)

    notice_context = search_notice_context(question, visible_roles) if visible_roles else ''
    used_notice = bool(notice_context)

    if not used_notice:
        cached = find_cached_answer(normalized)
        if cached:
            yield cached
            return

    almanac_context = search_almanac(question)
    almanac_version = _almanac_cache['mtime_ns']
    context = f"{almanac_context}\n\n{notice_context}".strip() if notice_context else almanac_context

    if FORCE_GROQ:
        print(f'[FORCE_GROQ ACTIVE] Skipping Gemini, using Groq directly: {question}')
        full_answer = ask_groq(question, context)
        yield full_answer
    else:
        if context:
            print(f'[GEMINI LANE] Cache miss - streaming from API: {question}')
        else:
            # ask_gemini_stream() also checks this and won't call the API
            # either way - this log line just makes it visible in the console
            # that no request was sent, instead of it looking identical to a
            # real call.
            print(f'[NO ALMANAC MATCH - SKIPPING GEMINI CALL] Question: {question}')

        full_answer = ''
        try:
            for chunk in ask_gemini_stream(question, context):
                full_answer += chunk
                yield chunk

        except Exception as e:
            if is_rate_limit_error(e):
                print(f'[GEMINI RATE LIMITED -> GROQ FALLBACK] Question: {question}')
                groq_answer = ask_groq(question, context)
                full_answer += groq_answer
                yield groq_answer
                # Fall through to the cache check below - Groq's answer (or a
                # partial-Gemini + Groq combination) should still get cached
                # like any other successful reply.
            else:
                print(f'[GEMINI STREAM ERROR] {e}')
                if not full_answer:
                    # Failed before any real text streamed - same clean
                    # fallback as the non-streaming path.
                    yield API_ERROR_MESSAGE
                # else: some real text already reached the browser: leave it
                # as-is rather than yielding a second, confusing error chunk.
                return  # never cache a plain failure, partial or not

    full_answer = full_answer.strip()
    if full_answer == NO_CONTEXT_MESSAGE or full_answer == GEMINI_DECLINED_PHRASE:
        log_unanswered_question(question)
    if full_answer and full_answer not in UNCACHEABLE_ANSWERS:
        if used_notice:
            print(f'[CACHE SKIPPED] Notice-grounded answer not cached: {normalized}')
        else:
            cache_answer(normalized, full_answer, almanac_version)
