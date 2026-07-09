import sqlite3

from hbw_seed import reset_and_seed
from hbw_seed.account_auth import (
    SESSION_COOKIE_NAME,
    auth_state_from_current_user,
    current_user,
    handle_auth_get,
    handle_auth_post,
    hash_password,
    initial_auth_state,
    login_user,
    protected_route_state,
    register_user,
    require_authenticated,
    verify_password,
)
from hbw_seed.public_api import success_response


def cookie_from(response):
    set_cookie = response.body["headers"]["Set-Cookie"]
    name, value = set_cookie.split(";", 1)[0].split("=", 1)
    return {name: value}


def register_payload(email=" Traveler@Example.TEST ", password="correct horse battery staple"):
    return {"email": email, "fullName": "Tara Traveler", "password": password}


def test_register_normalizes_email_hashes_password_sets_secure_cookie_and_returns_safe_user(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)

    response = register_user(str(database), register_payload())

    assert response.status_code == 201
    assert response.body["success"] is True
    user = response.body["data"]["user"]
    assert user["email"] == "traveler@example.test"
    assert set(user) == {"id", "email", "fullName", "role", "createdAt"}
    assert "password_hash" not in user
    assert "session" not in response.body["data"]
    assert f"{SESSION_COOKIE_NAME}=" in response.body["headers"]["Set-Cookie"]
    assert "HttpOnly" in response.body["headers"]["Set-Cookie"]
    assert "Secure" in response.body["headers"]["Set-Cookie"]
    assert "SameSite=Lax" in response.body["headers"]["Set-Cookie"]

    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT email, email_normalized, password_hash FROM users WHERE id = ?", (user["id"],)
        ).fetchone()
        raw_session = connection.execute("SELECT token_hash FROM user_sessions WHERE user_id = ?", (user["id"],)).fetchone()

    assert stored[0] == "traveler@example.test"
    assert stored[1] == "traveler@example.test"
    assert stored[2] != "correct horse battery staple"
    assert stored[2].startswith("scrypt_sha256$")
    assert verify_password("correct horse battery staple", stored[2]) is True
    assert raw_session[0] not in response.body["headers"]["Set-Cookie"]


def test_duplicate_registration_is_case_insensitive_and_returns_standard_conflict(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    first = register_user(str(database), register_payload(email="Casey@Example.test"))

    duplicate = register_user(str(database), register_payload(email=" casey@example.TEST "))

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.body == {
        "success": False,
        "data": None,
        "error": {"code": "duplicate_email", "message": "An account already exists for that email address."},
    }


def test_login_accepts_normalized_email_and_invalid_credentials_are_standard_errors(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    register_user(str(database), register_payload(email="Login@Example.test", password="strong-password"))

    wrong_password = login_user(str(database), {"email": "login@example.test", "password": "wrong-password"})
    missing_user = login_user(str(database), {"email": "missing@example.test", "password": "strong-password"})
    logged_in = login_user(str(database), {"email": " LOGIN@example.TEST ", "password": "strong-password"})

    assert wrong_password.status_code == 401
    assert wrong_password.body["error"]["code"] == "invalid_credentials"
    assert missing_user.status_code == 401
    assert missing_user.body["error"]["code"] == "invalid_credentials"
    assert logged_in.status_code == 200
    assert logged_in.body["data"]["user"]["email"] == "login@example.test"
    assert "password_hash" not in logged_in.body["data"]["user"]
    assert SESSION_COOKIE_NAME in cookie_from(logged_in)


def test_current_user_session_validation_and_logout_invalidation(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    registration = register_user(str(database), register_payload(email="Me@Example.test"))
    cookies = cookie_from(registration)

    me = current_user(str(database), cookies)
    via_dispatch = handle_auth_get(str(database), "/api/auth/me", cookies)
    logout = handle_auth_post(str(database), "/api/auth/logout", cookies=cookies)
    after_logout = current_user(str(database), cookies)

    assert me.status_code == 200
    assert me.body["data"]["user"]["email"] == "me@example.test"
    assert set(me.body["data"]["user"]) == {"id", "email", "fullName", "role", "createdAt"}
    assert via_dispatch.body == me.body
    assert logout.status_code == 200
    assert "Max-Age=0" in logout.body["headers"]["Set-Cookie"]
    assert after_logout.status_code == 401
    assert after_logout.body["error"]["code"] == "unauthenticated"


def test_expired_session_is_rejected(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    registration = register_user(str(database), register_payload(email="Expired@Example.test"))
    cookies = cookie_from(registration)

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE user_sessions SET expires_at = '2000-01-01T00:00:00Z'")
        connection.commit()

    response = current_user(str(database), cookies)

    assert response.status_code == 401
    assert response.body["error"]["code"] == "unauthenticated"


def test_protected_api_guard_rejects_unauthenticated_and_passes_safe_user(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    registration = register_user(str(database), register_payload(email="Guard@Example.test"))
    cookies = cookie_from(registration)

    rejected = require_authenticated(str(database), {}, lambda request: success_response({"user": request.user}))
    accepted = require_authenticated(
        str(database),
        cookies,
        lambda request: success_response({"seenUser": request.user, "path": request["path"]}),
        {"path": "/api/protected"},
    )

    assert rejected.status_code == 401
    assert rejected.body["error"]["code"] == "unauthenticated"
    assert accepted.status_code == 200
    assert accepted.body["data"]["seenUser"]["email"] == "guard@example.test"
    assert accepted.body["data"]["path"] == "/api/protected"


def test_frontend_auth_state_contract_covers_loading_authenticated_and_unauthenticated(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    registration = register_user(str(database), register_payload(email="State@Example.test"))
    authenticated_response = current_user(str(database), cookie_from(registration))
    unauthenticated_response = current_user(str(database), {})

    loading = initial_auth_state()
    authenticated = auth_state_from_current_user(authenticated_response)
    unauthenticated = auth_state_from_current_user(unauthenticated_response)

    assert loading == {"status": "loading", "isLoading": True, "isAuthenticated": False, "user": None, "error": None}
    assert authenticated["status"] == "authenticated"
    assert authenticated["isAuthenticated"] is True
    assert authenticated["user"]["email"] == "state@example.test"
    assert unauthenticated["status"] == "unauthenticated"
    assert unauthenticated["error"]["code"] == "unauthenticated"
    assert protected_route_state(loading)["render"] == "loading_indicator"
    assert protected_route_state(authenticated)["render"] == "children"
    assert protected_route_state(unauthenticated, "/account/reservations")["redirectTo"] == "/login?returnTo=/account/reservations"


def test_password_hash_helper_uses_unique_salts_and_verification(tmp_path):
    first = hash_password("same-password")
    second = hash_password("same-password")

    assert first != second
    assert verify_password("same-password", first) is True
    assert verify_password("wrong-password", first) is False
