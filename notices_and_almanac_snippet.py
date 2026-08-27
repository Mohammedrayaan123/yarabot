# ============================================================
# NOTICES UI + ALMANAC EDITOR — reference code for Claude Code
# ============================================================
# Gives non-technical management/admin staff two easy tools:
# 1. Post/manage school-wide notices through the dashboard,
#    surfaced to students/teachers via the chatbot
# 2. Edit the almanac (general school knowledge Gemini uses)
#    through a simple textarea, auto-reloading without restart
# ============================================================

# ---------------------------------------------------------
# ADD to dashboard.py — "Notices" page in sidebar navigation
# ---------------------------------------------------------
NOTICES_PAGE_CODE = '''
elif page == "Notices":
    st.title("School Notices / Announcements")
    st.caption("Post announcements that students and teachers can see through the chatbot.")

    st.header("Post New Notice")
    with st.form("add_notice_form", clear_on_submit=True):
        notice_title = st.text_input("Title (e.g. 'Sports Day Postponed')")
        notice_body = st.text_area("Message", height=150)
        notice_submitted = st.form_submit_button("Post Notice")

        if notice_submitted:
            if not notice_title.strip() or not notice_body.strip():
                st.error("Please fill in both title and message.")
            else:
                import datetime
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO notices (title, body, posted_by, date_posted)
                       VALUES (%s, %s, %s, %s)""",
                    (notice_title.strip(), notice_body.strip(), 0, datetime.date.today())
                )
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"Notice '{notice_title.strip()}' posted successfully!")

    st.header("Current Notices")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT notice_id, title, body, date_posted FROM notices ORDER BY date_posted DESC, notice_id DESC")
    notices = cursor.fetchall()
    cursor.close()
    conn.close()

    if not notices:
        st.info("No notices posted yet.")
    else:
        for notice_id, title, body, date_posted in notices:
            with st.expander(f"{title} — {date_posted}"):
                st.write(body)
                if st.button("Delete", key=f"del_notice_{notice_id}"):
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM notices WHERE notice_id=%s", (notice_id,))
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.success("Notice deleted.")
                    st.rerun()
'''
# Add "Notices" to the sidebar page list, insert this elif block
# following the same pattern as the other pages (Students, Teachers, etc.)


# ---------------------------------------------------------
# ADD to dashboard.py — "Almanac" page in sidebar navigation
# ---------------------------------------------------------
ALMANAC_PAGE_CODE = '''
elif page == "Almanac":
    st.title("School Almanac / General Information")
    st.caption(
        "This is the general school knowledge the AI assistant uses to answer "
        "questions like holidays, PTM dates, policies, and school rules. "
        "Edit it here - changes take effect automatically, no restart needed."
    )

    almanac_path = "school_almanac.txt"

    try:
        with open(almanac_path, "r", encoding="utf-8") as f:
            current_content = f.read()
    except FileNotFoundError:
        current_content = ""
        st.warning("No almanac file found yet. Start writing below to create one.")

    with st.form("edit_almanac_form"):
        new_content = st.text_area(
            "Almanac content",
            value=current_content,
            height=600,
            help="Keep related information grouped together, separated by blank lines - "
                 "this helps the AI find relevant sections when answering questions."
        )
        save_clicked = st.form_submit_button("Save Changes")

        if save_clicked:
            try:
                with open(almanac_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                st.success("Almanac updated! The chatbot will use the new information immediately.")
            except Exception as e:
                st.error(f"Could not save: {e}")

    st.divider()
    st.caption(f"Current file size: {len(current_content)} characters")
'''


# ---------------------------------------------------------
# UPDATE gemini_rag.py — auto-reload almanac when file changes
# Replace the one-time-load ALMANAC constant with a function that
# checks the file's last-modified time and reloads only when changed.
# ---------------------------------------------------------
ALMANAC_AUTORELOAD_CODE = '''
import os

_almanac_cache = {"content": "", "last_modified": 0}
ALMANAC_PATH = "school_almanac.txt"

def get_almanac():
    """Returns current almanac content, auto-reloading if the file changed."""
    try:
        current_mtime = os.path.getmtime(ALMANAC_PATH)
    except FileNotFoundError:
        return ""

    if current_mtime != _almanac_cache["last_modified"]:
        with open(ALMANAC_PATH, "r", encoding="utf-8") as f:
            _almanac_cache["content"] = f.read()
        _almanac_cache["last_modified"] = current_mtime
        print(f"[ALMANAC RELOADED] {len(_almanac_cache['content'])} chars")

    return _almanac_cache["content"]
'''
# Replace all usages of the old module-level ALMANAC constant
# (in search_almanac() etc.) with calls to get_almanac() instead.
# IMPORTANT: this snippet predates the NLP routing rework - the current
# codebase uses _score_almanac_sections()/almanac_top_score() for scoring,
# not a simple ALMANAC constant. Adapt get_almanac() to plug into whatever
# the CURRENT almanac-loading mechanism actually is.


# ---------------------------------------------------------
# ADD to nlp_helpers.py — new "notices" intent, all roles
# ---------------------------------------------------------
NOTICES_INTENT = {
    "notices": {
        "phrases": ["latest notices", "any announcements", "school notices",
                    "any updates", "recent announcements"],
        "keywords": ["notice", "notices", "announcement", "announcements"],
    },
}
# Before wiring this in, check it against the AMBIGUOUS_KEYWORDS audit and
# the personal-signal/phrase-match scoring rules built during the NLP
# routing rework - confirm no collision with existing intents.


# ---------------------------------------------------------
# ADD to app.py — shared notices handler, usable by all 3 roles
# ---------------------------------------------------------
def handle_notices():
    """Returns the most recent notices, newest first, capped at 5."""
    results = query("""
        SELECT title, body, date_posted FROM notices
        ORDER BY date_posted DESC, notice_id DESC
        LIMIT 5
    """, fetch=True, many=True)

    if not results:
        return "No notices posted at the moment."

    lines = []
    for title, body, date_posted in results:
        lines.append(f"**{title}** ({date_posted})\\n{body}")
    return "Latest notices:\\n\\n" + "\\n\\n".join(lines)

# Wire "notices" intent into the detect_intent() call and elif chain
# for answer_student(), answer_teacher(), AND answer_principal() -
# this should be visible to everyone, same handler for all three.
