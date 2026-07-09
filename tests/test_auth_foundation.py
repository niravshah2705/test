import sqlite3

from hbw_seed import reset_and_seed
from hbw_seed.auth import (
    AuthenticationError,
    AuthorizationError,
    getOptionalUser,
    register_user,
    requireAdmin,
    requireUser,
    sign_in,
    sign_out,
)


def seeded_database(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    return database


def assert_raises(expected_exception, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except expected_exception as exc:
        return exc
    raise AssertionError(f"Expected {expected_exception.__name__}")


def test_register_stores_password_hash_and_returns_safe_user(tmp_path):
    database = seeded_database(tmp_path)

    response = register_user(
        str(database),
        {"email": "New.Guest@Example.Test", "password": "Secur3Pass!", "fullName": "New Guest"},
    )

    assert response.status_code == 201
    assert response.body["success"] is True
    assert response.body["data"]["email"] == "new.guest@example.test"
    assert response.body["data"]["role"] == "GUEST"
    assert set(response.body["data"]) == {"id", "email", "fullName", "role"}
    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT password_hash FROM users WHERE email = 'new.guest@example.test'").fetchone()
    assert row[0] != "Secur3Pass!"
    assert row[0].startswith("pbkdf2_sha256$310000$")


def test_successful_login_creates_safe_session(tmp_path):
    database = seeded_database(tmp_path)

    response = sign_in(str(database), {"email": "guest@example.test", "password": "GuestPass123!"})

    assert response.status_code == 200
    data = response.body["data"]
    assert data["sessionId"].startswith("ses_")
    assert data["user"] == {
        "id": "usr_guest",
        "email": "guest@example.test",
        "fullName": "Gale Guest",
        "role": "GUEST",
    }
    assert getOptionalUser(str(database), data["sessionId"]) == data["user"]
    assert requireUser(str(database), data["sessionId"]) == data["user"]


def test_failed_login_does_not_reveal_email_existence(tmp_path):
    database = seeded_database(tmp_path)

    wrong_password = sign_in(str(database), {"email": "guest@example.test", "password": "wrong"})
    unknown_email = sign_in(str(database), {"email": "missing@example.test", "password": "wrong"})

    assert wrong_password.status_code == 401
    assert unknown_email.status_code == 401
    assert wrong_password.body == unknown_email.body
    assert wrong_password.body["error"] == {"code": "invalid_credentials", "message": "Invalid email or password."}


def test_malformed_login_uses_shared_validation_error_response(tmp_path):
    database = seeded_database(tmp_path)

    response = sign_in(str(database), {"email": "bad", "password": ""})

    assert response.status_code == 400
    assert response.body["success"] is False
    assert response.body["data"] is None
    assert response.body["error"]["code"] == "validation_error"
    assert response.body["error"]["fields"] == {
        "email": ["Must be a valid email address."],
        "password": ["Field is required."],
    }


def test_sign_out_invalidates_session(tmp_path):
    database = seeded_database(tmp_path)
    session_id = sign_in(str(database), {"email": "guest@example.test", "password": "GuestPass123!"}).body["data"]["sessionId"]

    response = sign_out(str(database), session_id)

    assert response.status_code == 204
    assert getOptionalUser(str(database), session_id) is None
    assert_raises(AuthenticationError, requireUser, str(database), session_id)


def test_guest_is_rejected_by_admin_guard(tmp_path):
    database = seeded_database(tmp_path)
    session_id = sign_in(str(database), {"email": "guest@example.test", "password": "GuestPass123!"}).body["data"]["sessionId"]

    assert_raises(AuthorizationError, requireAdmin, str(database), session_id)


def test_admin_guard_accepts_admin_server_side(tmp_path):
    database = seeded_database(tmp_path)
    session_id = sign_in(str(database), {"email": "admin@example.test", "password": "AdminPass123!"}).body["data"]["sessionId"]

    assert requireAdmin(str(database), session_id)["role"] == "ADMIN"


def test_deactivated_user_is_rejected_for_login_and_existing_sessions(tmp_path):
    database = seeded_database(tmp_path)
    session_id = sign_in(str(database), {"email": "guest@example.test", "password": "GuestPass123!"}).body["data"]["sessionId"]
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE users SET is_active = 0 WHERE id = 'usr_guest'")
        connection.commit()

    login_response = sign_in(str(database), {"email": "guest@example.test", "password": "GuestPass123!"})

    assert login_response.status_code == 401
    assert login_response.body["error"] == {"code": "invalid_credentials", "message": "Invalid email or password."}
    assert getOptionalUser(str(database), session_id) is None
    assert_raises(AuthenticationError, requireUser, str(database), session_id)
