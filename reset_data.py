"""
reset_data.py
--------------
Clears ALL data from every table (students, teachers, subjects,
timetable, exams, users/logins) but keeps the table structure intact.
Use this before entering REAL student/staff data, so old dummy data
doesn't mix in with it.

WARNING: This deletes everything currently in the database. Only run
this when you're sure - e.g. right before your friend starts entering
real data.

Run:
    python reset_data.py
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
    # Delete in this specific order because of foreign keys - tables that
    # REFERENCE other tables must be cleared first (e.g. timetable
    # references subjects and teachers, so timetable goes first)
    tables_in_order = ["timetable", "exams", "notes", "users", "students", "teachers", "subjects", "notices"]

    for table in tables_in_order:
        cursor.execute(f"DELETE FROM {table}")
        # Reset the auto-increment ID counter back to 1, so new entries
        # start clean at ID 1 instead of continuing from where dummy data left off
        cursor.execute(f"ALTER TABLE {table} AUTO_INCREMENT = 1")
        print(f"Cleared table: {table}")

    conn.commit()
    print("\nAll data cleared. Database is ready for real entries.")

cursor.close()
conn.close()
