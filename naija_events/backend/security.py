"""
security.py
-----------
Tiny helper for password hashing. Kept in its own file (instead of inside
utils.py) so that database.py can also use it without creating a circular
import, since utils.py already imports database.py.

We are not storing passwords as plain text anymore. Each password gets a
random salt mixed in before hashing with SHA-256, and the salt is stored
next to the hash (separated by a $) so it can be checked again at login.
"""

import hashlib
import uuid


def hash_password(password, salt=None):
    """Turn a plain password into 'salt$hash' using SHA-256."""
    if salt is None:
        salt = uuid.uuid4().hex
    digest = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return f"{salt}${digest}"


def verify_password(password, stored_value):
    """Check a plain password against a 'salt$hash' value from the database."""
    if not stored_value or "$" not in stored_value:
        return False
    salt, digest = stored_value.split("$", 1)
    check = hashlib.sha256((salt + password).encode("utf-8")).hexdigest()
    return check == digest
