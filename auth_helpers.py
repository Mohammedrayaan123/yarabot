"""
auth_helpers.py
----------------
Password hashing, shared by app.py and dashboard.py.
"""

import hashlib
import hmac


def hash_password(plain_password):
    return hashlib.sha256(plain_password.encode()).hexdigest()


def verify_password(plain_password, stored_hash):
    """Constant-time comparison - avoids leaking a match via response timing."""
    return hmac.compare_digest(hash_password(plain_password), stored_hash)
