"""User and passenger profile persistence helpers.

The repository in this module intentionally stays framework-neutral and SQLite
backed, matching the deterministic HBW fixture style. Passenger profiles are
owned by a login user but represent travelers independently from that login
identity, so callers can store reusable details for family members, coworkers,
or other travelers.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from .audit import record_audit_event, user_actor

PASSENGER_TYPES = {"adult", "child", "infant"}
GENDERS = {"female", "male", "non_binary", "unspecified"}
DOCUMENT_TYPES = {"passport", "national_id", "drivers_license", "known_traveler", "redress"}
COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 .()\-]{6,24}$")


class ProfileValidationError(ValueError):
    """Raised when profile persistence inputs fail validation."""


class ProfileAuthorizationError(PermissionError):
    """Raised when a user attempts to access another user's profile data."""


class ProfileNotFoundError(LookupError):
    """Raised when requested profile data does not exist for the owner."""


@dataclass(frozen=True)
class UserProfileInput:
    user_id: str
    legal_given_name: str
    legal_family_name: str
    date_of_birth: str
    country_code: str
    display_name: str | None = None
    legal_middle_name: str | None = None
    gender: str | None = None


@dataclass(frozen=True)
class ContactDetailInput:
    id: str
    user_id: str
    email: str
    phone: str
    label: str = "primary"


@dataclass(frozen=True)
class PassengerProfileInput:
    id: str
    user_id: str
    legal_given_name: str
    legal_family_name: str
    date_of_birth: str
    passenger_type: str
    display_name: str | None = None
    legal_middle_name: str | None = None
    gender: str | None = None
    contact_detail_id: str | None = None


@dataclass(frozen=True)
class PassengerDocumentInput:
    id: str
    user_id: str
    passenger_profile_id: str
    document_type: str
    issuing_country: str
    expires_on: str | None = None
    document_number_last4: str | None = None
    nationality_country: str | None = None


class ProfileRepository:
    """Create/read/update/list persistence for user-owned profile data."""

    def __init__(self, database_path: str):
        self.database_path = database_path

    def create_user_profile(self, profile: UserProfileInput | dict[str, Any]) -> dict[str, Any]:
        data = _coerce(profile)
        _require_fields(data, ["user_id", "legal_given_name", "legal_family_name", "date_of_birth", "country_code"])
        normalized = {
            "user_id": _non_empty(data["user_id"], "user_id"),
            "display_name": _optional_text(data.get("display_name")),
            "legal_given_name": _non_empty(data["legal_given_name"], "legal_given_name"),
            "legal_middle_name": _optional_text(data.get("legal_middle_name")),
            "legal_family_name": _non_empty(data["legal_family_name"], "legal_family_name"),
            "date_of_birth": _validate_past_or_today_date(data["date_of_birth"], "date_of_birth"),
            "gender": _validate_optional_choice(data.get("gender"), GENDERS, "gender"),
            "country_code": _validate_country_code(data["country_code"], "country_code"),
        }
        with _connect(self.database_path) as connection:
            _ensure_user_exists(connection, normalized["user_id"])
            connection.execute(
                """
                INSERT INTO user_profiles (
                    user_id, display_name, legal_given_name, legal_middle_name,
                    legal_family_name, date_of_birth, gender, country_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(normalized.values()),
            )
            record_audit_event(
                connection,
                actor=user_actor(normalized["user_id"]),
                event_type="user.profile.created",
                entity_type="user",
                entity_id=normalized["user_id"],
                user_id=normalized["user_id"],
                metadata={"changedFields": sorted(normalized), "auditWritePolicy": "best effort; profile correctness wins"},
                created_at="2031-04-01T08:00:00Z",
            )
            connection.commit()
            return self._user_profile_row(connection, normalized["user_id"])

    def get_user_profile(self, user_id: str) -> dict[str, Any]:
        with _connect(self.database_path) as connection:
            profile = self._user_profile_row(connection, user_id)
            if profile is None:
                raise ProfileNotFoundError("User profile not found.")
            return profile

    def update_user_profile(self, user_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"display_name", "legal_given_name", "legal_middle_name", "legal_family_name", "date_of_birth", "gender", "country_code"}
        normalized = _normalize_changes(changes, allowed, _normalize_user_profile_field)
        with _connect(self.database_path) as connection:
            if self._user_profile_row(connection, user_id) is None:
                raise ProfileNotFoundError("User profile not found.")
            _update_table(connection, "user_profiles", "user_id", user_id, normalized)
            record_audit_event(
                connection,
                actor=user_actor(user_id),
                event_type="user.profile.updated",
                entity_type="user",
                entity_id=user_id,
                user_id=user_id,
                metadata={"changedFields": sorted(normalized), "auditWritePolicy": "best effort; profile correctness wins"},
                created_at="2031-04-01T08:05:00Z",
            )
            connection.commit()
            return self._user_profile_row(connection, user_id)

    def create_contact_detail(self, contact: ContactDetailInput | dict[str, Any]) -> dict[str, Any]:
        data = _coerce(contact)
        _require_fields(data, ["id", "user_id", "email", "phone"])
        normalized = {
            "id": _non_empty(data["id"], "id"),
            "user_id": _non_empty(data["user_id"], "user_id"),
            "label": _non_empty(data.get("label", "primary"), "label"),
            "email": _validate_email(data["email"]),
            "phone": _validate_phone(data["phone"]),
        }
        with _connect(self.database_path) as connection:
            _ensure_user_exists(connection, normalized["user_id"])
            connection.execute("INSERT INTO contact_details VALUES (?, ?, ?, ?, ?)", tuple(normalized.values()))
            connection.commit()
            return self._contact_row(connection, normalized["user_id"], normalized["id"])

    def get_contact_detail(self, user_id: str, contact_detail_id: str) -> dict[str, Any]:
        with _connect(self.database_path) as connection:
            contact = self._contact_row(connection, user_id, contact_detail_id)
            if contact is None:
                _raise_for_missing_or_forbidden(connection, "contact_details", contact_detail_id, user_id)
            return contact

    def update_contact_detail(self, user_id: str, contact_detail_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"label", "email", "phone"}
        normalized = _normalize_changes(changes, allowed, _normalize_contact_field)
        with _connect(self.database_path) as connection:
            if self._contact_row(connection, user_id, contact_detail_id) is None:
                _raise_for_missing_or_forbidden(connection, "contact_details", contact_detail_id, user_id)
            _update_table(connection, "contact_details", "id", contact_detail_id, normalized)
            connection.commit()
            return self._contact_row(connection, user_id, contact_detail_id)

    def list_contact_details(self, user_id: str) -> list[dict[str, Any]]:
        with _connect(self.database_path) as connection:
            _ensure_user_exists(connection, user_id)
            rows = connection.execute(
                "SELECT * FROM contact_details WHERE user_id = ? ORDER BY label, id",
                (user_id,),
            ).fetchall()
            return [_contact_payload(row) for row in rows]

    def create_passenger_profile(self, passenger: PassengerProfileInput | dict[str, Any]) -> dict[str, Any]:
        data = _coerce(passenger)
        _require_fields(data, ["id", "user_id", "legal_given_name", "legal_family_name", "date_of_birth", "passenger_type"])
        normalized = {
            "id": _non_empty(data["id"], "id"),
            "user_id": _non_empty(data["user_id"], "user_id"),
            "display_name": _optional_text(data.get("display_name")),
            "legal_given_name": _non_empty(data["legal_given_name"], "legal_given_name"),
            "legal_middle_name": _optional_text(data.get("legal_middle_name")),
            "legal_family_name": _non_empty(data["legal_family_name"], "legal_family_name"),
            "date_of_birth": _validate_past_or_today_date(data["date_of_birth"], "date_of_birth"),
            "passenger_type": _validate_choice(data["passenger_type"], PASSENGER_TYPES, "passenger_type"),
            "gender": _validate_optional_choice(data.get("gender"), GENDERS, "gender"),
            "contact_detail_id": _optional_text(data.get("contact_detail_id")),
        }
        with _connect(self.database_path) as connection:
            _ensure_user_exists(connection, normalized["user_id"])
            _ensure_owned_contact(connection, normalized["user_id"], normalized["contact_detail_id"])
            connection.execute(
                """
                INSERT INTO passenger_profiles (
                    id, user_id, display_name, legal_given_name, legal_middle_name,
                    legal_family_name, date_of_birth, passenger_type, gender, contact_detail_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(normalized.values()),
            )
            connection.commit()
            return self._passenger_row(connection, normalized["user_id"], normalized["id"])

    def get_passenger_profile(self, user_id: str, passenger_profile_id: str) -> dict[str, Any]:
        with _connect(self.database_path) as connection:
            passenger = self._passenger_row(connection, user_id, passenger_profile_id)
            if passenger is None:
                _raise_for_missing_or_forbidden(connection, "passenger_profiles", passenger_profile_id, user_id)
            return passenger

    def update_passenger_profile(self, user_id: str, passenger_profile_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = {"display_name", "legal_given_name", "legal_middle_name", "legal_family_name", "date_of_birth", "passenger_type", "gender", "contact_detail_id"}
        normalized = _normalize_changes(changes, allowed, _normalize_passenger_field)
        with _connect(self.database_path) as connection:
            if self._passenger_row(connection, user_id, passenger_profile_id) is None:
                _raise_for_missing_or_forbidden(connection, "passenger_profiles", passenger_profile_id, user_id)
            if "contact_detail_id" in normalized:
                _ensure_owned_contact(connection, user_id, normalized["contact_detail_id"])
            _update_table(connection, "passenger_profiles", "id", passenger_profile_id, normalized)
            connection.commit()
            return self._passenger_row(connection, user_id, passenger_profile_id)

    def list_passenger_profiles(self, user_id: str) -> list[dict[str, Any]]:
        with _connect(self.database_path) as connection:
            _ensure_user_exists(connection, user_id)
            rows = connection.execute(
                "SELECT * FROM passenger_profiles WHERE user_id = ? ORDER BY display_name, legal_family_name, legal_given_name, id",
                (user_id,),
            ).fetchall()
            return [_passenger_payload(row) for row in rows]

    def create_passenger_document(self, document: PassengerDocumentInput | dict[str, Any], *, itinerary_requires_expiry: bool = False, travel_date: str | None = None) -> dict[str, Any]:
        data = _coerce(document)
        _require_fields(data, ["id", "user_id", "passenger_profile_id", "document_type", "issuing_country"])
        user_id = _non_empty(data["user_id"], "user_id")
        expires_on = _validate_document_expiry(data.get("expires_on"), itinerary_requires_expiry=itinerary_requires_expiry, travel_date=travel_date)
        normalized = {
            "id": _non_empty(data["id"], "id"),
            "passenger_profile_id": _non_empty(data["passenger_profile_id"], "passenger_profile_id"),
            "document_type": _validate_choice(data["document_type"], DOCUMENT_TYPES, "document_type"),
            "issuing_country": _validate_country_code(data["issuing_country"], "issuing_country"),
            "nationality_country": _validate_optional_country_code(data.get("nationality_country"), "nationality_country"),
            "expires_on": expires_on,
            "document_number_last4": _validate_last4(data.get("document_number_last4")),
        }
        with _connect(self.database_path) as connection:
            if self._passenger_row(connection, user_id, normalized["passenger_profile_id"]) is None:
                _raise_for_missing_or_forbidden(connection, "passenger_profiles", normalized["passenger_profile_id"], user_id)
            connection.execute(
                """
                INSERT INTO passenger_documents (
                    id, passenger_profile_id, document_type, issuing_country,
                    nationality_country, expires_on, document_number_last4
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(normalized.values()),
            )
            connection.commit()
            return self._document_row(connection, user_id, normalized["id"])

    def get_passenger_document(self, user_id: str, document_id: str) -> dict[str, Any]:
        with _connect(self.database_path) as connection:
            document = self._document_row(connection, user_id, document_id)
            if document is None:
                _raise_document_for_missing_or_forbidden(connection, document_id, user_id)
            return document

    def update_passenger_document(self, user_id: str, document_id: str, changes: dict[str, Any], *, itinerary_requires_expiry: bool = False, travel_date: str | None = None) -> dict[str, Any]:
        allowed = {"document_type", "issuing_country", "nationality_country", "expires_on", "document_number_last4"}
        def normalizer(field: str, value: Any) -> Any:
            if field == "expires_on":
                return _validate_document_expiry(value, itinerary_requires_expiry=itinerary_requires_expiry, travel_date=travel_date)
            return _normalize_document_field(field, value)

        normalized = _normalize_changes(changes, allowed, normalizer)
        with _connect(self.database_path) as connection:
            if self._document_row(connection, user_id, document_id) is None:
                _raise_document_for_missing_or_forbidden(connection, document_id, user_id)
            _update_table(connection, "passenger_documents", "id", document_id, normalized)
            connection.commit()
            return self._document_row(connection, user_id, document_id)

    def list_passenger_documents(self, user_id: str, passenger_profile_id: str) -> list[dict[str, Any]]:
        with _connect(self.database_path) as connection:
            if self._passenger_row(connection, user_id, passenger_profile_id) is None:
                _raise_for_missing_or_forbidden(connection, "passenger_profiles", passenger_profile_id, user_id)
            rows = connection.execute(
                """
                SELECT document.*
                FROM passenger_documents AS document
                JOIN passenger_profiles AS passenger ON passenger.id = document.passenger_profile_id
                WHERE passenger.user_id = ? AND passenger.id = ?
                ORDER BY document.document_type, document.id
                """,
                (user_id, passenger_profile_id),
            ).fetchall()
            return [_document_payload(row) for row in rows]

    def _user_profile_row(self, connection: sqlite3.Connection, user_id: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)).fetchone()
        return _user_profile_payload(row) if row else None

    def _contact_row(self, connection: sqlite3.Connection, user_id: str, contact_detail_id: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT * FROM contact_details WHERE user_id = ? AND id = ?", (user_id, contact_detail_id)).fetchone()
        return _contact_payload(row) if row else None

    def _passenger_row(self, connection: sqlite3.Connection, user_id: str, passenger_profile_id: str) -> dict[str, Any] | None:
        row = connection.execute("SELECT * FROM passenger_profiles WHERE user_id = ? AND id = ?", (user_id, passenger_profile_id)).fetchone()
        return _passenger_payload(row) if row else None

    def _document_row(self, connection: sqlite3.Connection, user_id: str, document_id: str) -> dict[str, Any] | None:
        row = connection.execute(
            """
            SELECT document.*
            FROM passenger_documents AS document
            JOIN passenger_profiles AS passenger ON passenger.id = document.passenger_profile_id
            WHERE passenger.user_id = ? AND document.id = ?
            """,
            (user_id, document_id),
        ).fetchone()
        return _document_payload(row) if row else None


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _coerce(value: Any) -> dict[str, Any]:
    if hasattr(value, "__dataclass_fields__"):
        return {field: getattr(value, field) for field in value.__dataclass_fields__}
    return dict(value)


def _require_fields(data: dict[str, Any], fields: list[str]) -> None:
    missing = [field for field in fields if field not in data or data[field] is None or str(data[field]).strip() == ""]
    if missing:
        raise ProfileValidationError(f"Missing required fields: {', '.join(missing)}.")


def _non_empty(value: Any, field: str) -> str:
    if value is None or str(value).strip() == "":
        raise ProfileValidationError(f"{field} is required.")
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_iso_date(value: Any, field: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ProfileValidationError(f"{field} must use YYYY-MM-DD format.") from exc


def _validate_past_or_today_date(value: Any, field: str) -> str:
    parsed = _parse_iso_date(value, field)
    if parsed > date.today():
        raise ProfileValidationError(f"{field} cannot be in the future.")
    return parsed.isoformat()


def _validate_document_expiry(value: Any, *, itinerary_requires_expiry: bool, travel_date: str | None) -> str | None:
    if value is None or str(value).strip() == "":
        if itinerary_requires_expiry:
            raise ProfileValidationError("expires_on is required for this itinerary.")
        return None
    parsed = _parse_iso_date(value, "expires_on")
    comparison_date = _parse_iso_date(travel_date, "travel_date") if travel_date else date.today()
    if parsed < comparison_date:
        raise ProfileValidationError("Document expires before the required travel date.")
    return parsed.isoformat()


def _validate_country_code(value: Any, field: str) -> str:
    text = _non_empty(value, field).upper()
    if not COUNTRY_CODE_RE.fullmatch(text):
        raise ProfileValidationError(f"{field} must be a two-letter ISO country code.")
    return text


def _validate_optional_country_code(value: Any, field: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _validate_country_code(value, field)


def _validate_choice(value: Any, choices: set[str], field: str) -> str:
    text = _non_empty(value, field).lower()
    if text not in choices:
        raise ProfileValidationError(f"{field} must be one of: {', '.join(sorted(choices))}.")
    return text


def _validate_optional_choice(value: Any, choices: set[str], field: str) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return _validate_choice(value, choices, field)


def _validate_email(value: Any) -> str:
    email = _non_empty(value, "email").lower()
    if not EMAIL_RE.fullmatch(email):
        raise ProfileValidationError("email must be a valid address.")
    return email


def _validate_phone(value: Any) -> str:
    phone = _non_empty(value, "phone")
    if not PHONE_RE.fullmatch(phone):
        raise ProfileValidationError("phone must be a valid contact phone number.")
    return phone


def _validate_last4(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    if len(text) != 4 or not text.isalnum():
        raise ProfileValidationError("document_number_last4 must contain exactly four letters or digits.")
    return text.upper()


def _normalize_user_profile_field(field: str, value: Any) -> Any:
    if field in {"legal_given_name", "legal_family_name"}:
        return _non_empty(value, field)
    if field in {"display_name", "legal_middle_name"}:
        return _optional_text(value)
    if field == "date_of_birth":
        return _validate_past_or_today_date(value, field)
    if field == "gender":
        return _validate_optional_choice(value, GENDERS, field)
    if field == "country_code":
        return _validate_country_code(value, field)
    raise ProfileValidationError(f"Unsupported field: {field}.")


def _normalize_contact_field(field: str, value: Any) -> Any:
    if field == "label":
        return _non_empty(value, field)
    if field == "email":
        return _validate_email(value)
    if field == "phone":
        return _validate_phone(value)
    raise ProfileValidationError(f"Unsupported field: {field}.")


def _normalize_passenger_field(field: str, value: Any) -> Any:
    if field in {"legal_given_name", "legal_family_name"}:
        return _non_empty(value, field)
    if field in {"display_name", "legal_middle_name", "contact_detail_id"}:
        return _optional_text(value)
    if field == "date_of_birth":
        return _validate_past_or_today_date(value, field)
    if field == "passenger_type":
        return _validate_choice(value, PASSENGER_TYPES, field)
    if field == "gender":
        return _validate_optional_choice(value, GENDERS, field)
    raise ProfileValidationError(f"Unsupported field: {field}.")


def _normalize_document_field(field: str, value: Any) -> Any:
    if field == "document_type":
        return _validate_choice(value, DOCUMENT_TYPES, field)
    if field == "issuing_country":
        return _validate_country_code(value, field)
    if field == "nationality_country":
        return _validate_optional_country_code(value, field)
    if field == "document_number_last4":
        return _validate_last4(value)
    raise ProfileValidationError(f"Unsupported field: {field}.")


def _normalize_changes(changes: dict[str, Any], allowed: set[str], normalizer) -> dict[str, Any]:
    if not changes:
        raise ProfileValidationError("At least one change is required.")
    unknown = set(changes) - allowed
    if unknown:
        raise ProfileValidationError(f"Unsupported fields: {', '.join(sorted(unknown))}.")
    return {field: normalizer(field, value) for field, value in changes.items()}


def _ensure_user_exists(connection: sqlite3.Connection, user_id: str) -> None:
    if connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise ProfileNotFoundError("User not found.")


def _ensure_owned_contact(connection: sqlite3.Connection, user_id: str, contact_detail_id: str | None) -> None:
    if contact_detail_id is None:
        return
    row = connection.execute("SELECT user_id FROM contact_details WHERE id = ?", (contact_detail_id,)).fetchone()
    if row is None:
        raise ProfileNotFoundError("Contact detail not found.")
    if row["user_id"] != user_id:
        raise ProfileAuthorizationError("Contact detail belongs to another user.")


def _raise_for_missing_or_forbidden(connection: sqlite3.Connection, table: str, entity_id: str, user_id: str) -> None:
    row = connection.execute(f"SELECT user_id FROM {table} WHERE id = ?", (entity_id,)).fetchone()
    if row is None:
        raise ProfileNotFoundError("Profile data not found.")
    if row["user_id"] != user_id:
        raise ProfileAuthorizationError("Profile data belongs to another user.")
    raise ProfileNotFoundError("Profile data not found.")


def _raise_document_for_missing_or_forbidden(connection: sqlite3.Connection, document_id: str, user_id: str) -> None:
    row = connection.execute(
        """
        SELECT passenger.user_id
        FROM passenger_documents AS document
        JOIN passenger_profiles AS passenger ON passenger.id = document.passenger_profile_id
        WHERE document.id = ?
        """,
        (document_id,),
    ).fetchone()
    if row is None:
        raise ProfileNotFoundError("Passenger document not found.")
    if row["user_id"] != user_id:
        raise ProfileAuthorizationError("Passenger document belongs to another user.")
    raise ProfileNotFoundError("Passenger document not found.")


def _update_table(connection: sqlite3.Connection, table: str, key_column: str, key_value: str, changes: dict[str, Any]) -> None:
    assignments = ", ".join(f"{field} = ?" for field in changes)
    connection.execute(
        f"UPDATE {table} SET {assignments} WHERE {key_column} = ?",
        (*changes.values(), key_value),
    )


def _user_profile_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "userId": row["user_id"],
        "displayName": row["display_name"],
        "legalName": _legal_name_payload(row),
        "dateOfBirth": row["date_of_birth"],
        "gender": row["gender"],
        "countryCode": row["country_code"],
    }


def _contact_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "label": row["label"],
        "email": row["email"],
        "phone": row["phone"],
    }


def _passenger_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "userId": row["user_id"],
        "displayName": row["display_name"],
        "legalName": _legal_name_payload(row),
        "dateOfBirth": row["date_of_birth"],
        "passengerType": row["passenger_type"],
        "gender": row["gender"],
        "contactDetailId": row["contact_detail_id"],
    }


def _document_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "passengerProfileId": row["passenger_profile_id"],
        "documentType": row["document_type"],
        "issuingCountry": row["issuing_country"],
        "nationalityCountry": row["nationality_country"],
        "expiresOn": row["expires_on"],
        "documentNumberLast4": row["document_number_last4"],
    }


def _legal_name_payload(row: sqlite3.Row) -> dict[str, Any]:
    parts = [row["legal_given_name"], row["legal_middle_name"], row["legal_family_name"]]
    return {
        "givenName": row["legal_given_name"],
        "middleName": row["legal_middle_name"],
        "familyName": row["legal_family_name"],
        "fullName": " ".join(part for part in parts if part),
    }
