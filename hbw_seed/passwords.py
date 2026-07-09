"""Shared password hashing helpers for authentication flows."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 310_000
SALT_BYTES = 16


class PasswordHashError(ValueError):
    """Raised when a stored password hash cannot be parsed."""


def hash_password(password: str, *, salt: str | None = None) -> str:
    """Return a salted PBKDF2-SHA256 password hash string.

    The raw password is never stored.  The encoded format is stable and includes
    the algorithm, iteration count, salt, and digest so deterministic tests can
    assert that only hashes are persisted.
    """

    if not isinstance(password, str) or not password:
        raise ValueError("Password is required.")
    password_salt = salt or secrets.token_urlsafe(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), password_salt.encode("utf-8"), ITERATIONS)
    encoded = base64.b64encode(digest).decode("ascii")
    return f"{ALGORITHM}${ITERATIONS}${password_salt}${encoded}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Return True when ``password`` matches ``stored_hash``."""

    try:
        algorithm, iterations, salt, expected = stored_hash.split("$", 3)
        if algorithm != ALGORITHM:
            raise PasswordHashError("Unsupported password hash algorithm.")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
        actual = base64.b64encode(digest).decode("ascii")
    except Exception:
        return False
    return hmac.compare_digest(actual, expected)
