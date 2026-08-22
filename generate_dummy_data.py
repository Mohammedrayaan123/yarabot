
import random
from datetime import date, timedelta
import mysql.connector
from config import DB_CONFIG

conn = mysql.connector.connect(**DB_CONFIG)
cursor = conn.cursor()

# ---- Name pools (mix it up so names don't repeat identically) ----
FIRST_NAMES = [
    "Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Krishna",
    "Ishaan", "Rohan", "Kabir", "Aryan", "Dhruv", "Karan", "Yash", "Zayn",
    "Ananya", "Diya", "Saanvi", "Aadhya", "Kavya", "Myra", "Anika", "Riya",
    "Ira", "Sara", "Aisha", "Fatima", "Zara", "Meera", "Nisha", "Priya",
    "Omar", "Hamza", "Yusuf", "Ali", "Rayan", "Faisal", "Tariq", "Bilal"
]
LAST_NAMES = [
    "Sharma", "Verma", "Gupta", "Khan", "Reddy", "Nair", "Iyer", "Rao",
    "Malhotra", "Kapoor", "Chopra", "Mehta", "Joshi", "Desai", "Pillai",
    "Ahmed", "Hussain", "Siddiqui", "Al-Farsi", "Al-Rashid"
]

CLASSES = [f"{grade}-{section}" for grade in range(6, 13) for section in ["A", "B", "C", "D"]]

SUBJECTS_BY_STAGE = {
    "middle": ["Mathematics", "Science", "English", "Social Studies", "Hindi"],
    "senior": ["Mathematics", "Physics", "Chemistry", "Biology", "English", "Computer Science"]
}


def random_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def random_dob(min_age=11, max_age=18):
    days_old = random.randint(min_age * 365, max_age * 365)
    return date.today() - timedelta(days=days_old)


# ---- 1. Generate 500 students ----
print("Adding 500 students...")
for _ in range(500):
    name = random_name()
    student_class = random.choice(CLASSES)
    roll_no = random.randint(1, 40)
    dob = random_dob()
    parent_name = random_name()
    parent_contact = f"9{random.randint(100000000, 999999999)}"
    fees_status = random.choice(["paid", "paid", "paid", "pending"])  # mostly paid, some pending
    attendance_pct = round(random.uniform(60, 100), 2)

    cursor.execute(
        """INSERT INTO students
           (name, class, roll_no, dob, parent_name, parent_contact, fees_status, attendance_pct)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (name, student_class, roll_no, dob, parent_name, parent_contact, fees_status, attendance_pct)
    )

conn.commit()
print("500 students added.")

# ---- 2. Generate 50 teachers ----
print("Adding 50 teachers...")
all_subjects = list(set(SUBJECTS_BY_STAGE["middle"] + SUBJECTS_BY_STAGE["senior"]))
teacher_ids_by_subject = {subj: [] for subj in all_subjects}

for _ in range(50):
    name = random_name()
    subject = random.choice(all_subjects)
    contact = f"9{random.randint(100000000, 999999999)}"
    assigned_classes = ", ".join(random.sample(CLASSES, k=random.randint(2, 4)))

    cursor.execute(
        """INSERT INTO teachers (name, subject, contact, classes_assigned)
           VALUES (%s, %s, %s, %s)""",
        (name, subject, contact, assigned_classes)
    )
    teacher_ids_by_subject[subject].append(cursor.lastrowid)

conn.commit()
print("50 teachers added.")

# ---- 3. Generate subjects (one row per subject per class) ----
print("Adding subjects...")
subject_id_lookup = {}  # (subject_name, class) -> subject_id

for cls in CLASSES:
    grade = int(cls.split("-")[0])
    stage = "senior" if grade >= 11 else "middle"
    for subj in SUBJECTS_BY_STAGE[stage]:
        cursor.execute(
            "INSERT INTO subjects (subject_name, class) VALUES (%s, %s)",
            (subj, cls)
        )
        subject_id_lookup[(subj, cls)] = cursor.lastrowid

conn.commit()
print(f"{len(subject_id_lookup)} subject entries added.")

# ---- 4. Generate a basic timetable (a few periods per class per day) ----
print("Adding timetable entries...")
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
count = 0

for cls in CLASSES:
    grade = int(cls.split("-")[0])
    stage = "senior" if grade >= 11 else "middle"
    subjects_for_class = SUBJECTS_BY_STAGE[stage]

    for day in DAYS:
        for period_no, subj in enumerate(subjects_for_class, start=1):
            subject_id = subject_id_lookup.get((subj, cls))
            candidates = teacher_ids_by_subject.get(subj)
            if not subject_id or not candidates:
                continue
            teacher_id = random.choice(candidates)

            cursor.execute(
                """INSERT INTO timetable (class, day, period_no, subject_id, teacher_id)
                   VALUES (%s, %s, %s, %s, %s)""",
                (cls, day, period_no, subject_id, teacher_id)
            )
            count += 1

conn.commit()
print(f"{count} timetable entries added.")

# ---- 5. Generate a few upcoming exams per class ----
print("Adding exams...")
exam_count = 0
for cls in CLASSES:
    grade = int(cls.split("-")[0])
    stage = "senior" if grade >= 11 else "middle"
    subjects_for_class = random.sample(SUBJECTS_BY_STAGE[stage], k=2)

    for subj in subjects_for_class:
        subject_id = subject_id_lookup.get((subj, cls))
        if not subject_id:
            continue
        exam_date = date.today() + timedelta(days=random.randint(5, 60))
        exam_type = random.choice(["Unit Test", "Half Yearly", "Pre-Board"])

        cursor.execute(
            """INSERT INTO exams (class, subject_id, exam_date, exam_type)
               VALUES (%s, %s, %s, %s)""",
            (cls, subject_id, exam_date, exam_type)
        )
        exam_count += 1

conn.commit()
print(f"{exam_count} exams added.")

cursor.close()
conn.close()

print("\nAll done! Your database now has realistic dummy data across all tables.")
