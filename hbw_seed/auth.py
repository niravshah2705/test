"""Authentication, sessions, and authorization helpers for HBW contracts."""

from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from .passwords import hash_password, verify_password
from .public_api import ApiResponse, error_response, success_response
from .sessions import (
    AuthenticationError,
    AuthorizationError,
    getOptionalUser,
    requireAdmin,
    requireUser,
    require_admin_response,
    require_user_response,
    sign_out,
)
from .sessions import create_session as _create_session
from .users import LoginSchema, UserConflictError, UserService, UserValidationError, safe_user_identity

INVALID_CREDENTIALS_MESSAGE = "Invalid email or password."


def register_user(database_path: str, payload: dict[str, Any]) -> ApiResponse:
    """Register a user and return the safe identity in the shared envelope."""

    try:
        user = UserService(database_path).register_user(payload)
    except UserValidationError as exc:
        return error_response(400, "validation_error", "Request body failed validation.", fields=exc.fields)
    except UserConflictError:
        return error_response(409, "conflict", "Email address is already registered.")
    return success_response(user, status_code=201)


def sign_in(database_path: str, payload: dict[str, Any]) -> ApiResponse:
    """Authenticate active users without revealing whether an email exists."""

    try:
        credentials = LoginSchema.parse(payload)
    except UserValidationError as exc:
        return error_response(400, "validation_error", "Request body failed validation.", fields=exc.fields)

    with _connect(database_path) as connection:
        row = connection.execute("SELECT * FROM users WHERE lower(email) = ?", (credentials.email,)).fetchone()
        if row is None or not verify_password(credentials.password, row["password_hash"]) or not bool(row["is_active"]):
            return _invalid_credentials()
        session_id = _create_session(database_path, row["id"])
        user = safe_user_identity(row)
    return success_response({"sessionId": session_id, "user": user})


def sign_out_user(database_path: str, session_id: str | None) -> ApiResponse:
    """Compatibility wrapper for sign-out route handlers/actions."""

    return sign_out(database_path, session_id)


def _invalid_credentials() -> ApiResponse:
    return error_response(401, "invalid_credentials", INVALID_CREDENTIALS_MESSAGE)


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _value(record: Mapping[str, Any], key: str) -> Any:
    return record[key]


def _get(record: Mapping[str, Any], key: str) -> Any:
    try:
        return record.get(key)  # type: ignore[attr-defined]
    except AttributeError:
        return record[key]


def _role(record: Mapping[str, Any]) -> str:
    return str(_get(record, "role") or "").upper()


def canViewReservation(actor: Mapping[str, Any] | None, reservation: Mapping[str, Any]) -> bool:
    """Return True when an authenticated actor may view reservation details."""

    if actor is None:
        return False
    if _role(actor) == "ADMIN":
        return True
    return _value(reservation, "user_id") is not None and _value(reservation, "user_id") == _get(actor, "id")


def canCancelReservation(actor: Mapping[str, Any] | None, reservation: Mapping[str, Any]) -> bool:
    """Return True when an authenticated actor may cancel a reservation."""

    if not canViewReservation(actor, reservation):
        return False
    if actor is not None and _role(actor) == "ADMIN":
        return False
    return _value(reservation, "status") in {"confirmed", "pending_payment"}


def canPayReservation(actor: Mapping[str, Any] | None, reservation: Mapping[str, Any]) -> bool:
    """Return True when an authenticated actor may attach payment to a reservation."""

    if not canViewReservation(actor, reservation):
        return False
    return actor is not None and _role(actor) != "ADMIN" and _value(reservation, "status") == "pending_payment"


def canAdministerHotel(actor: Mapping[str, Any] | None, hotel_id: str) -> bool:
    """Return True when an actor has operational hotel administration privileges."""

    return actor is not None and _role(actor) == "ADMIN"
