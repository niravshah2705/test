"""Flight offer detail and checkout draft helpers.

This module keeps the checkout flow framework-neutral for deterministic tests. It
builds frontend-safe offer detail/fare review payloads, validates contact and
traveler fields at field granularity, supports saved passenger profile reuse for
authenticated users, and persists immutable passenger snapshots on booking drafts.
"""

from __future__ import annotations

import copy
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping

from .flight_search import DEFAULT_NOW, _baggage_summary, _is_expired
from .flights import DeterministicMockFlightProvider, FlightBookingService, FlightOffer, FlightProvider
from .profiles import ProfileRepository, ProfileValidationError
from .public_api import ApiResponse, error_response, success_response

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z '\-]{0,39}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 .()\-]{6,24}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
DOCUMENT_TYPES = {"passport", "national_id"}
GENDERS = {"female", "male", "non_binary", "unspecified"}
PASSENGER_TYPES = {"adult", "child", "infant"}
AIRPORT_COUNTRIES = {"SFO": "US", "JFK": "US", "LAX": "US", "ORD": "US", "SEA": "US", "LHR": "GB", "YYZ": "CA"}
DEFAULT_TAX_RATE = 0.12


class CheckoutValidationError(ValueError):
    """Raised when checkout input has field-level validation errors."""

    def __init__(self, fields: dict[str, list[str]]):
        super().__init__("Checkout input failed validation.")
        self.fields = fields


@dataclass
class BookingDraftRepository:
    """SQLite persistence for flight booking drafts and immutable snapshots."""

    database_path: str

    def __post_init__(self) -> None:
        with self._connect() as connection:
            _ensure_schema(connection)
            connection.commit()

    def save(self, draft: dict[str, Any]) -> dict[str, Any]:
        immutable = copy.deepcopy(draft)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO flight_booking_drafts (
                    id, offer_id, user_id, checkout_type, contact_email, contact_phone,
                    total_cents, currency, status, created_at, expires_at, offer_snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    immutable["id"],
                    immutable["offerId"],
                    immutable.get("userId"),
                    immutable["checkoutType"],
                    immutable["contact"]["email"],
                    immutable["contact"]["phone"],
                    immutable["total"]["amountCents"],
                    immutable["total"]["currency"],
                    immutable["status"],
                    immutable["createdAt"],
                    immutable["expiresAt"],
                    repr(immutable["offerSnapshot"]),
                ),
            )
            for index, passenger in enumerate(immutable["passengerSnapshots"], start=1):
                connection.execute(
                    """
                    INSERT INTO flight_booking_passenger_snapshots (
                        id, draft_id, ordinal, passenger_profile_id, passenger_type,
                        legal_given_name, legal_middle_name, legal_family_name,
                        date_of_birth, gender, document_snapshot
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{immutable['id']}_pax_{index}",
                        immutable["id"],
                        index,
                        passenger.get("passengerProfileId"),
                        passenger["passengerType"],
                        passenger["legalName"]["givenName"],
                        passenger["legalName"].get("middleName"),
                        passenger["legalName"]["familyName"],
                        passenger["dateOfBirth"],
                        passenger.get("gender"),
                        repr(passenger.get("document")),
                    ),
                )
            connection.commit()
        return immutable

    def get(self, draft_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            draft = connection.execute("SELECT * FROM flight_booking_drafts WHERE id = ?", (draft_id,)).fetchone()
            if draft is None:
                return None
            passengers = connection.execute(
                "SELECT * FROM flight_booking_passenger_snapshots WHERE draft_id = ? ORDER BY ordinal",
                (draft_id,),
            ).fetchall()
        return {
            "id": draft["id"],
            "offerId": draft["offer_id"],
            "userId": draft["user_id"],
            "checkoutType": draft["checkout_type"],
            "contact": {"email": draft["contact_email"], "phone": draft["contact_phone"]},
            "total": {"amountCents": draft["total_cents"], "currency": draft["currency"], "formatted": f"{draft['currency']} {draft['total_cents'] / 100:.2f}"},
            "status": draft["status"],
            "createdAt": draft["created_at"],
            "expiresAt": draft["expires_at"],
            "passengerSnapshots": [_snapshot_payload(row) for row in passengers],
        }

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection


@dataclass
class InMemoryBookingDraftRepository:
    """Small test/local repository with copy-on-write immutable snapshots."""

    drafts: dict[str, dict[str, Any]] = field(default_factory=dict)

    def save(self, draft: dict[str, Any]) -> dict[str, Any]:
        immutable = copy.deepcopy(draft)
        self.drafts[immutable["id"]] = immutable
        return copy.deepcopy(immutable)

    def get(self, draft_id: str) -> dict[str, Any] | None:
        draft = self.drafts.get(draft_id)
        return copy.deepcopy(draft) if draft else None


def handle_offer_detail(
    offer_id: str,
    *,
    provider: FlightProvider | None = None,
    now: datetime = DEFAULT_NOW,
    required_passenger_types: list[str] | None = None,
) -> ApiResponse:
    """Return the offer detail/fare review contract for checkout."""

    try:
        detail = build_offer_detail(offer_id, provider=provider, now=now, required_passenger_types=required_passenger_types)
    except Exception:
        return error_response(503, "provider_unavailable", "Flight offer details are temporarily unavailable. Please retry.")
    return success_response(detail, meta={"providerPayloadExposed": False})


def build_offer_detail(
    offer_id: str,
    *,
    provider: FlightProvider | None = None,
    now: datetime = DEFAULT_NOW,
    required_passenger_types: list[str] | None = None,
) -> dict[str, Any]:
    offer = FlightBookingService(provider or DeterministicMockFlightProvider()).getOfferDetails(offer_id)
    return offer_detail_dto(offer, now=now, required_passenger_types=required_passenger_types)


def offer_detail_dto(offer: FlightOffer, *, now: datetime = DEFAULT_NOW, required_passenger_types: list[str] | None = None) -> dict[str, Any]:
    expires_at = offer.expires_at or "2031-07-01T07:45:00Z"
    expired = _is_expired(expires_at, now)
    traveler_types = required_passenger_types or ["adult"] * offer.passenger_count
    base_cents = round(offer.total.amount_cents / (1 + DEFAULT_TAX_RATE))
    taxes_cents = offer.total.amount_cents - base_cents
    passenger_count = max(len(traveler_types), 1)
    per_passenger = offer.total.amount_cents // passenger_count

    itineraries = []
    timeline = []
    previous_arrival: tuple[str, str] | None = None
    for itinerary in offer.itineraries:
        segments = []
        for segment in itinerary.segments:
            if previous_arrival and previous_arrival[0] == segment.origin:
                layover = _layover(previous_arrival[1], segment.departs_at, segment.origin)
                if layover:
                    timeline.append(layover)
            segment_payload = segment.to_payload()
            segments.append(segment_payload)
            timeline.append({"type": "segment", "segment": segment_payload})
            previous_arrival = (segment.destination, segment.arrives_at)
        itineraries.append({"id": itinerary.id, "segments": segments})
        previous_arrival = None

    document_required = _requires_international_document(offer)
    requirements = [
        {
            "ordinal": index,
            "passengerType": passenger_type,
            "fields": ["legalGivenName", "legalFamilyName", "dateOfBirth", "passengerType"] + (["gender"] if document_required else []),
            "documentRequired": document_required,
        }
        for index, passenger_type in enumerate(traveler_types, start=1)
    ]

    return {
        "id": offer.id,
        "status": "expired" if expired else offer.status,
        "expiresAt": expires_at,
        "isExpired": expired,
        "canContinue": not expired,
        "itineraries": itineraries,
        "timeline": timeline,
        "fareSummary": {"cabin": offer.cabin, "refundable": offer.refundable, "baseFare": _money(base_cents, offer.total.currency), "taxesAndFees": _money(taxes_cents, offer.total.currency), "total": offer.total.to_payload()},
        "passengerPriceBreakdown": [
            {"ordinal": index, "passengerType": passenger_type, "total": _money(per_passenger, offer.total.currency)}
            for index, passenger_type in enumerate(traveler_types, start=1)
        ],
        "baggageSummary": _baggage_summary(offer.checked_bags_included),
        "requiredPassengerForms": requirements,
        "documentRequirements": {"required": document_required, "acceptedTypes": sorted(DOCUMENT_TYPES) if document_required else []},
    }


def create_booking_draft(
    payload: Mapping[str, Any],
    *,
    provider: FlightProvider | None = None,
    repository: InMemoryBookingDraftRepository | BookingDraftRepository | None = None,
    profile_repository: ProfileRepository | None = None,
    now: datetime = DEFAULT_NOW,
) -> dict[str, Any]:
    """Validate checkout data and create a booking draft with passenger snapshots."""

    offer_id = _required_text(payload.get("offerId"), "offerId", {})
    detail = build_offer_detail(offer_id, provider=provider, now=now, required_passenger_types=list(payload.get("requiredPassengerTypes") or [] ) or None)
    if detail["isExpired"]:
        raise CheckoutValidationError({"offerId": ["Expired offers cannot continue to checkout."]})

    errors: dict[str, list[str]] = {}
    user_id = _optional_text(payload.get("userId"))
    contact = _validate_contact(payload.get("contact") or {}, errors)
    passenger_inputs = list(payload.get("passengers") or [])
    expected_forms = detail["requiredPassengerForms"]
    if len(passenger_inputs) != len(expected_forms):
        errors["passengers"] = [f"Expected {len(expected_forms)} passenger form(s)."]

    snapshots = []
    for index, requirement in enumerate(expected_forms):
        raw = passenger_inputs[index] if index < len(passenger_inputs) else {}
        snapshot = _validate_passenger(
            raw,
            index=index,
            expected_type=requirement["passengerType"],
            travel_date=_first_travel_date(detail),
            document_required=requirement["documentRequired"],
            user_id=user_id,
            profile_repository=profile_repository,
            errors=errors,
        )
        if snapshot:
            snapshots.append(snapshot)

    _validate_infants_have_adults(snapshots, errors)
    if errors:
        raise CheckoutValidationError(errors)

    draft = {
        "id": str(payload.get("draftId") or f"draft_{offer_id}_{len(snapshots)}"),
        "offerId": offer_id,
        "userId": user_id,
        "checkoutType": "authenticated" if user_id else "guest",
        "status": "draft",
        "createdAt": _iso(now),
        "expiresAt": detail["expiresAt"],
        "contact": contact,
        "total": detail["fareSummary"]["total"],
        "offerSnapshot": detail,
        "passengerSnapshots": snapshots,
    }
    return (repository or InMemoryBookingDraftRepository()).save(draft)


def handle_create_booking_draft(payload: Mapping[str, Any], **kwargs: Any) -> ApiResponse:
    try:
        return success_response(create_booking_draft(payload, **kwargs), status_code=201)
    except CheckoutValidationError as exc:
        return error_response(400, "validation_error", "Checkout fields failed validation.", fields=exc.fields)


def _validate_contact(raw: Mapping[str, Any], errors: dict[str, list[str]]) -> dict[str, str]:
    email = str(raw.get("email") or "").strip().lower()
    phone = str(raw.get("phone") or "").strip()
    if not EMAIL_RE.fullmatch(email):
        errors["contact.email"] = ["Contact email must be a valid address."]
    if not PHONE_RE.fullmatch(phone):
        errors["contact.phone"] = ["Contact phone must be a valid phone number."]
    return {"email": email, "phone": phone}


def _validate_passenger(
    raw: Mapping[str, Any],
    *,
    index: int,
    expected_type: str,
    travel_date: date,
    document_required: bool,
    user_id: str | None,
    profile_repository: ProfileRepository | None,
    errors: dict[str, list[str]],
) -> dict[str, Any] | None:
    prefix = f"passengers[{index}]"
    data = dict(raw)
    profile_id = _optional_text(data.get("profileId"))
    if profile_id:
        if not user_id or profile_repository is None:
            errors[f"{prefix}.profileId"] = ["Saved passenger profiles require an authenticated user."]
        else:
            try:
                profile = profile_repository.get_passenger_profile(user_id, profile_id)
                if data.get("saveToProfile"):
                    changes = _profile_changes(data)
                    if changes:
                        profile = profile_repository.update_passenger_profile(user_id, profile_id, changes)
                data = {**_profile_to_input(profile), **{key: value for key, value in data.items() if value not in (None, "")}}
                documents = profile_repository.list_passenger_documents(user_id, profile_id)
                if documents and "document" not in data:
                    data["document"] = documents[0]
            except Exception:
                errors[f"{prefix}.profileId"] = ["Saved passenger profile could not be loaded."]

    given = _validate_name(data.get("legalGivenName") or data.get("givenName"), f"{prefix}.legalGivenName", errors)
    middle = _optional_text(data.get("legalMiddleName") or data.get("middleName"))
    family = _validate_name(data.get("legalFamilyName") or data.get("familyName"), f"{prefix}.legalFamilyName", errors)
    passenger_type = str(data.get("passengerType") or expected_type).strip().lower()
    if passenger_type not in PASSENGER_TYPES:
        errors[f"{prefix}.passengerType"] = ["Passenger type must be adult, child, or infant."]
    elif passenger_type != expected_type:
        errors[f"{prefix}.passengerType"] = [f"Passenger type must match required {expected_type} traveler."]
    birth = _validate_birth_date(data.get("dateOfBirth"), passenger_type, travel_date, f"{prefix}.dateOfBirth", errors)
    gender = _optional_text(data.get("gender"))
    if document_required and gender not in GENDERS:
        errors[f"{prefix}.gender"] = ["Gender is required for this itinerary."]
    document = _validate_document(data.get("document") or {}, prefix, document_required, travel_date, errors)

    if any(key.startswith(prefix) for key in errors):
        return None
    return {
        "passengerProfileId": profile_id,
        "legalName": {"givenName": given, "middleName": middle, "familyName": family, "fullName": " ".join(part for part in [given, middle, family] if part)},
        "dateOfBirth": birth,
        "passengerType": passenger_type,
        "gender": gender,
        "document": document,
    }


def _validate_name(value: Any, field: str, errors: dict[str, list[str]]) -> str:
    text = str(value or "").strip()
    if not NAME_RE.fullmatch(text):
        errors[field] = ["Use 1-40 letters, spaces, apostrophes, or hyphens."]
    return text


def _validate_birth_date(value: Any, passenger_type: str, travel_date: date, field: str, errors: dict[str, list[str]]) -> str:
    try:
        parsed = date.fromisoformat(str(value or ""))
    except ValueError:
        errors[field] = ["Date of birth must use YYYY-MM-DD format."]
        return str(value or "")
    age = travel_date.year - parsed.year - ((travel_date.month, travel_date.day) < (parsed.month, parsed.day))
    if parsed > travel_date:
        errors[field] = ["Date of birth cannot be after travel date."]
    elif passenger_type == "adult" and age < 18:
        errors[field] = ["Adult travelers must be at least 18 on the travel date."]
    elif passenger_type == "child" and not (2 <= age < 18):
        errors[field] = ["Child travelers must be 2-17 on the travel date."]
    elif passenger_type == "infant" and age >= 2:
        errors[field] = ["Infant travelers must be under 2 on the travel date."]
    return parsed.isoformat()


def _validate_document(raw: Mapping[str, Any], prefix: str, required: bool, travel_date: date, errors: dict[str, list[str]]) -> dict[str, Any] | None:
    if not required and not raw:
        return None
    document_type = str(raw.get("documentType") or raw.get("document_type") or "").strip().lower()
    issuing_country = str(raw.get("issuingCountry") or raw.get("issuing_country") or "").strip().upper()
    nationality_country = str(raw.get("nationalityCountry") or raw.get("nationality_country") or issuing_country).strip().upper()
    expires_on = str(raw.get("expiresOn") or raw.get("expires_on") or "").strip()
    last4 = _optional_text(raw.get("documentNumberLast4") or raw.get("document_number_last4"))
    if document_type not in DOCUMENT_TYPES:
        errors[f"{prefix}.document.documentType"] = ["Document type must be passport or national_id."]
    if not COUNTRY_RE.fullmatch(issuing_country):
        errors[f"{prefix}.document.issuingCountry"] = ["Issuing country must be a two-letter ISO code."]
    if not COUNTRY_RE.fullmatch(nationality_country):
        errors[f"{prefix}.document.nationalityCountry"] = ["Nationality country must be a two-letter ISO code."]
    try:
        expiry = date.fromisoformat(expires_on)
        if expiry < travel_date:
            errors[f"{prefix}.document.expiresOn"] = ["Document must not expire before the travel date."]
    except ValueError:
        errors[f"{prefix}.document.expiresOn"] = ["Document expiry must use YYYY-MM-DD format."]
    if last4 is not None and (len(last4) != 4 or not last4.isalnum()):
        errors[f"{prefix}.document.documentNumberLast4"] = ["Document number last4 must contain exactly four letters or digits."]
    return {"documentType": document_type, "issuingCountry": issuing_country, "nationalityCountry": nationality_country, "expiresOn": expires_on, "documentNumberLast4": last4.upper() if last4 else None}


def _validate_infants_have_adults(snapshots: list[dict[str, Any]], errors: dict[str, list[str]]) -> None:
    adults = sum(1 for passenger in snapshots if passenger["passengerType"] == "adult")
    infants = sum(1 for passenger in snapshots if passenger["passengerType"] == "infant")
    if infants > adults:
        errors["passengers"] = ["Each infant must be associated with an adult traveler."]


def _profile_to_input(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "legalGivenName": profile["legalName"]["givenName"],
        "legalMiddleName": profile["legalName"].get("middleName"),
        "legalFamilyName": profile["legalName"]["familyName"],
        "dateOfBirth": profile["dateOfBirth"],
        "passengerType": profile["passengerType"],
        "gender": profile.get("gender"),
    }


def _profile_changes(data: Mapping[str, Any]) -> dict[str, Any]:
    mapping = {
        "legalGivenName": "legal_given_name",
        "legalMiddleName": "legal_middle_name",
        "legalFamilyName": "legal_family_name",
        "dateOfBirth": "date_of_birth",
        "passengerType": "passenger_type",
        "gender": "gender",
    }
    return {target: data[source] for source, target in mapping.items() if source in data and data[source] not in (None, "")}


def _first_travel_date(detail: dict[str, Any]) -> date:
    first = detail["itineraries"][0]["segments"][0]["departsAt"][:10]
    return date.fromisoformat(first)


def _requires_international_document(offer: FlightOffer) -> bool:
    for itinerary in offer.itineraries:
        for segment in itinerary.segments:
            if AIRPORT_COUNTRIES.get(segment.origin, "") != AIRPORT_COUNTRIES.get(segment.destination, ""):
                return True
    return False


def _layover(arrives_at: str, departs_at: str, airport: str) -> dict[str, Any] | None:
    try:
        arrival = datetime.fromisoformat(arrives_at.replace("Z", "+00:00"))
        departure = datetime.fromisoformat(departs_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    minutes = int((departure - arrival).total_seconds() // 60)
    if minutes <= 0:
        return None
    return {"type": "layover", "airport": airport, "durationMinutes": minutes}


def _money(amount_cents: int, currency: str) -> dict[str, Any]:
    return {"amountCents": amount_cents, "currency": currency, "formatted": f"{currency} {amount_cents / 100:.2f}"}


def _required_text(value: Any, field: str, errors: dict[str, list[str]]) -> str:
    text = _optional_text(value)
    if text is None:
        errors[field] = [f"{field} is required."]
        raise CheckoutValidationError(errors)
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def _ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS flight_booking_drafts (
            id TEXT PRIMARY KEY,
            offer_id TEXT NOT NULL,
            user_id TEXT,
            checkout_type TEXT NOT NULL CHECK (checkout_type IN ('guest', 'authenticated')),
            contact_email TEXT NOT NULL,
            contact_phone TEXT NOT NULL,
            total_cents INTEGER NOT NULL,
            currency TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('draft', 'submitted', 'expired')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            offer_snapshot TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS flight_booking_passenger_snapshots (
            id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL REFERENCES flight_booking_drafts(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            passenger_profile_id TEXT,
            passenger_type TEXT NOT NULL,
            legal_given_name TEXT NOT NULL,
            legal_middle_name TEXT,
            legal_family_name TEXT NOT NULL,
            date_of_birth TEXT NOT NULL,
            gender TEXT,
            document_snapshot TEXT
        )
        """
    )


def _snapshot_payload(row: sqlite3.Row) -> dict[str, Any]:
    parts = [row["legal_given_name"], row["legal_middle_name"], row["legal_family_name"]]
    return {
        "passengerProfileId": row["passenger_profile_id"],
        "legalName": {"givenName": row["legal_given_name"], "middleName": row["legal_middle_name"], "familyName": row["legal_family_name"], "fullName": " ".join(part for part in parts if part)},
        "dateOfBirth": row["date_of_birth"],
        "passengerType": row["passenger_type"],
        "gender": row["gender"],
    }
