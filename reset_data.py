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
    # FK order: tables that reference others (timetable -> subjects/teachers) go first.
    tables_in_order = ["timetable", "exams", "notes", "users", "students", "teachers", "subjects", "notices"]

    for table in tables_in_order:
        cursor.execute(f"DELETE FROM {table}")
        cursor.execute(f"ALTER TABLE {table} AUTO_INCREMENT = 1")
        print(f"Cleared table: {table}")

    conn.commit()
    print("\nAll data cleared. Database is ready for real entries.")

cursor.close()
conn.close()
