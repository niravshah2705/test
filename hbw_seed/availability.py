"""Room-type availability engine for deterministic hotel booking workflows.

The service in this module is intentionally framework-neutral and accepts an
open SQLite connection so callers can reuse it inside reservation transactions.
It treats stays as half-open date ranges: check-in is inclusive and checkout is
exclusive.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Any

MAX_GUESTS = 12
CONSUMING_RESERVATION_STATUSES = ("confirmed", "pending_payment")


@dataclass(frozen=True)
class StayDates:
    check_in: str
    check_out: str
    nights: int


class AvailabilityValidationError(ValueError):
    """Raised when availability inputs are invalid."""


class AvailabilityNotFoundError(LookupError):
    """Raised when the target hotel cannot be found."""


def calculate_room_type_availability(
    connection: sqlite3.Connection,
    *,
    hotel_id: str,
    check_in: str,
    check_out: str,
    adults: int,
    children: int,
    room_type_id: str | None = None,
    include_unavailable: bool = True,
) -> dict[str, Any]:
    """Return room-type availability for a hotel and stay.

    ``connection`` may already be inside a transaction. The function only reads
    current inventory state and can therefore be used safely by reservation
    creation code after acquiring its transactional lock.
    """

    stay = validate_availability_inputs(check_in, check_out, adults, children)
    hotel = connection.execute(
        "SELECT id FROM hotels WHERE id = ? AND is_searchable = 1",
        (hotel_id,),
    ).fetchone()
    if hotel is None:
        raise AvailabilityNotFoundError("Hotel not found.")

    total_guests = adults + children
    rows = _room_type_rows(connection, hotel_id, room_type_id)
    room_types: list[dict[str, Any]] = []
    for row in rows:
        occupancy_compatible = total_guests <= row["capacity"]
        if not occupancy_compatible and room_type_id is None:
            continue
        available_room_ids, unavailable_reasons = available_physical_room_ids(
            connection,
            hotel_id=hotel_id,
            room_type_id=row["id"],
            check_in=check_in,
            check_out=check_out,
        )
        active_inventory = _active_room_count(connection, row["id"])
        remaining_quantity = len(available_room_ids) if occupancy_compatible else 0
        reasons = list(unavailable_reasons)
        if not occupancy_compatible:
            reasons.insert(0, "occupancy_exceeded")
        if active_inventory == 0 and "no_active_inventory" not in reasons:
            reasons.append("no_active_inventory")
        if occupancy_compatible and active_inventory > 0 and remaining_quantity == 0 and "sold_out" not in reasons:
            reasons.append("sold_out")
        if not include_unavailable and remaining_quantity <= 0:
            continue

        nightly_rate_cents = row["nightly_rate_cents"]
        currency = row["currency"]
        room_types.append(
            {
                "code": row["id"],
                "name": row["name"],
                "capacity": row["capacity"],
                "description": row["description"],
                "activeInventory": active_inventory,
                "availableRooms": remaining_quantity,
                "remainingQuantity": remaining_quantity,
                "price": {"amountCents": nightly_rate_cents, "currency": currency, "unit": "night"},
                "nightlyRate": _money(nightly_rate_cents, currency),
                "totalPreTax": _money(nightly_rate_cents * stay.nights, currency),
                "occupancy": {
                    "adults": adults,
                    "children": children,
                    "totalGuests": total_guests,
                    "capacity": row["capacity"],
                    "compatible": occupancy_compatible,
                },
                "unavailableReasons": reasons,
            }
        )

    return {
        "hotelId": hotel_id,
        "checkIn": check_in,
        "checkOut": check_out,
        "nights": stay.nights,
        "adults": adults,
        "children": children,
        "available": any(room_type["remainingQuantity"] > 0 for room_type in room_types),
        "roomTypes": room_types,
    }


def validate_availability_inputs(check_in: str, check_out: str, adults: int, children: int) -> StayDates:
    """Validate date range and occupancy inputs for availability checks."""

    try:
        parsed_check_in = date.fromisoformat(check_in)
        parsed_check_out = date.fromisoformat(check_out)
    except ValueError as exc:
        raise AvailabilityValidationError("Dates must use YYYY-MM-DD format.") from exc
    nights = (parsed_check_out - parsed_check_in).days
    if nights <= 0:
        raise AvailabilityValidationError("check_out must be after check_in.")
    if adults < 1:
        raise AvailabilityValidationError("At least one adult is required.")
    if children < 0:
        raise AvailabilityValidationError("Children cannot be negative.")
    if adults + children > MAX_GUESTS:
        raise AvailabilityValidationError(f"Total guests must be less than or equal to {MAX_GUESTS}.")
    return StayDates(check_in=check_in, check_out=check_out, nights=nights)


def available_physical_room_ids(
    connection: sqlite3.Connection,
    *,
    hotel_id: str,
    room_type_id: str,
    check_in: str,
    check_out: str,
) -> tuple[list[str], list[str]]:
    """Return active available physical room IDs plus safe unavailable reasons."""

    validate_availability_inputs(check_in, check_out, 1, 0)
    active_room_ids = _active_room_ids(connection, room_type_id)
    if not active_room_ids:
        return [], ["no_active_inventory"]

    reasons: list[str] = []
    if _has_hotel_block(connection, hotel_id, check_in, check_out):
        return [], ["hotel_block"]
    if _has_room_type_block(connection, hotel_id, room_type_id, check_in, check_out):
        return [], ["room_type_block"]

    blocked_room_ids = _room_blocked_ids(connection, hotel_id, room_type_id, check_in, check_out)
    reserved_room_ids = _reserved_room_ids(connection, room_type_id, check_in, check_out)
    unavailable = blocked_room_ids | reserved_room_ids
    if blocked_room_ids:
        reasons.append("room_block")
    if reserved_room_ids:
        reasons.append("reserved")

    available = [room_id for room_id in active_room_ids if room_id not in unavailable]
    return available, reasons


def _room_type_rows(connection: sqlite3.Connection, hotel_id: str, room_type_id: str | None) -> list[sqlite3.Row]:
    where = ["room_type.hotel_id = ?"]
    params: list[Any] = [hotel_id]
    if room_type_id is not None:
        where.append("room_type.id = ?")
        params.append(room_type_id)
    rows = connection.execute(
        f"""
        SELECT room_type.*
        FROM room_types AS room_type
        WHERE {' AND '.join(where)}
          AND EXISTS (
            SELECT 1 FROM rooms AS room
            WHERE room.room_type_id = room_type.id AND room.status = 'active'
          )
        ORDER BY room_type.nightly_rate_cents, room_type.name
        """,
        params,
    ).fetchall()
    return list(rows)


def _active_room_ids(connection: sqlite3.Connection, room_type_id: str) -> list[str]:
    rows = connection.execute(
        """
        SELECT id
        FROM rooms
        WHERE room_type_id = ? AND status = 'active'
        ORDER BY id
        """,
        (room_type_id,),
    ).fetchall()
    return [row[0] for row in rows]


def _active_room_count(connection: sqlite3.Connection, room_type_id: str) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM rooms WHERE room_type_id = ? AND status = 'active'",
        (room_type_id,),
    ).fetchone()[0]


def _has_hotel_block(connection: sqlite3.Connection, hotel_id: str, check_in: str, check_out: str) -> bool:
    return (
        connection.execute(
            """
            SELECT 1
            FROM availability_blocks AS block
            WHERE block.hotel_id = ?
              AND block.block_type = 'hotel_closure'
              AND block.starts_on < ?
              AND block.ends_on > ?
            LIMIT 1
            """,
            (hotel_id, check_out, check_in),
        ).fetchone()
        is not None
    )


def _has_room_type_block(connection: sqlite3.Connection, hotel_id: str, room_type_id: str, check_in: str, check_out: str) -> bool:
    return (
        connection.execute(
            """
            SELECT 1
            FROM availability_blocks AS block
            WHERE block.hotel_id = ?
              AND block.room_type_id = ?
              AND block.block_type = 'room_type_closure'
              AND block.starts_on < ?
              AND block.ends_on > ?
            LIMIT 1
            """,
            (hotel_id, room_type_id, check_out, check_in),
        ).fetchone()
        is not None
    )


def _room_blocked_ids(connection: sqlite3.Connection, hotel_id: str, room_type_id: str, check_in: str, check_out: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT DISTINCT block.room_id
        FROM availability_blocks AS block
        JOIN rooms AS room ON room.id = block.room_id
        WHERE block.hotel_id = ?
          AND block.room_type_id = ?
          AND block.block_type = 'room_maintenance'
          AND block.starts_on < ?
          AND block.ends_on > ?
          AND room.room_type_id = ?
          AND room.status = 'active'
        """,
        (hotel_id, room_type_id, check_out, check_in, room_type_id),
    ).fetchall()
    return {row[0] for row in rows}


def _reserved_room_ids(connection: sqlite3.Connection, room_type_id: str, check_in: str, check_out: str) -> set[str]:
    rows = connection.execute(
        """
        SELECT DISTINCT reservation.room_id
        FROM reservations AS reservation
        JOIN rooms AS room ON room.id = reservation.room_id
        WHERE reservation.room_type_id = ?
          AND reservation.check_in < ?
          AND reservation.check_out > ?
          AND reservation.status IN ('confirmed', 'pending_payment')
          AND room.room_type_id = ?
          AND room.status = 'active'
        """,
        (room_type_id, check_out, check_in, room_type_id),
    ).fetchall()
    return {row[0] for row in rows if row[0] is not None}


def _money(amount_cents: int, currency: str) -> dict[str, Any]:
    return {"amountCents": amount_cents, "currency": currency, "formatted": f"{currency} {amount_cents / 100:.2f}"}
