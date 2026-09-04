"""
setup_database.py
------------------
Run this ONCE to create the 'school_bot' database and all its tables.
Safe to run again later if you ever need to reset everything (it won't
duplicate the database, it'll just make sure it exists).
"""

import mysql.connector
from config import DB_CONFIG

# Connect without a database selected yet, since we're about to create it.
connection_settings = {k: v for k, v in DB_CONFIG.items() if k != "database"}
connection = mysql.connector.connect(**connection_settings)

cursor = connection.cursor()

cursor.execute("CREATE DATABASE IF NOT EXISTS school_bot")
print("Database 'school_bot' ready.")

cursor.execute("USE school_bot")

tables = {}

# Departments and teachers reference each other (a department has an HOD
# teacher; a teacher belongs to a department) - a genuine circular FK, which
# MySQL can't satisfy from two single CREATE TABLE statements. Created here
# with hod_teacher_id as a plain column (no FK yet); the FK pointing back at
# teachers is added afterward via ALTER TABLE, once teachers exists too.
tables["departments"] = """
CREATE TABLE IF NOT EXISTS departments (
    department_id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(50),
    hod_teacher_id INT
)
"""

tables["students"] = """
CREATE TABLE IF NOT EXISTS students (
    student_id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100),
    class VARCHAR(10),
    roll_no INT,
    dob DATE,
    parent_name VARCHAR(100),
    parent_contact VARCHAR(15),
    fees_status ENUM('paid','pending'),
    attendance_pct DECIMAL(5,2),
    UNIQUE KEY uq_class_roll_no (class, roll_no)
)
"""

tables["teachers"] = """
CREATE TABLE IF NOT EXISTS teachers (
    teacher_id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100),
    contact VARCHAR(15),
    classes_assigned VARCHAR(100),
    department_id INT,
    FOREIGN KEY (department_id) REFERENCES departments(department_id)
)
"""

# A teacher can teach more than one subject - join table replacing the old
# single teachers.subject column. subject_id references the same `subjects`
# table timetable/exams already use (one row per subject-name/class pair);
# a teacher's subject link only ever cares about the name, not which class
# that particular row happens to carry.
tables["teacher_subjects"] = """
CREATE TABLE IF NOT EXISTS teacher_subjects (
    teacher_id INT,
    subject_id INT,
    PRIMARY KEY (teacher_id, subject_id),
    FOREIGN KEY (teacher_id) REFERENCES teachers(teacher_id),
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id)
)
"""

tables["subjects"] = """
CREATE TABLE IF NOT EXISTS subjects (
    subject_id INT PRIMARY KEY AUTO_INCREMENT,
    subject_name VARCHAR(50),
    class VARCHAR(10)
)
"""

tables["timetable"] = """
CREATE TABLE IF NOT EXISTS timetable (
    entry_id INT PRIMARY KEY AUTO_INCREMENT,
    class VARCHAR(10),
    day VARCHAR(10),
    period_no INT,
    subject_id INT,
    teacher_id INT,
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id),
    FOREIGN KEY (teacher_id) REFERENCES teachers(teacher_id)
)
"""

tables["exams"] = """
CREATE TABLE IF NOT EXISTS exams (
    exam_id INT PRIMARY KEY AUTO_INCREMENT,
    class VARCHAR(10),
    subject_id INT,
    exam_date DATE,
    exam_type VARCHAR(30),
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id)
)
"""

tables["notes"] = """
CREATE TABLE IF NOT EXISTS notes (
    note_id INT PRIMARY KEY AUTO_INCREMENT,
    subject_id INT,
    class VARCHAR(10),
    title VARCHAR(150),
    file_path VARCHAR(255),
    uploaded_by INT,
    FOREIGN KEY (subject_id) REFERENCES subjects(subject_id)
)
"""

# target_roles: comma-separated tokens from {student, teacher, hod,
# principal} (the same 4 access-tier buckets _effective_role()/
# HOD_LIKE_ROLES/PRINCIPAL_LIKE_ROLES already group logins into in app.py -
# not the raw 7-value users.role ENUM), or the literal 'all'. Visibility is
# additive the same way chat access already is: hod/vice_principal also see
# 'teacher'-targeted notices, assistant_principal also sees 'principal'-
# targeted ones (see app.py's _notice_visible_roles()).
tables["notices"] = """
CREATE TABLE IF NOT EXISTS notices (
    notice_id INT PRIMARY KEY AUTO_INCREMENT,
    title VARCHAR(150),
    body TEXT,
    posted_by INT,
    date_posted DATE,
    target_roles VARCHAR(100) DEFAULT 'all',
    priority ENUM('normal','important','urgent') DEFAULT 'normal'
)
"""

tables["unanswered_questions"] = """
CREATE TABLE IF NOT EXISTS unanswered_questions (
    id INT PRIMARY KEY AUTO_INCREMENT,
    question_text VARCHAR(500),
    normalized_question VARCHAR(500),
    ask_count INT DEFAULT 1,
    first_asked DATETIME,
    last_asked DATETIME
)
"""

tables["learned_phrases"] = """
CREATE TABLE IF NOT EXISTS learned_phrases (
    id INT PRIMARY KEY AUTO_INCREMENT,
    phrase_text VARCHAR(500),
    normalized_phrase VARCHAR(500),
    resolved_intent VARCHAR(100),
    role VARCHAR(20),
    ask_count INT DEFAULT 1,
    first_asked DATETIME,
    last_asked DATETIME,
    applied TINYINT(1) DEFAULT 0
)
"""

# last_seen_notice_id: notifications badge "seen" marker (see app.py's
# /api/notices-count) - a notice_id, not a "last checked" timestamp.
# notices.date_posted is a DATE with no time-of-day, so a notice posted
# LATER THE SAME DAY a user already checked would compare as
# date_posted <= last-checked-timestamp and silently never surface as
# unseen. notice_id has no such granularity problem - a clean total order
# regardless of what day anything happened on.
tables["users"] = """
CREATE TABLE IF NOT EXISTS users (
    user_id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(50) UNIQUE,
    password_hash VARCHAR(255),
    role ENUM('student','teacher','hod','vice_principal','assistant_principal','principal','admin'),
    linked_id INT,
    last_seen_notice_id INT DEFAULT 0
)
"""

# Principal-only kill switch: a single flag row, `key`/`value` both plain
# strings (not a boolean column) - `key` is a MySQL reserved word, backtick-
# quoted everywhere it's referenced. The actual chatbot_enabled='true' seed
# row is inserted by generate_dummy_data.py, not here (this file only
# creates schema, never data - matches every other table).
tables["system_settings"] = """
CREATE TABLE IF NOT EXISTS system_settings (
    `key` VARCHAR(50) PRIMARY KEY,
    value VARCHAR(255)
)
"""

# Needs `users` to already exist (performed_by FK) - see creation_order.
tables["system_logs"] = """
CREATE TABLE IF NOT EXISTS system_logs (
    log_id INT PRIMARY KEY AUTO_INCREMENT,
    action VARCHAR(50),
    performed_by INT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (performed_by) REFERENCES users(user_id)
)
"""

# FK order: departments must exist before teachers (teachers.department_id).
# timetable/teacher_subjects need subjects/teachers, so those go after both.
# system_logs needs users to already exist (performed_by FK).
creation_order = ["departments", "subjects", "teachers", "teacher_subjects", "students",
                   "timetable", "exams", "notes", "notices",
                   "unanswered_questions", "learned_phrases", "users",
                   "system_settings", "system_logs"]

for table_name in creation_order:
    cursor.execute(tables[table_name])
    print(f"Table '{table_name}' ready.")

# The other half of the circular departments<->teachers FK (see the
# departments table comment above) - teachers now exists, so this can
# finally be added. IF NOT EXISTS isn't valid syntax for ADD CONSTRAINT, so
# this is guarded separately instead of just re-running the same pattern as
# the tables above.
cursor.execute("""
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = 'school_bot' AND TABLE_NAME = 'departments'
    AND CONSTRAINT_NAME = 'fk_departments_hod_teacher'
""")
if cursor.fetchone()[0] == 0:
    cursor.execute("""
        ALTER TABLE departments
        ADD CONSTRAINT fk_departments_hod_teacher
        FOREIGN KEY (hod_teacher_id) REFERENCES teachers(teacher_id)
    """)
    print("Constraint 'fk_departments_hod_teacher' added.")

connection.commit()
cursor.close()
connection.close()

print("\nAll done! Your 'school_bot' database is fully set up.")
