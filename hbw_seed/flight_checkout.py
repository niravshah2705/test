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
from .flights import DeterministicMockFlightProvider, FlightBookingService, FlightOffer, FlightOrderRequest, FlightProvider, FlightProviderTimeout, FlightProviderUnavailable, RevalidationResult
from .profiles import ProfileRepository, ProfileValidationError
from .public_api import ApiResponse, error_response, success_response

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z '\-]{0,39}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 .()\-]{6,24}$")
COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
DOCUMENT_TYPES = {"passport", "national_id"}
GENDERS = {"female", "male", "non_binary", "unspecified"}
PAYMENT_TOKEN_RE = re.compile(r"^(tok|pm|ref)_[A-Za-z0-9_\-]{8,80}$")
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
SENSITIVE_PAYMENT_FIELDS = {"cardNumber", "card_number", "cvv", "cvc", "pan", "expiry", "expiration", "card"}
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
                    total_cents, currency, status, created_at, expires_at, offer_snapshot, audit_events
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    repr(immutable.get("auditEvents") or []),
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
            "offerSnapshot": _parse_snapshot(draft["offer_snapshot"]),
            "passengerSnapshots": [_snapshot_payload(row) for row in passengers],
            "revalidation": _parse_snapshot(draft["revalidation_snapshot"]) if "revalidation_snapshot" in draft.keys() else None,
            "paymentAllowed": draft["status"] in {"price_validated", "price_change_accepted"},
            "paymentAttempts": _parse_snapshot(draft["payment_attempts"]) if "payment_attempts" in draft.keys() else [],
            "providerOrder": _parse_snapshot(draft["provider_order"]) if "provider_order" in draft.keys() else None,
            "auditEvents": _parse_snapshot(draft["audit_events"]) if "audit_events" in draft.keys() else [],
        }

    def update(self, draft_id: str, changes: Mapping[str, Any]) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM flight_booking_drafts WHERE id = ?", (draft_id,)).fetchone()
            if current is None:
                connection.rollback()
                return None
            status = changes.get("status", current["status"])
            total = changes.get("total") or {"amountCents": current["total_cents"], "currency": current["currency"]}
            revalidation = changes.get("revalidation")
            payment_attempts = changes.get("paymentAttempts")
            provider_order = changes.get("providerOrder")
            audit_events = changes.get("auditEvents")
            connection.execute(
                "UPDATE flight_booking_drafts SET status = ?, total_cents = ?, currency = ?, revalidation_snapshot = ?, payment_attempts = ?, provider_order = ?, audit_events = ? WHERE id = ?",
                (
                    status,
                    int(total["amountCents"]),
                    total["currency"],
                    repr(revalidation) if revalidation is not None else current["revalidation_snapshot"],
                    repr(payment_attempts) if payment_attempts is not None else current["payment_attempts"],
                    repr(provider_order) if provider_order is not None else current["provider_order"],
                    repr(audit_events) if audit_events is not None else current["audit_events"],
                    draft_id,
                ),
            )
            connection.commit()
        return self.get(draft_id)

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

    def update(self, draft_id: str, changes: Mapping[str, Any]) -> dict[str, Any] | None:
        draft = self.drafts.get(draft_id)
        if draft is None:
            return None
        updated = copy.deepcopy(draft)
        updated.update(copy.deepcopy(dict(changes)))
        self.drafts[draft_id] = updated
        return copy.deepcopy(updated)


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
        "paymentAllowed": False,
        "revalidation": None,
        "createdAt": _iso(now),
        "expiresAt": detail["expiresAt"],
        "contact": contact,
        "total": detail["fareSummary"]["total"],
        "offerSnapshot": detail,
        "passengerSnapshots": snapshots,
        "paymentAttempts": [],
        "providerOrder": None,
        "auditEvents": [_audit_event("booking_draft.created", status="draft", details={"offerId": offer_id, "checkoutType": "authenticated" if user_id else "guest"})],
    }
    return (repository or InMemoryBookingDraftRepository()).save(draft)


def revalidate_booking_draft(
    draft_id: str,
    *,
    repository: InMemoryBookingDraftRepository | BookingDraftRepository,
    provider: FlightProvider | None = None,
    scenario: str = "success",
) -> dict[str, Any]:
    """Re-check a booking draft offer and gate payment through explicit review states."""

    draft = repository.get(draft_id)
    if draft is None:
        raise CheckoutValidationError({"draftId": ["Booking draft was not found."]})
    existing = draft.get("revalidation") or {}
    if existing.get("status") == "in_progress":
        return booking_review_payload(draft)
    if draft["status"] not in {"draft", "revalidating", "price_validated", "price_changed", "revalidation_failed"}:
        raise CheckoutValidationError({"status": ["Booking draft cannot be revalidated from its current status."]})
    if draft["status"] in {"price_validated", "price_changed"} and existing.get("scenario") == scenario:
        return booking_review_payload(draft)

    repository.update(draft_id, {"status": "revalidating", "revalidation": {"status": "in_progress", "scenario": scenario, "message": "Revalidation is already running."}})
    try:
        result = FlightBookingService(provider or DeterministicMockFlightProvider()).revalidateOffer(
            draft["offerId"], passengers=tuple(draft.get("passengerSnapshots") or ()), scenario=scenario
        )
    except FlightProviderTimeout as exc:
        updated = _update_with_audit(repository, draft, {"status": "revalidation_failed", "revalidation": _review_snapshot("retryable_failure", draft, None, str(exc), scenario)}, "booking_revalidation.failed", details={"result": "retryable_failure", "scenario": scenario})
        return booking_review_payload(updated or draft)
    except FlightProviderUnavailable as exc:
        updated = _update_with_audit(repository, draft, {"status": "revalidation_failed", "revalidation": _review_snapshot("retryable_failure", draft, None, str(exc), scenario)}, "booking_revalidation.failed", details={"result": "retryable_failure", "scenario": scenario})
        return booking_review_payload(updated or draft)

    review_status = _classify_revalidation(draft, result)
    new_total = result.offer.total.to_payload() if result.offer and review_status in {"unchanged", "price_increased", "price_decreased"} else draft["total"]
    next_status = "price_validated" if review_status == "unchanged" else "price_changed" if review_status in {"price_increased", "price_decreased"} else "unavailable"
    updated = _update_with_audit(
        repository,
        draft,
        {"status": next_status, "total": new_total if next_status == "price_validated" else draft["total"], "revalidation": _review_snapshot(review_status, draft, result, result.message, scenario)},
        "booking_revalidation.completed",
        details={"result": review_status, "scenario": scenario},
    )
    return booking_review_payload(updated or draft)


def accept_revalidated_price(draft_id: str, *, repository: InMemoryBookingDraftRepository | BookingDraftRepository) -> dict[str, Any]:
    draft = repository.get(draft_id)
    if draft is None:
        raise CheckoutValidationError({"draftId": ["Booking draft was not found."]})
    revalidation = draft.get("revalidation") or {}
    if draft["status"] != "price_changed" or revalidation.get("latestTotal") is None:
        raise CheckoutValidationError({"revalidation": ["No changed price is waiting for acceptance."]})
    updated = _update_with_audit(repository, draft, {"status": "price_change_accepted", "total": revalidation["latestTotal"], "revalidation": {**revalidation, "accepted": True}}, "booking_revalidation.price_accepted", details={"result": revalidation.get("status")})
    return booking_review_payload(updated or draft)


def booking_review_payload(draft: dict[str, Any]) -> dict[str, Any]:
    revalidation = draft.get("revalidation") or {"status": "not_started", "message": "Revalidate price before payment."}
    payment_allowed = draft["status"] in {"price_validated", "price_change_accepted"}
    actions = []
    if revalidation.get("status") in {"price_increased", "price_decreased"} and not revalidation.get("accepted"):
        actions.append({"label": "Accept new price", "action": "accept_price", "variant": "primary"})
    if revalidation.get("status") in {"unavailable", "material_change", "currency_mismatch"}:
        actions.append({"label": "Choose another offer", "href": "/search", "variant": "primary"})
    if revalidation.get("status") in {"retryable_failure", "not_started"}:
        actions.append({"label": "Retry price check", "action": "revalidate", "variant": "primary"})
    return {"draftId": draft["id"], "status": draft["status"], "paymentAllowed": payment_allowed, "currentTotal": draft["total"], "revalidation": revalidation, "actions": actions}


def finalize_booking_payment(
    draft_id: str,
    payload: Mapping[str, Any],
    *,
    repository: InMemoryBookingDraftRepository | BookingDraftRepository,
    provider: FlightProvider | None = None,
) -> dict[str, Any]:
    """Authorize tokenized payment and create the provider booking exactly once per idempotency key."""

    draft = repository.get(draft_id)
    if draft is None:
        raise CheckoutValidationError({"draftId": ["Booking draft was not found."]})
    validation = _validate_payment_payload(draft, payload)
    if validation["errors"]:
        attempt = _payment_attempt(draft, validation["idempotencyKey"], validation["paymentToken"], "failed", "validation_failed")
        updated = _append_payment_attempt(repository, draft, attempt)
        raise CheckoutValidationError(validation["errors"])

    idempotency_key = validation["idempotencyKey"]
    existing = _attempt_by_idempotency(draft, idempotency_key)
    if existing:
        return _confirmation_payload(draft, existing, duplicate=True)

    if draft["status"] in {"finalized", "ticketing_pending"}:
        completed = _latest_terminal_attempt(draft)
        if completed:
            return _confirmation_payload(draft, completed, duplicate=True)
        raise CheckoutValidationError({"status": ["Booking is already finalized."]})

    if draft["status"] not in {"price_validated", "price_change_accepted"}:
        raise CheckoutValidationError({"revalidation": ["Successful price revalidation is required before payment."]})

    amount = {"amountCents": int(payload.get("amountCents")), "currency": str(payload.get("currency") or "").upper()}
    if amount["amountCents"] != draft["total"]["amountCents"] or amount["currency"] != draft["total"]["currency"]:
        attempt = _payment_attempt(draft, idempotency_key, validation["paymentToken"], "failed", "amount_or_currency_mismatch")
        updated = _append_payment_attempt(repository, draft, attempt)
        raise CheckoutValidationError({"amount": ["Payment amount and currency must match the accepted revalidated price."]})

    scenario = str(payload.get("scenario") or "success")
    payment_attempt = _payment_attempt(draft, idempotency_key, validation["paymentToken"], "authorized", None)
    if scenario == "declined":
        payment_attempt["status"] = "declined"
        payment_attempt["failureReason"] = "payment_declined"
        updated = _append_payment_attempt(repository, draft, payment_attempt, event_type="booking_payment.declined")
        return _confirmation_payload(updated, payment_attempt)

    draft_with_payment = _append_payment_attempt(repository, draft, payment_attempt, event_type="booking_payment.authorized")
    try:
        order = FlightBookingService(provider or DeterministicMockFlightProvider()).createOrder(
            FlightOrderRequest(
                offer_id=draft["offerId"],
                passengers=tuple(draft.get("passengerSnapshots") or ()),
                contact_email=draft["contact"]["email"],
                scenario="error" if scenario == "provider_failure" else "success",
            )
        )
    except (FlightProviderTimeout, FlightProviderUnavailable) as exc:
        failed_attempt = {**payment_attempt, "status": "authorized_booking_failed", "failureReason": "provider_booking_failed"}
        updated = _update_with_audit(
            repository,
            draft_with_payment,
            {
                "status": "ticketing_pending",
                "paymentAttempts": _replace_attempt(draft_with_payment.get("paymentAttempts") or [], failed_attempt),
                "providerOrder": {"status": "failed", "message": str(exc)},
            },
            "booking_provider_order.failed",
            details={"failureReason": "provider_booking_failed"},
        )
        return _confirmation_payload(updated or draft_with_payment, failed_attempt)

    provider_order = order.to_payload()
    final_status = "finalized" if order.status == "ticketed" else "ticketing_pending"
    captured_attempt = {**payment_attempt, "status": "captured"}
    updated = _update_with_audit(
        repository,
        draft_with_payment,
        {
            "status": final_status,
            "paymentAttempts": _replace_attempt(draft_with_payment.get("paymentAttempts") or [], captured_attempt),
            "providerOrder": provider_order,
        },
        "booking_provider_order.created",
        details={"orderId": provider_order["id"], "orderStatus": provider_order["status"]},
    )
    return _confirmation_payload(updated or draft_with_payment, captured_attempt)


def poll_booking_finalization(
    draft_id: str,
    *,
    repository: InMemoryBookingDraftRepository | BookingDraftRepository,
    provider: FlightProvider | None = None,
) -> dict[str, Any]:
    draft = repository.get(draft_id)
    if draft is None:
        raise CheckoutValidationError({"draftId": ["Booking draft was not found."]})
    order = draft.get("providerOrder")
    attempt = _latest_terminal_attempt(draft)
    if not order or order.get("status") != "ticketing_pending":
        return _confirmation_payload(draft, attempt)
    latest = FlightBookingService(provider or DeterministicMockFlightProvider()).getOrderStatus(order["id"])
    updated_order = latest.to_payload()
    status = "finalized" if latest.status == "ticketed" else "ticketing_pending"
    updated = _update_with_audit(repository, draft, {"status": status, "providerOrder": updated_order}, "booking_provider_order.status_refreshed", details={"orderId": updated_order["id"], "orderStatus": updated_order["status"]})
    return _confirmation_payload(updated or draft, attempt)


def handle_finalize_booking_payment(draft_id: str, payload: Mapping[str, Any], **kwargs: Any) -> ApiResponse:
    try:
        result = finalize_booking_payment(draft_id, payload, **kwargs)
    except CheckoutValidationError as exc:
        return error_response(400, "validation_error", "Payment could not be finalized.", fields=exc.fields)
    status_code = 202 if result["status"] in {"ticketing_pending", "payment_declined", "booking_failed_after_payment"} else 200
    return success_response(result, status_code=status_code)


def handle_poll_booking_finalization(draft_id: str, **kwargs: Any) -> ApiResponse:
    try:
        return success_response(poll_booking_finalization(draft_id, **kwargs))
    except CheckoutValidationError as exc:
        return error_response(400, "validation_error", "Booking status could not be retrieved.", fields=exc.fields)


def handle_revalidate_booking_draft(draft_id: str, **kwargs: Any) -> ApiResponse:
    try:
        return success_response(revalidate_booking_draft(draft_id, **kwargs))
    except CheckoutValidationError as exc:
        return error_response(400, "validation_error", "Booking draft cannot be revalidated.", fields=exc.fields)


def handle_accept_revalidated_price(draft_id: str, **kwargs: Any) -> ApiResponse:
    try:
        return success_response(accept_revalidated_price(draft_id, **kwargs))
    except CheckoutValidationError as exc:
        return error_response(400, "validation_error", "Changed price cannot be accepted.", fields=exc.fields)



def handle_create_booking_draft(payload: Mapping[str, Any], **kwargs: Any) -> ApiResponse:
    try:
        return success_response(create_booking_draft(payload, **kwargs), status_code=201)
    except CheckoutValidationError as exc:
        return error_response(400, "validation_error", "Checkout fields failed validation.", fields=exc.fields)


def _classify_revalidation(draft: dict[str, Any], result: RevalidationResult) -> str:
    if result.status == "unavailable" or result.offer is None:
        return "unavailable"
    latest = result.offer.to_payload()
    snapshot = draft["offerSnapshot"]
    if latest["total"]["currency"] != draft["total"]["currency"]:
        return "currency_mismatch"
    if _material_flight_signature(latest) != _material_flight_signature(snapshot):
        return "material_change"
    latest_amount = latest["total"]["amountCents"]
    current_amount = draft["total"]["amountCents"]
    if latest_amount > current_amount:
        return "price_increased"
    if latest_amount < current_amount:
        return "price_decreased"
    return "unchanged"


def _review_snapshot(status: str, draft: dict[str, Any], result: RevalidationResult | None, message: str | None, scenario: str) -> dict[str, Any]:
    latest_total = result.offer.total.to_payload() if result and result.offer else None
    return {
        "status": status,
        "scenario": scenario,
        "message": message or _review_message(status),
        "previousTotal": draft["total"],
        "latestTotal": latest_total,
        "priceDeltaCents": (latest_total["amountCents"] - draft["total"]["amountCents"]) if latest_total and latest_total["currency"] == draft["total"]["currency"] else None,
        "paymentBlocked": status != "unchanged",
        "accepted": False,
    }


def _review_message(status: str) -> str:
    return {
        "unchanged": "Price rechecked successfully. You can continue to payment.",
        "price_increased": "The fare increased. Payment is blocked until you accept the new price.",
        "price_decreased": "The fare decreased. Accept the new price before payment.",
        "unavailable": "This offer is no longer available. Choose another offer.",
        "material_change": "Flight details changed materially. Choose another offer.",
        "currency_mismatch": "Currency changed unexpectedly. Choose another offer.",
        "retryable_failure": "Price could not be rechecked. Retry before payment.",
    }.get(status, "Revalidation status changed.")


def _material_flight_signature(payload: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    itineraries = payload.get("itineraries") or []
    return tuple(
        tuple((segment.get("marketingCarrier"), segment.get("operatingCarrier"), segment.get("flightNumber"), segment.get("origin"), segment.get("destination"), segment.get("departsAt"), segment.get("arrivesAt")) for segment in itinerary.get("segments", []))
        for itinerary in itineraries
    )


def _parse_snapshot(raw: Any) -> Any:
    if raw in (None, "None"):
        return None
    if isinstance(raw, str):
        try:
            import ast
            return ast.literal_eval(raw)
        except Exception:
            return None
    return raw



def _validate_payment_payload(draft: dict[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    errors: dict[str, list[str]] = {}
    _reject_sensitive_payment_fields(payload, "", errors)
    if str(payload.get("bookingId") or payload.get("draftId") or "") != draft["id"]:
        errors["bookingId"] = ["bookingId must match the booking being paid."]
    token = str(payload.get("paymentToken") or payload.get("paymentReference") or "").strip()
    if not PAYMENT_TOKEN_RE.fullmatch(token):
        errors["paymentToken"] = ["A provider token/reference is required."]
    idempotency_key = str(payload.get("idempotencyKey") or "").strip()
    if not IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
        errors["idempotencyKey"] = ["A stable idempotency key is required."]
    try:
        int(payload.get("amountCents"))
    except Exception:
        errors["amountCents"] = ["Payment amount is required in cents."]
    if not str(payload.get("currency") or "").strip():
        errors["currency"] = ["Payment currency is required."]
    return {"errors": errors, "paymentToken": token, "idempotencyKey": idempotency_key}


def _reject_sensitive_payment_fields(value: Any, path: str, errors: dict[str, list[str]]) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            field_path = f"{path}.{key}" if path else str(key)
            if _payment_field_is_sensitive(str(key)):
                errors[field_path] = ["Raw card data must be entered only in provider-hosted tokenized fields."]
            _reject_sensitive_payment_fields(child, field_path, errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_sensitive_payment_fields(child, f"{path}[{index}]" if path else f"[{index}]", errors)


def _payment_field_is_sensitive(field: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", field.lower())
    return field in SENSITIVE_PAYMENT_FIELDS or normalized in {"cardnumber", "number", "cvv", "cvc", "pan", "expiry", "expiration", "card"}


def _payment_attempt(draft: dict[str, Any], idempotency_key: str, payment_token: str, status: str, failure_reason: str | None) -> dict[str, Any]:
    suffix = re.sub(r"[^A-Za-z0-9]+", "_", idempotency_key).strip("_")[:40]
    attempt = {
        "id": f"pay_{draft['id']}_{suffix}",
        "bookingId": draft["id"],
        "status": status,
        "amount": draft["total"],
        "idempotencyKey": idempotency_key,
        "paymentReference": f"{payment_token.split('_', 1)[0]}_***{payment_token[-4:]}",
    }
    if failure_reason:
        attempt["failureReason"] = failure_reason
    return attempt


def _append_payment_attempt(repository: InMemoryBookingDraftRepository | BookingDraftRepository, draft: dict[str, Any], attempt: dict[str, Any], *, event_type: str | None = None) -> dict[str, Any]:
    attempts = list(draft.get("paymentAttempts") or [])
    attempts.append(attempt)
    changes: dict[str, Any] = {"paymentAttempts": attempts}
    if event_type:
        return _update_with_audit(repository, draft, changes, event_type, details={"paymentId": attempt["id"], "paymentStatus": attempt["status"]}) or {**draft, **changes}
    return repository.update(draft["id"], changes) or {**draft, **changes}


def _attempt_by_idempotency(draft: dict[str, Any], idempotency_key: str) -> dict[str, Any] | None:
    return next((attempt for attempt in draft.get("paymentAttempts") or [] if attempt.get("idempotencyKey") == idempotency_key), None)


def _replace_attempt(attempts: list[dict[str, Any]], replacement: dict[str, Any]) -> list[dict[str, Any]]:
    replaced = False
    result = []
    for attempt in attempts:
        if attempt.get("id") == replacement.get("id"):
            result.append(replacement)
            replaced = True
        else:
            result.append(attempt)
    if not replaced:
        result.append(replacement)
    return result


def _latest_terminal_attempt(draft: dict[str, Any]) -> dict[str, Any] | None:
    attempts = list(draft.get("paymentAttempts") or [])
    return attempts[-1] if attempts else None


def _confirmation_payload(draft: dict[str, Any], attempt: dict[str, Any] | None, *, duplicate: bool = False) -> dict[str, Any]:
    order = draft.get("providerOrder")
    payment_status = attempt.get("status") if attempt else "not_started"
    if payment_status == "declined":
        status = "payment_declined"
    elif payment_status == "authorized_booking_failed":
        status = "booking_failed_after_payment"
    elif draft.get("status") == "finalized":
        status = "confirmed"
    elif draft.get("status") == "ticketing_pending":
        status = "ticketing_pending"
    else:
        status = draft.get("status", "pending")
    return {
        "bookingId": draft["id"],
        "status": status,
        "duplicate": duplicate,
        "payment": {
            "id": attempt.get("id") if attempt else None,
            "status": payment_status,
            "amount": attempt.get("amount") if attempt else draft["total"],
        },
        "order": order,
        "contact": {"email": draft["contact"]["email"]},
        "itineraries": draft["offerSnapshot"].get("itineraries", []),
        "passengers": [{"name": passenger["legalName"]["fullName"], "passengerType": passenger["passengerType"]} for passenger in draft.get("passengerSnapshots") or []],
        "polling": {"enabled": status == "ticketing_pending", "href": f"/api/payments?bookingId={draft['id']}"},
    }


def _audit_event(event_type: str, *, status: str | None = None, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return {"type": event_type, "status": status, "details": copy.deepcopy(dict(details or {}))}


def _update_with_audit(
    repository: InMemoryBookingDraftRepository | BookingDraftRepository,
    draft: dict[str, Any],
    changes: Mapping[str, Any],
    event_type: str,
    *,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    next_status = str(changes.get("status", draft.get("status")))
    audit_events = list(draft.get("auditEvents") or [])
    audit_events.append(_audit_event(event_type, status=next_status, details=details))
    return repository.update(draft["id"], {**dict(changes), "auditEvents": audit_events})

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
    full_document_number = _optional_text(raw.get("documentNumber") or raw.get("document_number") or raw.get("fullDocumentNumber") or raw.get("full_document_number"))
    last4 = _optional_text(raw.get("documentNumberLast4") or raw.get("document_number_last4"))
    if full_document_number is not None:
        errors[f"{prefix}.document.documentNumber"] = ["Full document numbers must be tokenized before submission; send documentNumberLast4 only."]
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
            status TEXT NOT NULL CHECK (status IN ('draft', 'submitted', 'expired', 'revalidating', 'price_validated', 'price_changed', 'price_change_accepted', 'unavailable', 'revalidation_failed', 'ticketing_pending', 'finalized')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            offer_snapshot TEXT NOT NULL,
            revalidation_snapshot TEXT,
            payment_attempts TEXT,
            provider_order TEXT,
            audit_events TEXT
        )
        """
    )
    for column in ("revalidation_snapshot TEXT", "payment_attempts TEXT", "provider_order TEXT", "audit_events TEXT"):
        try:
            connection.execute(f"ALTER TABLE flight_booking_drafts ADD COLUMN {column}")
        except sqlite3.OperationalError:
            pass
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
        "document": _parse_snapshot(row["document_snapshot"]),
    }
