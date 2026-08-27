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
from google import genai
from openai import OpenAI


def load_almanac(path='school_almanac.txt'):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return ''


# ---------------------------------------------------------------
# Auto-reloading almanac cache.
#
# The dashboard now has a self-service "Almanac" editor (a textarea over
# this same file) meant for non-technical staff - it has to take effect
# immediately, since nobody running that page should need to know "someone
# has to restart the Flask server" is even a step. Re-reading the file on
# EVERY question would work too, but almanac_top_score() below is called on
# nearly every chat message (including most NLP-lane ones, as app.py's
# use_nlp_lane() tie-break), so that would mean a disk read per message for
# a value that in practice changes a few times a year. Caching by mtime
# gets the same "always current" behavior for free, at the cost of one
# cheap os.path.getmtime() stat call per question instead.
# ---------------------------------------------------------------
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

    Three robustness fixes here, all found from real queries that failed:
    - Strips trailing punctuation from question words before matching, so
      "12th?" matches "12th" instead of failing on the leftover "?".
    - Also checks a whitespace-stripped copy of each section, so a
      squished-together word like "sharktank" still matches an almanac
      entry written as two separate words ("Shark Tank").
    - Also strips apostrophes from both sides before matching, so "when is
      teachers day" (how anyone actually types it) matches an almanac
      section written with the grammatically-correct possessive
      "Teacher's Day" - "teachers" and "teacher's" don't share a substring
      otherwise (the apostrophe sits between "teacher" and "s"), which was
      silently starving this exact section of its rightful match score.
    """
    almanac = get_almanac()
    if not almanac:
        return []

    # Split into sections by double newline
    sections = [s.strip() for s in almanac.split('\n\n') if s.strip()]

    # Remove common stopwords before scoring
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


# The two fallback messages used whenever Gemini can't (or shouldn't) answer.
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

GEMINI_MODEL = 'gemini-3.5-flash-lite'

# Groq fallback - kicks in automatically when Gemini specifically hits a
# rate limit / quota error (not other kinds of failures). Groq's API is
# OpenAI-compatible, so this uses the openai SDK pointed at Groq's endpoint
# instead of a separate library.
#
# Model note: an earlier draft of this used 'llama-3.1-8b-instant', which
# turned out to no longer exist on Groq's API (404 model_not_found) -
# checked the live model list (client.models.list()) and picked
# 'openai/gpt-oss-20b' as the closest equivalent: small and fast, good fit
# for a fallback role. Same lesson learned twice already with Gemini model
# names this project - verify against the live API, don't trust a model
# string from memory or an old snippet.
GROQ_MODEL = 'openai/gpt-oss-20b'

# Debug/testing flag - when on, both gemini_answer() and gemini_answer_stream()
# skip Gemini entirely and go straight to Groq, so the Groq fallback path can
# be exercised on demand instead of waiting for a real Gemini rate limit.
# This is a SEPARATE early branch from the normal rate-limit fallback below -
# it doesn't touch is_rate_limit_error() or the try/except that drives the
# real fallback. Set FORCE_GROQ=true in .env to enable; see the matching
# comment there. Read once at import time (like GEMINI_MODEL/GROQ_MODEL
# above), not per-call - toggling it is meant to require a server restart.
FORCE_GROQ = os.getenv('FORCE_GROQ', 'false').strip().lower() == 'true'


def _build_prompt(question, context):
    """Shared prompt template for the Gemini and Groq calls (blocking and streaming)."""
    return f"""You are a helpful assistant for Yara International School in Riyadh, Saudi Arabia.
Answer the question using ONLY the school information provided below.
If the answer is not clearly in the provided information, say exactly:
"I don't have that information — please contact the school office directly."
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
        # Built fresh per call, same reasoning as ask_gemini()'s client:
        # a module-level client constructed at import time would bake in
        # whatever GROQ_API_KEY was (or wasn't) set at that moment, which
        # can be wrong if this module is ever imported before load_dotenv()
        # runs - already bit us once this project with a bare test script
        # and GEMINI_API_KEY.
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

    query_words = {_singularize(w) for w in normalized_question.split()}

    for cached_q, entry in list(_cache.items()):
        # Remove expired entries
        if now - entry['timestamp'] > CACHE_TTL_SECONDS:
            del _cache[cached_q]
            continue

        cached_words = {_singularize(w) for w in cached_q.split()}
        if not query_words or not cached_words:
            continue  # a stopwords-only question has nothing to match on

        overlap = len(query_words & cached_words) / min(len(query_words), len(cached_words))
        if overlap > best_score:
            best_score = overlap
            best_match = entry['answer']

    if best_score >= CACHE_SIMILARITY_THRESHOLD:
        print(f'[CACHE HIT] Score: {best_score:.2f} | Question: {normalized_question}')
        return best_match

    return None


# The two fallback messages (NO_CONTEXT_MESSAGE, API_ERROR_MESSAGE) must
# never be cached: caching a transient failure - we've hit a real 503 from
# Gemini in testing - would serve "contact the office" to every similar
# question for the full CACHE_TTL_SECONDS even after Gemini recovers
# seconds later.
UNCACHEABLE_ANSWERS = {NO_CONTEXT_MESSAGE, API_ERROR_MESSAGE}


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

    # Check cache first
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
        return answer

    # Store in cache for next time - whichever provider actually answered.
    cache_answer(normalized, answer)
    return answer


def gemini_answer_stream(question):
    """
    Streaming twin of gemini_answer(), used by app.py's /api/chat route for
    the Gemini lane. Same cache-first behavior:

    - Cache hit  -> yields the cached answer as a single chunk, instantly,
      no API call. This keeps the response shape consistent for app.js -
      every Gemini-lane reply arrives as a stream of 1+ chunks, whether or
      not it actually streamed from the API - the caller never needs to
      special-case a cache hit.
    - No almanac match -> yields NO_CONTEXT_MESSAGE as a single chunk, no
      API call (same short-circuit as gemini_answer()/ask_gemini()).
    - Cache miss with context -> streams real chunks from the Gemini API as
      they arrive, accumulating the full text, then caches the assembled
      answer once streaming completes (skipping the cache if the assembled
      answer is empty or happens to equal one of the uncacheable fallback
      messages, same rule as gemini_answer()).

    A network/API error mid-stream is handled the same way ask_gemini()
    handles a failure before ever starting a response: yield
    API_ERROR_MESSAGE and don't cache it. If the error happens AFTER some
    real chunks already streamed to the browser, the partial answer stays
    on screen and the error message is appended after it - still better
    than silently cutting the user off with nothing.

    Rate-limit fallback: if Gemini is specifically rate-limited (whether
    that happens before any text streamed, or partway through), Groq
    answers instead - as ONE chunk, not streamed itself (that can be added
    later the same way Gemini's streaming was). If Gemini had already
    streamed some real text before rate-limiting mid-response (rare - a
    429 almost always happens up front, before any chunk arrives), the
    Groq answer is appended as a further chunk rather than replacing what
    already reached the browser - there's no way to retract bytes already
    sent over an SSE stream.

    If FORCE_GROQ is on, Gemini is skipped entirely and Groq answers every
    time, sent as a single chunk (same shape as the rate-limit-fallback
    case above) - a separate debug branch, not a change to the fallback
    logic itself.
    """
    normalized = normalize_question(question)

    # Check cache first
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
    if full_answer and full_answer not in UNCACHEABLE_ANSWERS:
        cache_answer(normalized, full_answer)
