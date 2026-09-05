
import random
from datetime import date, timedelta
import mysql.connector
from config import DB_CONFIG
from validators import validate_name, validate_class, validate_contact

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


def random_phone():
    """Saudi-format mobile number (00966 + a 9-digit number starting with 5),
    matching school_almanac.txt's own real published numbers - the old
    Indian-style f"9{9 digits}" format didn't match anything the school
    actually uses."""
    return f"00966{random.randint(500000000, 599999999)}"


def assert_valid(label, value, validator):
    """Runs generated data through the real validator before it's ever
    inserted - a failure here means the generator itself produced something
    that would be rejected everywhere else (the dashboard forms, a future
    import script), so fail loudly instead of inserting bad dummy data."""
    valid, msg = validator(value)
    if not valid:
        raise ValueError(f"Generated {label} {value!r} failed validation: {msg}")


# ---- 1. Generate 500 students ----
print("Adding 500 students...")

# Roll numbers must be unique WITHIN a class (students.class + roll_no is a
# real-world uniqueness rule - see setup_database.py). A shared pool per
# class, shuffled once and popped from as students are assigned, replaces
# the old random.randint(1, 40), which could (and did) collide - 82 real
# duplicate (class, roll_no) pairs were found live before this fix.
roll_pools = {cls: random.sample(range(1, 100), 99) for cls in CLASSES}

for _ in range(500):
    name = random_name()
    assert_valid("student name", name, validate_name)

    student_class = random.choice(CLASSES)
    assert_valid("class", student_class, validate_class)

    if not roll_pools[student_class]:
        roll_pools[student_class] = random.sample(range(1, 100), 99)  # extremely unlikely, but don't crash
    roll_no = roll_pools[student_class].pop()

    dob = random_dob()
    parent_name = random_name()
    assert_valid("parent name", parent_name, validate_name)

    parent_contact = random_phone()
    assert_valid("parent contact", parent_contact, validate_contact)

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

# ---- 2. Generate subjects (one row per subject per class) ----
# Moved ahead of teachers: teachers now link to subjects via the
# teacher_subjects join table (a teacher can teach more than one subject -
# see setup_database.py), so a real subjects.subject_id has to exist before
# a teacher can be linked to one.
print("Adding subjects...")
all_subjects = list(set(SUBJECTS_BY_STAGE["middle"] + SUBJECTS_BY_STAGE["senior"]))
subject_id_lookup = {}  # (subject_name, class) -> subject_id
name_to_subject_id = {}  # subject_name -> subject_id, one representative row per name -
# subjects has a row per (name, class) pair, not a clean name lookup, but
# teacher_subjects only cares about the name, so the first (lowest id) row
# for that name stands in for it.

for cls in CLASSES:
    grade = int(cls.split("-")[0])
    stage = "senior" if grade >= 11 else "middle"
    for subj in SUBJECTS_BY_STAGE[stage]:
        cursor.execute(
            "INSERT INTO subjects (subject_name, class) VALUES (%s, %s)",
            (subj, cls)
        )
        subject_id_lookup[(subj, cls)] = cursor.lastrowid
        name_to_subject_id.setdefault(subj, cursor.lastrowid)

conn.commit()
print(f"{len(subject_id_lookup)} subject entries added.")

# ---- 3. Generate departments - created before teachers, since
# teachers.department_id is a FK to this table. Names map roughly to real
# subject areas (a "Science" department containing Physics/Chemistry/
# Biology teachers), not to the exact SUBJECTS_BY_STAGE list - departments
# are their own concept, not derived from it (see setup_database.py).
print("Adding departments...")
DEPARTMENT_NAMES = ["Science", "Mathematics", "English", "Social Studies", "Hindi", "Computer Science"]
department_ids = []
for dept_name in DEPARTMENT_NAMES:
    cursor.execute("INSERT INTO departments (name) VALUES (%s)", (dept_name,))
    department_ids.append(cursor.lastrowid)

conn.commit()
print(f"{len(department_ids)} departments added.")

# ---- 4. Generate 50 teachers, each teaching 1-3 subjects and belonging to
# exactly one department ----
print("Adding 50 teachers...")
teacher_ids_by_subject = {subj: [] for subj in all_subjects}
teacher_ids_by_department = {did: [] for did in department_ids}

for _ in range(50):
    name = random_name()
    assert_valid("teacher name", name, validate_name)

    contact = random_phone()
    assert_valid("teacher contact", contact, validate_contact)

    assigned_classes = ", ".join(random.sample(CLASSES, k=random.randint(2, 4)))
    department_id = random.choice(department_ids)

    cursor.execute(
        """INSERT INTO teachers (name, contact, classes_assigned, department_id)
           VALUES (%s, %s, %s, %s)""",
        (name, contact, assigned_classes, department_id)
    )
    teacher_id = cursor.lastrowid
    teacher_ids_by_department[department_id].append(teacher_id)

    teacher_subjects = random.sample(all_subjects, k=random.randint(1, 3))
    for subj in teacher_subjects:
        cursor.execute(
            "INSERT INTO teacher_subjects (teacher_id, subject_id) VALUES (%s, %s)",
            (teacher_id, name_to_subject_id[subj])
        )
        teacher_ids_by_subject[subj].append(teacher_id)

conn.commit()
print("50 teachers added.")

# ---- 5. Designate one HOD per department (any teacher already assigned
# to it - departments with zero teachers, vanishingly unlikely with 50
# teachers spread across 6 departments, are just left without one) ----
print("Assigning HODs...")
hod_count = 0
for department_id, tids in teacher_ids_by_department.items():
    if not tids:
        continue
    hod_id = random.choice(tids)
    cursor.execute("UPDATE departments SET hod_teacher_id=%s WHERE department_id=%s", (hod_id, department_id))
    hod_count += 1

conn.commit()
print(f"{hod_count} HODs assigned.")

# ---- 6. Generate a basic timetable (a few periods per class per day) ----
print("Adding timetable entries...")
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
count = 0
teachers_by_class = {cls: set() for cls in CLASSES}  # for step 6b below

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
            teachers_by_class[cls].add(teacher_id)

            cursor.execute(
                """INSERT INTO timetable (class, day, period_no, subject_id, teacher_id)
                   VALUES (%s, %s, %s, %s, %s)""",
                (cls, day, period_no, subject_id, teacher_id)
            )
            count += 1

conn.commit()
print(f"{count} timetable entries added.")

# ---- 6b. Designate one "class teacher" (homeroom teacher) per section -
# picked from teachers who actually teach that class already, same real-
# world logic as a school assigning homeroom duty to one of a class's own
# subject teachers rather than an unrelated staff member ----
print("Assigning class teachers...")
class_teacher_count = 0
for cls in CLASSES:
    candidates = list(teachers_by_class[cls])
    if not candidates:
        continue
    class_teacher_id = random.choice(candidates)
    cursor.execute(
        "INSERT INTO class_teachers (class, teacher_id) VALUES (%s, %s)",
        (cls, class_teacher_id)
    )
    class_teacher_count += 1

conn.commit()
print(f"{class_teacher_count} class teachers assigned.")

# ---- 7. Generate a few upcoming exams per class ----
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

# ---- 8. Seed the kill switch flag (INSERT IGNORE - re-running this script
# on a DB that already has real toggle history must not silently reset it
# back to enabled) ----
print("Seeding system_settings...")
cursor.execute("INSERT IGNORE INTO system_settings (`key`, value) VALUES ('chatbot_enabled', 'true')")
conn.commit()
print("system_settings seeded.")

# ---- 9. Seed sample notices - spread across dates (some inside the
# notifications badge's 7-day window, some outside it), target_roles
# (single/multi/"all"), and every priority level ----
print("Adding notices...")
SAMPLE_NOTICES = [
    ("Emergency: Early Dismissal Today", "Due to expected heavy rain, school will "
     "dismiss all students at 12:30 PM today. Buses have been notified.",
     "all", "urgent", 0),
    ("Fee Payment Deadline Extended", "The Term 2 fee payment deadline has been "
     "extended to the end of this week for students with pending dues.",
     "student", "urgent", 1),
    ("Staff Meeting This Friday", "All teaching staff are required to attend a "
     "short staff meeting in the main hall this Friday after last period.",
     "teacher,hod", "important", 2),
    ("Uniform Change Starting Next Term", "Starting next term, the winter uniform "
     "blazer becomes mandatory for all grades. Please purchase from the school store.",
     "all", "important", 3),
    ("HOD Monthly Review Meeting", "Department heads should submit their monthly "
     "progress reports before the review meeting on Monday.",
     "hod", "normal", 4),
    ("PTM Schedule for Grade 10-12", "Parent-teacher meetings for senior grades "
     "will be held over two days - please check the almanac for exact slots.",
     "student,teacher", "normal", 5),
    ("Budget Approval Meeting", "The quarterly budget review meeting has been "
     "moved up - please confirm attendance with the office.",
     "principal", "important", 6),
    ("Sports Day Postponed", "Sports Day has been postponed by one week due to "
     "ground maintenance. New date to follow.",
     "all", "normal", 8),
    ("Annual Day Rehearsal Schedule", "Rehearsals for the Annual Day performance "
     "begin next week - a full schedule has been shared with class teachers.",
     "student,teacher", "normal", 15),
    ("New Library Hours", "The school library will now stay open until 5 PM on "
     "weekdays to give students more time for after-school study.",
     "all", "normal", 20),
]

for title, body, target_roles, priority, days_ago in SAMPLE_NOTICES:
    notice_date = date.today() - timedelta(days=days_ago)
    cursor.execute(
        """INSERT INTO notices (title, body, posted_by, date_posted, target_roles, priority)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        # posted_by=0: same "posted via the admin dashboard" sentinel dashboard.py's
        # own Notices page uses - there's no real principal user_id to attribute
        # dummy seed data to.
        (title, body, 0, notice_date, target_roles, priority)
    )

conn.commit()
print(f"{len(SAMPLE_NOTICES)} notices added.")

cursor.close()
conn.close()

print("\nAll done! Your database now has realistic dummy data across all tables.")
