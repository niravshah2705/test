"""Framework-neutral account authentication and session contracts.

The helpers in this module provide HTTP-shaped registration, login, logout,
current-user, session-validation, and authenticated-guard behavior without
binding the seed package to a web framework.  Adapters can map ``ApiResponse``
headers to real ``Set-Cookie`` headers and pass request cookies back into the
handlers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from .public_api import ApiResponse, error_response, success_response

SESSION_COOKIE_NAME = "hbw_session"
SESSION_DURATION = timedelta(hours=12)
PASSWORD_ALGORITHM = "scrypt_sha256"
PASSWORD_SCRYPT_N = 2**14
PASSWORD_SCRYPT_R = 8
PASSWORD_SCRYPT_P = 1
SAFE_USER_FIELDS = ("id", "email", "fullName", "role", "createdAt")


@dataclass(frozen=True)
class AuthStateContract:
    """Serializable frontend auth-state provider contract."""

    status: str
    is_loading: bool
    is_authenticated: bool
    user: dict[str, Any] | None
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "isLoading": self.is_loading,
            "isAuthenticated": self.is_authenticated,
            "user": self.user,
            "error": self.error,
        }


class AuthenticatedRequest(Mapping[str, Any]):
    """Mapping wrapper that exposes the authenticated user to guarded handlers."""

    def __init__(self, request: Mapping[str, Any], user: dict[str, Any]) -> None:
        self._data = dict(request)
        self._data["user"] = user
        self.user = user

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        return self._data.get(key, default)


def ensure_auth_schema(database_path: str) -> None:
    """Create or migrate auth columns/tables needed by account endpoints."""

    with _connect(database_path) as connection:
        user_columns = {row["name"] for row in connection.execute("PRAGMA table_info(users)")}
        if "password_hash" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        if "email_normalized" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN email_normalized TEXT")
            connection.execute("UPDATE users SET email_normalized = LOWER(TRIM(email)) WHERE email_normalized IS NULL")
        if "updated_at" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN updated_at TEXT")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_normalized ON users(email_normalized)")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS user_sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                user_agent TEXT,
                ip_address TEXT
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_user_id ON user_sessions(user_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_user_sessions_token_hash ON user_sessions(token_hash)")
        connection.commit()


def handle_auth_post(
    database_path: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
    cookies: Mapping[str, str] | None = None,
    request_meta: Mapping[str, str] | None = None,
) -> ApiResponse:
    """Dispatch auth POST routes.

    Supported routes:
    - ``/api/auth/register``
    - ``/api/auth/login``
    - ``/api/auth/logout``
    """

    if path == "/api/auth/register":
        return register_user(database_path, payload or {}, request_meta=request_meta)
    if path == "/api/auth/login":
        return login_user(database_path, payload or {}, request_meta=request_meta)
    if path == "/api/auth/logout":
        return logout_user(database_path, cookies or {})
    return error_response(404, "not_found", "Endpoint not found.")


def handle_auth_get(database_path: str, path: str, cookies: Mapping[str, str] | None = None) -> ApiResponse:
    """Dispatch auth GET routes for current-user/session validation."""

    if path in {"/api/auth/me", "/api/auth/session"}:
        return current_user(database_path, cookies or {})
    return error_response(404, "not_found", "Endpoint not found.")


def register_user(
    database_path: str,
    payload: Mapping[str, Any],
    *,
    request_meta: Mapping[str, str] | None = None,
) -> ApiResponse:
    """Register a guest account with normalized email and hashed password."""

    ensure_auth_schema(database_path)
    normalized_email = normalize_email(str(payload.get("email", "")))
    full_name = str(payload.get("fullName") or payload.get("full_name") or "").strip()
    password = str(payload.get("password", ""))
    errors = _credential_errors(normalized_email, full_name, password, require_name=True)
    if errors:
        return _validation_error(errors)

    now = _utc_now()
    user_id = f"usr_{secrets.token_hex(12)}"
    password_hash = hash_password(password)
    try:
        with _connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO users (id, email, full_name, role, is_test_account, created_at, password_hash, email_normalized, updated_at)
                VALUES (?, ?, ?, 'guest', 0, ?, ?, ?, ?)
                """,
                (user_id, normalized_email, full_name, now, password_hash, normalized_email, now),
            )
            session = _create_session(connection, user_id, request_meta=request_meta)
            user = _user_by_id(connection, user_id)
            connection.commit()
    except sqlite3.IntegrityError:
        return error_response(409, "duplicate_email", "An account already exists for that email address.")

    response = success_response({"user": safe_user(user)}, status_code=201)
    return _with_session_cookie(response, session["token"], session["expires_at"])


def login_user(
    database_path: str,
    payload: Mapping[str, Any],
    *,
    request_meta: Mapping[str, str] | None = None,
) -> ApiResponse:
    """Authenticate credentials and issue a new server-validated session."""

    ensure_auth_schema(database_path)
    normalized_email = normalize_email(str(payload.get("email", "")))
    password = str(payload.get("password", ""))
    if not normalized_email or not password:
        return error_response(401, "invalid_credentials", "Email or password is incorrect.")

    with _connect(database_path) as connection:
        user = _user_by_email(connection, normalized_email)
        if user is None or not user["password_hash"] or not verify_password(password, user["password_hash"]):
            return error_response(401, "invalid_credentials", "Email or password is incorrect.")
        session = _create_session(connection, user["id"], request_meta=request_meta)
        connection.commit()

    response = success_response({"user": safe_user(user)})
    return _with_session_cookie(response, session["token"], session["expires_at"])


def logout_user(database_path: str, cookies: Mapping[str, str]) -> ApiResponse:
    """Invalidate the presented session and clear the cookie."""

    ensure_auth_schema(database_path)
    token = cookies.get(SESSION_COOKIE_NAME)
    if token:
        with _connect(database_path) as connection:
            connection.execute(
                "UPDATE user_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                (_utc_now(), _hash_token(token)),
            )
            connection.commit()
    return _clear_session_cookie(success_response({"loggedOut": True}))


def current_user(database_path: str, cookies: Mapping[str, str]) -> ApiResponse:
    """Return the safe profile for the active session."""

    session_user = validate_session(database_path, cookies)
    if session_user is None:
        return unauthorized_response()
    return success_response({"user": session_user})


def validate_session(database_path: str, cookies: Mapping[str, str]) -> dict[str, Any] | None:
    """Validate a session cookie and return safe user fields when active."""

    ensure_auth_schema(database_path)
    token = cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    now = _utc_now()
    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT users.*
            FROM user_sessions AS session
            JOIN users ON users.id = session.user_id
            WHERE session.token_hash = ?
              AND session.revoked_at IS NULL
              AND session.expires_at > ?
            """,
            (_hash_token(token), now),
        ).fetchone()
    return safe_user(row) if row is not None else None


def require_authenticated(
    database_path: str,
    cookies: Mapping[str, str],
    handler: Callable[[AuthenticatedRequest], ApiResponse],
    request: Mapping[str, Any] | None = None,
) -> ApiResponse:
    """Run ``handler`` only when a valid session is present."""

    user = validate_session(database_path, cookies)
    if user is None:
        return unauthorized_response()
    return handler(AuthenticatedRequest(request or {}, user))


def auth_state_from_current_user(response: ApiResponse) -> dict[str, Any]:
    """Translate a current-user response into frontend provider state."""

    if response.status_code == 200 and response.body.get("success"):
        user = response.body["data"]["user"]
        return AuthStateContract("authenticated", False, True, user).to_dict()
    error = response.body.get("error") or {"code": "unknown", "message": "Unable to load auth state."}
    return AuthStateContract(
        "unauthenticated",
        False,
        False,
        None,
        {"code": str(error.get("code", "unknown")), "message": str(error.get("message", "Unable to load auth state."))},
    ).to_dict()


def initial_auth_state() -> dict[str, Any]:
    """Return the loading state used before current-user fetch resolves."""

    return AuthStateContract("loading", True, False, None).to_dict()


def protected_route_state(auth_state: Mapping[str, Any], return_to: str = "/account") -> dict[str, Any]:
    """Return serializable protected-route behavior for frontend adapters."""

    status = auth_state.get("status")
    if status == "loading":
        return {
            "status": "loading",
            "render": "loading_indicator",
            "redirectTo": None,
            "message": "Checking your session before showing account details.",
        }
    if status == "authenticated":
        return {"status": "authorized", "render": "children", "redirectTo": None, "message": None}
    return {
        "status": "redirect",
        "render": None,
        "redirectTo": f"/login?returnTo={return_to}",
        "message": "Please sign in to continue.",
    }


def safe_user(user: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    """Expose only safe profile fields."""

    return {
        "id": user["id"],
        "email": user["email_normalized"] or normalize_email(user["email"]),
        "fullName": user["full_name"],
        "role": user["role"],
        "createdAt": user["created_at"],
    }


def normalize_email(email: str) -> str:
    """Normalize email addresses for uniqueness and login lookup."""

    return email.strip().lower()


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Hash a password with scrypt and a per-password salt."""

    salt = salt or os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_SCRYPT_N,
        r=PASSWORD_SCRYPT_R,
        p=PASSWORD_SCRYPT_P,
    )
    return "$".join(
        [
            PASSWORD_ALGORITHM,
            str(PASSWORD_SCRYPT_N),
            str(PASSWORD_SCRYPT_R),
            str(PASSWORD_SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against the stored scrypt hash."""

    try:
        algorithm, n_raw, r_raw, p_raw, salt_raw, digest_raw = stored_hash.split("$", 5)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        salt = base64.b64decode(salt_raw.encode("ascii"))
        expected_digest = base64.b64decode(digest_raw.encode("ascii"))
        actual_digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_raw),
            r=int(r_raw),
            p=int(p_raw),
        )
        return hmac.compare_digest(actual_digest, expected_digest)
    except (ValueError, TypeError):
        return False


def unauthorized_response() -> ApiResponse:
    return error_response(401, "unauthenticated", "Authentication is required.")


def _credential_errors(normalized_email: str, full_name: str, password: str, *, require_name: bool) -> dict[str, list[str]]:
    errors: dict[str, list[str]] = {}
    if not normalized_email or "@" not in normalized_email:
        errors["email"] = ["Enter a valid email address."]
    if require_name and not full_name:
        errors["fullName"] = ["Full name is required."]
    if len(password) < 8:
        errors["password"] = ["Password must be at least 8 characters."]
    return errors


def _validation_error(fields: dict[str, list[str]]) -> ApiResponse:
    return error_response(400, "validation_error", "Request validation failed.", fields=fields)


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _user_by_id(connection: sqlite3.Connection, user_id: str) -> sqlite3.Row:
    return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def _user_by_email(connection: sqlite3.Connection, normalized_email: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM users WHERE email_normalized = ?", (normalized_email,)).fetchone()


def _create_session(
    connection: sqlite3.Connection,
    user_id: str,
    *,
    request_meta: Mapping[str, str] | None = None,
) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    now = _utc_now()
    expires_at = _format_datetime(_parse_datetime(now) + SESSION_DURATION)
    connection.execute(
        """
        INSERT INTO user_sessions (id, user_id, token_hash, created_at, expires_at, user_agent, ip_address)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"sess_{secrets.token_hex(12)}",
            user_id,
            _hash_token(token),
            now,
            expires_at,
            (request_meta or {}).get("user_agent"),
            (request_meta or {}).get("ip_address"),
        ),
    )
    return {"token": token, "expires_at": expires_at}


def _with_session_cookie(response: ApiResponse, token: str, expires_at: str) -> ApiResponse:
    body = dict(response.body)
    body["headers"] = {"Set-Cookie": _session_cookie(token, expires_at)}
    return ApiResponse(response.status_code, body)


def _clear_session_cookie(response: ApiResponse) -> ApiResponse:
    body = dict(response.body)
    body["headers"] = {"Set-Cookie": f"{SESSION_COOKIE_NAME}=; Max-Age=0; Path=/; HttpOnly; SameSite=Lax; Secure"}
    return ApiResponse(response.status_code, body)


def _session_cookie(token: str, expires_at: str) -> str:
    return f"{SESSION_COOKIE_NAME}={token}; Expires={expires_at}; Path=/; HttpOnly; SameSite=Lax; Secure"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return _format_datetime(datetime.now(timezone.utc))


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
