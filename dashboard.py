"""
dashboard.py
------------
The school management dashboard - admin-only, protected by a login
screen. Organized as separate "pages" using a sidebar menu.

WHAT'S NEW IN THIS VERSION:
1. Login screen - only admins can access this dashboard now.
2. Full Edit/Delete for Subjects, Timetable, and Exams (previously
   only Students and Teachers had this).
3. Graceful error handling - if the database connection fails, you
   get a clear message instead of a scary crash screen.
4. Basic input validation - e.g. contact numbers must be 10 digits.
5. A little visual polish.

To run this file:
    streamlit run dashboard.py
"""

import os
import time
import datetime
import streamlit as st
import mysql.connector
import pandas as pd
from auth_helpers import hash_password, verify_password
from config import DB_CONFIG
from validators import (
    validate_name, validate_class, validate_contact,
    validate_roll_no, validate_attendance, validate_username,
    validate_password, validate_subject_name,
    validate_classes_assigned, collect_errors
)

# ---- Page setup ----
st.set_page_config(page_title="School Dashboard", page_icon="🏫", layout="wide")

# A little bit of visual polish - custom styling for headers and buttons
st.markdown("""
<style>
h1 { color: #2c3e50; }
h2 { color: #34495e; border-bottom: 2px solid #eee; padding-bottom: 6px; }
div.stButton button, div.stFormSubmitButton button { border-radius: 6px; }
</style>
""", unsafe_allow_html=True)


# ---- Helper function: connect to our database, with graceful error handling ----
def get_connection():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as err:
        st.error(
            "⚠️ Could not connect to the database. Make sure MySQL is running "
            f"and your config.py settings are correct.\n\nDetails: {err}"
        )
        st.stop()


def safe_query(query, params=None, fetch=False, many=False):
    """
    Runs a SQL query safely with error handling built in.
    - fetch=True  → returns rows (for SELECT)
    - fetch=False → commits the change (for INSERT/UPDATE/DELETE)
    - many=False  → single row; many=True → all rows
    Returns None if something goes wrong (shows error to user).
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        if fetch:
            result = cursor.fetchall() if many else cursor.fetchone()
        else:
            conn.commit()
            result = cursor.lastrowid  # useful for INSERT to get the new ID
        cursor.close()
        conn.close()
        return result
    except mysql.connector.IntegrityError as e:
        st.error(f"⚠️ Duplicate or invalid entry: {e}")
        return None
    except mysql.connector.Error as e:
        st.error(f"⚠️ Database error: {e}")
        return None
    except Exception as e:
        st.error(f"⚠️ Unexpected error: {e}")
        return None




# =========================================================
# ADMIN CREDENTIALS
# Read from the environment so the real password isn't sitting in source.
# Falls back to the old admin/admin123 default for local dev - set
# DASHBOARD_ADMIN_USERNAME / DASHBOARD_ADMIN_PASSWORD_HASH before real
# deployment (use auth_helpers.hash_password() to generate the hash).
# =========================================================
ADMIN_USERNAME = os.environ.get("DASHBOARD_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD_HASH = os.environ.get(
    "DASHBOARD_ADMIN_PASSWORD_HASH", hash_password("admin1234")
)
if "DASHBOARD_ADMIN_PASSWORD_HASH" not in os.environ:
    print("WARNING: DASHBOARD_ADMIN_PASSWORD_HASH not set - using the default "
          "admin/admin123 login. Set it before deploying for real.")

# Failed-login rate limiting - resets on server restart, which is fine at
# this scale (single admin, single school).
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW = 300  # seconds


# =========================================================
# LOGIN GATE - nothing below this runs until logged in
# =========================================================
if "admin_logged_in" not in st.session_state:
    st.session_state.admin_logged_in = False
if "login_attempts" not in st.session_state:
    st.session_state.login_attempts = 0
if "login_window_start" not in st.session_state:
    st.session_state.login_window_start = 0.0

if not st.session_state.admin_logged_in:
    st.title("🏫 School Dashboard - Admin Login")

    now = time.time()
    if now - st.session_state.login_window_start > LOGIN_ATTEMPT_WINDOW:
        st.session_state.login_attempts = 0
        st.session_state.login_window_start = now

    if st.session_state.login_attempts >= LOGIN_ATTEMPT_LIMIT:
        wait_left = int(LOGIN_ATTEMPT_WINDOW - (now - st.session_state.login_window_start))
        st.error(f"Too many failed attempts. Please try again in {max(wait_left, 1)} seconds.")
    else:
        with st.form("admin_login_form"):
            username_input = st.text_input("Username")
            password_input = st.text_input("Password", type="password")
            login_clicked = st.form_submit_button("Log In")

            if login_clicked:
                if username_input == ADMIN_USERNAME and verify_password(password_input, ADMIN_PASSWORD_HASH):
                    st.session_state.admin_logged_in = True
                    st.session_state.login_attempts = 0
                    st.rerun()
                else:
                    st.session_state.login_attempts += 1
                    st.error("Incorrect username or password.")

    st.stop()  # nothing past this point runs until login succeeds


# =========================================================
# SIDEBAR NAVIGATION (only reachable after login)
# =========================================================
st.sidebar.title("🏫 School Dashboard")
if st.sidebar.button("Log Out"):
    st.session_state.admin_logged_in = False
    st.rerun()

page = st.sidebar.radio(
    "Go to",
    ["Students", "Teachers", "Subjects", "Timetable", "Exams", "Logins",
     "Notices", "Almanac", "Suggested Additions"]
)


# =========================================================
# PAGE: STUDENTS
# =========================================================
if page == "Students":
    st.title("Students")

    st.header("Add New Student")
    with st.form("add_student_form", clear_on_submit=True):
        name = st.text_input("Student Name")
        student_class = st.text_input("Class (e.g. 10-A)")
        roll_no = st.number_input("Roll Number", min_value=1, step=1)
        dob = st.date_input("Date of Birth")
        parent_name = st.text_input("Parent's Name")
        parent_contact = st.text_input("Parent's Contact Number (10 digits)")
        fees_status = st.selectbox("Fees Status", ["paid", "pending"])
        attendance_pct = st.number_input("Attendance %", min_value=0.0, max_value=100.0, value=100.0)

        submitted = st.form_submit_button("Add Student")

        if submitted:
            errors = collect_errors(
                validate_name(name),
                validate_class(student_class),
                validate_contact(parent_contact),
                validate_attendance(attendance_pct),
            )
            # Parent name is optional but if filled, validate it
            if parent_name.strip():
                errors += collect_errors(validate_name(parent_name))

            if errors:
                for e in errors:
                    st.error(f"⚠️ {e}")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO students
                       (name, class, roll_no, dob, parent_name, parent_contact, fees_status, attendance_pct)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (name.strip(), student_class.strip().upper(), roll_no, dob,
                     parent_name.strip(), parent_contact.strip(), fees_status, attendance_pct)
                )
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"✅ Student '{name.strip()}' added successfully!")

    st.header("Current Students")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT student_id, name, class, roll_no, fees_status, attendance_pct FROM students")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if rows:
        df = pd.DataFrame(rows, columns=["ID", "Name", "Class", "Roll No", "Fees", "Attendance %"])
        st.dataframe(df, hide_index=True)
    else:
        st.info("No students added yet. Use the form above to add one.")

    st.header("Edit or Delete a Student")
    if not rows:
        st.info("No students to edit yet.")
    else:
        student_options = {f"{r[1]} (ID {r[0]})": r[0] for r in rows}
        selected_label = st.selectbox("Select a student", list(student_options.keys()), key="edit_select_student")
        selected_id = student_options[selected_label]

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, class, roll_no, dob, parent_name, parent_contact, fees_status, attendance_pct "
            "FROM students WHERE student_id = %s",
            (selected_id,)
        )
        current = cursor.fetchone()
        cursor.close()
        conn.close()

        with st.form("edit_student_form"):
            e_name = st.text_input("Student Name", value=current[0])
            e_class = st.text_input("Class", value=current[1])
            e_roll_no = st.number_input("Roll Number", min_value=1, step=1, value=current[2])
            e_dob = st.date_input("Date of Birth", value=current[3])
            e_parent_name = st.text_input("Parent's Name", value=current[4])
            e_parent_contact = st.text_input("Parent's Contact Number", value=current[5])
            e_fees_status = st.selectbox("Fees Status", ["paid", "pending"], index=["paid", "pending"].index(current[6]))
            e_attendance = st.number_input("Attendance %", min_value=0.0, max_value=100.0, value=float(current[7]))

            col1, col2 = st.columns(2)
            with col1:
                update_clicked = st.form_submit_button("Save Changes")
            with col2:
                delete_clicked = st.form_submit_button("Delete Student", type="secondary")

            if update_clicked:
                errors = collect_errors(
                    validate_name(e_name),
                    validate_class(e_class),
                    validate_contact(e_parent_contact),
                    validate_attendance(e_attendance),
                )
                if errors:
                    for e in errors:
                        st.error(f"⚠️ {e}")
                else:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        """UPDATE students
                           SET name=%s, class=%s, roll_no=%s, dob=%s, parent_name=%s,
                               parent_contact=%s, fees_status=%s, attendance_pct=%s
                           WHERE student_id=%s""",
                        (e_name.strip(), e_class.strip().upper(), e_roll_no, e_dob,
                         e_parent_name.strip(), e_parent_contact.strip(),
                         e_fees_status, e_attendance, selected_id)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.success(f"✅ '{e_name.strip()}' updated successfully!")
                    st.rerun()

            if delete_clicked:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM students WHERE student_id=%s", (selected_id,))
                conn.commit()
                cursor.close()
                conn.close()
                st.warning(f"'{e_name}' was deleted.")
                st.rerun()


# =========================================================
# PAGE: TEACHERS
# =========================================================
elif page == "Teachers":
    st.title("Teachers")

    st.header("Add New Teacher")
    with st.form("add_teacher_form", clear_on_submit=True):
        t_name = st.text_input("Teacher Name")
        t_subject = st.text_input("Subject Taught (e.g. Mathematics)")
        t_contact = st.text_input("Contact Number (10 digits)")
        t_classes = st.text_input("Classes Assigned (e.g. 10-A, 10-B, 9-C)")

        t_submitted = st.form_submit_button("Add Teacher")

        if t_submitted:
            errors = collect_errors(
                validate_name(t_name),
                validate_subject_name(t_subject),
                validate_contact(t_contact),
                validate_classes_assigned(t_classes),
            )
            if errors:
                for e in errors:
                    st.error(f"⚠️ {e}")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO teachers (name, subject, contact, classes_assigned)
                       VALUES (%s, %s, %s, %s)""",
                    (t_name.strip(), t_subject.strip(), t_contact.strip(), t_classes.strip())
                )
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"✅ Teacher '{t_name.strip()}' added successfully!")

    st.header("Current Teachers")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT teacher_id, name, subject, contact, classes_assigned FROM teachers")
    teacher_rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if teacher_rows:
        teacher_df = pd.DataFrame(
            teacher_rows,
            columns=["ID", "Name", "Subject", "Contact", "Classes Assigned"]
        )
        st.dataframe(teacher_df, hide_index=True)
    else:
        st.info("No teachers added yet. Use the form above to add one.")

    st.header("Edit or Delete a Teacher")
    if not teacher_rows:
        st.info("No teachers to edit yet.")
    else:
        teacher_edit_options = {f"{r[1]} (ID {r[0]})": r[0] for r in teacher_rows}
        selected_teacher_label = st.selectbox("Select a teacher", list(teacher_edit_options.keys()), key="edit_select_teacher")
        selected_teacher_id = teacher_edit_options[selected_teacher_label]

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name, subject, contact, classes_assigned FROM teachers WHERE teacher_id = %s",
            (selected_teacher_id,)
        )
        current_t = cursor.fetchone()
        cursor.close()
        conn.close()

        with st.form("edit_teacher_form"):
            te_name = st.text_input("Teacher Name", value=current_t[0])
            te_subject = st.text_input("Subject Taught", value=current_t[1])
            te_contact = st.text_input("Contact Number", value=current_t[2])
            te_classes = st.text_input("Classes Assigned", value=current_t[3])

            colA, colB = st.columns(2)
            with colA:
                t_update_clicked = st.form_submit_button("Save Changes")
            with colB:
                t_delete_clicked = st.form_submit_button("Delete Teacher", type="secondary")

            if t_update_clicked:
                errors = collect_errors(
                    validate_name(te_name),
                    validate_subject_name(te_subject),
                    validate_contact(te_contact),
                    validate_classes_assigned(te_classes),
                )
                if errors:
                    for e in errors:
                        st.error(f"⚠️ {e}")
                else:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        """UPDATE teachers
                           SET name=%s, subject=%s, contact=%s, classes_assigned=%s
                           WHERE teacher_id=%s""",
                        (te_name.strip(), te_subject.strip(),
                         te_contact.strip(), te_classes.strip(), selected_teacher_id)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.success(f"✅ '{te_name.strip()}' updated successfully!")
                    st.rerun()

            if t_delete_clicked:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM teachers WHERE teacher_id=%s", (selected_teacher_id,))
                conn.commit()
                cursor.close()
                conn.close()
                st.warning(f"'{te_name}' was deleted.")
                st.rerun()


# =========================================================
# PAGE: SUBJECTS
# =========================================================
elif page == "Subjects":
    st.title("Subjects")

    st.header("Add New Subject")
    with st.form("add_subject_form", clear_on_submit=True):
        s_name = st.text_input("Subject Name (e.g. Mathematics)")
        s_class = st.text_input("Class (e.g. 10-A)")

        s_submitted = st.form_submit_button("Add Subject")

        if s_submitted:
            errors = collect_errors(
                validate_subject_name(s_name),
                validate_class(s_class),
            )
            if errors:
                for e in errors:
                    st.error(f"⚠️ {e}")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO subjects (subject_name, class) VALUES (%s, %s)",
                    (s_name.strip(), s_class.strip().upper())
                )
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"✅ Subject '{s_name.strip()}' added for class {s_class.strip().upper()}.")

    st.header("Current Subjects")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subject_id, subject_name, class FROM subjects")
    subject_list = cursor.fetchall()
    cursor.close()
    conn.close()

    if subject_list:
        subj_df = pd.DataFrame(subject_list, columns=["ID", "Subject Name", "Class"])
        st.dataframe(subj_df, hide_index=True)
    else:
        st.info("No subjects added yet. Use the form above to add one.")

    st.header("Edit or Delete a Subject")
    if not subject_list:
        st.info("No subjects to edit yet.")
    else:
        subject_edit_options = {f"{name} ({cls}) (ID {sid})": sid for sid, name, cls in subject_list}
        selected_subj_label = st.selectbox("Select a subject", list(subject_edit_options.keys()), key="edit_select_subject")
        selected_subj_id = subject_edit_options[selected_subj_label]

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT subject_name, class FROM subjects WHERE subject_id = %s", (selected_subj_id,))
        current_s = cursor.fetchone()
        cursor.close()
        conn.close()

        with st.form("edit_subject_form"):
            es_name = st.text_input("Subject Name", value=current_s[0])
            es_class = st.text_input("Class", value=current_s[1])

            colX, colY = st.columns(2)
            with colX:
                s_update_clicked = st.form_submit_button("Save Changes")
            with colY:
                s_delete_clicked = st.form_submit_button("Delete Subject", type="secondary")

            if s_update_clicked:
                errors = collect_errors(
                    validate_subject_name(es_name),
                    validate_class(es_class),
                )
                if errors:
                    for e in errors:
                        st.error(f"⚠️ {e}")
                else:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE subjects SET subject_name=%s, class=%s WHERE subject_id=%s",
                        (es_name.strip(), es_class.strip().upper(), selected_subj_id)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.success(f"'{es_name}' updated successfully!")
                    st.rerun()

            if s_delete_clicked:
                conn = get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("DELETE FROM subjects WHERE subject_id=%s", (selected_subj_id,))
                    conn.commit()
                    st.warning(f"'{es_name}' was deleted.")
                except mysql.connector.Error:
                    st.error(
                        "Can't delete this subject - it's still used in the "
                        "Timetable or Exams. Delete those entries first."
                    )
                finally:
                    cursor.close()
                    conn.close()
                st.rerun()


# =========================================================
# PAGE: TIMETABLE
# =========================================================
elif page == "Timetable":
    st.title("Timetable")

    # Fetch subjects and teachers for the dropdowns
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subject_id, subject_name, class FROM subjects")
    subject_list = cursor.fetchall()
    cursor.execute("SELECT teacher_id, name FROM teachers")
    teacher_list = cursor.fetchall()
    cursor.close()
    conn.close()

    subject_options = {f"{name} ({cls})": sid for sid, name, cls in subject_list}
    teacher_options = {name: tid for tid, name in teacher_list}

    st.header("Add Timetable Entry")
    if not subject_options or not teacher_options:
        st.warning("Add at least one subject AND one teacher before creating timetable entries.")
    else:
        tab1, tab2 = st.tabs(["Add One Entry", "Bulk Upload (CSV)"])

        with tab1:
            with st.form("add_timetable_form", clear_on_submit=True):
                tt_class = st.text_input("Class (e.g. 10-A)")
                tt_day = st.selectbox("Day", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"])
                tt_period = st.number_input("Period Number", min_value=1, max_value=10, step=1)
                tt_subject_label = st.selectbox("Subject", list(subject_options.keys()))
                tt_teacher_label = st.selectbox("Teacher", list(teacher_options.keys()))

                tt_submitted = st.form_submit_button("Add Timetable Entry")

                if tt_submitted:
                    if tt_class.strip() == "":
                        st.error("Please enter a class.")
                    else:
                        subject_id = subject_options[tt_subject_label]
                        teacher_id = teacher_options[tt_teacher_label]

                        conn = get_connection()
                        cursor = conn.cursor()
                        cursor.execute(
                            """INSERT INTO timetable (class, day, period_no, subject_id, teacher_id)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (tt_class, tt_day, tt_period, subject_id, teacher_id)
                        )
                        conn.commit()
                        cursor.close()
                        conn.close()
                        st.success(f"Timetable entry added: {tt_class}, {tt_day}, Period {tt_period}.")

        with tab2:
            st.write(
                "Upload a CSV with these exact column headers: "
                "**class, day, period_no, subject_name, teacher_name**"
            )
            st.caption(
                "subject_name and teacher_name must match names already in the "
                "Subjects and Teachers pages exactly (case-sensitive)."
            )

            template_df = pd.DataFrame({
                "class": ["10-A"], "day": ["Monday"], "period_no": [1],
                "subject_name": ["Mathematics"], "teacher_name": ["Enter a real teacher name"]
            })
            st.download_button(
                "Download CSV Template",
                template_df.to_csv(index=False),
                file_name="timetable_template.csv"
            )

            uploaded_file = st.file_uploader("Upload filled CSV", type="csv")

            if uploaded_file is not None:
                upload_df = pd.read_csv(uploaded_file)
                required_cols = {"class", "day", "period_no", "subject_name", "teacher_name"}

                if not required_cols.issubset(upload_df.columns):
                    st.error(f"CSV must contain these columns: {', '.join(required_cols)}")
                else:
                    name_to_subject_id = {f"{name}|{cls}": sid for sid, name, cls in subject_list}
                    name_to_teacher_id = {name: tid for name, tid in teacher_options.items()}

                    success_count = 0
                    error_rows = []

                    conn = get_connection()
                    cursor = conn.cursor()
                    for i, row in upload_df.iterrows():
                        subject_key = f"{row['subject_name']}|{row['class']}"
                        teacher_name = row['teacher_name']

                        if subject_key not in name_to_subject_id:
                            error_rows.append(f"Row {i+1}: subject '{row['subject_name']}' not found for class '{row['class']}'")
                            continue
                        if teacher_name not in name_to_teacher_id:
                            error_rows.append(f"Row {i+1}: teacher '{teacher_name}' not found")
                            continue

                        cursor.execute(
                            """INSERT INTO timetable (class, day, period_no, subject_id, teacher_id)
                               VALUES (%s, %s, %s, %s, %s)""",
                            (row['class'], row['day'], int(row['period_no']),
                             name_to_subject_id[subject_key], name_to_teacher_id[teacher_name])
                        )
                        success_count += 1
                    conn.commit()
                    cursor.close()
                    conn.close()

                    if success_count:
                        st.success(f"Added {success_count} timetable entries.")
                    if error_rows:
                        st.warning("Some rows had issues:\n" + "\n".join(error_rows))

    st.header("Current Timetable")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.entry_id, t.class, t.day, t.period_no, s.subject_name, te.name
        FROM timetable t
        JOIN subjects s ON t.subject_id = s.subject_id
        JOIN teachers te ON t.teacher_id = te.teacher_id
        ORDER BY t.class, FIELD(t.day,'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'), t.period_no
    """)
    tt_rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if tt_rows:
        tt_df = pd.DataFrame(tt_rows, columns=["ID", "Class", "Day", "Period", "Subject", "Teacher"])
        st.dataframe(tt_df.drop(columns=["ID"]), hide_index=True)
    else:
        st.info("No timetable entries yet.")

    st.header("Edit or Delete a Timetable Entry")
    if not tt_rows:
        st.info("No entries to edit yet.")
    else:
        tt_edit_options = {
            f"{cls}, {day}, Period {period}: {subj} ({teacher})": eid
            for eid, cls, day, period, subj, teacher in tt_rows
        }
        selected_tt_label = st.selectbox("Select an entry", list(tt_edit_options.keys()), key="edit_select_timetable")
        selected_tt_id = tt_edit_options[selected_tt_label]

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT class, day, period_no, subject_id, teacher_id FROM timetable WHERE entry_id = %s",
            (selected_tt_id,)
        )
        current_tt = cursor.fetchone()
        cursor.close()
        conn.close()

        days_list = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        subject_ids = list(subject_options.values())
        teacher_ids = list(teacher_options.values())
        subject_labels = list(subject_options.keys())
        teacher_labels = list(teacher_options.keys())

        with st.form("edit_timetable_form"):
            et_class = st.text_input("Class", value=current_tt[0])
            et_day = st.selectbox("Day", days_list, index=days_list.index(current_tt[1]))
            et_period = st.number_input("Period Number", min_value=1, max_value=10, step=1, value=current_tt[2])
            et_subject_label = st.selectbox(
                "Subject", subject_labels,
                index=subject_ids.index(current_tt[3]) if current_tt[3] in subject_ids else 0
            )
            et_teacher_label = st.selectbox(
                "Teacher", teacher_labels,
                index=teacher_ids.index(current_tt[4]) if current_tt[4] in teacher_ids else 0
            )

            colP, colQ = st.columns(2)
            with colP:
                tt_update_clicked = st.form_submit_button("Save Changes")
            with colQ:
                tt_delete_clicked = st.form_submit_button("Delete Entry", type="secondary")

            if tt_update_clicked:
                errors = collect_errors(validate_class(et_class))
                if errors:
                    for e in errors:
                        st.error(f"⚠️ {e}")
                else:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        """UPDATE timetable
                           SET class=%s, day=%s, period_no=%s, subject_id=%s, teacher_id=%s
                           WHERE entry_id=%s""",
                        (et_class.strip().upper(), et_day, et_period,
                         subject_options[et_subject_label],
                         teacher_options[et_teacher_label], selected_tt_id)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                st.success("Timetable entry updated!")
                st.rerun()

            if tt_delete_clicked:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM timetable WHERE entry_id=%s", (selected_tt_id,))
                conn.commit()
                cursor.close()
                conn.close()
                st.warning("Timetable entry deleted.")
                st.rerun()


# =========================================================
# PAGE: EXAMS
# =========================================================
elif page == "Exams":
    st.title("Exams")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subject_id, subject_name, class FROM subjects")
    subject_list = cursor.fetchall()
    cursor.close()
    conn.close()

    subject_options = {f"{name} ({cls})": sid for sid, name, cls in subject_list}

    st.header("Add Exam")
    if not subject_options:
        st.warning("Add at least one subject before creating exams.")
    else:
        with st.form("add_exam_form", clear_on_submit=True):
            ex_class = st.text_input("Class (e.g. 10-A)", key="exam_class")
            ex_subject_label = st.selectbox("Subject", list(subject_options.keys()), key="exam_subject")
            ex_date = st.date_input("Exam Date")
            ex_type = st.selectbox("Exam Type", ["Unit Test", "Half Yearly", "Annual", "Pre-Board", "Other"])

            ex_submitted = st.form_submit_button("Add Exam")

            if ex_submitted:
                if ex_class.strip() == "":
                    st.error("Please enter a class.")
                else:
                    subject_id = subject_options[ex_subject_label]

                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        """INSERT INTO exams (class, subject_id, exam_date, exam_type)
                           VALUES (%s, %s, %s, %s)""",
                        (ex_class, subject_id, ex_date, ex_type)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.success(f"Exam added: {ex_class}, {ex_date}.")

    st.header("Current Exams")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.exam_id, e.class, s.subject_name, e.exam_date, e.exam_type
        FROM exams e
        JOIN subjects s ON e.subject_id = s.subject_id
        ORDER BY e.exam_date
    """)
    exam_rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if exam_rows:
        exam_df = pd.DataFrame(exam_rows, columns=["ID", "Class", "Subject", "Exam Date", "Exam Type"])
        st.dataframe(exam_df.drop(columns=["ID"]), hide_index=True)
    else:
        st.info("No exams added yet.")

    st.header("Edit or Delete an Exam")
    if not exam_rows:
        st.info("No exams to edit yet.")
    else:
        exam_edit_options = {
            f"{cls}, {subj}, {edate} ({etype})": eid
            for eid, cls, subj, edate, etype in exam_rows
        }
        selected_exam_label = st.selectbox("Select an exam", list(exam_edit_options.keys()), key="edit_select_exam")
        selected_exam_id = exam_edit_options[selected_exam_label]

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT class, subject_id, exam_date, exam_type FROM exams WHERE exam_id = %s",
            (selected_exam_id,)
        )
        current_e = cursor.fetchone()
        cursor.close()
        conn.close()

        subject_ids = list(subject_options.values())
        subject_labels = list(subject_options.keys())
        exam_types = ["Unit Test", "Half Yearly", "Annual", "Pre-Board", "Other"]

        with st.form("edit_exam_form"):
            ee_class = st.text_input("Class", value=current_e[0])
            ee_subject_label = st.selectbox(
                "Subject", subject_labels,
                index=subject_ids.index(current_e[1]) if current_e[1] in subject_ids else 0
            )
            ee_date = st.date_input("Exam Date", value=current_e[2])
            ee_type = st.selectbox(
                "Exam Type", exam_types,
                index=exam_types.index(current_e[3]) if current_e[3] in exam_types else 0
            )

            colM, colN = st.columns(2)
            with colM:
                exam_update_clicked = st.form_submit_button("Save Changes")
            with colN:
                exam_delete_clicked = st.form_submit_button("Delete Exam", type="secondary")

            if exam_update_clicked:
                errors = collect_errors(validate_class(ee_class))
                if errors:
                    for e in errors:
                        st.error(f"⚠️ {e}")
                else:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        """UPDATE exams SET class=%s, subject_id=%s, exam_date=%s, exam_type=%s
                           WHERE exam_id=%s""",
                        (ee_class.strip().upper(), subject_options[ee_subject_label],
                         ee_date, ee_type, selected_exam_id)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.success("✅ Exam updated!")
                    st.rerun()

            if exam_delete_clicked:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM exams WHERE exam_id=%s", (selected_exam_id,))
                conn.commit()
                cursor.close()
                conn.close()
                st.warning("Exam deleted.")
                st.rerun()


# =========================================================
# PAGE: LOGINS
# =========================================================
elif page == "Logins":
    st.title("Login Credentials")

    st.header("Create Login Credentials")
    st.caption("Give a student or teacher a username + password so they can log into the chatbot.")

    login_role = st.radio("Create login for:", ["Student", "Teacher", "Principal"], key="login_role_choice")

    if login_role == "Principal":
        st.caption("Principal accounts see school-wide stats, not personal records, so no need to link to a specific person.")
        with st.form("create_principal_login_form", clear_on_submit=True):
            new_username = st.text_input("Username")
            new_password = st.text_input("Password", type="password")
            login_submitted = st.form_submit_button("Create Login")

            if login_submitted:
                errors = collect_errors(
                    validate_username(new_username),
                    validate_password(new_password)
                )
                if errors:
                    for e in errors:
                        st.error(f"⚠️ {e}")
                else:
                    hashed = hash_password(new_password)
                    conn = get_connection()
                    cursor = conn.cursor()
                    try:
                        cursor.execute(
                            """INSERT INTO users (username, password_hash, role, linked_id)
                               VALUES (%s, %s, 'principal', 0)""",
                            (new_username.strip(), hashed)
                        )
                        conn.commit()
                        st.success(f"✅ Principal login created. Username: {new_username.strip()}")
                    except mysql.connector.IntegrityError:
                        st.error("⚠️ That username is already taken. Please choose another.")
                    finally:
                        cursor.close()
                        conn.close()
    else:
        conn = get_connection()
        cursor = conn.cursor()
        if login_role == "Student":
            cursor.execute("SELECT student_id, name, class FROM students")
            people = cursor.fetchall()
            people_options = {f"{name} ({cls})": pid for pid, name, cls in people}
        else:
            cursor.execute("SELECT teacher_id, name, subject FROM teachers")
            people = cursor.fetchall()
            people_options = {f"{name} ({subj})": pid for pid, name, subj in people}
        cursor.close()
        conn.close()

        if not people_options:
            st.info(f"No {login_role.lower()}s added yet.")
        else:
            with st.form("create_login_form", clear_on_submit=True):
                person_label = st.selectbox("Select person", list(people_options.keys()))
                new_username = st.text_input("Username (min 4 chars, no spaces)")
                new_password = st.text_input("Password (min 6 chars)", type="password")

                login_submitted = st.form_submit_button("Create Login")

                if login_submitted:
                    errors = collect_errors(
                        validate_username(new_username),
                        validate_password(new_password)
                    )
                    if errors:
                        for e in errors:
                            st.error(f"⚠️ {e}")
                    else:
                        linked_id = people_options[person_label]
                        hashed = hash_password(new_password)

                        conn = get_connection()
                        cursor = conn.cursor()
                        try:
                            cursor.execute(
                                """INSERT INTO users (username, password_hash, role, linked_id)
                                   VALUES (%s, %s, %s, %s)""",
                                (new_username.strip(), hashed, login_role.lower(), linked_id)
                            )
                            conn.commit()
                            st.success(f"✅ Login created for {person_label}. Username: {new_username.strip()}")
                        except mysql.connector.IntegrityError:
                            st.error("⚠️ That username is already taken. Please choose another.")
                        finally:
                            cursor.close()
                            conn.close()

    st.header("Existing Logins")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT username, role FROM users")
    user_rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if user_rows:
        user_df = pd.DataFrame(user_rows, columns=["Username", "Role"])
        st.dataframe(user_df, hide_index=True)
    else:
        st.info("No logins created yet.")


# =========================================================
# PAGE: NOTICES
# Lets non-technical staff post school-wide announcements that show up
# through the chatbot's "notices" intent for all three roles (student,
# teacher, principal) - see handle_notices() in app.py for the chatbot
# side, and the "notices" entry in nlp_helpers.py's INTENT_DATA.
# =========================================================
elif page == "Notices":
    st.title("School Notices / Announcements")
    st.caption("Post announcements that students, teachers, and the principal can see through the chatbot.")

    st.header("Post New Notice")
    with st.form("add_notice_form", clear_on_submit=True):
        notice_title = st.text_input("Title (e.g. 'Sports Day Postponed')")
        notice_body = st.text_area("Message", height=150)
        notice_submitted = st.form_submit_button("Post Notice")

        if notice_submitted:
            if not notice_title.strip() or not notice_body.strip():
                st.error("⚠️ Please fill in both title and message.")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO notices (title, body, posted_by, date_posted)
                       VALUES (%s, %s, %s, %s)""",
                    # posted_by=0: the dashboard admin login is a fixed env-var
                    # credential, not a row in `users` - there's no real user_id
                    # to reference here, so 0 is a sentinel for "posted via the
                    # admin dashboard".
                    (notice_title.strip(), notice_body.strip(), 0, datetime.date.today())
                )
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"✅ Notice '{notice_title.strip()}' posted successfully!")

    st.header("Current Notices")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT notice_id, title, body, date_posted FROM notices "
        "ORDER BY date_posted DESC, notice_id DESC"
    )
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
                    st.warning(f"'{title}' was deleted.")
                    st.rerun()


# =========================================================
# PAGE: ALMANAC
# Self-service editor for school_almanac.txt - the general school
# knowledge Gemini uses (holidays, PTM dates, policies, etc). Saving here
# takes effect immediately: gemini_rag.py's get_almanac() auto-reloads the
# file whenever its last-modified time changes, so there's no need to
# restart the Flask app after saving.
# =========================================================
elif page == "Almanac":
    st.title("School Almanac / General Information")
    st.caption(
        "This is the general school knowledge the AI assistant (Nova) uses to answer "
        "questions like holidays, PTM dates, admissions, and school policies. "
        "Edit it here - changes take effect automatically, no restart needed."
    )

    # Must match gemini_rag.py's ALMANAC_PATH exactly - both dashboard.py
    # and app.py/gemini_rag.py are run from the project root, so the same
    # relative filename resolves to the same file for both.
    almanac_path = "school_almanac.txt"

    try:
        with open(almanac_path, "r", encoding="utf-8") as f:
            current_content = f.read()
    except FileNotFoundError:
        current_content = ""
        st.warning("⚠️ No almanac file found yet. Start writing below to create one.")

    with st.form("edit_almanac_form"):
        new_content = st.text_area(
            "Almanac content",
            value=current_content,
            height=600,
            help="Keep related information grouped together, separated by a blank line - "
                 "this helps the AI find the right section when answering a question."
        )
        save_clicked = st.form_submit_button("Save Changes")

        if save_clicked:
            try:
                with open(almanac_path, "w", encoding="utf-8") as f:
                    f.write(new_content)
                st.success("✅ Almanac updated! The chatbot will use the new information immediately - no restart needed.")
            except Exception as e:
                st.error(f"⚠️ Could not save: {e}")

    st.divider()
    st.caption(f"Current file size: {len(current_content)} characters")


# =========================================================
# PAGE: SUGGESTED ADDITIONS
# Questions Nova (the Gemini lane) genuinely had no almanac context for -
# see log_unanswered_question() in gemini_rag.py, which only logs a
# question here when the final reply equalled NO_CONTEXT_MESSAGE exactly,
# not every Gemini-lane question. Near-duplicate phrasings of the same
# question are grouped into one row (ask_count) using the same
# normalize_question() + word-overlap matching the response cache already
# uses, at the same 0.85 similarity threshold.
#
# NOTHING here is ever applied automatically - every "Add to Almanac"
# requires a human admin to type the actual answer and click Save. Given
# this project's repeated ambiguous-keyword routing bugs
# (AMBIGUOUS_KEYWORDS in nlp_helpers.py), auto-modifying almanac content or
# routing without review is exactly the kind of risk that bug class came
# from - this page is a review queue, not an auto-apply pipeline.
# =========================================================
elif page == "Suggested Additions":
    st.title("💡 Suggested Additions")
    st.caption(
        "Questions students, teachers, or the principal asked that Nova genuinely "
        "couldn't answer, sorted by how often they've been asked. Add the ones worth "
        "having to the almanac (with your own answer), or dismiss the rest."
    )

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, question_text, ask_count, first_asked, last_asked "
        "FROM unanswered_questions ORDER BY ask_count DESC, last_asked DESC"
    )
    suggestions = cursor.fetchall()
    cursor.close()
    conn.close()

    if not suggestions:
        st.info("No unanswered questions logged yet - once Nova can't answer something, it'll show up here.")
    else:
        # Must match gemini_rag.py's ALMANAC_PATH exactly - see the same
        # note on the Almanac page above.
        almanac_path = "school_almanac.txt"

        for qid, question_text, ask_count, first_asked, last_asked in suggestions:
            with st.expander(f"({ask_count}x) {question_text}"):
                st.caption(f"First asked: {first_asked} · Last asked: {last_asked}")

                col1, col2 = st.columns(2)
                if col1.button("Add to Almanac", key=f"add_btn_{qid}"):
                    st.session_state[f"show_add_form_{qid}"] = True

                if col2.button("Dismiss", key=f"dismiss_btn_{qid}"):
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM unanswered_questions WHERE id=%s", (qid,))
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.warning("Dismissed.")
                    st.rerun()

                if st.session_state.get(f"show_add_form_{qid}"):
                    with st.form(f"add_form_{qid}"):
                        topic = st.text_input(
                            "Topic (what this almanac entry is about)",
                            value=question_text,
                        )
                        answer = st.text_area(
                            "Answer (what Nova should say)",
                            height=120,
                            help="Written as a plain statement, same style as the rest of the "
                                 "almanac - not a Q&A format."
                        )
                        submit = st.form_submit_button("Save to Almanac")

                        if submit:
                            if not answer.strip():
                                st.error("⚠️ Please write an answer before saving.")
                            else:
                                # Same read-then-write mechanism as the Almanac page above:
                                # gemini_rag.py's get_almanac() picks up the mtime change
                                # automatically, no restart needed.
                                try:
                                    with open(almanac_path, "r", encoding="utf-8") as f:
                                        existing = f.read()
                                except FileNotFoundError:
                                    existing = ""

                                new_section = f"{topic.strip()}\n{answer.strip()}"
                                separator = "\n\n" if existing.strip() else ""
                                updated = existing.rstrip("\n") + separator + new_section + "\n"

                                with open(almanac_path, "w", encoding="utf-8") as f:
                                    f.write(updated)

                                conn = get_connection()
                                cursor = conn.cursor()
                                cursor.execute("DELETE FROM unanswered_questions WHERE id=%s", (qid,))
                                conn.commit()
                                cursor.close()
                                conn.close()

                                st.session_state[f"show_add_form_{qid}"] = False
                                st.success("✅ Added to the almanac! Nova can answer this immediately - no restart needed.")
                                st.rerun()
