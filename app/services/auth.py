"""
Password hashing + session tokens for the staff CRM screen (GET /crm).
Deliberately stdlib-only (hashlib's PBKDF2, secrets) rather than adding a
new dependency for something this small.

Sessions are server-side (a StaffSession row per login), not a signed/JWT
cookie — the cookie only ever holds a random opaque token; nothing about
who the staff member is or what role they have is encoded in it or
trusted from the client. That's deliberate: logging a device out, or
disabling a staff account, has to take effect immediately (this can guard
clinical data), and that's only possible if the server holds the
authoritative list of valid sessions rather than trusting a self-verifying
token until it expires.
"""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone

PBKDF2_ITERATIONS = 260_000
SESSION_COOKIE_NAME = "grip_staff_session"
SESSION_TTL_HOURS = 12


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"{PBKDF2_ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        iterations_str, salt_hex, hash_hex = stored_hash.split("$")
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def new_session_token() -> str:
    """The raw value that goes in the cookie. Never stored anywhere as-is
    (see hash_session_token) — only its hash lives in the database, so a
    database read alone can never be replayed as a working session."""
    return secrets.token_urlsafe(32)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS)