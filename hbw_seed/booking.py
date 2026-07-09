"""Framework-neutral booking domain helpers for deterministic HBW tests.

The functions in this module intentionally model the failure-prone reservation,
payment, authorization, and cancellation decisions against the deterministic
SQLite fixture schema. They are small enough for unit tests while still using the
same database-backed inventory rules as the public API contract layer.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

from .public_api import ApiResponse, error_response, success_response

MAX_GUESTS = 12
HOLD_EXPIRES_AT = "2031-06-09T23:59:00Z"
PAYMENT_PROVIDER = "fixture_gateway"


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
                INSERT INTO reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
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
        connection.execute("UPDATE reservations SET status = 'expired' WHERE id = ?", (reservation_id,))
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
) -> dict[str, Any]:
    """Persist successful/failed payment provider events idempotently."""

    with _connect(database_path) as connection:
        existing = connection.execute(
            "SELECT * FROM payment_records WHERE provider = ? AND provider_reference = ?",
            (PAYMENT_PROVIDER, provider_reference),
        ).fetchone()
        if existing is not None:
            return {"duplicate": True, "paymentId": existing["id"], "status": existing["status"]}

        reservation = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
        if reservation is None:
            raise BookingValidationError("Unknown reservation.")
        if amount_cents != reservation["total_cents"] or currency != reservation["currency"]:
            connection.execute(
                """
                INSERT INTO payment_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                ),
            )
            connection.commit()
            raise BookingValidationError("Payment amount or currency does not match reservation total.")

        status = "captured" if event_type == "payment.succeeded" else "voided"
        connection.execute(
            """
            INSERT INTO payment_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"pay_{provider_reference}",
                reservation_id,
                PAYMENT_PROVIDER,
                provider_reference,
                amount_cents,
                currency,
                status,
                created_at,
            ),
        )
        if status == "captured":
            connection.execute(
                "UPDATE reservations SET status = 'confirmed', expires_at = NULL WHERE id = ?",
                (reservation_id,),
            )
        connection.commit()
        return {"duplicate": False, "paymentId": f"pay_{provider_reference}", "status": status}


def get_reservation_for_user(database_path: str, reservation_id: str, user_id: str) -> dict[str, Any]:
    """Return a reservation only when the authenticated user owns it."""

    with _connect(database_path) as connection:
        row = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
        if row is None:
            raise LookupError("Reservation not found.")
        if row["user_id"] != user_id:
            raise PermissionError("Reservation belongs to another user.")
        return _reservation_payload(row)


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
            if row["user_id"] != user_id:
                raise PermissionError("Reservation belongs to another user.")
            if row["status"] not in {"confirmed", "pending_payment"}:
                raise BookingConflict("Reservation is not eligible for cancellation.")

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
                refund_id = f"ref_{payment['id']}"
                connection.execute(
                    "INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?)",
                    (refund_id, payment["id"], payment["amount_cents"], "Guest cancelled eligible reservation.", "succeeded", cancelled_at),
                )
                connection.execute("UPDATE payment_records SET status = 'refunded' WHERE id = ?", (payment["id"],))
                refund_payload = {"id": refund_id, "amount": format_money(payment["amount_cents"], payment["currency"]), "status": "succeeded"}
            connection.commit()
            cancelled = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
            payload = _reservation_payload(cancelled)
            payload["refund"] = refund_payload
            return payload
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
    except PermissionError as exc:
        return error_response(403, "forbidden", str(exc))
    except LookupError as exc:
        return error_response(404, "not_found", str(exc))


def booking_api_cancel_reservation(database_path: str, reservation_id: str, user_id: str) -> ApiResponse:
    """HTTP-shaped cancellation adapter for response contract tests."""

    try:
        return success_response(cancel_reservation(database_path, reservation_id=reservation_id, user_id=user_id))
    except PermissionError as exc:
        return error_response(403, "forbidden", str(exc))
    except LookupError as exc:
        return error_response(404, "not_found", str(exc))
    except BookingConflict as exc:
        return error_response(409, "reservation_conflict", str(exc))


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _reservation_payload(row: sqlite3.Row, *, duplicate_request: bool = False) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "hotelId": row["hotel_id"],
        "roomTypeId": row["room_type_id"],
        "roomId": row["room_id"],
        "userId": row["user_id"],
        "guestEmail": row["guest_email"],
        "guestName": row["guest_name"],
        "checkIn": row["check_in"],
        "checkOut": row["check_out"],
        "status": row["status"],
        "checkoutType": row["checkout_type"],
        "total": format_money(row["total_cents"], row["currency"]),
        "expiresAt": row["expires_at"],
        "cancelledAt": row["cancelled_at"],
    }
    if duplicate_request:
        payload["duplicateRequest"] = True
    return payload
