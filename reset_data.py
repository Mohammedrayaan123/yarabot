"""
reset_data.py
--------------
Clears all table data (keeps the schema) so dummy data doesn't mix with
real entries. WARNING: deletes everything currently in the database.
"""

import mysql.connector
from config import DB_CONFIG

conn = mysql.connector.connect(**DB_CONFIG)
cursor = conn.cursor()

confirm = input(
    "This will DELETE all current data (students, teachers, subjects, "
    "timetable, exams, logins). Type YES to continue: "
)

if confirm.strip() != "YES":
    print("Cancelled. No data was deleted.")
else:
    # departments and teachers reference each other (departments.hod_teacher_id
    # -> teachers, teachers.department_id -> departments) - a genuine circular
    # FK, so a plain per-table DELETE loop can't clear both no matter the
    # order. Break the cycle first by nulling every hod_teacher_id; teachers
    # can then be deleted (nothing else references it once teacher_subjects
    # is gone too), and departments after that (teachers, its only referrer,
    # is already gone).
    cursor.execute("UPDATE departments SET hod_teacher_id = NULL")

    # FK order: tables that reference others (timetable -> subjects/teachers,
    # teacher_subjects -> teachers/subjects) go first.
    tables_in_order = ["timetable", "exams", "notes", "users", "students",
                        "teacher_subjects", "teachers", "departments", "subjects", "notices"]

    for table in tables_in_order:
        cursor.execute(f"DELETE FROM {table}")
        cursor.execute(f"ALTER TABLE {table} AUTO_INCREMENT = 1")
        print(f"Cleared table: {table}")

    conn.commit()
    print("\nAll data cleared. Database is ready for real entries.")

cursor.close()
conn.close()
