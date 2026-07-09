"""Shared session retrieval and server-side authorization guards."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .public_api import ApiResponse, error_response
from .users import safe_user_identity

SESSION_TTL_HOURS = 12


class AuthenticationError(PermissionError):
    """Raised when a request does not have a valid authenticated session."""


class AuthorizationError(PermissionError):
    """Raised when an authenticated user lacks required privileges."""


def create_session(database_path: str, user_id: str, *, now: datetime | None = None) -> str:
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(hours=SESSION_TTL_HOURS)
    session_id = f"ses_{uuid.uuid4().hex}"
    with _connect(database_path) as connection:
        connection.execute(
            "INSERT INTO auth_sessions (id, user_id, created_at, expires_at, revoked_at) VALUES (?, ?, ?, ?, NULL)",
            (session_id, user_id, _format_time(issued_at), _format_time(expires_at)),
        )
        connection.commit()
    return session_id


def sign_out(database_path: str, session_id: str | None) -> ApiResponse:
    """Invalidate a session idempotently."""

    if session_id:
        with _connect(database_path) as connection:
            connection.execute("UPDATE auth_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL", (_format_time(datetime.now(timezone.utc)), session_id))
            connection.commit()
    return ApiResponse(204, {"success": True, "data": None, "error": None})


def getOptionalUser(database_path: str, session_id: str | None) -> dict[str, str] | None:
    """Return the safe current user identity, or None when no valid session exists."""

    if not session_id:
        return None
    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT users.*
            FROM auth_sessions AS session
            JOIN users ON users.id = session.user_id
            WHERE session.id = ?
              AND session.revoked_at IS NULL
              AND session.expires_at > ?
              AND users.is_active = 1
            """,
            (session_id, _format_time(datetime.now(timezone.utc))),
        ).fetchone()
    if row is None:
        return None
    return safe_user_identity(row)


def requireUser(database_path: str, session_id: str | None) -> dict[str, str]:
    """Require an authenticated, active user for server-rendered code paths."""

    user = getOptionalUser(database_path, session_id)
    if user is None:
        raise AuthenticationError("Authentication required.")
    return user


def requireAdmin(database_path: str, session_id: str | None) -> dict[str, str]:
    """Require an authenticated administrator for server-side admin resources."""

    user = requireUser(database_path, session_id)
    if user["role"] != "ADMIN":
        raise AuthorizationError("Administrator privileges required.")
    return user


def require_user_response(database_path: str, session_id: str | None) -> dict[str, str] | ApiResponse:
    try:
        return requireUser(database_path, session_id)
    except AuthenticationError:
        return error_response(401, "unauthorized", "Authentication required.")


def require_admin_response(database_path: str, session_id: str | None) -> dict[str, str] | ApiResponse:
    try:
        return requireAdmin(database_path, session_id)
    except AuthenticationError:
        return error_response(401, "unauthorized", "Authentication required.")
    except AuthorizationError:
        return error_response(403, "forbidden", "Administrator privileges required.")


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _format_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
