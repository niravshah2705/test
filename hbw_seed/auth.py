"""Authorization helpers for deterministic Hotel Booking Workflow contracts."""

from __future__ import annotations

from typing import Any, Mapping


def _value(record: Mapping[str, Any], key: str) -> Any:
    return record[key]


def _get(record: Mapping[str, Any], key: str) -> Any:
    try:
        return record.get(key)  # type: ignore[attr-defined]
    except AttributeError:
        return record[key]


def canViewReservation(actor: Mapping[str, Any] | None, reservation: Mapping[str, Any]) -> bool:
    """Return True when an authenticated actor may view reservation details."""

    if actor is None:
        return False
    if _get(actor, "role") == "admin":
        return True
    return _value(reservation, "user_id") is not None and _value(reservation, "user_id") == _get(actor, "id")


def canCancelReservation(actor: Mapping[str, Any] | None, reservation: Mapping[str, Any]) -> bool:
    """Return True when an authenticated actor may cancel a reservation."""

    if not canViewReservation(actor, reservation):
        return False
    if actor is not None and _get(actor, "role") == "admin":
        return False
    return _value(reservation, "status") in {"confirmed", "pending_payment"}


def canPayReservation(actor: Mapping[str, Any] | None, reservation: Mapping[str, Any]) -> bool:
    """Return True when an authenticated actor may attach payment to a reservation."""

    if not canViewReservation(actor, reservation):
        return False
    return actor is not None and _get(actor, "role") != "admin" and _value(reservation, "status") == "pending_payment"


def canAdministerHotel(actor: Mapping[str, Any] | None, hotel_id: str) -> bool:
    """Return True when an actor has operational hotel administration privileges."""

    return actor is not None and _get(actor, "role") == "admin"
