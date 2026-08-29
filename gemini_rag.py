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
_almanac_cache = {'content': load_almanac(ALMANAC_PATH), 'mtime': None}
try:
    _almanac_cache['mtime'] = os.path.getmtime(ALMANAC_PATH)
except OSError:
    _almanac_cache['mtime'] = None


def get_almanac():
    """
    Returns the current almanac text, transparently reloading it from disk
    whenever the file's last-modified time has changed since the last read.
    This is the single source of truth _score_almanac_sections() (and
    therefore search_almanac()/almanac_top_score()) reads from - there's no
    other module-level almanac constant anywhere else to keep in sync.

    If the file has been deleted out from under a running server, keep
    serving the last good copy rather than suddenly returning '' - a
    momentary editor save-in-progress or a bad path shouldn't blank out
    every general-knowledge answer.
    """
    try:
        current_mtime = os.path.getmtime(ALMANAC_PATH)
    except OSError:
        return _almanac_cache['content']

    if current_mtime != _almanac_cache['mtime']:
        _almanac_cache['content'] = load_almanac(ALMANAC_PATH)
        _almanac_cache['mtime'] = current_mtime
        print(f"[ALMANAC RELOADED] {len(_almanac_cache['content'])} chars")

    return _almanac_cache['content']


def _score_almanac_sections(question):
    """
    Score every almanac section against a question by how many question
    words appear in it. Shared scoring core behind both search_almanac()
    (the Gemini lane's context lookup) and almanac_top_score() (app.py's
    NLP-vs-Gemini routing tie-break) - one algorithm, two callers, so a
    future tweak to the matching rules can't drift between them.

    Returns a list of (score, section) tuples, highest score first. Empty
    list if the almanac is missing or nothing scores.

    Three fixes found from real queries that failed: strips trailing
    punctuation ("12th?" still matches "12th"); also checks a
    whitespace-stripped copy of each section, so "sharktank" matches
    "Shark Tank"; strips apostrophes from both sides, so "teachers day"
    matches the almanac's "Teacher's Day" (otherwise they don't share a
    substring).
    """
    almanac = get_almanac()
    if not almanac:
        return []

    sections = [s.strip() for s in almanac.split('\n\n') if s.strip()]

    stopwords = {'what', 'when', 'where', 'how', 'is', 'are', 'the',
                 'a', 'an', 'my', 'me', 'i', 'do', 'does', 'please',
                 'can', 'you', 'tell', 'give', 'show'}

    cleaned_question = question.lower()
    for ch in '?!.,':
        cleaned_question = cleaned_question.replace(ch, '')
    cleaned_question = cleaned_question.replace("'", "").replace("’", "")
    question_words = set(cleaned_question.split()) - stopwords

    scored = []
    for section in sections:
        section_lower = section.lower().replace("'", "").replace("’", "")
        section_squished = re.sub(r'\s+', '', section_lower)
        score = sum(
            1 for word in question_words
            if word in section_lower or word in section_squished
        )
        if score > 0:
            scored.append((score, section))

    scored.sort(reverse=True, key=lambda pair: pair[0])
    return scored


def search_almanac(question):
    """
    Find the most relevant sections of the almanac for this question.
    Returns the top 3 most relevant sections joined together, or '' if
    nothing scores.

    Returning '' (not an arbitrary slice of the almanac) matters: it lets
    ask_gemini() take its existing "no context -> don't call the API" path
    for genuinely unrelated questions instead of handing Gemini 2000
    irrelevant characters and spending a quota-limited API call asking it
    to guess anyway.
    """
    scored = _score_almanac_sections(question)
    top = [section for _, section in scored[:3]]
    return '\n\n'.join(top)


def almanac_top_score(question):
    """
    The single highest section score for this question - a rough measure of
    "how strongly does real almanac content match this question", used by
    app.py's use_nlp_lane() as a second signal before trusting a weak NLP
    intent match. Returns 0 if nothing matches or the almanac is empty.

    This is what makes a NEW event added to school_almanac.txt later
    (a future "Founders' Day", "Alumni Meet", whatever) automatically
    protected against being misrouted to NLP, with no code change here -
    it's scored the same generic way as everything else in the file, not
    matched against a hand-maintained list of known event names.
    """
    scored = _score_almanac_sections(question)
    return scored[0][0] if scored else 0


# The fallback messages used whenever Gemini can't (or shouldn't) answer.
# Named constants instead of inline strings so ask_gemini(), the streaming
# path below, and UNCACHEABLE_ANSWERS all stay in sync automatically - two
# hand-typed copies of the same string is exactly how they'd quietly drift.
NO_CONTEXT_MESSAGE = (
    "I don't have general school information available yet. "
    "Please contact the school office directly."
)
API_ERROR_MESSAGE = (
    "I'm having trouble connecting to my knowledge base right now. "
    "Please contact the school office for this information."
)
# NO_CONTEXT_MESSAGE only fires on the short-circuit "context is completely
# empty" path, before Gemini is ever called. A live call with SOME weak,
# irrelevant context (e.g. "does the school have a swimming pool" scores >0
# just off the word "school") instead follows this instructed refusal
# wording - the far more common real "no info" case. Checked alongside
# NO_CONTEXT_MESSAGE wherever "did Gemini draw a blank" matters.
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
    """Shared prompt template for the Gemini and Groq calls (blocking and streaming)."""
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
def classify_personal_intent(question, role, possible_intents):
    """
    Asks Groq to pick exactly one of possible_intents (the role-specific
    list from app.py's ROLE_PERSONAL_INTENTS), or NONE if it's general
    school information rather than something personal to this user.

    Privacy boundary: sends only the question text, the role label, and the
    intent names - never any student/personal data. Same boundary as the
    Gemini/almanac lane.

    Fails open: returns None (never raises) on NONE, a malformed response,
    or any error - always means "fall through to the normal Gemini/almanac
    lane". A classifier that's also uncertain must never force a guess
    through, same principle as Suggested Additions' "nothing auto-applies"
    rule.
    """
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
    """
    Send the question + relevant almanac context to Gemini.
    Gemini is instructed to ONLY answer from the provided context.

    Raises on failure rather than swallowing it (this used to catch its own
    exceptions and return API_ERROR_MESSAGE directly) - the caller
    (gemini_answer) needs to see the real exception to tell a rate-limit
    failure, which should fall back to Groq, apart from any other failure,
    which shouldn't.
    """
    if not context:
        return NO_CONTEXT_MESSAGE

    client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=_build_prompt(question, context)
    )
    return response.text.strip()


def ask_gemini_stream(question, context):
    """
    Streaming twin of ask_gemini(). Yields text chunks as they arrive from
    Gemini. Raises on failure, same reasoning as ask_gemini() - the caller
    (gemini_answer_stream) needs the real exception to route a rate-limit
    failure to Groq.
    """
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
CACHE_SIMILARITY_THRESHOLD = 0.85  # 85% similar = treat as same question


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
    """
    Check cache for a similar question.

    Uses word-overlap similarity, not character-level diffing: character
    diffing punishes length differences too harshly on short questions (e.g.
    "ptm" vs "ptm date" scores 0.55 despite one being a strict subset of the
    other), so a short follow-up question would never hit the cache. Overlap
    coefficient - how much of the SHORTER question's words appear in the
    longer one - scores that pair 1.0 while still keeping unrelated short
    questions ("ptm" vs "fee structure") apart at 0.0. Words are lightly
    singularized first so "hajj holiday dates" still matches "hajj holidays".

    Returns cached answer string or None if not found / expired.
    """
    now = time.time()
    best_match = None
    best_score = 0

    query_words = _normalized_word_set(normalized_question)

    for cached_q, entry in list(_cache.items()):
        # Remove expired entries
        if now - entry['timestamp'] > CACHE_TTL_SECONDS:
            del _cache[cached_q]
            continue

        cached_words = _normalized_word_set(cached_q)
        overlap = _overlap_score(query_words, cached_words)
        if overlap > best_score:
            best_score = overlap
            best_match = entry['answer']

    if best_score >= CACHE_SIMILARITY_THRESHOLD:
        print(f'[CACHE HIT] Score: {best_score:.2f} | Question: {normalized_question}')
        return best_match

    return None


# These three must never be cached. NO_CONTEXT_MESSAGE/API_ERROR_MESSAGE:
# caching a transient failure (a real 503 from Gemini, seen in testing)
# would serve "contact the office" to every similar question for the full
# TTL even after Gemini recovers. GEMINI_DECLINED_PHRASE has the same
# problem plus a Suggested-Additions-specific one: caching it would skip
# log_unanswered_question() on repeat askings (ask_count could never go
# above 1), and keep serving early askers a stale refusal even after an
# admin adds the real answer to the almanac.
UNCACHEABLE_ANSWERS = {NO_CONTEXT_MESSAGE, API_ERROR_MESSAGE, GEMINI_DECLINED_PHRASE}


# =========================================================
# SUGGESTED ADDITIONS
# Tracks real content gaps - questions that resolved to NO_CONTEXT_MESSAGE
# or GEMINI_DECLINED_PHRASE (not every Gemini-lane question) - so the
# dashboard's "Suggested Additions" page can surface them for review.
#
# Deliberately never touches school_almanac.txt or routing logic on its
# own - every addition still requires an admin to type the actual answer
# and click Add in the dashboard, given this project's history of
# ambiguous-keyword routing bugs from auto-applying changes.
# =========================================================
def log_unanswered_question(question):
    """
    Records (or, for a near-duplicate phrasing, increments) an unanswered
    question in the unanswered_questions table.

    Reuses find_cached_answer()'s exact grouping mechanism - same
    normalize_question() + _normalized_word_set() + _overlap_score() at the
    same CACHE_SIMILARITY_THRESHOLD (0.85) - so "when is sports day" and
    "whens sports day" increment one row's ask_count instead of creating
    separate near-duplicate entries, the same way they'd hit the same
    cache entry.

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


def cache_answer(normalized_question, answer):
    """Store a Gemini answer in the cache."""
    _cache[normalized_question] = {
        'answer': answer,
        'timestamp': time.time()
    }
    print(f'[CACHE STORED] Question: {normalized_question}')
    print(f'[CACHE SIZE] {len(_cache)} entries')


def gemini_answer(question):
    """
    Main entry point — search almanac then ask Gemini, falling back to Groq
    automatically if Gemini is specifically rate-limited (not for other
    kinds of failures, which still show the normal error message).

    If FORCE_GROQ is on, Gemini is skipped entirely and Groq answers every
    time - a separate debug branch from the rate-limit fallback above, not
    a change to it.
    """
    normalized = normalize_question(question)

    cached = find_cached_answer(normalized)
    if cached:
        return cached

    context = search_almanac(question)

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

    # Store in cache for next time - whichever provider actually answered.
    cache_answer(normalized, answer)
    return answer


def gemini_answer_stream(question):
    """
    Streaming twin of gemini_answer(), used by app.py's /api/chat route.

    Cache hit and no-almanac-match both yield a single chunk with no API
    call, so app.js always sees a stream of 1+ chunks regardless of whether
    the reply actually streamed. A cache miss with context streams real
    Gemini chunks and caches the assembled answer once done.

    A mid-stream error yields API_ERROR_MESSAGE uncached - if real chunks
    already reached the browser, they stay and the error is appended after
    rather than replacing them (an SSE stream can't retract bytes already
    sent). A rate limit mid-stream falls back to Groq as one appended
    chunk, same reasoning. FORCE_GROQ skips Gemini entirely, one chunk.
    """
    normalized = normalize_question(question)

    cached = find_cached_answer(normalized)
    if cached:
        yield cached
        return

    context = search_almanac(question)

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
        cache_answer(normalized, full_answer)
