# YaraBot — Full Project Briefing for Claude Code
### (Updated — read this before starting any new work)

## What this project is
A production-ready AI school assistant chatbot for Yara International School, Riyadh.
Built by a Grade 12 student (Rayaan) as an independent production project — separate
from his CBSE school project. Teacher confirmed: if testing goes well, the whole
school will use it. This is NOT a toy demo — treat it like a real product.

Project name: **YaraBot**. Chatbot persona name: **Nova** — "Hi, I'm Nova, your
personalized assistant for Yara International School." (Persona rename may or may
not be implemented yet in the code you're looking at — check for "Yara School
Assistant" strings still lingering if so.)

---

## Architecture

```
Frontend (HTML/CSS/JS, Tailwind, Lottie)  ←→  Flask backend (app.py)  ←→  MySQL
                                                      ↓
                                          NLP (nlp_helpers.py) — primary lane
                                          for personal data (attendance, exams,
                                          timetable, fees, periods, classes, etc.)
                                                      ↓
                                          Gemini RAG (gemini_rag.py) — secondary
                                          lane for general school knowledge
                                          (holidays, PTM dates, policies), grounded
                                          in school_almanac.txt
                                                      ↓
                                          Groq — automatic fallback if Gemini
                                          rate-limits

Streamlit dashboard (dashboard.py) ← SEPARATE admin tool, same MySQL DB, DO NOT TOUCH
unless explicitly asked
```

**Two-lane chat routing (important, core design):**
- Personal questions ("my attendance", "my exam") → NLP + MySQL, instant, offline
- General questions (holidays, PTM, policies) → Gemini + almanac, streamed
- If Gemini rate-limits → auto-falls back to Groq
- Repeated questions → served from in-memory cache, no API call at all
- Personal data NEVER reaches Gemini/Groq — privacy-by-design

---

## File structure

```
YaraBot/
├── app.py                  ← Flask backend: chat API, login, two-lane routing,
│                              shared NLP helpers (extract_day_from_question,
│                              estimate_current_period_number,
│                              extract_subject/teacher_name/class_from_question)
├── gemini_rag.py            ← Gemini RAG, streaming (SSE), Groq fallback, caching
├── auth_helpers.py          ← SHA-256 hashing + hmac.compare_digest verification
├── config.py                ← DB credentials (git-excluded) — may now support
│                              both local + cloud MySQL via USE_CLOUD_DB env var
├── validators.py            ← Input validation functions
├── nlp_helpers.py            ← NLP intent detection engine, INTENT_DATA dict
├── dashboard.py              ← Streamlit admin dashboard — DO NOT TOUCH unless asked
├── setup_database.py         ← Creates school_bot DB and all tables
├── generate_dummy_data.py    ← Inserts 500 dummy students, 50 dummy teachers
├── reset_data.py              ← Safely clears all table data (confirmation prompt)
├── school_almanac.txt         ← 320+ lines, REAL data extracted from all 5 grade-
│                                group academic calendars (KG, I-III, IV-V, VI-VIII,
│                                IX-XII) — this is genuine school data, not dummy
├── .env                       ← GEMINI_API_KEY, GROQ_API_KEY, FORCE_GROQ debug flag,
│                                possibly CLOUD_DB_* vars — git-excluded
├── .gitignore                 ← excludes config.py, .env, __pycache__, notes/
├── templates/
│   └── index.html             ← Full chatbot UI: login + chat in one file,
│                                Tailwind, marked.js, Lottie, mobile-responsive
│                                with collapsible sidebar
└── static/
    ├── app.js                  ← Frontend logic: login, ID card, quick actions,
    │                              streaming message rendering, mobile sidebar toggle
    ├── Yaralogo.png              ← real school logo
    ├── chatbot.json               ← Lottie animation for bot avatar
    └── Error404.json                ← Lottie animation for 404 page
```

---

## Database (MySQL)

Database name: `school_bot`

### Tables
- **students**: student_id (PK), name, class (e.g. "10-A"), roll_no, dob,
  parent_name, parent_contact, fees_status ENUM(paid/pending), attendance_pct
- **teachers**: teacher_id (PK), name, subject, contact, classes_assigned
- **subjects**: subject_id (PK), subject_name, class
- **timetable**: entry_id (PK), class, day, period_no, subject_id (FK),
  teacher_id (FK) — NOTE: no real period start/end times yet, current-period
  detection uses a rough heuristic (period 1≈8am, ~1hr each) — flagged as a
  known limitation until real data migration happens
- **exams**: exam_id (PK), class, subject_id (FK), exam_date, exam_type
- **users** (logins): user_id (PK), username (UNIQUE), password_hash (SHA-256),
  role ENUM(student/teacher/principal/admin), linked_id
- **notes**, **notices**: exist but currently unused (notes feature was
  deliberately scrapped, notices UI never built)

---

## What's fully built and working

### Core system
- Full CRUD dashboard (Streamlit) — students, teachers, subjects, timetable
  (with CSV bulk upload), exams, login creation
- Role-based login: student, teacher, principal, admin — SHA-256 + constant-time
  comparison, rate-limited (5 attempts/5 min), generic error messages (no user
  enumeration)
- Security hardening: env-based Flask secret key, debug=False by default,
  hashed dashboard admin credentials

### Chatbot frontend
- Real HTML/CSS/JS (NOT Streamlit) — Flask backend, Tailwind CSS, Inter font
- Lottie animated bot avatar (chatbot.json) in chat bubbles, login page, sidebar
- School branding: real logo (Yaralogo.png), school colors (#2E3191 blue,
  #FDD835 gold, extracted from actual logo)
- ID card showing name/class/roll/attendance (color-coded)/fees status
- Quick action chips (role-specific)
- Mobile/tablet responsive: collapsible sidebar with hamburger menu, slide-in
  overlay, backdrop-to-close — tested and confirmed working
- Custom 404 page with Error404.json Lottie animation
- Timestamps, markdown rendering (marked.js) in bot replies

### NLP layer (nlp_helpers.py + app.py)
Rule-based intent detection with fuzzy matching (difflib), phrase + keyword
scoring, contraction handling, stopword removal. NOT an LLM — deliberately
explainable and offline-capable for personal data queries.

**18+ intents across 3 roles, recently expanded significantly:**
- Students: attendance, exams, timetable (now day-specific — "today"/"Monday"),
  fees, identity ("what's my name"), roll number, class, next period,
  subject-teacher lookup ("who teaches me maths")
- Teachers: period count, timetable (day-specific), classes assigned,
  next class, current class, free periods today, periods remaining, identity
- Principal: total students/teachers, class-wise breakdown, live teacher
  location ("where is Mr. X right now"), classroom occupant lookup ("who's
  teaching 10-A now"), free-teachers-right-now, teacher schedule lookup,
  school-wide subject-teacher lookup, low-attendance flagging (<75%),
  pending-fees flagging, teacher-count-by-subject

Shared helpers built once in app.py, reused across all roles:
`extract_day_from_question()`, `estimate_current_period_number()`,
`extract_subject_from_question()`, `extract_teacher_name_from_question()`,
`extract_class_from_question()`

**Known real bugs already found and fixed during this NLP expansion** (worth
knowing so you don't reintroduce them):
- `clean_question()` had a naive `.replace("im","i am")` that mid-word-matched
  and corrupted "timetable" → fixed with word-boundary regex
- Teacher names stored as "Mr Imdadullah " — naive `name.split()[0]` extracted
  the title "Mr" as first name — needs smarter matching
- "Science" is a literal substring of "Computer Science" — naive first-match
  subject extraction picked wrong subject — fixed with two-tier match
  (full-name-first, abbreviation-fallback)
- `teachers.subject` has whitespace-duplicated rows ('Computer Science' vs
  'Computer Science ') as genuinely distinct DB values — needs TRIM() in
  comparisons
- Bare phrases like "roll no" or "next period" have no first-person wording,
  so the personal-vs-general router sent them to Gemini instead of NLP —
  needed explicit handling

### Gemini RAG + Groq fallback
- `gemini_rag.py`: two-lane routing, `is_personal_question()` router in app.py
  checks for first-person wording ("my", "I", "me") to decide NLP vs Gemini lane
- Gemini model: `gemini-3.5-flash-lite` (~500 requests/day free tier)
- Groq model: `openai/gpt-oss-20b` (1,000 RPD confirmed via Groq dashboard,
  30 RPM, 200K TPD) — auto-fires when Gemini returns a 429/rate-limit error
- Streaming responses via SSE (`stream_with_context`, `text/event-stream`) —
  Gemini answers stream word-by-word; cache hits and Groq fallback answers
  return as a single instant chunk
- In-memory caching: 24hr TTL, fuzzy question matching (difflib, 85% similarity
  threshold), normalizes questions before comparing (strips stopwords/punctuation)
- `UNCACHEABLE_ANSWERS` protection — transient errors (503s etc.) are never
  cached, so a temporary API hiccup doesn't poison the cache for 24 hours
- `ask_gemini()`/`ask_gemini_stream()` properly RAISE exceptions on failure
  (don't swallow them) — this is what makes the rate-limit-detection →
  Groq-fallback logic actually work
- Groq client is built FRESH per call, not once at module level — this avoids
  a real bug hit earlier where `GEMINI_API_KEY` was read before
  `load_dotenv()` had run
- `FORCE_GROQ` debug env var exists — set true to force all Gemini-lane
  questions through Groq instead, for isolated testing without burning
  Gemini quota
- Model names in this space go stale FAST — always verify against the
  provider's live model list before trusting any suggested name (multiple
  models have gone dead mid-project: gemini-2.0-flash, gemini-2.5-flash-lite,
  llama-3.1-8b-instant)

---

## What's NOT yet done / left to build

### High priority
1. **Real data migration** — currently running on dummy data (500 fake
   students, 50 fake teachers via `generate_dummy_data.py`). BLOCKED on
   finding out where the school's real records actually live (Excel? paper?
   other software?) — Rayaan needs to ask the school office. Once known,
   build either a CSV importer or rely on manual dashboard entry.
   Real period start/end times should be added to the `timetable` table
   at this same stage, to replace the current rough time heuristic.
2. **Hosting** — moving from local-only to actually deployed:
   - Database: Aiven (cloud MySQL) — may already be in progress, check
     if `config.py` has been updated to support `USE_CLOUD_DB` env var
     switching between local and cloud
   - Backend: Render (Flask hosting)
   - Frontend: Netlify (static hosting)
   - This is a 3-service deployment, build bottom-up: DB → backend → frontend
3. Session timeout handling — currently a 401 shows an error, should
   redirect cleanly to login

### Designed but deliberately NOT implemented yet (waiting on school's decision)
- **Management role expansion**: currently only a single hardcoded "principal"
  role exists. A design exists (not yet built) to replace this with a proper
  `management` table supporting Vice Principal, Assistant Principal, etc.,
  each with real name + designation shown on their ID card, all sharing equal
  access. Rayaan deliberately paused this — wants to see what the actual
  school decision-makers ask for before building more speculative features.
- **Almanac self-service editor**: a design exists (not yet built) to add a
  dashboard page where non-technical staff can edit `school_almanac.txt`
  directly through a textarea + save button, with `gemini_rag.py` auto-
  reloading the file on change (via mtime check) instead of requiring a
  server restart. Also paused for the same reason as above.
- Both of these are ready to build the moment Rayaan says go — don't build
  them unprompted.

### Lower priority / nice-to-have
- Notices/announcements UI (table exists in DB, unused)
- Password reset flow
- Groq streaming (currently Groq fallback answers are non-streaming, sent as
  one chunk — noted as acceptable for v1 since Gemini streaming covers the
  common case)

---

## Key design decisions — locked, do not reverse without explicit request

1. Rule-based NLP for personal/core queries — explainable, offline-capable,
   privacy-safe. This is intentional, not a limitation to "fix" by switching
   to an LLM for everything.
2. Gemini/Groq only ever see general school knowledge (the almanac) — NEVER
   student personal data. This is a hard privacy boundary, not a technical
   convenience.
3. Dashboard stays Streamlit (internal admin tool only) — the chatbot was
   deliberately rebuilt from Streamlit to Flask+HTML/CSS/JS specifically
   because Streamlit's visual ceiling wasn't good enough for the student/
   teacher/principal-facing product. Don't suggest reverting the chatbot to
   Streamlit, and don't "upgrade" the dashboard's visuals unless asked —
   it's intentionally left as-is.
4. Vanilla JS, no React/Vue — keeps the frontend deployable as plain static
   files on Netlify with zero build step.
5. Flask server-side sessions (not JWT) — deliberate simplicity choice for
   now, JWT is a possible future upgrade, not a current requirement.
6. Notes/PPT feature was built then explicitly removed from scope (not a bug
   that it's missing) — may return in a future update, not now.

---

## Workflow notes — how Rayaan works with you

- Rayaan plans and designs with a separate Claude chat (not you, cannot
  communicate with you directly) — that chat writes reference code and
  detailed prompts, which get pasted to you here.
- Rayaan wants actual working reference code in prompts, not just prose
  descriptions — but you should still exercise FULL judgment to adapt, fix,
  refactor, or diverge from any reference code where it doesn't fit the real
  current codebase. Reference code is a starting point, never a rigid spec.
- You've caught several real bugs during this project by testing thoroughly
  rather than trusting reference code or your own first implementation at
  face value (see the NLP bug list above, and the Unicode crash / silent
  exception-swallowing bugs found earlier). Keep doing this — verify
  computationally, test through the real Flask route (not just module-level),
  and flag anything you find rather than silently fixing and moving on.
- When something in a reference snippet references a function/table/variable
  that doesn't match reality, don't force it — check the actual current file
  and adapt.
- Context window management: if you're running low on context mid-task,
  finish the current logical unit of work if close to done, then let Rayaan
  know to start a fresh session rather than pushing through with degraded context.

---

## Running the project

```bash
# Chatbot (Flask)
cd YaraBot
python app.py
# → http://localhost:5000

# Dashboard (Streamlit) — separate terminal
cd YaraBot
streamlit run dashboard.py
# → http://localhost:8501

# First-time setup only
python setup_database.py
python generate_dummy_data.py
# Then create logins via the Streamlit dashboard → Logins page
```

---

## School context (for realistic answers/testing)

- School: Yara International School (Y.I.S.), Riyadh, Saudi Arabia
- CBSE-affiliated, Kindergarten through Grade XII
- Motto: "We want your Kid to be a Star!" (source of the star/gold branding)
- Academic year 2026-27 real calendar data is in `school_almanac.txt`
- No-mobile-phones-at-school policy — meaning teachers/principal are the
  PRIMARY daytime chatbot users (students mostly use it before/after school
  or at home) — this shaped several NLP priorities (live "what class am I
  teaching now" type features were prioritized for teachers specifically
  because of this)
