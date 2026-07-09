"""Framework-neutral booking domain helpers for deterministic HBW tests.

The functions in this module intentionally model the failure-prone reservation,
payment, authorization, and cancellation decisions against the deterministic
SQLite fixture schema. They are small enough for unit tests while still using the
same database-backed inventory rules as the public API contract layer.
"""

from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from .audit import actor_from_user, record_audit_event, system_actor, user_actor
from .auth import canAdministerHotel, canCancelReservation, canPayReservation, canViewReservation
from .dto import admin_reservation_dto, guest_reservation_dto, payment_safe_dto
from .public_api import ApiResponse, error_response, success_response

MAX_GUESTS = 12
HOLD_EXPIRES_AT = "2031-06-09T23:59:00Z"
PAYMENT_PROVIDER = "fixture_gateway"

RESERVATION_TRANSITIONS = {
    "pending_payment": {"confirmed", "expired", "cancelled"},
    "confirmed": {"cancelled", "completed"},
    "expired": set(),
    "cancelled": set(),
    "completed": set(),
}
PAYMENT_TRANSITIONS = {
    None: {"authorized", "captured", "voided"},
    "authorized": {"captured", "voided"},
    "captured": {"refunded"},
    "voided": {"authorized"},
    "refunded": set(),
}
PAYMENT_PROVIDER_EVENTS = {
    "payment.authorized": "authorized",
    "payment.succeeded": "captured",
    "payment.failed": "voided",
}


@dataclass(frozen=True)
class StayDates:
    check_in: str
    check_out: str
    nights: int


class BookingConflict(Exception):
    """Raised when inventory or request identity conflicts are detected."""


class BookingValidationError(ValueError):
    """Raised for invalid utility/service inputs."""


def parse_stay_dates(check_in: str, check_out: str) -> StayDates:
    """Validate ISO stay dates and return the half-open night count."""

    try:
        parsed_check_in = date.fromisoformat(check_in)
        parsed_check_out = date.fromisoformat(check_out)
    except ValueError as exc:
        raise BookingValidationError("Dates must use YYYY-MM-DD format.") from exc
    nights = (parsed_check_out - parsed_check_in).days
    if nights <= 0:
        raise BookingValidationError("check_out must be after check_in.")
    return StayDates(check_in=check_in, check_out=check_out, nights=nights)


def format_money(amount_cents: int, currency: str = "USD") -> dict[str, Any]:
    """Return the shared money shape used by API payload assertions."""

    if amount_cents < 0:
        raise BookingValidationError("amount_cents must be non-negative.")
    if currency != currency.upper() or len(currency) != 3:
        raise BookingValidationError("currency must be a three-letter uppercase code.")
    return {"amountCents": amount_cents, "currency": currency, "formatted": f"{currency} {amount_cents / 100:.2f}"}


def validate_occupancy(adults: int, children: int, room_capacity: int) -> int:
    """Validate guest counts against platform and room capacity limits."""

    if adults < 1:
        raise BookingValidationError("At least one adult is required.")
    if children < 0:
        raise BookingValidationError("Children cannot be negative.")
    guests = adults + children
    if guests > MAX_GUESTS:
        raise BookingValidationError(f"Total guests must be less than or equal to {MAX_GUESTS}.")
    if guests > room_capacity:
        raise BookingValidationError("Guest count exceeds room capacity.")
    return guests


def calculate_total_cents(nightly_rate_cents: int, check_in: str, check_out: str) -> int:
    """Calculate the deterministic reservation total for a stay."""

    if nightly_rate_cents < 0:
        raise BookingValidationError("nightly_rate_cents must be non-negative.")
    return nightly_rate_cents * parse_stay_dates(check_in, check_out).nights


def assert_reservation_transition(current_status: str, next_status: str, *, via: str) -> None:
    """Validate centralized reservation lifecycle transitions."""

    allowed = RESERVATION_TRANSITIONS.get(current_status)
    if allowed is None or next_status not in allowed:
        raise BookingConflict(f"Reservation cannot transition from {current_status} to {next_status} via {via}.")


def assert_payment_transition(current_status: str | None, next_status: str, *, amount_cents: int, captured_cents: int = 0, refunded_cents: int = 0) -> None:
    """Validate centralized provider-derived payment lifecycle transitions and refund limits."""

    allowed = PAYMENT_TRANSITIONS.get(current_status)
    if allowed is None or next_status not in allowed:
        display_current = current_status if current_status is not None else "new"
        raise BookingConflict(f"Payment cannot transition from {display_current} to {next_status}.")
    if next_status == "refunded" and refunded_cents + amount_cents > captured_cents:
        raise BookingConflict("Refund amount cannot exceed captured payment amount.")


def assert_stay_completed(check_out: str, *, today: str) -> None:
    """Validate date-only completion rules for stays after checkout date."""

    try:
        checkout_date = date.fromisoformat(check_out)
        today_date = date.fromisoformat(today)
    except ValueError as exc:
        raise BookingValidationError("Dates must use YYYY-MM-DD format.") from exc
    if today_date <= checkout_date:
        raise BookingConflict("Reservation stay has not passed date-only completion rules.")


def available_room_ids(
    connection: sqlite3.Connection,
    hotel_id: str,
    room_type_id: str,
    check_in: str,
    check_out: str,
) -> list[str]:
    """Return active room IDs not blocked by closures, holds, or reservations."""

    parse_stay_dates(check_in, check_out)
    rows = connection.execute(
        """
        SELECT room.id
        FROM rooms AS room
        WHERE room.room_type_id = ?
          AND room.status = 'active'
          AND NOT EXISTS (
            SELECT 1 FROM availability_blocks AS block
            WHERE block.hotel_id = ?
              AND block.starts_on < ?
              AND block.ends_on > ?
              AND (
                block.block_type = 'hotel_closure'
                OR block.room_type_id = ?
                OR block.room_id = room.id
              )
          )
          AND NOT EXISTS (
            SELECT 1 FROM reservations AS reservation
            WHERE reservation.room_id = room.id
              AND reservation.check_in < ?
              AND reservation.check_out > ?
              AND reservation.status IN ('confirmed', 'pending_payment')
          )
        ORDER BY room.id
        """,
        (room_type_id, hotel_id, check_out, check_in, room_type_id, check_out, check_in),
    ).fetchall()
    return [row[0] for row in rows]


def create_pending_reservation(
    database_path: str,
    *,
    reservation_id: str,
    hotel_id: str,
    room_type_id: str,
    user_id: str | None,
    guest_email: str,
    guest_name: str,
    check_in: str,
    check_out: str,
    adults: int,
    children: int,
    created_at: str = "2031-04-01T10:00:00Z",
) -> dict[str, Any]:
    """Create a pending reservation transactionally against current inventory."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
            if duplicate is not None:
                connection.rollback()
                return _reservation_payload(duplicate, duplicate_request=True)

            room_type = connection.execute(
                "SELECT * FROM room_types WHERE id = ? AND hotel_id = ?",
                (room_type_id, hotel_id),
            ).fetchone()
            if room_type is None:
                raise BookingValidationError("Unknown room type for hotel.")
            validate_occupancy(adults, children, room_type["capacity"])
            total_cents = calculate_total_cents(room_type["nightly_rate_cents"], check_in, check_out)
            room_ids = available_room_ids(connection, hotel_id, room_type_id, check_in, check_out)
            if not room_ids:
                raise BookingConflict("No rooms available for the requested stay.")

            checkout_type = "authenticated" if user_id else "guest"
            connection.execute(
                """
                INSERT INTO reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reservation_id,
                    hotel_id,
                    room_type_id,
                    room_ids[0],
                    user_id,
                    guest_email,
                    guest_name,
                    check_in,
                    check_out,
                    "pending_payment",
                    checkout_type,
                    total_cents,
                    room_type["currency"],
                    created_at,
                    None,
                    HOLD_EXPIRES_AT,
                    _generate_confirmation_secret(),
                ),
            )
            record_audit_event(
                connection,
                actor=user_actor(user_id),
                event_type="reservation.created",
                entity_type="reservation",
                entity_id=reservation_id,
                metadata={
                    "hotelId": hotel_id,
                    "roomTypeId": room_type_id,
                    "roomId": room_ids[0],
                    "checkoutType": checkout_type,
                    "totalCents": total_cents,
                    "currency": room_type["currency"],
                    "auditWritePolicy": "best effort; reservation correctness wins",
                },
                created_at=created_at,
            )
            connection.commit()
            row = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
            return _reservation_payload(row, duplicate_request=False)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def expire_pending_reservation(database_path: str, reservation_id: str, *, now: str) -> bool:
    """Expire a pending payment hold once its deterministic expiry has passed."""

    with _connect(database_path) as connection:
        row = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
        if row is None or row["status"] != "pending_payment" or row["expires_at"] is None or now <= row["expires_at"]:
            return False
        assert_reservation_transition(row["status"], "expired", via="expiration_service")
        connection.execute("UPDATE reservations SET status = 'expired' WHERE id = ?", (reservation_id,))
        record_audit_event(
            connection,
            actor=system_actor(),
            event_type="reservation.expired",
            entity_type="reservation",
            entity_id=reservation_id,
            metadata={"auditWritePolicy": "best effort; expiration correctness wins"},
            created_at=now,
        )
        connection.commit()
        return True


def record_payment_webhook(
    database_path: str,
    *,
    provider_reference: str,
    reservation_id: str,
    amount_cents: int,
    currency: str = "USD",
    event_type: str = "payment.succeeded",
    created_at: str = "2031-04-01T10:05:00Z",
    failure_message: str | None = None,
) -> dict[str, Any]:
    """Persist provider payment events idempotently only when lifecycle rules allow them."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
            if reservation is None:
                raise BookingValidationError("Unknown reservation.")
            status = PAYMENT_PROVIDER_EVENTS.get(event_type)
            if status is None:
                raise BookingValidationError("Unsupported payment provider event.")

            existing = connection.execute(
                "SELECT * FROM payment_records WHERE provider = ? AND provider_reference = ?",
                (PAYMENT_PROVIDER, provider_reference),
            ).fetchone()

            if amount_cents != reservation["total_cents"] or currency != reservation["currency"]:
                if existing is None:
                    assert_payment_transition(None, "voided", amount_cents=amount_cents)
                    connection.execute(
                        """
                        INSERT INTO payment_records
                        (id, reservation_id, provider, provider_reference, amount_cents, currency, status, created_at, failure_message)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            f"pay_{provider_reference}",
                            reservation_id,
                            PAYMENT_PROVIDER,
                            provider_reference,
                            amount_cents,
                            currency,
                            "voided",
                            created_at,
                            "Payment amount or currency did not match the reservation total.",
                        ),
                    )
                    record_audit_event(
                        connection,
                        actor=system_actor("webhook"),
                        event_type="payment.failed",
                        entity_type="payment",
                        entity_id=f"pay_{provider_reference}",
                        metadata={
                            "reservationId": reservation_id,
                            "provider": PAYMENT_PROVIDER,
                            "amountCents": amount_cents,
                            "currency": currency,
                            "failureReason": "amount_or_currency_mismatch",
                            "auditWritePolicy": "best effort; payment correctness wins",
                        },
                        created_at=created_at,
                    )
                    connection.commit()
                else:
                    connection.rollback()
                raise BookingValidationError("Payment amount or currency does not match reservation total.")

            if existing is not None and existing["status"] == status:
                connection.rollback()
                return {"duplicate": True, "paymentId": existing["id"], "status": existing["status"]}

            if status == "captured":
                assert_reservation_transition(reservation["status"], "confirmed", via="payment_webhook")
            elif reservation["status"] != "pending_payment":
                raise BookingConflict(f"Payment event {event_type} is invalid for {reservation['status']} reservation.")

            safe_failure_message = _safe_payment_failure_message(status, failure_message)
            if existing is not None:
                current_payment_status = existing["status"]
                if current_payment_status == "refunded":
                    current_payment_status = "captured"
                assert_payment_transition(current_payment_status, status, amount_cents=amount_cents)
                connection.execute(
                    "UPDATE payment_records SET status = ?, amount_cents = ?, currency = ?, failure_message = ? WHERE id = ?",
                    (status, amount_cents, currency, safe_failure_message, existing["id"]),
                )
                payment_id = existing["id"]
            else:
                prior_payment = connection.execute(
                    """
                    SELECT * FROM payment_records
                    WHERE reservation_id = ? AND status IN ('authorized', 'captured', 'refunded')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (reservation_id,),
                ).fetchone()
                current_payment_status = prior_payment["status"] if prior_payment is not None else None
                if current_payment_status == "refunded":
                    current_payment_status = "captured"
                assert_payment_transition(current_payment_status, status, amount_cents=amount_cents)
                payment_id = f"pay_{provider_reference}"
                connection.execute(
                    """
                    INSERT INTO payment_records
                    (id, reservation_id, provider, provider_reference, amount_cents, currency, status, created_at, failure_message)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payment_id,
                        reservation_id,
                        PAYMENT_PROVIDER,
                        provider_reference,
                        amount_cents,
                        currency,
                        status,
                        created_at,
                        safe_failure_message,
                    ),
                )

            if status == "captured":
                connection.execute(
                    "UPDATE reservations SET status = 'confirmed', expires_at = NULL WHERE id = ?",
                    (reservation_id,),
                )
                record_audit_event(
                    connection,
                    actor=system_actor("webhook"),
                    event_type="reservation.confirmed",
                    entity_type="reservation",
                    entity_id=reservation_id,
                    metadata={"paymentId": payment_id, "auditWritePolicy": "best effort; payment correctness wins"},
                    created_at=created_at,
                )
            record_audit_event(
                connection,
                actor=system_actor("webhook"),
                event_type=event_type,
                entity_type="payment",
                entity_id=payment_id,
                metadata={
                    "reservationId": reservation_id,
                    "provider": PAYMENT_PROVIDER,
                    "amountCents": amount_cents,
                    "currency": currency,
                    "status": status,
                    "auditWritePolicy": "best effort; payment correctness wins",
                },
                created_at=created_at,
            )
            connection.commit()
            return {"duplicate": False, "paymentId": payment_id, "status": status}
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def get_reservation_for_user(database_path: str, reservation_id: str, user_id: str) -> dict[str, Any]:
    """Return a guest-safe reservation only when the authenticated user owns it."""

    with _connect(database_path) as connection:
        row = _reservation_by_id(connection, reservation_id)
        actor = _actor_by_id(connection, user_id)
        if row is None:
            raise LookupError("Reservation not found.")
        if not canViewReservation(actor, row) or actor is None or actor["role"] == "admin":
            raise PermissionError("Reservation access denied.")
        return guest_reservation_dto(row)


def complete_reservation_stay(
    database_path: str,
    reservation_id: str,
    *,
    today: str,
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Mark a confirmed reservation completed after the stay has passed by date-only rules."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _reservation_by_id(connection, reservation_id)
            if row is None:
                raise LookupError("Reservation not found.")
            assert_stay_completed(row["check_out"], today=today)
            assert_reservation_transition(row["status"], "completed", via="completion_service")
            connection.execute("UPDATE reservations SET status = 'completed' WHERE id = ?", (reservation_id,))
            record_audit_event(
                connection,
                actor=system_actor(),
                event_type="reservation.completed",
                entity_type="reservation",
                entity_id=reservation_id,
                metadata={"auditWritePolicy": "best effort; completion correctness wins"},
                created_at=completed_at or f"{today}T00:00:00Z",
            )
            connection.commit()
            completed = _reservation_by_id(connection, reservation_id)
            return guest_reservation_dto(completed)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def lookup_guest_reservation(database_path: str, reservation_id: str, confirmation_secret: str) -> dict[str, Any]:
    """Return guest reservation details only with the non-guessable confirmation secret."""

    with _connect(database_path) as connection:
        row = _reservation_by_id(connection, reservation_id)
        if row is None or row["checkout_type"] != "guest" or not secrets.compare_digest(row["confirmation_secret"], confirmation_secret):
            raise LookupError("Reservation not found.")
        return guest_reservation_dto(row)


def cancel_reservation(
    database_path: str,
    *,
    reservation_id: str,
    user_id: str,
    cancelled_at: str = "2031-04-02T10:00:00Z",
) -> dict[str, Any]:
    """Cancel an eligible authenticated reservation and create a refund if paid."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
            if row is None:
                raise LookupError("Reservation not found.")
            actor = _actor_by_id(connection, user_id)
            if not canViewReservation(actor, row) or actor is None or actor["role"] == "admin":
                raise PermissionError("Reservation access denied.")
            if row["status"] not in {"confirmed", "pending_payment"}:
                raise BookingConflict("Reservation is not eligible for cancellation.")
            if not canCancelReservation(actor, row):
                raise PermissionError("Reservation cancellation denied.")
            assert_reservation_transition(row["status"], "cancelled", via="cancellation_service")

            connection.execute(
                "UPDATE reservations SET status = 'cancelled', cancelled_at = ? WHERE id = ?",
                (cancelled_at, reservation_id),
            )
            refund_payload = None
            payment = connection.execute(
                """
                SELECT * FROM payment_records
                WHERE reservation_id = ? AND status = 'captured'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (reservation_id,),
            ).fetchone()
            if payment is not None:
                refunded_cents = connection.execute(
                    """
                    SELECT COALESCE(SUM(amount_cents), 0) FROM refunds
                    WHERE payment_record_id = ? AND status = 'succeeded'
                    """,
                    (payment["id"],),
                ).fetchone()[0]
                assert_payment_transition(
                    "captured",
                    "refunded",
                    amount_cents=payment["amount_cents"],
                    captured_cents=payment["amount_cents"],
                    refunded_cents=refunded_cents,
                )
                refund_id = f"ref_{payment['id']}"
                connection.execute(
                    "INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?)",
                    (refund_id, payment["id"], payment["amount_cents"], "Guest cancelled eligible reservation.", "succeeded", cancelled_at),
                )
                connection.execute("UPDATE payment_records SET status = 'refunded' WHERE id = ?", (payment["id"],))
                record_audit_event(
                    connection,
                    actor=actor_from_user(actor),
                    event_type="refund.created",
                    entity_type="refund",
                    entity_id=refund_id,
                    metadata={
                        "reservationId": reservation_id,
                        "paymentId": payment["id"],
                        "amountCents": payment["amount_cents"],
                        "currency": payment["currency"],
                        "auditWritePolicy": "best effort; cancellation correctness wins",
                    },
                    created_at=cancelled_at,
                )
                refund_payload = {"id": refund_id, "amount": format_money(payment["amount_cents"], payment["currency"]), "status": "succeeded"}
            record_audit_event(
                connection,
                actor=actor_from_user(actor),
                event_type="reservation.cancelled",
                entity_type="reservation",
                entity_id=reservation_id,
                metadata={"refundId": refund_payload["id"] if refund_payload else None, "auditWritePolicy": "best effort; cancellation correctness wins"},
                created_at=cancelled_at,
            )
            connection.commit()
            cancelled = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
            return guest_reservation_dto(cancelled, refund=refund_payload)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def create_payment_intent(
    database_path: str,
    *,
    payment_id: str,
    reservation_id: str,
    user_id: str,
    provider_reference: str,
    created_at: str = "2031-04-01T10:03:00Z",
) -> dict[str, Any]:
    """Create an authorized fixture payment intent and audit it without provider secrets."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            reservation = _reservation_by_id(connection, reservation_id)
            actor = _actor_by_id(connection, user_id)
            if reservation is None:
                raise LookupError("Reservation not found.")
            if not canPayReservation(actor, reservation):
                raise PermissionError("Payment access denied.")
            prior_payment = connection.execute(
                "SELECT * FROM payment_records WHERE reservation_id = ? AND status IN ('authorized', 'captured', 'refunded') ORDER BY created_at DESC LIMIT 1",
                (reservation_id,),
            ).fetchone()
            assert_payment_transition(prior_payment["status"] if prior_payment is not None else None, "authorized", amount_cents=reservation["total_cents"])
            existing = connection.execute("SELECT * FROM payment_records WHERE id = ?", (payment_id,)).fetchone()
            if existing is not None:
                connection.rollback()
                return payment_safe_dto(existing)
            connection.execute(
                """
                INSERT INTO payment_records
                (id, reservation_id, provider, provider_reference, amount_cents, currency, status, created_at, failure_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment_id,
                    reservation_id,
                    PAYMENT_PROVIDER,
                    provider_reference,
                    reservation["total_cents"],
                    reservation["currency"],
                    "authorized",
                    created_at,
                    None,
                ),
            )
            record_audit_event(
                connection,
                actor=actor_from_user(actor),
                event_type="payment_intent.created",
                entity_type="payment",
                entity_id=payment_id,
                metadata={
                    "reservationId": reservation_id,
                    "provider": PAYMENT_PROVIDER,
                    "amountCents": reservation["total_cents"],
                    "currency": reservation["currency"],
                    "auditWritePolicy": "best effort; payment correctness wins",
                },
                created_at=created_at,
            )
            connection.commit()
            payment = connection.execute("SELECT * FROM payment_records WHERE id = ?", (payment_id,)).fetchone()
            return payment_safe_dto(payment)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise



class FixturePaymentProvider:
    """Deterministic provider abstraction that returns only client-safe values."""

    provider = PAYMENT_PROVIDER

    def create_intent(self, *, reservation_id: str, amount_cents: int, currency: str) -> dict[str, Any]:
        provider_reference = f"fx_intent_{reservation_id}"
        return {
            "provider": self.provider,
            "providerReference": provider_reference,
            "clientSecret": f"cs_test_{provider_reference}",
            "amount": format_money(amount_cents, currency),
        }

    def event_for_confirmation(self, *, provider_reference: str, outcome: str) -> dict[str, str]:
        events = {
            "succeeded": "payment.succeeded",
            "failed": "payment.failed",
            "requires_payment_method": "payment.failed",
        }
        return {"providerReference": provider_reference, "eventType": events.get(outcome, outcome)}


def _safe_payment_failure_message(status: str, failure_message: str | None) -> str | None:
    if status != "voided":
        return None
    if failure_message and 0 < len(failure_message) <= 160:
        lowered = failure_message.lower()
        sensitive_terms = ("card", "cvc", "cvv", "pan", "secret", "token")
        if not any(term in lowered for term in sensitive_terms):
            return failure_message
    return "Payment was not authorized. Please try another payment method."


def _payment_client_payload(row: sqlite3.Row, *, client_secret: str | None = None) -> dict[str, Any]:
    payload = payment_safe_dto(row)
    payload["client"] = {"clientSecret": client_secret} if client_secret else {}
    return payload


def create_payment_authorization(
    database_path: str,
    *,
    reservation_id: str,
    user_id: str,
    amount_cents: int,
    currency: str = "USD",
    created_at: str = "2031-04-01T10:03:00Z",
    provider: FixturePaymentProvider | None = None,
) -> dict[str, Any]:
    """Create a provider-backed payment intent only for a pending reservation total."""

    provider = provider or FixturePaymentProvider()
    with _connect(database_path) as connection:
        reservation = _reservation_by_id(connection, reservation_id)
        actor = _actor_by_id(connection, user_id)
        if reservation is None:
            raise LookupError("Reservation not found.")
        if not canPayReservation(actor, reservation):
            raise PermissionError("Payment access denied.")
        if reservation["expires_at"] is not None and created_at > reservation["expires_at"]:
            raise BookingConflict("Reservation payment window has expired.")
        if amount_cents != reservation["total_cents"] or currency != reservation["currency"]:
            raise BookingValidationError("Payment amount or currency does not match reservation total.")
        provider_intent = provider.create_intent(
            reservation_id=reservation_id,
            amount_cents=reservation["total_cents"],
            currency=reservation["currency"],
        )

    payment = create_payment_intent(
        database_path,
        payment_id=f"pay_{provider_intent['providerReference']}",
        reservation_id=reservation_id,
        user_id=user_id,
        provider_reference=provider_intent["providerReference"],
        created_at=created_at,
    )
    with _connect(database_path) as connection:
        row = connection.execute("SELECT * FROM payment_records WHERE id = ?", (payment["id"],)).fetchone()
        return _payment_client_payload(row, client_secret=provider_intent["clientSecret"])


def confirm_payment_authorization(
    database_path: str,
    *,
    reservation_id: str,
    user_id: str,
    provider_reference: str,
    amount_cents: int,
    currency: str = "USD",
    outcome: str = "succeeded",
    failure_message: str | None = None,
    confirmed_at: str = "2031-04-01T10:05:00Z",
    provider: FixturePaymentProvider | None = None,
) -> dict[str, Any]:
    """Confirm or reconcile a provider payment status for an owned reservation."""

    provider = provider or FixturePaymentProvider()
    with _connect(database_path) as connection:
        reservation = _reservation_by_id(connection, reservation_id)
        actor = _actor_by_id(connection, user_id)
        if reservation is None:
            raise LookupError("Reservation not found.")
        if actor is None or not canViewReservation(actor, reservation) or actor["role"] == "admin":
            raise PermissionError("Payment access denied.")
    provider_event = provider.event_for_confirmation(provider_reference=provider_reference, outcome=outcome)
    result = record_payment_webhook(
        database_path,
        provider_reference=provider_event["providerReference"],
        reservation_id=reservation_id,
        amount_cents=amount_cents,
        currency=currency,
        event_type=provider_event["eventType"],
        created_at=confirmed_at,
        failure_message=failure_message,
    )
    with _connect(database_path) as connection:
        payment = connection.execute("SELECT * FROM payment_records WHERE id = ?", (result["paymentId"],)).fetchone()
        reservation = _reservation_by_id(connection, reservation_id)
        return {"duplicate": result["duplicate"], "payment": payment_safe_dto(payment), "reservation": guest_reservation_dto(reservation)}

def admin_update_hotel(
    database_path: str,
    *,
    hotel_id: str,
    user_id: str,
    changes: dict[str, Any],
    updated_at: str = "2031-04-03T10:00:00Z",
) -> dict[str, Any]:
    """Update safe hotel admin fields and record a blocking audit event."""

    allowed = {"name", "city", "country", "address", "star_rating", "is_searchable", "description"}
    return _admin_update_entity(
        database_path,
        table="hotels",
        entity_type="hotel",
        entity_id=hotel_id,
        user_id=user_id,
        changes=changes,
        allowed_fields=allowed,
        hotel_id=hotel_id,
        updated_at=updated_at,
    )


def admin_update_room_type(
    database_path: str,
    *,
    room_type_id: str,
    user_id: str,
    changes: dict[str, Any],
    updated_at: str = "2031-04-03T10:05:00Z",
) -> dict[str, Any]:
    """Update safe room-type admin fields and record a blocking audit event."""

    allowed = {"name", "capacity", "nightly_rate_cents", "currency", "description"}
    with _connect(database_path) as connection:
        row = connection.execute("SELECT * FROM room_types WHERE id = ?", (room_type_id,)).fetchone()
        if row is None:
            raise LookupError("Room type not found.")
        hotel_id = row["hotel_id"]
    return _admin_update_entity(
        database_path,
        table="room_types",
        entity_type="room_type",
        entity_id=room_type_id,
        user_id=user_id,
        changes=changes,
        allowed_fields=allowed,
        hotel_id=hotel_id,
        updated_at=updated_at,
    )


def admin_update_room(
    database_path: str,
    *,
    room_id: str,
    user_id: str,
    changes: dict[str, Any],
    updated_at: str = "2031-04-03T10:10:00Z",
) -> dict[str, Any]:
    """Update safe physical-room admin fields and record a blocking audit event."""

    allowed = {"room_number", "floor", "status"}
    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT room.*, room_type.hotel_id
            FROM rooms AS room
            JOIN room_types AS room_type ON room_type.id = room.room_type_id
            WHERE room.id = ?
            """,
            (room_id,),
        ).fetchone()
        if row is None:
            raise LookupError("Room not found.")
        hotel_id = row["hotel_id"]
    return _admin_update_entity(
        database_path,
        table="rooms",
        entity_type="room",
        entity_id=room_id,
        user_id=user_id,
        changes=changes,
        allowed_fields=allowed,
        hotel_id=hotel_id,
        updated_at=updated_at,
    )


def admin_create_availability_block(
    database_path: str,
    *,
    block_id: str,
    hotel_id: str,
    user_id: str,
    block_type: str,
    starts_on: str,
    ends_on: str,
    reason: str,
    room_type_id: str | None = None,
    room_id: str | None = None,
    created_at: str = "2031-04-03T10:15:00Z",
) -> dict[str, Any]:
    """Create an availability block with a blocking admin audit record."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            actor = _actor_by_id(connection, user_id)
            if not canAdministerHotel(actor, hotel_id):
                raise PermissionError("Admin access required.")
            connection.execute(
                "INSERT INTO availability_blocks VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (block_id, hotel_id, room_type_id, room_id, block_type, starts_on, ends_on, reason),
            )
            record_audit_event(
                connection,
                actor=actor_from_user(actor),
                event_type="availability_block.created",
                entity_type="availability_block",
                entity_id=block_id,
                metadata={
                    "hotelId": hotel_id,
                    "roomTypeId": room_type_id,
                    "roomId": room_id,
                    "blockType": block_type,
                    "startsOn": starts_on,
                    "endsOn": ends_on,
                    "reason": reason,
                    "auditWritePolicy": "blocking for admin inventory mutations",
                },
                created_at=created_at,
                block_on_failure=True,
            )
            connection.commit()
            return dict(connection.execute("SELECT * FROM availability_blocks WHERE id = ?", (block_id,)).fetchone())
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def admin_delete_availability_block(
    database_path: str,
    *,
    block_id: str,
    user_id: str,
    deleted_at: str = "2031-04-03T10:20:00Z",
) -> bool:
    """Delete an availability block with a blocking admin audit record."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM availability_blocks WHERE id = ?", (block_id,)).fetchone()
            if row is None:
                raise LookupError("Availability block not found.")
            actor = _actor_by_id(connection, user_id)
            if not canAdministerHotel(actor, row["hotel_id"]):
                raise PermissionError("Admin access required.")
            connection.execute("DELETE FROM availability_blocks WHERE id = ?", (block_id,))
            record_audit_event(
                connection,
                actor=actor_from_user(actor),
                event_type="availability_block.deleted",
                entity_type="availability_block",
                entity_id=block_id,
                metadata={
                    "hotelId": row["hotel_id"],
                    "roomTypeId": row["room_type_id"],
                    "roomId": row["room_id"],
                    "blockType": row["block_type"],
                    "startsOn": row["starts_on"],
                    "endsOn": row["ends_on"],
                    "auditWritePolicy": "blocking for admin inventory mutations",
                },
                created_at=deleted_at,
                block_on_failure=True,
            )
            connection.commit()
            return True
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def booking_api_create_reservation(database_path: str, payload: dict[str, Any]) -> ApiResponse:
    """HTTP-shaped create reservation adapter for response contract tests."""

    required = ["reservationId", "hotelId", "roomTypeId", "guestEmail", "guestName", "checkIn", "checkOut", "adults", "children"]
    missing = [field for field in required if field not in payload]
    if missing:
        return error_response(400, "validation_error", "Request body failed validation.", fields={field: ["Field is required."] for field in missing})
    try:
        reservation = create_pending_reservation(
            database_path,
            reservation_id=payload["reservationId"],
            hotel_id=payload["hotelId"],
            room_type_id=payload["roomTypeId"],
            user_id=payload.get("userId"),
            guest_email=payload["guestEmail"],
            guest_name=payload["guestName"],
            check_in=payload["checkIn"],
            check_out=payload["checkOut"],
            adults=int(payload["adults"]),
            children=int(payload["children"]),
        )
    except BookingConflict as exc:
        return error_response(409, "inventory_conflict", str(exc))
    except BookingValidationError as exc:
        return error_response(400, "validation_error", str(exc))
    status_code = 200 if reservation.get("duplicateRequest") else 201
    return success_response(reservation, status_code=status_code)


def booking_api_get_reservation(database_path: str, reservation_id: str, user_id: str) -> ApiResponse:
    """HTTP-shaped get reservation adapter for authorization contract tests."""

    try:
        return success_response(get_reservation_for_user(database_path, reservation_id, user_id))
    except PermissionError:
        return error_response(403, "forbidden", "You are not authorized to access this reservation.")
    except LookupError as exc:
        return error_response(404, "not_found", str(exc))


def booking_api_cancel_reservation(database_path: str, reservation_id: str, user_id: str) -> ApiResponse:
    """HTTP-shaped cancellation adapter for response contract tests."""

    try:
        return success_response(cancel_reservation(database_path, reservation_id=reservation_id, user_id=user_id))
    except PermissionError:
        return error_response(403, "forbidden", "You are not authorized to cancel this reservation.")
    except LookupError as exc:
        return error_response(404, "not_found", str(exc))
    except BookingConflict as exc:
        return error_response(409, "reservation_conflict", str(exc))


def booking_api_get_guest_reservation(database_path: str, reservation_id: str, confirmation_secret: str) -> ApiResponse:
    """HTTP-shaped guest confirmation lookup guarded by a non-guessable secret."""

    try:
        return success_response(lookup_guest_reservation(database_path, reservation_id, confirmation_secret))
    except LookupError:
        return error_response(404, "not_found", "Reservation not found.")


def booking_api_create_payment_intent(database_path: str, payload: dict[str, Any], user_id: str) -> ApiResponse:
    """POST /api/payments/create-intent adapter returning only safe provider client data."""

    required = ["reservationId", "amountCents"]
    missing = [field for field in required if field not in payload]
    if missing:
        return error_response(400, "validation_error", "Request body failed validation.", fields={field: ["Field is required."] for field in missing})
    try:
        payment = create_payment_authorization(
            database_path,
            reservation_id=payload["reservationId"],
            user_id=user_id,
            amount_cents=int(payload["amountCents"]),
            currency=payload.get("currency", "USD"),
            created_at=payload.get("createdAt", "2031-04-01T10:03:00Z"),
        )
        return success_response({"payment": payment}, status_code=201)
    except PermissionError:
        return error_response(403, "forbidden", "You are not authorized to pay for this reservation.")
    except LookupError as exc:
        return error_response(404, "not_found", str(exc))
    except BookingValidationError:
        return error_response(400, "validation_error", "Payment could not be processed.")
    except BookingConflict as exc:
        return error_response(409, "payment_conflict", str(exc))


def booking_api_confirm_payment(database_path: str, payload: dict[str, Any], user_id: str) -> ApiResponse:
    """POST /api/payments/confirm adapter for client redirects and reconciliation."""

    required = ["reservationId", "providerReference", "amountCents"]
    missing = [field for field in required if field not in payload]
    if missing:
        return error_response(400, "validation_error", "Request body failed validation.", fields={field: ["Field is required."] for field in missing})
    try:
        result = confirm_payment_authorization(
            database_path,
            reservation_id=payload["reservationId"],
            user_id=user_id,
            provider_reference=payload["providerReference"],
            amount_cents=int(payload["amountCents"]),
            currency=payload.get("currency", "USD"),
            outcome=payload.get("outcome", "succeeded"),
            failure_message=payload.get("failureMessage"),
            confirmed_at=payload.get("confirmedAt", "2031-04-01T10:05:00Z"),
        )
        return success_response(result, status_code=200 if result["duplicate"] else 201)
    except PermissionError:
        return error_response(403, "forbidden", "You are not authorized to pay for this reservation.")
    except LookupError as exc:
        return error_response(404, "not_found", str(exc))
    except BookingValidationError:
        return error_response(400, "validation_error", "Payment could not be processed.")
    except BookingConflict as exc:
        return error_response(409, "payment_conflict", str(exc))


def booking_api_record_payment(database_path: str, payload: dict[str, Any], user_id: str) -> ApiResponse:
    """Backward-compatible payment adapter that authorizes caller and hides provider secrets."""

    return booking_api_confirm_payment(database_path, {**payload, "outcome": payload.get("eventType", "payment.succeeded")}, user_id)


def booking_api_handle_provider_event(database_path: str, payload: dict[str, Any]) -> ApiResponse:
    """Provider event handler for asynchronous webhook-style payment events."""

    required = ["reservationId", "providerReference", "amountCents", "eventType"]
    missing = [field for field in required if field not in payload]
    if missing:
        return error_response(400, "validation_error", "Request body failed validation.", fields={field: ["Field is required."] for field in missing})
    try:
        result = record_payment_webhook(
            database_path,
            provider_reference=payload["providerReference"],
            reservation_id=payload["reservationId"],
            amount_cents=int(payload["amountCents"]),
            currency=payload.get("currency", "USD"),
            event_type=payload["eventType"],
            created_at=payload.get("createdAt", "2031-04-01T10:05:00Z"),
            failure_message=payload.get("failureMessage"),
        )
    except BookingValidationError:
        return error_response(400, "validation_error", "Payment could not be processed.")
    except BookingConflict as exc:
        return error_response(409, "payment_conflict", str(exc))
    with _connect(database_path) as connection:
        payment = connection.execute("SELECT * FROM payment_records WHERE id = ?", (result["paymentId"],)).fetchone()
        reservation = _reservation_by_id(connection, payload["reservationId"])
        return success_response(
            {"duplicate": result["duplicate"], "payment": payment_safe_dto(payment), "reservation": guest_reservation_dto(reservation)},
            status_code=200 if result["duplicate"] else 201,
        )


def booking_api_admin_get_reservation(database_path: str, reservation_id: str, user_id: str) -> ApiResponse:
    """HTTP-shaped admin reservation endpoint guarded by hotel administration auth."""

    with _connect(database_path) as connection:
        row = _reservation_by_id(connection, reservation_id)
        actor = _actor_by_id(connection, user_id)
        if row is None:
            return error_response(404, "not_found", "Reservation not found.")
        if not canAdministerHotel(actor, row["hotel_id"]):
            return error_response(403, "forbidden", "Admin access required.")
        return success_response(admin_reservation_dto(row))


def _admin_update_entity(
    database_path: str,
    *,
    table: str,
    entity_type: str,
    entity_id: str,
    user_id: str,
    changes: dict[str, Any],
    allowed_fields: set[str],
    hotel_id: str,
    updated_at: str,
) -> dict[str, Any]:
    unknown = sorted(set(changes) - allowed_fields)
    if unknown:
        raise BookingValidationError(f"Unsupported admin update field: {unknown[0]}.")
    if not changes:
        raise BookingValidationError("At least one change is required.")
    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            actor = _actor_by_id(connection, user_id)
            if not canAdministerHotel(actor, hotel_id):
                raise PermissionError("Admin access required.")
            current = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone()
            if current is None:
                raise LookupError(f"{entity_type.replace('_', ' ').title()} not found.")
            assignments = ", ".join(f"{field} = ?" for field in changes)
            connection.execute(f"UPDATE {table} SET {assignments} WHERE id = ?", (*changes.values(), entity_id))
            record_audit_event(
                connection,
                actor=actor_from_user(actor),
                event_type=f"{entity_type}.updated",
                entity_type=entity_type,
                entity_id=entity_id,
                metadata={
                    "hotelId": hotel_id,
                    "changedFields": sorted(changes),
                    "auditWritePolicy": "blocking for admin inventory mutations",
                },
                created_at=updated_at,
                block_on_failure=True,
            )
            connection.commit()
            return dict(connection.execute(f"SELECT * FROM {table} WHERE id = ?", (entity_id,)).fetchone())
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _actor_by_id(connection: sqlite3.Connection, user_id: str | None) -> sqlite3.Row | None:
    if user_id is None:
        return None
    return connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def _reservation_by_id(connection: sqlite3.Connection, reservation_id: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()


def _generate_confirmation_secret() -> str:
    return f"cnf_{secrets.token_urlsafe(24)}"


def _reservation_payload(row: sqlite3.Row, *, duplicate_request: bool = False) -> dict[str, Any]:
    return guest_reservation_dto(row, duplicate_request=duplicate_request)
