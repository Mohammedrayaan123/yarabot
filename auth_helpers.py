"""
auth_helpers.py
----------------
Small shared file with password hashing functions.
Both dashboard.py and app.py import from here, so we don't
repeat the same code twice.

WHAT IS "HASHING"?
Instead of storing a password like "root2008" directly in the database
(which would be unsafe - anyone who peeks at the database could read
every password), we run it through a one-way scrambling function.
"root2008" always turns into the SAME scrambled text, but you can't
reverse the scrambled text back into "root2008".

So when someone logs in, we don't check "is the stored password equal
to what they typed" - we check "does hashing what they typed produce
the SAME scrambled text as what's stored".
"""

import hashlib
import hmac


def hash_password(plain_password):
    """Turn a plain text password into a scrambled (hashed) version."""
    return hashlib.sha256(plain_password.encode()).hexdigest()


def verify_password(plain_password, stored_hash):
    """Check if a typed password matches the stored hash.
    Uses a constant-time comparison so the response time can't leak how
    many characters of the hash matched (a timing side-channel)."""
    return hmac.compare_digest(hash_password(plain_password), stored_hash)
