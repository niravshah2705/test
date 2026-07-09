"""Framework-neutral booking domain helpers for deterministic HBW tests.

The functions in this module intentionally model the failure-prone reservation,
payment, authorization, and cancellation decisions against the deterministic
SQLite fixture schema. They are small enough for unit tests while still using the
same database-backed inventory rules as the public API contract layer.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import date
from math import ceil
from typing import Any

from .audit import actor_from_user, record_audit_event, system_actor, user_actor
from .auth import canAdministerHotel, canCancelReservation, canPayReservation, canViewReservation
from .dto import admin_reservation_dto, guest_reservation_dto, payment_safe_dto
from .money import MoneyValidationError, format_money as _format_money, multiply_money, money
from .occupancy import MAX_GUESTS, OccupancyValidationError, validate_occupancy as _validate_occupancy
from .public_api import ApiResponse, error_response, success_response
from .stay import StayDates, StayValidationError, parse_stay_dates as _parse_stay_dates

HOLD_EXPIRES_AT = "2031-06-09T23:59:00Z"
PAYMENT_PROVIDER = "fixture_gateway"
ADMIN_MAX_PAGE_SIZE = 50
ADMIN_DEFAULT_PAGE = 1
ADMIN_DEFAULT_PAGE_SIZE = 20
CANCELLABLE_STATUSES = {"confirmed", "pending_payment"}



class BookingConflict(Exception):
    """Raised when inventory or request identity conflicts are detected."""


class BookingValidationError(ValueError):
    """Raised for invalid utility/service inputs."""


def parse_stay_dates(check_in: str, check_out: str) -> StayDates:
    """Validate ISO stay dates and return the half-open night count."""

    try:
        return _parse_stay_dates(check_in, check_out)
    except StayValidationError as exc:
        raise BookingValidationError(str(exc)) from exc


def format_money(amount_cents: int, currency: str = "USD") -> dict[str, Any]:
    """Return the shared money shape used by API payload assertions."""

    try:
        return _format_money(amount_cents, currency)
    except MoneyValidationError as exc:
        message = str(exc).replace("amount_minor", "amount_cents")
        raise BookingValidationError(message) from exc


def validate_occupancy(adults: int, children: int, room_capacity: int) -> int:
    """Validate guest counts against platform and room capacity limits."""

    try:
        return _validate_occupancy(adults, children, room_capacity).total_guests
    except OccupancyValidationError as exc:
        raise BookingValidationError(str(exc)) from exc


def calculate_total_cents(nightly_rate_cents: int, check_in: str, check_out: str) -> int:
    """Calculate the deterministic reservation total for a stay."""

    try:
        return multiply_money(money(nightly_rate_cents), parse_stay_dates(check_in, check_out).nights).amount_cents
    except MoneyValidationError as exc:
        message = str(exc).replace("amount_minor", "nightly_rate_cents")
        raise BookingValidationError(message) from exc


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
                OR (block.block_type = 'room_type_closure' AND block.room_type_id = ?)
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


def overlapping_availability_blocks(
    connection: sqlite3.Connection,
    hotel_id: str,
    starts_on: str,
    ends_on: str,
) -> list[dict[str, Any]]:
    """Return blocks for a hotel whose half-open ranges overlap the requested dates."""

    parse_stay_dates(starts_on, ends_on)
    cursor = connection.execute(
        """
        SELECT *
        FROM availability_blocks
        WHERE hotel_id = ?
          AND starts_on < ?
          AND ends_on > ?
        ORDER BY starts_on, id
        """,
        (hotel_id, ends_on, starts_on),
    )
    columns = [description[0] for description in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


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
            record_audit_event(
                connection,
                actor=system_actor("webhook"),
                event_type="reservation.confirmed",
                entity_type="reservation",
                entity_id=reservation_id,
                metadata={"paymentId": f"pay_{provider_reference}", "auditWritePolicy": "best effort; payment correctness wins"},
                created_at=created_at,
            )
        record_audit_event(
            connection,
            actor=system_actor("webhook"),
            event_type="payment.succeeded" if status == "captured" else "payment.failed",
            entity_type="payment",
            entity_id=f"pay_{provider_reference}",
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
        return {"duplicate": False, "paymentId": f"pay_{provider_reference}", "status": status}


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
    """Cancel an eligible authenticated reservation through the shared cancellation service."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
            if row is None:
                raise LookupError("Reservation not found.")
            actor = _actor_by_id(connection, user_id)
            if not canViewReservation(actor, row) or actor is None or actor["role"] == "admin":
                raise PermissionError("Reservation access denied.")
            cancelled, refund_payload, duplicate = _cancel_reservation_in_transaction(
                connection,
                row,
                actor=actor,
                cancelled_at=cancelled_at,
                reason="Guest cancelled eligible reservation.",
            )
            connection.commit()
            return guest_reservation_dto(cancelled, refund=refund_payload) | ({"duplicateRequest": True} if duplicate else {})
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def admin_cancel_reservation(
    database_path: str,
    *,
    reservation_id: str,
    user_id: str,
    reason: str = "Admin cancelled eligible reservation.",
    cancelled_at: str = "2031-04-02T10:00:00Z",
) -> dict[str, Any]:
    """Cancel any eligible reservation for an authorized administrator."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _reservation_by_id(connection, reservation_id)
            if row is None:
                raise LookupError("Reservation not found.")
            actor = _actor_by_id(connection, user_id)
            if not canAdministerHotel(actor, row["hotel_id"]):
                raise PermissionError("Admin access required.")
            cancelled, refund_payload, duplicate = _cancel_reservation_in_transaction(
                connection,
                row,
                actor=actor,
                cancelled_at=cancelled_at,
                reason=reason,
            )
            connection.commit()
            payload = admin_reservation_dto(cancelled)
            if refund_payload is not None:
                payload["refund"] = refund_payload
            if duplicate:
                payload["duplicateRequest"] = True
            return payload
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def admin_refund_reservation(
    database_path: str,
    *,
    reservation_id: str,
    user_id: str,
    amount_cents: int | None = None,
    reason: str = "Admin refund.",
    refund_id: str | None = None,
    created_at: str = "2031-04-02T10:05:00Z",
) -> dict[str, Any]:
    """Refund a captured payment for an authorized admin without exceeding refundable balance."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _reservation_by_id(connection, reservation_id)
            if row is None:
                raise LookupError("Reservation not found.")
            actor = _actor_by_id(connection, user_id)
            if not canAdministerHotel(actor, row["hotel_id"]):
                raise PermissionError("Admin access required.")
            payment = _latest_refundable_payment(connection, reservation_id)
            if payment is None:
                raise BookingConflict("Reservation has no refundable captured payment.")
            refundable_cents = _refundable_amount_cents(connection, payment)
            refund_amount = refundable_cents if amount_cents is None else int(amount_cents)
            refund_payload, duplicate = _create_refund_in_transaction(
                connection,
                payment,
                actor=actor,
                amount_cents=refund_amount,
                reason=reason,
                created_at=created_at,
                refund_id=refund_id,
            )
            connection.commit()
            return {"duplicateRequest": duplicate, "refund": refund_payload, "refundableAmount": format_money(max(refundable_cents - (0 if duplicate else refund_amount), 0), payment["currency"])}
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise


def admin_search_reservations(database_path: str, *, user_id: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    """Search operational reservations for admins with bounded pagination and safe filters."""

    filters = filters or {}
    page = _bounded_positive_int(filters.get("page", ADMIN_DEFAULT_PAGE), "page", minimum=1)
    page_size = _bounded_positive_int(filters.get("pageSize", filters.get("page_size", ADMIN_DEFAULT_PAGE_SIZE)), "pageSize", minimum=1, maximum=ADMIN_MAX_PAGE_SIZE)
    with _connect(database_path) as connection:
        actor = _actor_by_id(connection, user_id)
        if not canAdministerHotel(actor, str(filters.get("hotelId") or filters.get("hotel_id") or "*")):
            raise PermissionError("Admin access required.")
        where: list[str] = []
        params: list[Any] = []
        _append_exact_filter(where, params, "reservation.hotel_id", filters.get("hotelId") or filters.get("hotel_id"))
        _append_exact_filter(where, params, "reservation.status", filters.get("status"))
        _append_like_filter(where, params, "reservation.guest_email", filters.get("guestEmail") or filters.get("guest_email"))
        confirmation = filters.get("confirmationCode") or filters.get("confirmation_code")
        if confirmation:
            where.append("(reservation.id = ? OR reservation.confirmation_secret = ?)")
            params.extend([confirmation, confirmation])
        check_in_from = filters.get("checkInFrom") or filters.get("check_in_from")
        check_in_to = filters.get("checkInTo") or filters.get("check_in_to")
        if check_in_from:
            date.fromisoformat(str(check_in_from))
            where.append("reservation.check_in >= ?")
            params.append(check_in_from)
        if check_in_to:
            date.fromisoformat(str(check_in_to))
            where.append("reservation.check_in <= ?")
            params.append(check_in_to)
        where_sql = " WHERE " + " AND ".join(where) if where else ""
        total = connection.execute(f"SELECT COUNT(*) FROM reservations AS reservation{where_sql}", params).fetchone()[0]
        rows = connection.execute(
            f"""
            SELECT reservation.*
            FROM reservations AS reservation
            {where_sql}
            ORDER BY reservation.created_at DESC, reservation.id
            LIMIT ? OFFSET ?
            """,
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
    return {
        "reservations": [admin_reservation_dto(row) for row in rows],
        "pagination": {"page": page, "pageSize": page_size, "total": total, "totalPages": ceil(total / page_size) if total else 0},
    }


def admin_update_reservation_status(
    database_path: str,
    *,
    reservation_id: str,
    user_id: str,
    status: str,
    updated_at: str = "2031-04-02T10:10:00Z",
) -> dict[str, Any]:
    """Apply supported admin reservation status transitions."""

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = _reservation_by_id(connection, reservation_id)
            if row is None:
                raise LookupError("Reservation not found.")
            actor = _actor_by_id(connection, user_id)
            if not canAdministerHotel(actor, row["hotel_id"]):
                raise PermissionError("Admin access required.")
            if status == row["status"]:
                raise BookingConflict("Invalid reservation status transition.")
            if status == "cancelled":
                cancelled, refund_payload, duplicate = _cancel_reservation_in_transaction(
                    connection,
                    row,
                    actor=actor,
                    cancelled_at=updated_at,
                    reason="Admin status transition cancelled reservation.",
                )
                connection.commit()
                payload = admin_reservation_dto(cancelled)
                if refund_payload is not None:
                    payload["refund"] = refund_payload
                if duplicate:
                    payload["duplicateRequest"] = True
                return payload
            if status == "expired" and row["status"] == "pending_payment":
                connection.execute("UPDATE reservations SET status = 'expired' WHERE id = ?", (reservation_id,))
                record_audit_event(
                    connection,
                    actor=actor_from_user(actor),
                    event_type="reservation.expired",
                    entity_type="reservation",
                    entity_id=reservation_id,
                    metadata={"auditWritePolicy": "blocking for admin reservation status mutations"},
                    created_at=updated_at,
                    block_on_failure=True,
                )
                connection.commit()
                return admin_reservation_dto(connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone())
            raise BookingConflict("Invalid reservation status transition.")
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
            existing = connection.execute("SELECT * FROM payment_records WHERE id = ?", (payment_id,)).fetchone()
            if existing is not None:
                connection.rollback()
                return payment_safe_dto(existing)
            connection.execute(
                "INSERT INTO payment_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payment_id,
                    reservation_id,
                    PAYMENT_PROVIDER,
                    provider_reference,
                    reservation["total_cents"],
                    reservation["currency"],
                    "authorized",
                    created_at,
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


def _validate_availability_block_target(
    connection: sqlite3.Connection,
    *,
    hotel_id: str,
    block_type: str,
    room_type_id: str | None,
    room_id: str | None,
) -> tuple[str | None, str | None]:
    if connection.execute("SELECT 1 FROM hotels WHERE id = ?", (hotel_id,)).fetchone() is None:
        raise LookupError("Hotel not found.")
    if block_type == "hotel_closure":
        if room_type_id is not None or room_id is not None:
            raise BookingValidationError("Hotel-level availability blocks must not include room type or room targets.")
        return None, None
    if block_type == "room_type_closure":
        if room_type_id is None or room_id is not None:
            raise BookingValidationError("Room-type availability blocks must target exactly one room type.")
        room_type = connection.execute("SELECT * FROM room_types WHERE id = ? AND hotel_id = ?", (room_type_id, hotel_id)).fetchone()
        if room_type is None:
            raise LookupError("Room type not found for hotel.")
        return room_type_id, None
    if block_type == "room_maintenance":
        if room_id is None:
            raise BookingValidationError("Room-level availability blocks must target exactly one physical room.")
        room = connection.execute(
            """
            SELECT room.id, room.room_type_id
            FROM rooms AS room
            JOIN room_types AS room_type ON room_type.id = room.room_type_id
            WHERE room.id = ? AND room_type.hotel_id = ?
            """,
            (room_id, hotel_id),
        ).fetchone()
        if room is None:
            raise LookupError("Room not found for hotel.")
        if room_type_id is not None and room_type_id != room["room_type_id"]:
            raise BookingValidationError("Room-level availability block room type must match the physical room.")
        return room["room_type_id"], room_id
    raise BookingValidationError("Availability block type is invalid.")


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
    """Create an availability block with validated target/date semantics and admin audit."""

    parse_stay_dates(starts_on, ends_on)
    if not reason or not reason.strip():
        raise BookingValidationError("Availability block reason is required.")

    with _connect(database_path) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            actor = _actor_by_id(connection, user_id)
            if not canAdministerHotel(actor, hotel_id):
                raise PermissionError("Admin access required.")
            resolved_room_type_id, resolved_room_id = _validate_availability_block_target(
                connection,
                hotel_id=hotel_id,
                block_type=block_type,
                room_type_id=room_type_id,
                room_id=room_id,
            )
            connection.execute(
                """
                INSERT INTO availability_blocks
                    (id, hotel_id, room_type_id, room_id, block_type, starts_on, ends_on, reason, created_by_admin_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    block_id,
                    hotel_id,
                    resolved_room_type_id,
                    resolved_room_id,
                    block_type,
                    starts_on,
                    ends_on,
                    reason.strip(),
                    user_id,
                    created_at,
                    created_at,
                ),
            )
            record_audit_event(
                connection,
                actor=actor_from_user(actor),
                event_type="availability_block.created",
                entity_type="availability_block",
                entity_id=block_id,
                metadata={
                    "hotelId": hotel_id,
                    "roomTypeId": resolved_room_type_id,
                    "roomId": resolved_room_id,
                    "blockType": block_type,
                    "startsOn": starts_on,
                    "endsOn": ends_on,
                    "reason": reason.strip(),
                    "auditWritePolicy": "blocking for admin inventory mutations",
                },
                created_at=created_at,
                block_on_failure=True,
            )
            connection.commit()
            return _row_to_dict(connection.execute("SELECT * FROM availability_blocks WHERE id = ?", (block_id,)).fetchone())
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


def booking_api_record_payment(database_path: str, payload: dict[str, Any], user_id: str) -> ApiResponse:
    """HTTP-shaped payment adapter that authorizes caller and hides provider secrets."""

    required = ["reservationId", "providerReference", "amountCents"]
    missing = [field for field in required if field not in payload]
    if missing:
        return error_response(400, "validation_error", "Request body failed validation.", fields={field: ["Field is required."] for field in missing})
    with _connect(database_path) as connection:
        reservation = _reservation_by_id(connection, payload["reservationId"])
        actor = _actor_by_id(connection, user_id)
        if reservation is None:
            return error_response(404, "not_found", "Reservation not found.")
        if not canPayReservation(actor, reservation):
            return error_response(403, "forbidden", "You are not authorized to pay for this reservation.")
    try:
        result = record_payment_webhook(
            database_path,
            provider_reference=payload["providerReference"],
            reservation_id=payload["reservationId"],
            amount_cents=int(payload["amountCents"]),
            currency=payload.get("currency", "USD"),
            event_type=payload.get("eventType", "payment.succeeded"),
        )
    except BookingValidationError:
        return error_response(400, "validation_error", "Payment could not be processed.")
    with _connect(database_path) as connection:
        payment = connection.execute("SELECT * FROM payment_records WHERE id = ?", (result["paymentId"],)).fetchone()
        return success_response({"duplicate": result["duplicate"], "payment": payment_safe_dto(payment)}, status_code=200 if result["duplicate"] else 201)


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

def booking_api_admin_search_reservations(database_path: str, query: dict[str, Any], user_id: str) -> ApiResponse:
    """HTTP-shaped admin reservation search endpoint."""

    try:
        return success_response(admin_search_reservations(database_path, user_id=user_id, filters=query))
    except PermissionError:
        return error_response(403, "forbidden", "Admin access required.")
    except (BookingValidationError, ValueError) as exc:
        return error_response(400, "validation_error", str(exc))


def booking_api_admin_cancel_reservation(database_path: str, reservation_id: str, user_id: str, payload: dict[str, Any] | None = None) -> ApiResponse:
    """HTTP-shaped admin reservation cancellation endpoint."""

    payload = payload or {}
    try:
        return success_response(admin_cancel_reservation(database_path, reservation_id=reservation_id, user_id=user_id, reason=payload.get("reason", "Admin cancelled eligible reservation.")))
    except PermissionError:
        return error_response(403, "forbidden", "Admin access required.")
    except LookupError as exc:
        return error_response(404, "not_found", str(exc))
    except BookingConflict as exc:
        return error_response(409, "reservation_conflict", str(exc))


def booking_api_admin_refund_reservation(database_path: str, reservation_id: str, user_id: str, payload: dict[str, Any] | None = None) -> ApiResponse:
    """HTTP-shaped admin refund endpoint."""

    payload = payload or {}
    try:
        result = admin_refund_reservation(
            database_path,
            reservation_id=reservation_id,
            user_id=user_id,
            amount_cents=payload.get("amountCents"),
            reason=payload.get("reason", "Admin refund."),
            refund_id=payload.get("refundId"),
        )
        return success_response(result, status_code=200 if result["duplicateRequest"] else 201)
    except PermissionError:
        return error_response(403, "forbidden", "Admin access required.")
    except LookupError as exc:
        return error_response(404, "not_found", str(exc))
    except BookingConflict as exc:
        return error_response(409, "reservation_conflict", str(exc))
    except BookingValidationError as exc:
        return error_response(400, "validation_error", str(exc))


def booking_api_admin_update_reservation_status(database_path: str, reservation_id: str, user_id: str, payload: dict[str, Any]) -> ApiResponse:
    """HTTP-shaped admin reservation status endpoint."""

    if "status" not in payload:
        return error_response(400, "validation_error", "Request body failed validation.", fields={"status": ["Field is required."]})
    try:
        return success_response(admin_update_reservation_status(database_path, reservation_id=reservation_id, user_id=user_id, status=payload["status"]))
    except PermissionError:
        return error_response(403, "forbidden", "Admin access required.")
    except LookupError as exc:
        return error_response(404, "not_found", str(exc))
    except BookingConflict as exc:
        return error_response(409, "reservation_conflict", str(exc))


def _cancel_reservation_in_transaction(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    *,
    actor: sqlite3.Row,
    cancelled_at: str,
    reason: str,
) -> tuple[sqlite3.Row, dict[str, Any] | None, bool]:
    if row["status"] == "cancelled":
        return row, _existing_refund_payload(connection, row["id"]), True
    if row["status"] not in CANCELLABLE_STATUSES:
        raise BookingConflict("Reservation is not eligible for cancellation.")
    if actor["role"] != "admin" and not canCancelReservation(actor, row):
        raise PermissionError("Reservation cancellation denied.")

    connection.execute("UPDATE reservations SET status = 'cancelled', cancelled_at = ? WHERE id = ?", (cancelled_at, row["id"]))
    refund_payload = None
    payment = _latest_refundable_payment(connection, row["id"])
    if payment is not None and _refundable_amount_cents(connection, payment) > 0:
        refund_payload, _ = _create_refund_in_transaction(
            connection,
            payment,
            actor=actor,
            amount_cents=_refundable_amount_cents(connection, payment),
            reason=reason,
            created_at=cancelled_at,
            refund_id=f"ref_{payment['id']}",
        )
    record_audit_event(
        connection,
        actor=actor_from_user(actor),
        event_type="reservation.cancelled",
        entity_type="reservation",
        entity_id=row["id"],
        metadata={"refundId": refund_payload["id"] if refund_payload else None, "auditWritePolicy": "best effort; cancellation correctness wins"},
        created_at=cancelled_at,
    )
    return connection.execute("SELECT * FROM reservations WHERE id = ?", (row["id"],)).fetchone(), refund_payload, False


def _latest_refundable_payment(connection: sqlite3.Connection, reservation_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT * FROM payment_records
        WHERE reservation_id = ? AND status IN ('captured', 'refunded')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (reservation_id,),
    ).fetchone()


def _refundable_amount_cents(connection: sqlite3.Connection, payment: sqlite3.Row) -> int:
    refunded = connection.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM refunds WHERE payment_record_id = ? AND status = 'succeeded'",
        (payment["id"],),
    ).fetchone()[0]
    return max(payment["amount_cents"] - refunded, 0)


def _create_refund_in_transaction(
    connection: sqlite3.Connection,
    payment: sqlite3.Row,
    *,
    actor: sqlite3.Row,
    amount_cents: int,
    reason: str,
    created_at: str,
    refund_id: str | None = None,
) -> tuple[dict[str, Any], bool]:
    if amount_cents <= 0:
        raise BookingValidationError("Refund amount must be greater than zero.")
    refundable_cents = _refundable_amount_cents(connection, payment)
    refund_id = refund_id or f"ref_{payment['id']}_{amount_cents}"
    existing = connection.execute("SELECT * FROM refunds WHERE id = ?", (refund_id,)).fetchone()
    if existing is not None:
        return _refund_payload(existing, payment["currency"]), True
    if amount_cents > refundable_cents:
        raise BookingValidationError("Refund amount exceeds captured refundable amount.")

    provider_result = _refund_payment_provider(payment, amount_cents=amount_cents, refund_id=refund_id)
    connection.execute(
        "INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?)",
        (refund_id, payment["id"], amount_cents, reason, provider_result["status"], created_at),
    )
    if amount_cents == refundable_cents:
        connection.execute("UPDATE payment_records SET status = 'refunded' WHERE id = ?", (payment["id"],))
    record_audit_event(
        connection,
        actor=actor_from_user(actor),
        event_type="refund.created",
        entity_type="refund",
        entity_id=refund_id,
        metadata={
            "reservationId": payment["reservation_id"],
            "paymentId": payment["id"],
            "amountCents": amount_cents,
            "currency": payment["currency"],
            "provider": payment["provider"],
            "auditWritePolicy": "best effort; cancellation correctness wins",
        },
        created_at=created_at,
    )
    return {"id": refund_id, "amount": format_money(amount_cents, payment["currency"]), "status": provider_result["status"]}, False


def _refund_payment_provider(payment: sqlite3.Row, *, amount_cents: int, refund_id: str) -> dict[str, str]:
    """Fixture payment-provider abstraction for deterministic refund tests."""

    if payment["provider"] != PAYMENT_PROVIDER:
        raise BookingValidationError("Unsupported payment provider.")
    return {"providerRefundId": f"pvr_{refund_id}", "status": "succeeded"}


def _existing_refund_payload(connection: sqlite3.Connection, reservation_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT refund.*, payment.currency
        FROM refunds AS refund
        JOIN payment_records AS payment ON payment.id = refund.payment_record_id
        WHERE payment.reservation_id = ?
        ORDER BY refund.created_at DESC
        LIMIT 1
        """,
        (reservation_id,),
    ).fetchone()
    if row is None:
        return None
    return _refund_payload(row, row["currency"])


def _refund_payload(row: sqlite3.Row, currency: str) -> dict[str, Any]:
    return {"id": row["id"], "amount": format_money(row["amount_cents"], currency), "status": row["status"]}


def _bounded_positive_int(value: Any, field: str, *, minimum: int, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise BookingValidationError(f"{field} must be an integer.") from exc
    if parsed < minimum:
        raise BookingValidationError(f"{field} must be greater than or equal to {minimum}.")
    if maximum is not None and parsed > maximum:
        raise BookingValidationError(f"{field} must be less than or equal to {maximum}.")
    return parsed


def _append_exact_filter(where: list[str], params: list[Any], column: str, value: Any) -> None:
    if value not in (None, ""):
        where.append(f"{column} = ?")
        params.append(value)


def _append_like_filter(where: list[str], params: list[Any], column: str, value: Any) -> None:
    if value not in (None, ""):
        where.append(f"LOWER({column}) LIKE ?")
        params.append(f"%{str(value).lower()}%")


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


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


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
