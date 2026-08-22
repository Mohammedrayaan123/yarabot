"""
setup_database.py
------------------
Run this ONCE to create the 'school_bot' database and all its tables.
Safe to run again later if you ever need to reset everything (it won't
duplicate the database, it'll just make sure it exists).
"""

import mysql.connector
from config import DB_CONFIG

# ---- STEP 1: Connect to MySQL (not to a specific database yet) ----
# We connect WITHOUT specifying a database first, since we're about to
# create it. So we take our config but leave out the "database" part.
connection_settings = {k: v for k, v in DB_CONFIG.items() if k != "database"}
connection = mysql.connector.connect(**connection_settings)

cursor = connection.cursor()

# ---- STEP 2: Create the database if it doesn't already exist ----
cursor.execute("CREATE DATABASE IF NOT EXISTS school_bot")
print("Database 'school_bot' ready.")

# ---- STEP 3: Switch to using that database ----
cursor.execute("USE school_bot")

# ---- STEP 4: Create all the tables ----
# Each CREATE TABLE IF NOT EXISTS means: only create it if it's not already there.

tables = {}

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
    attendance_pct DECIMAL(5,2)
)
"""

tables["teachers"] = """
CREATE TABLE IF NOT EXISTS teachers (
    teacher_id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100),
    subject VARCHAR(50),
    contact VARCHAR(15),
    classes_assigned VARCHAR(100)
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

tables["notices"] = """
CREATE TABLE IF NOT EXISTS notices (
    notice_id INT PRIMARY KEY AUTO_INCREMENT,
    title VARCHAR(150),
    body TEXT,
    posted_by INT,
    date_posted DATE
)
"""

tables["users"] = """
CREATE TABLE IF NOT EXISTS users (
    user_id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(50) UNIQUE,
    password_hash VARCHAR(255),
    role ENUM('student','teacher','principal','admin'),
    linked_id INT
)
"""

# Run each CREATE TABLE statement, in order (order matters because of
# foreign keys — e.g. 'timetable' refers to 'teachers' and 'subjects',
# so those must exist first)
creation_order = ["subjects", "teachers", "students", "timetable", "exams", "notes", "notices", "users"]

for table_name in creation_order:
    cursor.execute(tables[table_name])
    print(f"Table '{table_name}' ready.")

# ---- STEP 5: Save changes and close ----
connection.commit()
cursor.close()
connection.close()

print("\nAll done! Your 'school_bot' database is fully set up.")
