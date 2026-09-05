"""
dashboard.py
------------
Admin-only school management dashboard, protected by a login screen.
Organized as separate "pages" via a sidebar menu.

Run: streamlit run dashboard.py
"""

import os
import time
import datetime
import streamlit as st
import mysql.connector
import pandas as pd
from auth_helpers import hash_password, verify_password
from config import DB_CONFIG
from nlp_helpers import check_phrase_safety, apply_phrase_to_intent_data, ALWAYS_SCORED_INTENTS
from app import ROLE_PERSONAL_INTENTS
from validators import (
    validate_name, validate_class, validate_contact,
    validate_roll_no, validate_attendance, validate_username,
    validate_password, validate_subject_name,
    validate_classes_assigned, collect_errors
)
import csv_import

st.set_page_config(page_title="School Dashboard", page_icon="🏫", layout="wide")

st.markdown("""
<style>
h1 { color: #2c3e50; }
h2 { color: #34495e; border-bottom: 2px solid #eee; padding-bottom: 6px; }
div.stButton button, div.stFormSubmitButton button { border-radius: 6px; }
</style>
""", unsafe_allow_html=True)


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
    """fetch=True for SELECT (many=True for all rows), fetch=False commits
    an INSERT/UPDATE/DELETE. Returns None (and shows a Streamlit error) on failure."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        if fetch:
            result = cursor.fetchall() if many else cursor.fetchone()
        else:
            conn.commit()
            result = cursor.lastrowid
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
    ["Students", "Teachers", "Departments", "Class Teachers", "Subjects", "Timetable", "Exams",
     "Logins", "System Status", "Notices", "Almanac", "Suggested Additions", "Learned Phrases"]
)


# =========================================================
# PAGE: STUDENTS
# =========================================================
if page == "Students":
    st.title("Students")

    st.header("Add New Student")
    tab1, tab2 = st.tabs(["Add One Entry", "Bulk Upload (CSV)"])

    with tab1:
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

    with tab2:
        st.write(
            "Upload a CSV with student records. Column headers are matched "
            "flexibly - **Student Name**/**Name**/**Full Name**, "
            "**Class**/**Grade**(+**Section**), **Roll No**/**Roll Number**, "
            "**DOB**/**Date of Birth**, **Parent Name**/**Guardian**, "
            "**Parent Contact**/**Phone**/**Mobile** all work - so a real "
            "school export doesn't need to be retyped into an exact template."
        )
        st.caption(
            "fees_status and attendance_pct aren't part of a roster export "
            "(they're tracked over time in this app, not a one-time import "
            "field) - every imported row gets the default you choose below."
        )

        template_df = pd.DataFrame({
            "Student Name": ["Aarav Sharma"], "Class": ["10-A"], "Roll No": [5],
            "DOB": ["15/06/2010"], "Parent Name": ["Rohan Sharma"],
            "Parent Contact": ["00966512345678"],
        })
        st.download_button(
            "Download CSV Template", template_df.to_csv(index=False),
            file_name="students_template.csv", key="student_template_dl"
        )

        col_a, col_b = st.columns(2)
        default_fees_status = col_a.selectbox("Default fees status for imported rows", ["pending", "paid"])
        default_attendance = col_b.number_input(
            "Default attendance % for imported rows", min_value=0.0, max_value=100.0, value=0.0
        )

        uploaded_file = st.file_uploader("Upload student CSV", type="csv", key="student_csv_upload")

        if uploaded_file is not None:
            upload_df = pd.read_csv(uploaded_file)

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT class, roll_no FROM students")
            existing_pairs = {
                (csv_import.normalize_class_code(str(cls)), int(roll)) for cls, roll in cursor.fetchall()
            }
            cursor.close()
            conn.close()

            results, missing, mapping = csv_import.dry_run_students(
                upload_df, existing_pairs,
                fees_status_default=default_fees_status,
                attendance_pct_default=default_attendance,
            )

            if missing:
                st.error(
                    "Could not find a column for: " + ", ".join(missing) +
                    ". Detected columns: " + ", ".join(f"'{c}'" for c in upload_df.columns)
                )
            else:
                valid_rows = [r for r in results if r["status"] == "valid"]
                rejected_rows = [r for r in results if r["status"] == "rejected"]

                st.subheader(f"Dry run: {len(valid_rows)} would be imported, {len(rejected_rows)} rejected")

                if valid_rows:
                    st.success(f"✅ {len(valid_rows)} row(s) ready to import:")
                    preview_df = pd.DataFrame([
                        {"Row": r["row_number"], "Name": r["data"]["name"], "Class": r["data"]["class"],
                         "Roll No": r["data"]["roll_no"], "DOB": r["data"]["dob"],
                         "Parent Name": r["data"]["parent_name"], "Parent Contact": r["data"]["parent_contact"]}
                        for r in valid_rows
                    ])
                    st.dataframe(preview_df, hide_index=True)

                if rejected_rows:
                    st.error(f"⚠️ {len(rejected_rows)} row(s) rejected - nothing here will be imported:")
                    rejected_df = pd.DataFrame([
                        {"Row": r["row_number"], "Reasons": "; ".join(r["reasons"])}
                        for r in rejected_rows
                    ])
                    st.dataframe(rejected_df, hide_index=True)

                if valid_rows:
                    if st.button(f"Confirm Import ({len(valid_rows)} students)", key="confirm_student_import"):
                        conn = get_connection()
                        cursor = conn.cursor()

                        def _insert_student(data):
                            cursor.execute(
                                """INSERT INTO students
                                   (name, class, roll_no, dob, parent_name, parent_contact,
                                    fees_status, attendance_pct)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                (data["name"], data["class"], data["roll_no"], data["dob"],
                                 data["parent_name"], data["parent_contact"],
                                 data["fees_status"], data["attendance_pct"])
                            )

                        count = csv_import.apply_students(results, _insert_student)
                        conn.commit()
                        cursor.close()
                        conn.close()
                        st.success(f"✅ Imported {count} students.")
                        st.rerun()

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

    # Teachers link to subjects via teacher_subjects now (a teacher can
    # teach more than one) - subject_id FK's target is the existing
    # `subjects` table, which has one row per (subject_name, class) pair,
    # not a clean subject-name lookup. teacher_subjects only cares about
    # the NAME, so the first (lowest id) row for each distinct name stands
    # in for it - every consumer of this map only ever reads the name back
    # via a join, never that representative row's own class value.
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subject_id, subject_name FROM subjects ORDER BY subject_name, subject_id")
    _subject_rows = cursor.fetchall()
    cursor.close()
    conn.close()
    name_to_subject_id = {}
    for _sid, _sname in _subject_rows:
        name_to_subject_id.setdefault(_sname, _sid)
    subject_name_options = sorted(name_to_subject_id.keys())

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT department_id, name FROM departments ORDER BY name")
    _department_rows = cursor.fetchall()
    cursor.close()
    conn.close()
    department_id_by_name = {"(None)": None}
    department_id_by_name.update({name: did for did, name in _department_rows})
    department_name_options = list(department_id_by_name.keys())

    t_tab1, t_tab2 = st.tabs(["Add One Entry", "Bulk Upload (CSV)"])

    with t_tab1:
        with st.form("add_teacher_form", clear_on_submit=True):
            t_name = st.text_input("Teacher Name")
            t_subjects = st.multiselect("Subjects Taught", subject_name_options)
            if not subject_name_options:
                st.caption("No subjects exist yet - add one on the Subjects page first.")
            t_department_label = st.selectbox("Department", department_name_options)
            if not _department_rows:
                st.caption("No departments exist yet - add one on the Departments page first.")
            t_contact = st.text_input("Contact Number (10 digits)")
            t_classes = st.text_input("Classes Assigned (e.g. 10-A, 10-B, 9-C)")

            t_submitted = st.form_submit_button("Add Teacher")

            if t_submitted:
                errors = collect_errors(
                    validate_name(t_name),
                    (bool(t_subjects), "Select at least one subject."),
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
                        """INSERT INTO teachers (name, contact, classes_assigned, department_id)
                           VALUES (%s, %s, %s, %s)""",
                        (t_name.strip(), t_contact.strip(), t_classes.strip(),
                         department_id_by_name[t_department_label])
                    )
                    new_teacher_id = cursor.lastrowid
                    for sname in t_subjects:
                        cursor.execute(
                            "INSERT INTO teacher_subjects (teacher_id, subject_id) VALUES (%s, %s)",
                            (new_teacher_id, name_to_subject_id[sname])
                        )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.success(f"✅ Teacher '{t_name.strip()}' added successfully!")

    with t_tab2:
        st.write(
            "Upload a CSV with teacher records. Column headers are matched "
            "flexibly - **Teacher Name**/**Name**, **Subject**/**Department**, "
            "**Contact**/**Phone**, **Classes**/**Assigned Classes** all work. "
            "A teacher can teach more than one subject - separate them with "
            "a comma or semicolon in the Subject column (e.g. "
            "\"Mathematics, Physics\")."
        )
        st.caption(
            "⚠️ Duplicate teacher names are flagged as a warning, not blocked - "
            "with ~130 real staff, duplicate names are real (this school already "
            "has two different teachers both named \"Tariq Al-Rashid\"). "
            "Flagged rows are still imported; the chatbot disambiguates by "
            "subject at query time if a name match is ambiguous."
        )

        t_template_df = pd.DataFrame({
            "Teacher Name": ["Krishna Gupta"], "Subject": ["Mathematics, Physics"],
            "Contact": ["00966512345678"], "Classes": ["10-A, 10-B"],
        })
        st.download_button(
            "Download CSV Template", t_template_df.to_csv(index=False),
            file_name="teachers_template.csv", key="teacher_template_dl"
        )

        t_uploaded_file = st.file_uploader("Upload teacher CSV", type="csv", key="teacher_csv_upload")

        if t_uploaded_file is not None:
            t_upload_df = pd.read_csv(t_uploaded_file)

            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM teachers")
            existing_teacher_names = {row[0].strip().lower() for row in cursor.fetchall()}
            cursor.close()
            conn.close()

            t_results, t_missing, t_mapping = csv_import.dry_run_teachers(t_upload_df, existing_teacher_names)

            if t_missing:
                st.error(
                    "Could not find a column for: " + ", ".join(t_missing) +
                    ". Detected columns: " + ", ".join(f"'{c}'" for c in t_upload_df.columns)
                )
            else:
                t_valid_rows = [r for r in t_results if r["status"] in ("valid", "warning")]
                t_warning_rows = [r for r in t_results if r["status"] == "warning"]
                t_rejected_rows = [r for r in t_results if r["status"] == "rejected"]

                st.subheader(
                    f"Dry run: {len(t_valid_rows)} would be imported "
                    f"({len(t_warning_rows)} with a duplicate-name warning), "
                    f"{len(t_rejected_rows)} rejected"
                )

                if t_valid_rows:
                    st.success(f"✅ {len(t_valid_rows)} row(s) ready to import:")
                    t_preview_df = pd.DataFrame([
                        {"Row": r["row_number"], "Name": r["data"]["name"],
                         "Subjects": ", ".join(r["data"]["subjects"]),
                         "Contact": r["data"]["contact"], "Classes Assigned": r["data"]["classes_assigned"],
                         "Warning": "; ".join(r["reasons"]) if r["status"] == "warning" else ""}
                        for r in t_valid_rows
                    ])
                    st.dataframe(t_preview_df, hide_index=True)

                if t_rejected_rows:
                    st.error(f"⚠️ {len(t_rejected_rows)} row(s) rejected - nothing here will be imported:")
                    t_rejected_df = pd.DataFrame([
                        {"Row": r["row_number"], "Reasons": "; ".join(r["reasons"])}
                        for r in t_rejected_rows
                    ])
                    st.dataframe(t_rejected_df, hide_index=True)

                if t_valid_rows:
                    if st.button(f"Confirm Import ({len(t_valid_rows)} teachers)", key="confirm_teacher_import"):
                        conn = get_connection()
                        cursor = conn.cursor()
                        # Local copy, not the page-level map: a CSV subject
                        # name that isn't in the Subjects page yet gets a
                        # school-wide entry created for it (class left blank)
                        # the first time this import batch sees it, rather
                        # than rejecting an otherwise-valid teacher row.
                        csv_name_to_subject_id = dict(name_to_subject_id)

                        def _insert_teacher(data):
                            cursor.execute(
                                """INSERT INTO teachers (name, contact, classes_assigned)
                                   VALUES (%s, %s, %s)""",
                                (data["name"], data["contact"], data["classes_assigned"])
                            )
                            new_id = cursor.lastrowid
                            for sname in data["subjects"]:
                                if sname not in csv_name_to_subject_id:
                                    cursor.execute(
                                        "INSERT INTO subjects (subject_name, class) VALUES (%s, NULL)",
                                        (sname,)
                                    )
                                    csv_name_to_subject_id[sname] = cursor.lastrowid
                                cursor.execute(
                                    "INSERT INTO teacher_subjects (teacher_id, subject_id) VALUES (%s, %s)",
                                    (new_id, csv_name_to_subject_id[sname])
                                )

                        t_count = csv_import.apply_teachers(t_results, _insert_teacher)
                        conn.commit()
                        cursor.close()
                        conn.close()
                        st.success(f"✅ Imported {t_count} teachers.")
                        st.rerun()

    st.header("Current Teachers")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT te.teacher_id, te.name,
               COALESCE(GROUP_CONCAT(DISTINCT s.subject_name ORDER BY s.subject_name SEPARATOR ', '), ''),
               te.contact, te.classes_assigned, COALESCE(d.name, '(None)')
        FROM teachers te
        LEFT JOIN teacher_subjects ts ON te.teacher_id = ts.teacher_id
        LEFT JOIN subjects s ON ts.subject_id = s.subject_id
        LEFT JOIN departments d ON te.department_id = d.department_id
        GROUP BY te.teacher_id, te.name, te.contact, te.classes_assigned, d.name
    """)
    teacher_rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if teacher_rows:
        teacher_df = pd.DataFrame(
            teacher_rows,
            columns=["ID", "Name", "Subjects", "Contact", "Classes Assigned", "Department"]
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
            "SELECT name, contact, classes_assigned, department_id FROM teachers WHERE teacher_id = %s",
            (selected_teacher_id,)
        )
        current_t = cursor.fetchone()
        cursor.execute(
            """SELECT s.subject_name FROM teacher_subjects ts
               JOIN subjects s ON ts.subject_id = s.subject_id
               WHERE ts.teacher_id = %s ORDER BY s.subject_name""",
            (selected_teacher_id,)
        )
        current_t_subjects = [r[0] for r in cursor.fetchall()]
        cursor.close()
        conn.close()

        current_department_label = next(
            (label for label, did in department_id_by_name.items() if did == current_t[3]),
            "(None)"
        )

        with st.form("edit_teacher_form"):
            te_name = st.text_input("Teacher Name", value=current_t[0])
            te_subjects = st.multiselect(
                "Subjects Taught", subject_name_options,
                default=[s for s in current_t_subjects if s in subject_name_options]
            )
            te_department_label = st.selectbox(
                "Department", department_name_options,
                index=department_name_options.index(current_department_label)
            )
            te_contact = st.text_input("Contact Number", value=current_t[1])
            te_classes = st.text_input("Classes Assigned", value=current_t[2])

            colA, colB = st.columns(2)
            with colA:
                t_update_clicked = st.form_submit_button("Save Changes")
            with colB:
                t_delete_clicked = st.form_submit_button("Delete Teacher", type="secondary")

            if t_update_clicked:
                errors = collect_errors(
                    validate_name(te_name),
                    (bool(te_subjects), "Select at least one subject."),
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
                           SET name=%s, contact=%s, classes_assigned=%s, department_id=%s
                           WHERE teacher_id=%s""",
                        (te_name.strip(), te_contact.strip(), te_classes.strip(),
                         department_id_by_name[te_department_label], selected_teacher_id)
                    )
                    # Replace this teacher's subject links wholesale with
                    # whatever's selected now - simplest correct semantics
                    # for a "save this form's current state" edit.
                    cursor.execute("DELETE FROM teacher_subjects WHERE teacher_id=%s", (selected_teacher_id,))
                    for sname in te_subjects:
                        cursor.execute(
                            "INSERT INTO teacher_subjects (teacher_id, subject_id) VALUES (%s, %s)",
                            (selected_teacher_id, name_to_subject_id[sname])
                        )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.success(f"✅ '{te_name.strip()}' updated successfully!")
                    st.rerun()

            if t_delete_clicked:
                conn = get_connection()
                cursor = conn.cursor()
                # teacher_subjects rows must go first - it has a FK on
                # teacher_id, same FK-safe-order reasoning as everywhere
                # else in this project (see reset_data.py). A department
                # that has THIS teacher as its HOD must be cleared too -
                # departments.hod_teacher_id is also an FK to teachers.
                cursor.execute("DELETE FROM teacher_subjects WHERE teacher_id=%s", (selected_teacher_id,))
                cursor.execute("UPDATE departments SET hod_teacher_id=NULL WHERE hod_teacher_id=%s", (selected_teacher_id,))
                cursor.execute("DELETE FROM teachers WHERE teacher_id=%s", (selected_teacher_id,))
                conn.commit()
                cursor.close()
                conn.close()
                st.warning(f"'{te_name}' was deleted.")
                st.rerun()


# =========================================================
# PAGE: DEPARTMENTS
# Teacher-to-department assignment itself lives on the Teachers page (a
# "Department" field on the Add/Edit forms) - one place to set it, matching
# how a teacher's other single-valued fields (contact, classes_assigned)
# already work there. This page is for the departments themselves: create/
# edit/delete, and assigning each one's HOD.
# =========================================================
elif page == "Departments":
    st.title("Departments")

    st.header("Add New Department")
    with st.form("add_department_form", clear_on_submit=True):
        d_name = st.text_input("Department Name (e.g. Science)")
        d_submitted = st.form_submit_button("Add Department")

        if d_submitted:
            if not d_name.strip():
                st.error("⚠️ Department name cannot be empty.")
            else:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute("INSERT INTO departments (name) VALUES (%s)", (d_name.strip(),))
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"✅ Department '{d_name.strip()}' added.")

    st.header("Current Departments")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT d.department_id, d.name, hod.name,
               (SELECT COUNT(*) FROM teachers te WHERE te.department_id = d.department_id)
        FROM departments d
        LEFT JOIN teachers hod ON d.hod_teacher_id = hod.teacher_id
        ORDER BY d.name
    """)
    department_rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if department_rows:
        dept_df = pd.DataFrame(department_rows, columns=["ID", "Name", "HOD", "Teachers"])
        st.dataframe(dept_df, hide_index=True)
    else:
        st.info("No departments added yet. Use the form above to add one.")

    st.header("Edit or Delete a Department")
    if not department_rows:
        st.info("No departments to edit yet.")
    else:
        dept_edit_options = {f"{r[1]} (ID {r[0]})": r[0] for r in department_rows}
        selected_dept_label = st.selectbox("Select a department", list(dept_edit_options.keys()), key="edit_select_department")
        selected_dept_id = dept_edit_options[selected_dept_label]

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name, hod_teacher_id FROM departments WHERE department_id=%s", (selected_dept_id,))
        current_d = cursor.fetchone()
        cursor.execute("SELECT teacher_id, name FROM teachers ORDER BY name")
        all_teachers = cursor.fetchall()
        cursor.close()
        conn.close()

        hod_options = {"(No HOD assigned)": None}
        hod_options.update({name: tid for tid, name in all_teachers})
        hod_labels = list(hod_options.keys())
        current_hod_label = next((label for label, tid in hod_options.items() if tid == current_d[1]),
                                  "(No HOD assigned)")

        with st.form("edit_department_form"):
            de_name = st.text_input("Department Name", value=current_d[0])
            de_hod_label = st.selectbox(
                "Head of Department", hod_labels, index=hod_labels.index(current_hod_label)
            )

            colA, colB = st.columns(2)
            with colA:
                d_update_clicked = st.form_submit_button("Save Changes")
            with colB:
                d_delete_clicked = st.form_submit_button("Delete Department", type="secondary")

            if d_update_clicked:
                if not de_name.strip():
                    st.error("⚠️ Department name cannot be empty.")
                else:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE departments SET name=%s, hod_teacher_id=%s WHERE department_id=%s",
                        (de_name.strip(), hod_options[de_hod_label], selected_dept_id)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.success(f"✅ '{de_name.strip()}' updated successfully!")
                    st.rerun()

            if d_delete_clicked:
                conn = get_connection()
                cursor = conn.cursor()
                # Teachers in this department become unassigned rather than
                # blocking the delete - department_id is nullable by design
                # (see setup_database.py).
                cursor.execute("UPDATE teachers SET department_id=NULL WHERE department_id=%s", (selected_dept_id,))
                cursor.execute("DELETE FROM departments WHERE department_id=%s", (selected_dept_id,))
                conn.commit()
                cursor.close()
                conn.close()
                st.warning(f"'{current_d[0]}' was deleted. Its teachers are now unassigned.")
                st.rerun()


# =========================================================
# PAGE: CLASS TEACHERS
# =========================================================
elif page == "Class Teachers":
    st.title("Class Teachers")
    st.write(
        "Assign one class teacher (homeroom teacher) per class section - "
        "answers a student's 'who is my class teacher'."
    )

    conn = get_connection()
    cursor = conn.cursor()
    # There's no standalone "classes" table (see setup_database.py) - class
    # codes are free text scattered across several tables, so the list of
    # classes that actually exist is derived from wherever one might be
    # recorded, same as how the chatbot itself has no single source of truth
    # for "every class" either.
    cursor.execute("""
        SELECT DISTINCT class FROM students
        UNION
        SELECT DISTINCT class FROM timetable
        ORDER BY class
    """)
    all_classes = [row[0] for row in cursor.fetchall()]

    cursor.execute("SELECT teacher_id, name FROM teachers ORDER BY name")
    all_teachers = cursor.fetchall()

    cursor.execute("SELECT class, teacher_id FROM class_teachers")
    current_assignments = dict(cursor.fetchall())
    cursor.close()
    conn.close()

    if not all_classes:
        st.info("No classes found yet - add students or a timetable first.")
    else:
        teacher_options = {"(No class teacher assigned)": None}
        teacher_options.update({name: tid for tid, name in all_teachers})
        teacher_labels = list(teacher_options.keys())

        selected_class = st.selectbox("Select a class", all_classes, key="class_teacher_select_class")
        current_tid = current_assignments.get(selected_class)
        current_label = next(
            (label for label, tid in teacher_options.items() if tid == current_tid),
            "(No class teacher assigned)"
        )

        with st.form("class_teacher_form"):
            ct_label = st.selectbox(
                "Class Teacher", teacher_labels, index=teacher_labels.index(current_label)
            )
            ct_submitted = st.form_submit_button("Save")

            if ct_submitted:
                conn = get_connection()
                cursor = conn.cursor()
                new_tid = teacher_options[ct_label]
                if new_tid is None:
                    cursor.execute("DELETE FROM class_teachers WHERE class=%s", (selected_class,))
                else:
                    cursor.execute(
                        """INSERT INTO class_teachers (class, teacher_id) VALUES (%s, %s)
                           ON DUPLICATE KEY UPDATE teacher_id=%s""",
                        (selected_class, new_tid, new_tid)
                    )
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"✅ Class teacher for '{selected_class}' updated.")
                st.rerun()

    st.header("Current Class Teacher Assignments")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ct.class, te.name
        FROM class_teachers ct
        JOIN teachers te ON ct.teacher_id = te.teacher_id
        ORDER BY ct.class
    """)
    class_teacher_rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if class_teacher_rows:
        st.dataframe(pd.DataFrame(class_teacher_rows, columns=["Class", "Class Teacher"]), hide_index=True)
    else:
        st.info("No class teachers assigned yet.")


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

    login_role = st.radio(
        "Create login for:",
        ["Student", "Teacher", "HOD", "Vice Principal", "Assistant Principal", "Principal"],
        key="login_role_choice"
    )
    # DB role values use underscores ("vice_principal") - can't just
    # .lower() the label the way the original Student/Teacher/Principal-only
    # version did ("Vice Principal".lower() has a space, not an underscore).
    ROLE_VALUE_BY_LABEL = {
        "Student": "student", "Teacher": "teacher", "HOD": "hod",
        "Vice Principal": "vice_principal", "Assistant Principal": "assistant_principal",
        "Principal": "principal",
    }
    role_value = ROLE_VALUE_BY_LABEL[login_role]

    if login_role in ("Principal", "Assistant Principal"):
        st.caption(f"{login_role} accounts see school-wide stats, not personal records, so no need to link to a specific person.")
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
                               VALUES (%s, %s, %s, 0)""",
                            (new_username.strip(), hashed, role_value)
                        )
                        conn.commit()
                        st.success(f"✅ {login_role} login created. Username: {new_username.strip()}")
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
            # Teacher, HOD, and Vice Principal all link to a teacher record -
            # HOD/vice_principal log in AS a teacher (see _build_profile()/
            # HOD_LIKE_ROLES in app.py), just with extra department-scoped
            # access on top.
            cursor.execute("""
                SELECT te.teacher_id, te.name,
                       COALESCE(GROUP_CONCAT(DISTINCT s.subject_name ORDER BY s.subject_name SEPARATOR ', '), '')
                FROM teachers te
                LEFT JOIN teacher_subjects ts ON te.teacher_id = ts.teacher_id
                LEFT JOIN subjects s ON ts.subject_id = s.subject_id
                GROUP BY te.teacher_id, te.name
            """)
            people = cursor.fetchall()
            people_options = {(f"{name} ({subj})" if subj else name): pid for pid, name, subj in people}
        cursor.close()
        conn.close()

        if not people_options:
            st.info("No students added yet." if login_role == "Student" else "No teachers added yet.")
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
                                (new_username.strip(), hashed, role_value, linked_id)
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
# PAGE: SYSTEM STATUS
# Principal-only kill switch's re-enable side (app.py's /api/kill-switch
# only ever turns it OFF - see that endpoint's docstring). "Visible only to
# principal and admin roles" is already satisfied by this dashboard's own
# login gate at the top of this file - a single shared ADMIN_USERNAME/
# PASSWORD credential, not tied to any individual `users` row, guards
# every page here, so nobody else can reach this one either. For the same
# reason, a re-enable's performed_by is logged as NULL (no real user_id
# exists to attribute it to from here) and shown as "Dashboard Admin"
# below - a disable, by contrast, always carries a real principal's
# user_id, logged by app.py from their actual chatbot session.
# =========================================================
elif page == "System Status":
    st.title("System Status")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM system_settings WHERE `key`='chatbot_enabled'")
    status_row = cursor.fetchone()
    cursor.close()
    conn.close()
    enabled = status_row is not None and status_row[0] == "true"

    if enabled:
        st.success("🟢 YaraBot is ONLINE")
    else:
        st.error("🔴 YaraBot is OFFLINE")

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COALESCE(u.username, 'Dashboard Admin'), sl.timestamp
            FROM system_logs sl
            LEFT JOIN users u ON sl.performed_by = u.user_id
            WHERE sl.action = 'disable'
            ORDER BY sl.log_id DESC LIMIT 1
        """)
        last_disable = cursor.fetchone()
        cursor.close()
        conn.close()

        if last_disable:
            who, when = last_disable
            st.caption(f"Disabled by **{who}** on {when.strftime('%B %d, %Y at %I:%M %p')}")

        st.header("Re-enable YaraBot")

        # Turning it back on is deliberate, not emergency - a simple
        # click + confirmation dialog is enough, no 5-second hold like the
        # chatbot's own disable button.
        @st.dialog("Confirm")
        def _confirm_reenable():
            st.write("Are you sure you want to bring YaraBot back online?")
            colA, colB = st.columns(2)
            with colA:
                if st.button("Yes, bring it online", type="primary", use_container_width=True):
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute("UPDATE system_settings SET value='true' WHERE `key`='chatbot_enabled'")
                    cursor.execute(
                        "INSERT INTO system_logs (action, performed_by) VALUES (%s, %s)",
                        ("enable", None)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.rerun()
            with colB:
                if st.button("Cancel", use_container_width=True):
                    st.rerun()

        if st.button("Re-enable YaraBot"):
            _confirm_reenable()

    st.header("Recent Toggle History")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT sl.timestamp, sl.action, COALESCE(u.username, 'Dashboard Admin')
        FROM system_logs sl
        LEFT JOIN users u ON sl.performed_by = u.user_id
        ORDER BY sl.log_id DESC LIMIT 10
    """)
    history_rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if history_rows:
        history_df = pd.DataFrame(history_rows, columns=["Timestamp", "Action", "By"])
        st.dataframe(history_df, hide_index=True)
    else:
        st.info("No toggle events yet.")


# =========================================================
# PAGE: NOTICES
# Lets the principal/assistant_principal (this dashboard's own login gate
# already restricts it to them - see the System Status page's docstring
# for why there's no finer-grained per-role check possible here) post
# school-wide announcements that show up through the chatbot's "notices"
# intent - see handle_notices()/_notice_visible_roles() in app.py for the
# chatbot side, and the "notices" entry in nlp_helpers.py's INTENT_DATA.
# =========================================================
elif page == "Notices":
    st.title("School Notices / Announcements")
    st.caption("Post announcements students, teachers, HODs, and/or the principal can see through the chatbot.")

    # Labels shown here map to app.py's _notice_visible_roles() vocabulary -
    # the 4 access-tier buckets (student/teacher/hod/principal), not the
    # raw 7-value users.role ENUM. "HODs" also reaches vice_principal and
    # "Principal" also reaches assistant_principal automatically (same
    # additive visibility app.py already applies for chat access).
    NOTICE_TARGET_LABELS = {"Students": "student", "Teachers": "teacher",
                             "HODs": "hod", "Principal": "principal"}
    NOTICE_PRIORITY_DISPLAY = {"urgent": "🔴 Urgent", "important": "🟡 Important", "normal": "Normal"}

    st.header("Post New Notice")
    with st.form("add_notice_form", clear_on_submit=True):
        notice_title = st.text_input("Title (e.g. 'Sports Day Postponed')")
        notice_body = st.text_area("Message", height=150)
        notice_target_labels = st.multiselect(
            "Visible to (leave empty for everyone)",
            list(NOTICE_TARGET_LABELS.keys())
        )
        notice_priority = st.selectbox("Priority", ["normal", "important", "urgent"], index=0)
        notice_submitted = st.form_submit_button("Post Notice")

        if notice_submitted:
            if not notice_title.strip() or not notice_body.strip():
                st.error("⚠️ Please fill in both title and message.")
            else:
                target_roles = (
                    ",".join(NOTICE_TARGET_LABELS[label] for label in notice_target_labels)
                    if notice_target_labels else "all"
                )
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    """INSERT INTO notices (title, body, posted_by, date_posted, target_roles, priority)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    # posted_by=0: dashboard admin isn't a row in `users`, so 0 is
                    # a sentinel for "posted via the admin dashboard".
                    (notice_title.strip(), notice_body.strip(), 0, datetime.date.today(),
                     target_roles, notice_priority)
                )
                conn.commit()
                cursor.close()
                conn.close()
                st.success(f"✅ Notice '{notice_title.strip()}' posted successfully!")

    st.header("Current Notices")
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT notice_id, title, body, date_posted, target_roles, priority FROM notices "
        "ORDER BY date_posted DESC, notice_id DESC"
    )
    notices = cursor.fetchall()
    cursor.close()
    conn.close()

    if not notices:
        st.info("No notices posted yet.")
    else:
        for notice_id, title, body, date_posted, target_roles, priority in notices:
            audience = "Everyone" if target_roles in (None, "all") else target_roles.replace(",", ", ")
            priority_label = NOTICE_PRIORITY_DISPLAY.get(priority, priority or "Normal")
            with st.expander(f"{title} — {date_posted}  ·  {priority_label}  ·  {audience}"):
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
# Questions Nova genuinely had no almanac context for - see
# log_unanswered_question() in gemini_rag.py. Near-duplicate phrasings
# group into one row (ask_count) via the same word-overlap matching the
# response cache uses.
#
# Nothing here applies automatically - every "Add to Almanac" requires a
# human to type the answer and click Save. This is a review queue, not an
# auto-apply pipeline, given this project's history of ambiguous-keyword
# routing bugs.
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


# =========================================================
# PAGE: LEARNED PHRASES
# Candidate phrasings the AI classifier (app.py's classifier lane) resolved
# correctly even though NLP's own score_intent() missed them - grouped by
# near-duplicate wording via the same word-overlap mechanism as Suggested
# Additions above, sorted by how often each has been asked.
#
# Safety status is computed FRESH on every page load (nlp_helpers.
# check_phrase_safety()), never cached - approving one candidate can
# change the collision picture for another still pending review, so a
# stored verdict would go stale and actively mislead.
#
# Approve edits nlp_helpers.py's INTENT_DATA directly on disk (Option A) -
# this does NOT take effect until app.py is restarted, since the running
# Flask process already has the OLD module loaded in memory. Said
# explicitly next to the button below, unlike the Almanac editor's
# genuinely-instant apply, so the two don't get confused. Option B (a
# hot-reloadable phrases file, same mtime-based auto-reload pattern as
# the almanac) is a deliberate future upgrade, not an oversight - see
# gemini_rag.py's LEARNED PHRASES section for the full reasoning.
# =========================================================
elif page == "Learned Phrases":
    st.title("🧠 Learned Phrases")
    st.caption(
        "Phrasings the AI classifier figured out that NLP's own scoring missed, sorted "
        "by how often they've been asked. Approving one adds it to NLP's phrase list "
        "directly, so it's answered instantly next time with no AI call needed."
    )
    st.warning(
        "⚠️ **Approve edits nlp_helpers.py on disk, but does NOT take effect until the "
        "app is restarted.** The running server already has the old phrase list loaded "
        "in memory - this is not an instant-apply action like the Almanac editor."
    )

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, phrase_text, resolved_intent, role, ask_count, first_asked, last_asked "
        "FROM learned_phrases WHERE applied = 0 ORDER BY ask_count DESC, last_asked DESC"
    )
    candidates = cursor.fetchall()
    cursor.close()
    conn.close()

    if not candidates:
        st.info("No learned-phrase candidates yet - once the AI classifier resolves something "
                "NLP itself missed, it'll show up here.")
    else:
        for cid, phrase_text, resolved_intent, role, ask_count, first_asked, last_asked in candidates:
            # Every role list that resolved_intent shows up in, unioned -
            # so check_phrase_safety()'s diagnostic warnings can tell
            # "shares vocabulary with an intent that's actually scored
            # alongside this one" apart from a same-named overlap in an
            # unrelated role's intent list.
            same_role_intents = {
                other for role_list in ROLE_PERSONAL_INTENTS.values()
                if resolved_intent in role_list
                for other in role_list if other != resolved_intent
            }
            # role_groups: the actual go/no-go now (see check_phrase_safety()'s
            # docstring) - one full real candidate list PER role resolved_intent
            # appears in (greeting/thanks/help always folded in too, since
            # every role's real detect_intent() call includes them even though
            # ROLE_PERSONAL_INTENTS itself doesn't), so the routing simulation
            # matches exactly what a live question in that role would face -
            # not the flattened same_role_intents union, which would blur
            # separate roles' candidate lists together and reject phrases that
            # are actually fine in each role on its own.
            role_groups = [
                set(role_list) | ALWAYS_SCORED_INTENTS
                for role_list in ROLE_PERSONAL_INTENTS.values()
                if resolved_intent in role_list
            ]
            status, reason = check_phrase_safety(phrase_text, resolved_intent, same_role_intents, role_groups)
            tag = "🟢 Safe to add" if status == "safe" else "🔴 Needs review"

            with st.expander(f'({ask_count}x) "{phrase_text}" → {resolved_intent} [{role}] — {tag}'):
                st.caption(f"First asked: {first_asked} · Last asked: {last_asked}")
                if status == "safe":
                    st.success("🟢 Safe to add — no collision found against any other intent.")
                else:
                    st.error(f"🔴 Needs review — {reason}")

                col1, col2 = st.columns(2)
                approve_clicked = col1.button("Approve", key=f"approve_btn_{cid}")
                col1.caption("Takes effect after the next app restart.")
                dismiss_clicked = col2.button("Dismiss", key=f"dismiss_btn_{cid}")

                if approve_clicked:
                    try:
                        inserted = apply_phrase_to_intent_data(phrase_text, resolved_intent)
                    except ValueError as e:
                        st.error(f"Could not apply: {e}")
                    else:
                        conn = get_connection()
                        cursor = conn.cursor()
                        cursor.execute("UPDATE learned_phrases SET applied=1 WHERE id=%s", (cid,))
                        conn.commit()
                        cursor.close()
                        conn.close()
                        if inserted:
                            st.success(
                                f'✅ Added "{phrase_text}" to \'{resolved_intent}\' in nlp_helpers.py. '
                                "Restart the app for this to take effect."
                            )
                        else:
                            st.info("Already present in nlp_helpers.py - marked resolved.")
                        st.rerun()

                if dismiss_clicked:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM learned_phrases WHERE id=%s", (cid,))
                    conn.commit()
                    cursor.close()
                    conn.close()
                    st.warning("Dismissed.")
                    st.rerun()
