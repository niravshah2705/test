"""Booking notification request abstractions and deterministic adapters.

This module intentionally stops at message generation and dispatch abstraction. It
persists provider-agnostic notification requests and offers an in-memory adapter
for tests without provisioning email/SMS providers.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol

BOOKING_CONFIRMATION = "booking_confirmation"
BOOKING_FAILED = "booking_failed"
PAYMENT_FAILED = "payment_failed"
BOOKING_PENDING = "booking_pending"
BOOKING_NOTIFICATION_KINDS = {BOOKING_CONFIRMATION, BOOKING_FAILED, PAYMENT_FAILED, BOOKING_PENDING}


@dataclass(frozen=True)
class ItinerarySummary:
    hotel_name: str
    city: str
    country: str
    room_type_name: str
    check_in: str
    check_out: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "hotelName": self.hotel_name,
            "city": self.city,
            "country": self.country,
            "roomTypeName": self.room_type_name,
            "checkIn": self.check_in,
            "checkOut": self.check_out,
        }


@dataclass(frozen=True)
class BookingNotificationTemplateData:
    booking_reference: str
    itinerary_summary: ItinerarySummary
    passenger_names: tuple[str, ...]
    status: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "bookingReference": self.booking_reference,
            "itinerarySummary": self.itinerary_summary.to_payload(),
            "passengerNames": list(self.passenger_names),
            "status": self.status,
        }


@dataclass(frozen=True)
class BookingConfirmationTemplateData(BookingNotificationTemplateData):
    pass


@dataclass(frozen=True)
class BookingFailedTemplateData(BookingNotificationTemplateData):
    pass


@dataclass(frozen=True)
class PaymentFailedTemplateData(BookingNotificationTemplateData):
    pass


@dataclass(frozen=True)
class BookingPendingTemplateData(BookingNotificationTemplateData):
    pass


@dataclass(frozen=True)
class NotificationRequest:
    id: str
    booking_id: str
    kind: str
    channel: str
    recipient: str | None
    payload: dict[str, Any]
    status: str
    created_at: str


class NotificationDispatcher(Protocol):
    """Provider-agnostic dispatch interface for notification requests."""

    def dispatch(self, request: NotificationRequest) -> None:
        """Dispatch a generated notification request."""


class InMemoryNotificationDispatcher:
    """Test adapter that records dispatched notification requests in memory."""

    def __init__(self) -> None:
        self.messages: list[NotificationRequest] = []

    def dispatch(self, request: NotificationRequest) -> None:
        self.messages.append(request)


def notification_rows(connection: sqlite3.Connection, booking_id: str | None = None) -> list[dict[str, Any]]:
    """Return persisted notification records with decoded payloads for tests."""

    sql = "SELECT * FROM notification_records"
    params: tuple[Any, ...] = ()
    if booking_id is not None:
        sql += " WHERE booking_id = ?"
        params = (booking_id,)
    sql += " ORDER BY created_at, id"
    rows = connection.execute(sql, params).fetchall()
    return [_row_to_dict(row) for row in rows]


def create_booking_notification(
    connection: sqlite3.Connection,
    booking_id: str,
    kind: str,
    *,
    dispatcher: NotificationDispatcher | None = None,
    created_at: str,
) -> dict[str, Any]:
    """Create and optionally dispatch a booking notification request.

    Confirmation notifications are idempotent per booking so refreshes or
    duplicate payment webhooks do not create duplicate confirmation messages.
    """

    if kind not in BOOKING_NOTIFICATION_KINDS:
        raise ValueError("Unsupported booking notification kind.")
    if kind == BOOKING_CONFIRMATION:
        existing = connection.execute(
            "SELECT * FROM notification_records WHERE booking_id = ? AND kind = ?",
            (booking_id, BOOKING_CONFIRMATION),
        ).fetchone()
        if existing is not None:
            payload = _row_to_dict(existing)
            payload["duplicate"] = True
            return payload

    row = _booking_context(connection, booking_id)
    if row is None:
        raise LookupError("Reservation not found.")
    template_data = _template_data_for_kind(kind, row)
    payload = template_data.to_payload()
    recipient = _recipient_for_booking(row)
    status = "queued" if recipient is not None else "skipped_missing_contact"
    notification_id = _notification_id(booking_id, kind)

    connection.execute(
        """
        INSERT INTO notification_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            notification_id,
            booking_id,
            kind,
            "email",
            recipient,
            json.dumps(payload, sort_keys=True),
            status,
            created_at,
            None,
        ),
    )
    request = NotificationRequest(
        id=notification_id,
        booking_id=booking_id,
        kind=kind,
        channel="email",
        recipient=recipient,
        payload=payload,
        status=status,
        created_at=created_at,
    )
    if recipient is not None and dispatcher is not None:
        dispatcher.dispatch(request)
        status = "dispatched"
        connection.execute(
            "UPDATE notification_records SET status = ?, dispatched_at = ? WHERE id = ?",
            (status, created_at, notification_id),
        )
    result = request.__dict__ | {"status": status, "dispatchedAt": created_at if status == "dispatched" else None, "duplicate": False}
    return result


def _booking_context(connection: sqlite3.Connection, booking_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT reservation.*, hotel.name AS hotel_name, hotel.city, hotel.country, room_type.name AS room_type_name
        FROM reservations AS reservation
        JOIN hotels AS hotel ON hotel.id = reservation.hotel_id
        JOIN room_types AS room_type ON room_type.id = reservation.room_type_id
        WHERE reservation.id = ?
        """,
        (booking_id,),
    ).fetchone()


def _template_data_for_kind(kind: str, row: sqlite3.Row) -> BookingNotificationTemplateData:
    base_kwargs = {
        "booking_reference": row["id"],
        "itinerary_summary": ItinerarySummary(
            hotel_name=row["hotel_name"],
            city=row["city"],
            country=row["country"],
            room_type_name=row["room_type_name"],
            check_in=row["check_in"],
            check_out=row["check_out"],
        ),
        "passenger_names": _passenger_names(row),
        "status": row["status"],
    }
    if kind == BOOKING_CONFIRMATION:
        return BookingConfirmationTemplateData(**base_kwargs)
    if kind == BOOKING_FAILED:
        return BookingFailedTemplateData(**base_kwargs)
    if kind == PAYMENT_FAILED:
        return PaymentFailedTemplateData(**base_kwargs)
    return BookingPendingTemplateData(**base_kwargs)


def _recipient_for_booking(row: sqlite3.Row) -> str | None:
    email = str(row["guest_email"] or "").strip()
    return email if "@" in email else None


def _passenger_names(row: sqlite3.Row) -> tuple[str, ...]:
    guest_name = str(row["guest_name"] or "").strip()
    return (guest_name,) if guest_name else ()


def _notification_id(booking_id: str, kind: str) -> str:
    return f"ntf_{kind}_{booking_id}"


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "bookingId": row["booking_id"],
        "kind": row["kind"],
        "channel": row["channel"],
        "recipient": row["recipient"],
        "payload": json.loads(row["payload_json"]),
        "status": row["status"],
        "createdAt": row["created_at"],
        "dispatchedAt": row["dispatched_at"],
    }
