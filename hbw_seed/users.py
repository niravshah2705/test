"""User-domain schemas and service operations."""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .passwords import hash_password

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
VALID_ROLES = {"GUEST", "ADMIN"}


class UserValidationError(ValueError):
    """Raised when user input does not satisfy the auth schema."""

    def __init__(self, fields: dict[str, list[str]]):
        super().__init__("Request body failed validation.")
        self.fields = fields


class UserConflictError(ValueError):
    """Raised when a user cannot be created because the email is taken."""


@dataclass(frozen=True)
class UserRegistrationSchema:
    email: str
    password: str
    full_name: str
    role: str = "GUEST"

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "UserRegistrationSchema":
        fields: dict[str, list[str]] = {}
        email = _normalise_email(payload.get("email"))
        password = payload.get("password")
        full_name = str(payload.get("fullName") or payload.get("full_name") or "").strip()
        role = _normalise_role(payload.get("role", "GUEST"))

        if not email or not EMAIL_PATTERN.fullmatch(email):
            fields["email"] = ["Must be a valid email address."]
        if not isinstance(password, str) or len(password) < 8:
            fields["password"] = ["Must be at least 8 characters."]
        if not full_name:
            fields["fullName"] = ["Field is required."]
        if role not in VALID_ROLES:
            fields["role"] = ["Must be GUEST or ADMIN."]
        if fields:
            raise UserValidationError(fields)
        return cls(email=email, password=password, full_name=full_name, role=role)


@dataclass(frozen=True)
class LoginSchema:
    email: str
    password: str

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "LoginSchema":
        fields: dict[str, list[str]] = {}
        email = _normalise_email(payload.get("email"))
        password = payload.get("password")
        if not email or not EMAIL_PATTERN.fullmatch(email):
            fields["email"] = ["Must be a valid email address."]
        if not isinstance(password, str) or not password:
            fields["password"] = ["Field is required."]
        if fields:
            raise UserValidationError(fields)
        return cls(email=email, password=password)


class UserService:
    """Persistence service for user-domain operations."""

    def __init__(self, database_path: str):
        self.database_path = database_path

    def register_user(self, payload: dict[str, Any]) -> dict[str, Any]:
        registration = UserRegistrationSchema.parse(payload)
        user_id = str(payload.get("id") or f"usr_{uuid.uuid4().hex}")
        created_at = _utc_now()
        with _connect(self.database_path) as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO users (id, email, full_name, role, password_hash, is_active, is_test_account, created_at)
                    VALUES (?, ?, ?, ?, ?, 1, 0, ?)
                    """,
                    (
                        user_id,
                        registration.email,
                        registration.full_name,
                        registration.role.lower(),
                        hash_password(registration.password),
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise UserConflictError("Email address is already registered.") from exc
            connection.commit()
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return safe_user_identity(row)


def safe_user_identity(row: sqlite3.Row | dict[str, Any]) -> dict[str, str]:
    """Return only safe identity fields needed for auth decisions."""

    return {
        "id": row["id"],
        "email": row["email"],
        "fullName": row["full_name"],
        "role": _normalise_role(row["role"]),
    }


def _normalise_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalise_role(value: Any) -> str:
    return str(value or "").strip().upper()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection
