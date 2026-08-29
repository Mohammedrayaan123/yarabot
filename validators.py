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
    """Name must be non-empty and contain only letters and spaces."""
    name = name.strip()
    if not name:
        return False, "Name cannot be empty."
    if not re.match(r"^[A-Za-z\s]+$", name):
        return False, "Name should only contain letters and spaces."
    if len(name) < 2:
        return False, "Name is too short."
    return True, ""


def validate_class(class_str):
    """
    Class must follow the pattern: number-letter (e.g. 10-A, 6-B, 12-F).
    Grade must be between 1 and 12.
    Section must be a single uppercase letter.
    """
    class_str = class_str.strip().upper()
    if not class_str:
        return False, "Class cannot be empty."
    pattern = r"^(\d{1,2})-([A-Z])$"
    match = re.match(pattern, class_str)
    if not match:
        return False, "Class must be in the format 'Grade-Section' (e.g. 10-A, 6-B)."
    grade = int(match.group(1))
    if grade < 1 or grade > 12:
        return False, "Grade must be between 1 and 12."
    return True, ""


def validate_contact(contact):
    """Contact number must be exactly 10 digits."""
    contact = contact.strip()
    if not contact:
        return False, "Contact number cannot be empty."
    if not contact.isdigit():
        return False, "Contact number must contain digits only."
    if len(contact) != 10:
        return False, "Contact number must be exactly 10 digits."
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
