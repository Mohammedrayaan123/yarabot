"""
validators.py
-------------
Input validation for the dashboard forms, centralized so rules stay
consistent instead of duplicated per form.

Each function returns (is_valid: bool, error_message: str) - empty
string when valid.
"""

import re


def validate_name(name):
    """
    Name must be 2-100 chars, Unicode letters plus hyphens/apostrophes/
    periods/spaces (e.g. "Al-Rashid", "O'Brien", Arabic-script names), and
    contain at least one actual letter - not purely numbers or punctuation.
    """
    name = name.strip()
    if not name:
        return False, "Name cannot be empty."
    if len(name) < 2:
        return False, "Name is too short."
    if len(name) > 100:
        return False, "Name is too long (max 100 characters)."
    if not re.match(r"^[\w\-'.\s]+$", name, re.UNICODE):
        return False, "Name should only contain letters, spaces, hyphens, apostrophes, or periods."
    # \w includes digits, so a bare check above lets "123" or "12-34" through -
    # strip everything but letters and see if anything real is left.
    letters_only = re.sub(r"[\d\-'.\s]", "", name, flags=re.UNICODE)
    if not letters_only:
        return False, "Name must contain at least one letter."
    return True, ""


# Single source of truth for the class-code shape, shared with
# extract_class_from_question() (app.py) and has_class_code() (nlp_helpers.py) -
# both import these two names from here instead of keeping their own copies.
# They used to be defined independently and had already drifted (this pattern
# allows a space OR no separator - "10 A"/"10A" - which the old validate_class
# rejected but the other two already accepted); importing one shared pattern
# is what stops that happening again.
GRADE_SECTION_PATTERN = r"\b(\d{1,2})[\s-]?([A-Za-z])\b"

# Standalone early-years codes - no grade number, no section letter. See
# school_almanac.txt's own "Grades offered: Kindergarten (Nursery, LKG, UKG)
# through Grade XII" - the old 1-12-only range had no way to represent these.
EARLY_YEARS_CLASSES = ["Nursery", "LKG", "UKG"]


def validate_class(class_str):
    """
    Class must be one of the early-years standalone codes (Nursery/LKG/UKG,
    case-insensitive, no section) or Grade-Section for grades 1-12 (e.g.
    10-A, 6 B, 12F - hyphen, space, or no separator all accepted).
    """
    class_str = class_str.strip()
    if not class_str:
        return False, "Class cannot be empty."

    if class_str.lower() in (c.lower() for c in EARLY_YEARS_CLASSES):
        return True, ""

    match = re.fullmatch(GRADE_SECTION_PATTERN, class_str.upper())
    if not match:
        return False, (
            "Class must be 'Nursery', 'LKG', 'UKG', or 'Grade-Section' "
            "(e.g. 10-A, 6-B)."
        )
    grade = int(match.group(1))
    if grade < 1 or grade > 12:
        return False, "Grade must be between 1 and 12."
    return True, ""


def validate_contact(contact):
    """
    Contact number, after stripping hyphens/spaces/parentheses, must be an
    optional '+' or '00' international prefix followed by 7-15 digits - a
    plain 10-digit number (the old rule) still passes, but so does the
    school's own real Saudi format ("00966-11-2869960", "+966592888865").
    """
    contact = contact.strip()
    if not contact:
        return False, "Contact number cannot be empty."
    stripped = re.sub(r"[\-\s()]", "", contact)
    if not re.fullmatch(r"(\+|00)?\d{7,15}", stripped):
        return False, (
            "Contact number must be 7-15 digits, with an optional '+' or "
            "'00' international prefix."
        )
    return True, ""


def validate_roll_no(roll_no):
    """Roll number must be a positive integer."""
    try:
        roll = int(roll_no)
        if roll < 1:
            return False, "Roll number must be a positive number."
        return True, ""
    except (ValueError, TypeError):
        return False, "Roll number must be a valid number."


def validate_attendance(attendance):
    """Attendance must be between 0 and 100."""
    try:
        att = float(attendance)
        if att < 0 or att > 100:
            return False, "Attendance must be between 0 and 100."
        return True, ""
    except (ValueError, TypeError):
        return False, "Attendance must be a valid number."


def validate_username(username):
    """At least 4 characters, no spaces, letters/numbers/underscores only."""
    username = username.strip()
    if not username:
        return False, "Username cannot be empty."
    if len(username) < 4:
        return False, "Username must be at least 4 characters."
    if " " in username:
        return False, "Username cannot contain spaces."
    if not re.match(r"^[A-Za-z0-9_]+$", username):
        return False, "Username can only contain letters, numbers, and underscores."
    return True, ""


def validate_password(password):
    """Password must be at least 6 characters."""
    if not password:
        return False, "Password cannot be empty."
    if len(password) < 6:
        return False, "Password must be at least 6 characters."
    return True, ""


def validate_subject_name(subject_name):
    """Subject name must be non-empty and reasonable length."""
    subject_name = subject_name.strip()
    if not subject_name:
        return False, "Subject name cannot be empty."
    if len(subject_name) < 2:
        return False, "Subject name is too short."
    if len(subject_name) > 50:
        return False, "Subject name is too long (max 50 characters)."
    return True, ""


def validate_classes_assigned(classes_str):
    """
    Classes assigned should be a comma-separated list of valid class codes.
    e.g. "10-A, 10-B, 9-C"
    Each part must match the class format.
    """
    if not classes_str.strip():
        return False, "Classes assigned cannot be empty."
    parts = [p.strip() for p in classes_str.split(",")]
    for part in parts:
        valid, msg = validate_class(part)
        if not valid:
            return False, f"'{part}' is not a valid class format. {msg}"
    return True, ""


def collect_errors(*validation_results):
    """Flattens multiple (is_valid, error_message) tuples into just the error messages, for checking several fields before a save."""
    return [msg for valid, msg in validation_results if not valid and msg]
