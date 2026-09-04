"""
csv_import.py
--------------
Column-mapping, validation, and dedup logic for bulk student/teacher CSV
imports (dashboard.py's "Bulk Upload (CSV)" tabs). Kept separate from
dashboard.py the same way nlp_helpers.py/validators.py are kept separate
from app.py - pure functions, testable without Streamlit or a live DB
connection, with dashboard.py only gluing them to the UI and the actual
INSERTs.

Two-phase by design: dry_run_students()/dry_run_teachers() never touch the
database - they return what WOULD happen. dashboard.py only calls
apply_students()/apply_teachers() after the admin has reviewed the dry run
and clicked an explicit "Confirm Import" button.
"""

import re
import pandas as pd

from validators import (
    validate_name, validate_contact, validate_class, validate_roll_no,
    validate_subject_name, validate_classes_assigned,
    GRADE_SECTION_PATTERN, EARLY_YEARS_CLASSES,
)


# =========================================================
# COLUMN MAPPING
# Real school exports won't use this app's own internal column names -
# match on common real-world header variants instead of demanding an exact
# template. "class"/"grade"/"section" are handled specially (see
# resolve_class_code() below): some exports have one combined "Class"
# column, others have separate "Grade" and "Section" columns that need to
# be joined into the "10-A" format the rest of the app expects.
# =========================================================
STUDENT_COLUMN_SYNONYMS = {
    "name": ["student name", "name", "full name"],
    "class": ["class"],
    "grade": ["grade"],
    "section": ["section"],
    "roll_no": ["roll no", "roll number", "rollno", "roll_no"],
    "dob": ["dob", "date of birth", "birth date", "birthdate"],
    "parent_name": ["parent name", "guardian", "guardian name", "parent's name"],
    "parent_contact": [
        "parent contact", "phone", "mobile", "contact",
        "parent's contact", "parent contact number", "parent phone",
    ],
}
STUDENT_REQUIRED_FIELDS = ["name", "roll_no"]  # class is checked via resolve_class_code(), not a single key

TEACHER_COLUMN_SYNONYMS = {
    "name": ["teacher name", "name", "full name"],
    "subject": ["subject", "department"],
    "contact": ["contact", "phone", "mobile"],
    "classes_assigned": ["classes", "assigned classes", "classes assigned", "classes taught"],
}
TEACHER_REQUIRED_FIELDS = ["name", "subject"]


def map_columns(headers, synonyms):
    """
    Matches CSV headers (case/whitespace-insensitive) against a synonym map
    {canonical_field: [acceptable header variants]}. Returns
    {canonical_field: original_header_string} - a canonical field simply
    absent from the result means no matching column was found in this CSV.
    """
    normalized = {" ".join(str(h).strip().lower().split()): h for h in headers}
    mapping = {}
    for canonical, variants in synonyms.items():
        for variant in variants:
            if variant in normalized:
                mapping[canonical] = normalized[variant]
                break
    return mapping


def missing_required_columns(mapping, required_fields, requires_class=False):
    """
    required_fields entries are canonical field names checked directly
    against `mapping`. requires_class is a separate flag (not just another
    entry in required_fields) since "class" isn't a single canonical key -
    resolve_class_code() accepts EITHER a "class" column OR a "grade"+
    "section" pair, so students (requires_class=True) need this special
    either/or check; teachers (requires_class=False, no class concept at
    all) must not have it applied to them.
    """
    missing = []
    for field in required_fields:
        if field not in mapping:
            missing.append(field)
    if requires_class and "class" not in mapping and "grade" not in mapping:
        missing.append("class (or grade/grade+section)")
    return missing


def _cell(row, mapping, field):
    """String value of a mapped cell, '' if the field isn't mapped or the
    cell is blank/NaN - pandas reads an empty CSV cell as float NaN, not ''."""
    if field not in mapping:
        return ""
    value = row[mapping[field]]
    if pd.isna(value):
        return ""
    return str(value).strip()


def resolve_class_code(row, mapping):
    """
    Returns (class_code_or_None, error_or_None). Prefers an explicit
    "class" column. Falls back to combining separate "grade" + "section"
    columns (a common shape in real school exports) if both are present.
    A lone "grade" column is used as-is - it might already be a full class
    code ("10-A"), or a bare early-years code ("Nursery") - validate_class()
    is the actual judge either way.
    """
    if "class" in mapping:
        value = _cell(row, mapping, "class")
        return (value, None) if value else (None, "class/grade value is blank")
    if "grade" in mapping and "section" in mapping:
        grade = _cell(row, mapping, "grade")
        section = _cell(row, mapping, "section")
        if not grade:
            return None, "grade or section value is blank"
        # An early-years grade ("Nursery"/"LKG"/"UKG") has no section at
        # all by design - don't require one just because a Section column
        # happens to exist in this export (for the numeric-grade rows).
        if grade.lower() in (c.lower() for c in EARLY_YEARS_CLASSES):
            return grade, None
        if not section:
            return None, "grade or section value is blank"
        return f"{grade}-{section}", None
    if "grade" in mapping:
        value = _cell(row, mapping, "grade")
        return (value, None) if value else (None, "class/grade value is blank")
    return None, "no class/grade/section column found"


def normalize_class_code(class_code):
    """
    Canonical storage form - 'Nursery'/'LKG'/'UKG' in their proper case, or
    'GRADE-SECTION' uppercase with a single hyphen, regardless of whether
    the source used a space or no separator at all ('10 A'/'10A'). Reuses
    GRADE_SECTION_PATTERN/EARLY_YEARS_CLASSES from validators.py - the same
    single source of truth extract_class_from_question() (app.py) and
    has_class_code() (nlp_helpers.py) already share, so an imported "10 a"
    ends up stored exactly the way the chatbot's own lookups expect it.
    """
    stripped = class_code.strip()
    for code in EARLY_YEARS_CLASSES:
        if stripped.lower() == code.lower():
            return code
    match = re.fullmatch(GRADE_SECTION_PATTERN, stripped.upper())
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    return stripped.upper()  # already failed validate_class() by this point anyway


def _parse_dob(raw):
    """Returns (date_or_None, error_or_None). dayfirst=True since this
    school is not in the US - "05/06/2015" is read as 5 June, not May 6."""
    if not raw:
        return None, None
    parsed = pd.to_datetime(raw, dayfirst=True, errors="coerce")
    if pd.isna(parsed):
        return None, f"could not parse date '{raw}'"
    return parsed.date(), None


# =========================================================
# STUDENTS
# =========================================================
def validate_student_row(row, mapping):
    """
    Validates ONE CSV row against the mapped columns, through the SAME
    validate_name()/validate_contact()/validate_class() the dashboard's own
    "Add Student" form already uses - a row that would be rejected there
    is rejected here too, no separate rulebook to drift out of sync.

    Returns (data_dict_or_None, reasons: list[str]). data_dict is None if
    ANY reason was found; dob/parent_name/parent_contact are optional
    (blank/missing column -> stored as None/'', not a rejection) since a
    real roster export may not carry them at all.
    """
    reasons = []

    name = _cell(row, mapping, "name")
    if not name:
        reasons.append("name is missing")
    else:
        valid, msg = validate_name(name)
        if not valid:
            reasons.append(f"name: {msg}")

    class_code, class_err = resolve_class_code(row, mapping)
    if class_err:
        reasons.append(f"class: {class_err}")
    else:
        valid, msg = validate_class(class_code)
        if not valid:
            reasons.append(f"class: {msg}")

    roll_no_raw = _cell(row, mapping, "roll_no")
    roll_no = None
    if not roll_no_raw:
        reasons.append("roll number is missing")
    else:
        valid, msg = validate_roll_no(roll_no_raw)
        if not valid:
            reasons.append(f"roll number: {msg}")
        else:
            roll_no = int(float(roll_no_raw))  # pandas may read "12" as 12.0

    dob_raw = _cell(row, mapping, "dob")
    dob, dob_err = _parse_dob(dob_raw)
    if dob_err:
        reasons.append(f"dob: {dob_err}")

    parent_name = _cell(row, mapping, "parent_name")
    if parent_name:
        valid, msg = validate_name(parent_name)
        if not valid:
            reasons.append(f"parent name: {msg}")

    parent_contact = _cell(row, mapping, "parent_contact")
    if parent_contact:
        valid, msg = validate_contact(parent_contact)
        if not valid:
            reasons.append(f"parent contact: {msg}")

    if reasons:
        return None, reasons

    return {
        "name": name,
        "class": normalize_class_code(class_code),
        "roll_no": roll_no,
        "dob": dob,
        "parent_name": parent_name,
        "parent_contact": parent_contact,
    }, []


def dry_run_students(df, existing_pairs, fees_status_default="pending", attendance_pct_default=0.0):
    """
    Row-by-row validation + a second pass checking (class, roll_no)
    uniqueness - both within this CSV and against existing_pairs (already
    in the students table). existing_pairs: set of (class, roll_no) tuples,
    already normalize_class_code()'d by the caller.

    fees_status/attendance_pct aren't part of any real school roster export
    (they're tracked over time in this app, not a one-time import field),
    so every imported row gets the same admin-chosen default rather than a
    per-row column.

    Returns list of dicts: {row_number, status ('valid'/'rejected'),
    reasons, data}. row_number is 1-indexed to match what a human sees
    opening the spreadsheet (pandas' own 0-indexed df.iterrows() + 2, to
    also account for the header row).

    Never touches the database - see apply_students() for the actual insert.
    """
    mapping = map_columns(df.columns, STUDENT_COLUMN_SYNONYMS)
    missing = missing_required_columns(mapping, STUDENT_REQUIRED_FIELDS, requires_class=True)
    if missing:
        return None, missing, mapping

    results = []
    for i, row in df.iterrows():
        row_number = i + 2
        data, reasons = validate_student_row(row, mapping)
        if data is not None:
            data["fees_status"] = fees_status_default
            data["attendance_pct"] = attendance_pct_default
        results.append({"row_number": row_number, "status": "valid" if data else "rejected",
                         "reasons": reasons, "data": data})

    # Second pass: (class, roll_no) uniqueness. A row that already failed
    # its own field validation doesn't need a dedup check too - one clear
    # reason beats stacking a second, possibly-confusing one on top.
    seen_in_csv = {}  # (class, roll_no) -> [row_numbers]
    for r in results:
        if r["status"] != "valid":
            continue
        key = (r["data"]["class"], r["data"]["roll_no"])
        seen_in_csv.setdefault(key, []).append(r["row_number"])

    for r in results:
        if r["status"] != "valid":
            continue
        key = (r["data"]["class"], r["data"]["roll_no"])
        conflicts_in_csv = [n for n in seen_in_csv[key] if n != r["row_number"]]
        if conflicts_in_csv:
            r["status"] = "rejected"
            r["reasons"].append(
                f"duplicate (class, roll_no) = {key} also used by row(s) {', '.join(map(str, conflicts_in_csv))} in this file"
            )
        elif key in existing_pairs:
            r["status"] = "rejected"
            r["reasons"].append(f"(class, roll_no) = {key} already exists in the database")

    return results, [], mapping


def apply_students(results, insert_fn):
    """
    Inserts every 'valid' row from a dry_run_students() result via
    insert_fn(data_dict) - dashboard.py passes in the actual cursor.execute
    call, so this file never needs its own DB connection. Returns the
    count actually inserted.
    """
    count = 0
    for r in results:
        if r["status"] == "valid":
            insert_fn(r["data"])
            count += 1
    return count


# =========================================================
# TEACHERS
# Duplicate NAMES are a warning here, not a rejection - real, confirmed
# duplicates already exist in this school's own data (two different
# teachers both named "Tariq Al-Rashid"), so treating a repeated name as
# an error would incorrectly block legitimate rows. The chatbot side
# handles the resulting ambiguity at query time instead (see
# extract_teacher_name_from_question()'s clarification in app.py).
# =========================================================
def validate_teacher_row(row, mapping):
    """Returns (data_dict_or_None, reasons: list[str]). contact/
    classes_assigned are optional - blank/missing -> stored as ''."""
    reasons = []

    name = _cell(row, mapping, "name")
    if not name:
        reasons.append("name is missing")
    else:
        valid, msg = validate_name(name)
        if not valid:
            reasons.append(f"name: {msg}")

    # A teacher can teach more than one subject - the "Subject"/"Department"
    # column holds a comma- or semicolon-separated list ("Mathematics,
    # Physics"), split and each part validated on its own via the same
    # validate_subject_name() a single subject already went through.
    subject_raw = _cell(row, mapping, "subject")
    subjects = []
    if not subject_raw:
        reasons.append("subject is missing")
    else:
        for part in re.split(r'[,;]', subject_raw):
            part = part.strip()
            if not part:
                continue
            valid, msg = validate_subject_name(part)
            if not valid:
                reasons.append(f"subject '{part}': {msg}")
            elif part not in subjects:
                subjects.append(part)
        if not subjects and not reasons:
            reasons.append("subject is missing")

    contact = _cell(row, mapping, "contact")
    if contact:
        valid, msg = validate_contact(contact)
        if not valid:
            reasons.append(f"contact: {msg}")

    classes_assigned = _cell(row, mapping, "classes_assigned")
    if classes_assigned:
        valid, msg = validate_classes_assigned(classes_assigned)
        if not valid:
            reasons.append(f"classes assigned: {msg}")

    if reasons:
        return None, reasons

    return {
        "name": name,
        "subjects": subjects,
        "contact": contact,
        "classes_assigned": classes_assigned,
    }, []


def dry_run_teachers(df, existing_names):
    """
    Row-by-row validation, then a duplicate-NAME pass (warning, not a
    rejection - see the module note above) against both other rows in this
    CSV and existing_names (already in the teachers table, lowercased by
    the caller for a case-insensitive check).

    Returns list of dicts: {row_number, status ('valid'/'warning'/
    'rejected'), reasons, data}. 'warning' rows are still imported - the
    reasons are informational, not blocking.
    """
    mapping = map_columns(df.columns, TEACHER_COLUMN_SYNONYMS)
    missing = missing_required_columns(mapping, TEACHER_REQUIRED_FIELDS)
    if missing:
        return None, missing, mapping

    results = []
    for i, row in df.iterrows():
        row_number = i + 2
        data, reasons = validate_teacher_row(row, mapping)
        results.append({"row_number": row_number, "status": "valid" if data else "rejected",
                         "reasons": reasons, "data": data})

    name_counts_in_csv = {}
    for r in results:
        if r["status"] == "valid":
            key = r["data"]["name"].strip().lower()
            name_counts_in_csv[key] = name_counts_in_csv.get(key, 0) + 1

    for r in results:
        if r["status"] != "valid":
            continue
        key = r["data"]["name"].strip().lower()
        if name_counts_in_csv[key] > 1:
            r["status"] = "warning"
            r["reasons"].append(f"another row in this file also uses the name '{r['data']['name']}'")
        elif key in existing_names:
            r["status"] = "warning"
            r["reasons"].append(f"a teacher named '{r['data']['name']}' already exists in the database")

    return results, [], mapping


def apply_teachers(results, insert_fn):
    """Inserts every 'valid' OR 'warning' row (warnings are informational,
    not blocking - see the module note above) via insert_fn(data_dict).
    Returns the count actually inserted."""
    count = 0
    for r in results:
        if r["status"] in ("valid", "warning"):
            insert_fn(r["data"])
            count += 1
    return count