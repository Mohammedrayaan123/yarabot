# YaraBot — Full Project Briefing for Claude Code
### (Fully updated — read this before starting any new work)

## What this project is
A production-ready AI school assistant chatbot for Yara International School, Riyadh.
Built by Grade 12 student Rayaan as an independent production project — separate from
his CBSE school project. Teacher confirmed: if testing goes well, the whole school will
use it. This is LIVE on the internet right now, not a local demo.

Project name: **YaraBot**. Chatbot persona: **Nova**.

---

## Architecture

```
Frontend (HTML/CSS/JS, Tailwind, Lottie)  ←→  Flask backend (app.py)  ←→  MySQL (Aiven cloud)
                                                      ↓
                                          NLP (nlp_helpers.py) — primary lane
                                          for personal data (attendance, exams,
                                          timetable, fees, periods, classes, etc.)
                                                      ↓
                                          Gemini RAG (gemini_rag.py) — secondary
                                          lane for general school knowledge,
                                          grounded in school_almanac.txt
                                                      ↓
                                          Groq — automatic fallback if Gemini
                                          rate-limits

Streamlit dashboard (dashboard.py) ← SEPARATE admin tool, same MySQL DB, DO NOT TOUCH
unless explicitly asked
```

**Two-lane chat routing — MAJOR REWORK, read this carefully:**
The routing is NOT simple pronoun-detection anymore. It went through a significant
architectural fix after repeated bugs where ambiguous shared keywords (e.g. "teacher"
appearing in both "who teaches me maths" AND "when is teacher's day") caused NLP to
falsely claim questions that belonged to Gemini/the almanac.

Current routing logic (in app.py, `use_nlp_lane()` and related):
1. NLP's `detect_intent_with_score()` runs FIRST on every question (not gated by
   pronouns anymore)
2. `score_intent()` in nlp_helpers.py no longer awards points for a bare
   `AMBIGUOUS_KEYWORDS` match alone — it needs either a real phrase match OR a
   personal signal (my/i/me/mine, via `has_personal_signal()`)
3. The almanac itself is also scored via `almanac_top_score()` /
   `_score_almanac_sections()` (shared logic with the Gemini search)
4. If NLP's match is weak (score<3) or absent, and the almanac has a
   strong-enough match (≥2 and ≥ NLP score), Gemini wins the routing decision
5. This is self-maintaining — new content added to school_almanac.txt is
   automatically protected against future keyword collisions, no code changes needed

This took 3 rounds of real bugs to get right (exam schedule vs timetable, teacher vs
teacher's day). A 58-case regression suite passed after the fix. DO NOT revert to
simple pronoun-based routing if asked to "simplify" — this architecture is deliberate
and hard-won.

---

## File structure

```
YaraBot/
├── app.py                  ← Flask backend: chat API, login, two-lane routing,
│                              shared helpers (extract_day_from_question,
│                              estimate_current_period_number,
│                              extract_subject/teacher_name/class_from_question),
│                              /healthz endpoint (unauthenticated, touches DB)
├── gemini_rag.py            ← Gemini RAG, streaming (SSE), Groq fallback, caching,
│                              almanac_top_score(), _score_almanac_sections()
├── auth_helpers.py          ← SHA-256 hashing + hmac.compare_digest (constant-time)
├── config.py                ← DB credentials — GIT-TRACKED now (repo is private,
│                              prod uses cloud env vars via USE_CLOUD_DB anyway)
├── validators.py            ← Input validation functions
├── nlp_helpers.py            ← NLP intent detection, INTENT_DATA, AMBIGUOUS_KEYWORDS,
│                              score_intent(), detect_intent_with_score(),
│                              has_personal_signal()
├── dashboard.py              ← Streamlit admin dashboard — DO NOT TOUCH unless asked
├── setup_database.py         ← Creates school_bot DB and all tables
├── generate_dummy_data.py    ← Inserts 500 dummy students, 50 dummy teachers
├── reset_data.py              ← Safely clears all table data (confirmation prompt)
├── school_almanac.txt         ← 320+ lines, REAL data from all 5 grade-group
│                                academic calendars (KG, I-III, IV-V, VI-VIII, IX-XII)
├── .env                       ← GEMINI_API_KEY, GROQ_API_KEY, FLASK_SECRET_KEY,
│                                CLOUD_DB_HOST/PORT/USER/PASSWORD/NAME,
│                                USE_CLOUD_DB, FORCE_GROQ debug flag (git-excluded)
├── .gitignore                 ← excludes .env, __pycache__, notes/ (NOT config.py)
├── templates/
│   └── index.html             ← Full chatbot UI: login + chat, Tailwind, marked.js,
│                                Lottie mascots, mobile-responsive with collapsible
│                                sidebar, Playfair Display + Quicksand fonts
└── static/
    ├── app.js                  ← Frontend logic: login, ID card, quick actions,
    │                              streaming message rendering, mobile sidebar toggle,
    │                              LOTTIE_VIEWBOX_CROPS map
    ├── Yaralogo.png              ← optimized school logo (480×311px, 3.4KB)
    ├── chatbot.json               ← Lottie bot avatar animation
    ├── Camaleon.json               ← decorative chameleon mascot (sidebar only)
    ├── Error404.json                ← 404 page Lottie animation
    └── favicon
```

---

## Database (MySQL, hosted on Aiven)

Database name: `school_bot`

### Tables
- **students**: student_id (PK), name, class (e.g. "10-A"), roll_no, dob,
  parent_name, parent_contact, fees_status ENUM(paid/pending), attendance_pct
- **teachers**: teacher_id (PK), name, subject, contact, classes_assigned
- **subjects**: subject_id (PK), subject_name, class
- **timetable**: entry_id (PK), class, day, period_no, subject_id (FK),
  teacher_id (FK) — NOTE: no real period start/end times yet, current-period
  detection uses a rough heuristic (period 1≈8am, ~1hr each) — known limitation
  until real data migration happens
- **exams**: exam_id (PK), class, subject_id (FK), exam_date, exam_type
- **users** (logins): user_id (PK), username (UNIQUE), password_hash (SHA-256),
  role ENUM(student/teacher/principal/admin), linked_id
- **notices**: notice_id (PK), title, body, posted_by, date_posted — MAY BE IN
  ACTIVE DEVELOPMENT (Notices UI was just being built when this briefing was
  written — check dashboard.py for a "Notices" page and app.py for a "notices"
  NLP intent to see if this landed)
- **notes**: exists, unused, deliberately scrapped feature

---

## What's fully built and working

### Core system
- Full CRUD dashboard (Streamlit) — students, teachers, subjects, timetable
  (with CSV bulk upload), exams, login creation
- Role-based login: student, teacher, principal, admin
- Security: env-based Flask secret key, debug=False by default, SHA-256 +
  constant-time password comparison, rate-limited login (IP+username combo,
  5 attempts/5min, live countdown timer on frontend), generic auth error
  messages (no user enumeration), hashed dashboard admin credentials,
  DB-unreachable vs wrong-credentials properly distinguished (503 vs 200 —
  a sleeping Aiven DB no longer looks like "wrong password" to the user)

### Chatbot frontend
- Real HTML/CSS/JS (Flask-served, NOT Streamlit) — Tailwind CSS, Inter font
  (body text), Playfair Display (greeting name only), Quicksand bold (all
  "YaraBot" wordmark instances)
- Lottie animated bot avatar (chatbot.json) — had a real padding bug (artwork
  only filled ~30%×72% of its canvas) fixed via manual SVG viewBox override
  (`LOTTIE_VIEWBOX_CROPS` map in app.js), verified against all 150 animation
  frames for zero clipping
- Decorative chameleon mascot (Camaleon.json) — sidebar ONLY (explicitly
  removed from login page per Rayaan), perched over the ID card's top-right
  corner. Position and size are FULLY INDEPENDENT per breakpoint (6 CSS vars:
  size/offset-top/offset-right × desktop/mobile) — don't collapse these back
  into shared variables, Rayaan specifically wanted independent tuning after
  a single-offset version looked wrong on mobile
- School branding: real logo, school colors (#2E3191 blue, #FDD835 gold,
  extracted from the actual logo)
- ID card: name/class/roll/attendance (color-coded)/fees status
- Quick action chips (role-specific)
- Mobile/tablet responsive: collapsible sidebar (hamburger + slide-in overlay),
  tap targets all 44×44px minimum, 16px font on inputs (iOS zoom fix),
  `.full-height` class using 100dvh with 100vh fallback (fixes a real bug
  where the mobile keyboard opening would scroll the header off-screen with
  no way back — DO NOT revert to `h-screen`/100vh)
- Custom 404 page with Error404.json Lottie
- Timestamps, markdown rendering (marked.js) in bot replies
- `#chat-messages` has `tabindex="0"` + `role="region" aria-label="Chat
  messages"` for keyboard/screen-reader accessibility (deliberately NOT
  `role="log"` — would trigger aria-live announcing every streamed chunk)
- `/healthz` endpoint — unauthenticated, touches DB with `SELECT 1` directly
  (bypasses the `query()` helper on purpose, since that would mask a real
  connection failure). Pinged by UptimeRobot every 5 min to keep Render and
  Aiven both permanently awake — no more cold-start delays

### NLP layer (nlp_helpers.py + app.py)
Rule-based intent detection, fuzzy matching (difflib), NOT an LLM — deliberately
explainable and offline-capable. See "Two-lane chat routing" above for the full
architectural picture.

**18+ intents across 3 roles:**
- Students: attendance, exams, timetable (day-specific — "today"/"Monday"),
  fees, identity ("what's my name"), roll number, class, next period,
  subject-teacher lookup ("who teaches me maths" — handles MULTIPLE teachers
  per subject correctly now, was a real fetchone()→fetchall() bug fix)
- Teachers: period count (day-specific, was also a real bug — "periods today"
  used to show weekly total, now correctly filters), timetable (day-specific),
  classes assigned, next class, current class, free periods today, periods
  remaining, identity
- Principal: total students/teachers, class-wise breakdown, live teacher
  location ("where is Mr. X right now"), classroom occupant lookup, free-
  teachers-right-now, teacher schedule lookup, school-wide subject-teacher
  lookup, low-attendance flagging (<75%), pending-fees flagging,
  teacher-count-by-subject

**Known real bugs already found and fixed** (don't reintroduce these):
- `clean_question()` had a naive `.replace("im","i am")` that mid-word-matched
  and corrupted "timetable" → fixed with word-boundary regex
- Teacher names stored as "Mr Imdadullah " (trailing space, title as first
  word) — name extraction needed smarter matching, not naive `.split()[0]`
- "Science" is a literal substring of "Computer Science" — subject extraction
  needed two-tier matching (full-name-first, abbreviation-fallback)
- `teachers.subject` has whitespace-duplicated rows as distinct DB values —
  needs `TRIM()` in comparisons
- Almanac says "Teacher's Day" (possessive) but nobody types the apostrophe —
  apostrophes now stripped from both sides before matching

### Gemini RAG + Groq fallback
- `gemini_rag.py`: two-lane routing (see above), `is_personal_question()`/
  `use_nlp_lane()` in app.py
- Gemini model: `gemini-3.5-flash-lite` (~500 requests/day free tier)
- Groq model: `openai/gpt-oss-20b` (1,000 RPD confirmed via Groq dashboard,
  30 RPM, 200K TPD) — auto-fires when Gemini returns a 429/rate-limit error
- Streaming responses via SSE — Gemini answers stream word-by-word; cache
  hits and Groq fallback answers return as a single instant chunk
- In-memory caching: 24hr TTL, fuzzy question matching (difflib, 85%
  similarity), `UNCACHEABLE_ANSWERS` protection so transient errors (503s
  etc.) never get cached for 24 hours
- `ask_gemini()`/`ask_gemini_stream()` properly RAISE exceptions on failure
  (don't swallow them) — this is what makes rate-limit-detection →
  Groq-fallback actually work
- Groq client built FRESH per call, not once at module level (avoids a
  `load_dotenv()` timing bug hit earlier)
- `FORCE_GROQ` debug env var — set true to force all Gemini-lane questions
  through Groq for isolated testing
- Model names in this space go stale FAST — always verify against the
  provider's live model list before trusting any suggested name (multiple
  models have gone dead mid-project)

---

## Hosting (LIVE, not local)

- **Database**: Aiven (cloud MySQL, free tier — no hour cap, just sleeps on
  inactivity, 1GB storage limit not close to being hit)
- **Backend + Frontend**: Render (single Flask app serves both API and HTML/JS,
  gunicorn as the production WSGI server — NOT `app.run()` directly, that's
  Flask's dev server and shows a warning in production). Free tier = 750
  instance hours/month (confirmed via web search, NOT 150 as initially
  assumed) — always-on is safe since 750hrs > a full month's hours
- **Netlify**: NOT used — decided a single Render deployment serving
  everything is sufficient, no need to split frontend separately
- **GitHub**: private repo, `config.py` IS tracked (repo is private, and
  production only ever uses the cloud env-var path anyway via
  `USE_CLOUD_DB=true`)
- **Deploy flow**: local code changes → `git add -A && git commit -m "..." &&
  git push` → GitHub → Render auto-detects the new commit and redeploys.
  **NOTHING IS LIVE UNTIL PUSHED.** Rayaan has repeatedly tested against
  pre-push local state and reported false bugs — always confirm push +
  Render redeploy completed before treating a live-site test as valid.
- **Keep-alive**: UptimeRobot pings `/healthz` every 5 minutes, 24/7, keeping
  both Render and Aiven permanently awake

`config.py` supports switching between local MySQL and Aiven cloud MySQL via
a `USE_CLOUD_DB` environment variable (default false = local), reading cloud
credentials from `CLOUD_DB_HOST/PORT/USER/PASSWORD/NAME` in `.env`.

---

## Mobile UI/UX — comprehensive audit completed

Lighthouse mobile scores: **Performance 0.69** (explicitly accepted as final —
see Key Design Decisions below), **Accessibility 1.00**, **Best Practices 1.00**.

Fixed during the audit: WCAG contrast failures, missing image dimensions
(CLS risk), logo resized from 331KB to 3.4KB (99% smaller), missing favicon
added, render-blocking scripts deferred, all tap targets brought to 44×44px
minimum, a Tailwind CDN JIT compiler bug (classes only used in JS-injected
template strings silently fail to generate CSS — fixed + warning comment
left in app.js for future dynamic class additions).

axe-core: 0 violations across the whole app after fixes (was 1 critical +
several moderate + multiple contrast failures).

**Testing-tool quirks encountered repeatedly** (not app bugs — don't waste
time trying to "fix" these if you hit them again):
- The headless browser testing environment doesn't animate CSS transitions
  triggered by `classList` changes — must disable the transition before
  reading a computed position to get real values
- Arrow-key scroll behavior inside a scrollable focused region can't be
  directly observed in this environment — verify via an isolated throwaway
  test element to distinguish "tool limitation" from "real bug" rather than
  assuming either way

---

## Branding decisions

- Project name: **YaraBot** (kept, considered "YIS Connect" but Rayaan chose
  to keep the original)
- Chatbot persona: **Nova** — "Hi, I'm Nova, your personalized assistant for
  Yara International School" — confirmed live in the greeting
- Fonts: Playfair Display (greeting name) and Quicksand bold (wordmark) were
  chosen as legitimate free alternatives — Claude's own greeting font is
  Anthropic's proprietary "Anthropic Serif" (not usable), Instagram's
  wordmark is hand-drawn custom lettering (no real font exists to copy)
- The playful mascot direction (chameleon, bouncy Lottie animations) is a
  deliberate blend with the originally-locked "professional & clean, Meta AI
  style" design language — Rayaan explicitly chose to blend both, not an
  accidental drift

---

## What's NOT yet done / left to build

### In progress or just-requested (check current state first)
1. **Notices UI + Almanac self-service editor** — reference code was just
   handed off before this briefing was written. Check dashboard.py for a
   "Notices" page and "Almanac" page, and app.py/nlp_helpers.py for a
   "notices" NLP intent, to see what actually landed. If not done yet: add
   a dashboard page for posting/deleting notices (title + body + date,
   surfaced via chatbot to all 3 roles), and a dashboard page with a textarea
   for editing `school_almanac.txt` directly, with auto-reload (checking file
   mtime) so changes take effect without restarting Flask — note this needs
   to integrate with the CURRENT almanac scoring functions
   (`_score_almanac_sections()`/`almanac_top_score()`), not a simplified
   version, since the routing architecture has changed since this was first
   designed.

### Clearly not started
2. **Session timeout handling** — a 401 (expired session) currently shows a
   raw error instead of redirecting cleanly to the login screen
3. **Real data migration** — currently running on dummy data (500 fake
   students, 50 fake teachers via `generate_dummy_data.py`). BLOCKED on
   Rayaan finding out where the school's real records actually live (Excel?
   paper? other software?) — needs a conversation with the school office.
   Real period start/end times should be added to the `timetable` table at
   this same stage, to replace the current rough time heuristic.
4. **Password reset flow** — doesn't exist yet
5. **Technical PDF for Rayaan** — a comprehensive, easy-to-understand
   document explaining the full tech stack and architecture, meant as
   viva/teacher-Q&A prep material. Requested but not yet built (a SEPARATE
   11-page non-technical PDF for school management, `YaraBot_Project_
   Overview.pdf`, has already been delivered — don't confuse the two)

---

## Key design decisions — locked, do not reverse without explicit request

1. Rule-based NLP for personal/core queries — explainable, offline-capable,
   privacy-safe. This is intentional, not a limitation to "fix" by switching
   to an LLM for everything.
2. Gemini/Groq only ever see general school knowledge (the almanac) — NEVER
   student personal data. Hard privacy boundary, not a technical convenience.
3. Dashboard stays Streamlit (internal admin tool only) — the chatbot was
   deliberately rebuilt from Streamlit to Flask+HTML/CSS/JS specifically
   because Streamlit's visual ceiling wasn't good enough for the student/
   teacher/principal-facing product. Don't suggest reverting the chatbot to
   Streamlit, and don't "upgrade" the dashboard's visuals unless asked.
4. Vanilla JS, no React/Vue, no build step — keeps the frontend deployable
   as plain files served directly by Flask/Render with zero build pipeline.
5. Flask server-side sessions (not JWT) — deliberate simplicity choice.
6. Notes/PPT feature was built then explicitly removed from scope.
7. Lighthouse Performance score of 0.69 is EXPLICITLY ACCEPTED AS FINAL —
   improving it further would require reversing the no-build-step
   architecture (self-hosted compiled Tailwind, a real bundler), which
   Rayaan decided isn't worth it given the app's actual users (mostly WiFi,
   low-stakes school utility, not a consumer app competing for attention).
   Do not "fix" this score by suggesting architecture changes.
8. The NLP-first routing architecture (see "Two-lane chat routing" above)
   replaced a simpler pronoun-based router after repeated real bugs. Don't
   simplify it back.
9. The chameleon mascot's position/size CSS variables are deliberately split
   into independent desktop/mobile pairs (6 total vars) — don't collapse
   these back into shared variables, this was a specific fix for a reported
   UX problem.

---

## Workflow notes — how Rayaan works with you

- Rayaan plans and designs with a separate Claude chat (not you, cannot
  communicate with you directly) — that chat writes reference code and
  detailed prompts, which get pasted to you here.
- Rayaan wants actual working reference code in prompts, not just prose
  descriptions — but you should still exercise FULL judgment to adapt, fix,
  refactor, or diverge from any reference code where it doesn't fit the real
  current codebase. Reference code is a starting point, never a rigid spec.
  This matters MORE now than earlier in the project — a lot of reference
  code written earlier (e.g. the notices/almanac snippet) predates major
  architecture changes and will need real adaptation, not verbatim use.
- You've repeatedly caught real bugs by testing thoroughly rather than
  trusting first implementations or even this chat's own reference code/
  prompts at face value — e.g. finding that a suggested CSS fix
  (`preserveAspectRatio: slice`) wasn't actually the cause of a padding
  issue, then measuring the real cause across 150 animation frames instead.
  Keep doing this — verify computationally, test through the real Flask
  route (not just module-level), and flag anything you find rather than
  silently fixing and moving on.
- You distinguish real bugs from testing-tool limitations by building
  isolated minimal test cases rather than assuming either way — this has
  correctly prevented "fixing" non-existent bugs at least twice (CSS
  transitions not animating in headless testing, arrow-key scroll not
  directly observable).
- Model names go stale fast this cycle — always verify against live model
  lists, don't trust suggested names (Rayaan's or the other chat's) at
  face value.
- **CRITICAL recurring gotcha**: nothing is live until `git push`. Rayaan
  has repeatedly tested against pre-push local state and reported false
  bugs as a result. Always confirm the push happened and Render finished
  redeploying before treating a live-site test report as accurate.
- Bug-class thinking works well for this project: when a bug pattern
  repeats (ambiguous keyword collisions happened 3 times), fix the
  structural root cause once rather than patching each instance as it
  surfaces. Rayaan explicitly values this over whack-a-mole fixes.

---

## Running the project

```bash
# Chatbot (Flask)
cd YaraBot
python app.py
# Opens at http://localhost:5000

# Dashboard (Streamlit) — separate terminal
cd YaraBot
streamlit run dashboard.py
# Opens at http://localhost:8501

# First time setup only
python setup_database.py       # creates DB and tables (local or cloud,
                                 # depending on USE_CLOUD_DB in .env)
python generate_dummy_data.py   # fills with 500 students, 50 teachers
# Then create logins via Streamlit dashboard → Logins page

# Deploy to production
git add -A
git commit -m "..."
git push
# Render auto-detects the new commit and redeploys — check Render's
# dashboard/logs to confirm the deploy actually succeeded before assuming
# changes are live
```

---

## Important context

- School: Yara International School, Riyadh, Saudi Arabia — CBSE-affiliated,
  Kindergarten through Grade XII. Motto: "We want your Kid to be a Star!"
  (source of the blue/gold branding and the star/chameleon visual motifs)
- Developer: Grade 12 student (Mohammed Rayaan), team lead. One friend
  helps with manual data entry via the dashboard.
- Teacher's condition: "If testing goes well, whole school uses it" —
  already confirmed as a genuine possibility, this is being built as a real
  production system, not a school-project demo
- This is intentionally kept as an independent project, separate from
  Rayaan's actual CBSE school project (which is a different, simpler,
  not-yet-started project)
- No-mobile-phones-at-school policy — means teachers/principal are the
  PRIMARY daytime chatbot users (students mostly use it before/after school
  or at home) — this shaped several NLP feature priorities (live "what
  class am I teaching right now" features were prioritized for teachers
  specifically because of this usage pattern)
