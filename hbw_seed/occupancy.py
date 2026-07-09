"""Shared occupancy validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MAX_GUESTS = 12


class OccupancyValidationError(ValueError):
    """Raised when guest counts are invalid."""


@dataclass(frozen=True)
class Occupancy:
    adults: int
    children: int
    total_guests: int
    room_capacity: int | None = None

    def to_payload(self) -> dict[str, Any]:
        payload = {"adults": self.adults, "children": self.children, "totalGuests": self.total_guests}
        if self.room_capacity is not None:
            payload["roomCapacity"] = self.room_capacity
        return payload


def parse_guest_count(value: Any, field: str, *, minimum: int) -> int:
    """Parse an integer guest count and reject fractional/bool values."""

    if isinstance(value, bool):
        raise OccupancyValidationError(f"{field} must be an integer.")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or not (stripped.isdecimal() or (stripped.startswith("-") and stripped[1:].isdecimal())):
            raise OccupancyValidationError(f"{field} must be an integer.")
        parsed = int(stripped)
    else:
        raise OccupancyValidationError(f"{field} must be an integer.")
    if parsed < minimum:
        if field == "adults" and minimum == 1:
            raise OccupancyValidationError("At least one adult is required.")
        if field == "children" and minimum == 0:
            raise OccupancyValidationError("Children cannot be negative.")
        raise OccupancyValidationError(f"{field} must be greater than or equal to {minimum}.")
    return parsed


def validate_occupancy(
    adults: Any,
    children: Any,
    room_capacity: Any | None = None,
    *,
    max_guests: int = MAX_GUESTS,
) -> Occupancy:
    """Validate adults, children, platform total, and optional room capacity."""

    parsed_adults = parse_guest_count(adults, "adults", minimum=1)
    parsed_children = parse_guest_count(children, "children", minimum=0)
    total_guests = parsed_adults + parsed_children
    if total_guests > max_guests:
        raise OccupancyValidationError(f"Total guests must be less than or equal to {max_guests}.")

    parsed_capacity: int | None = None
    if room_capacity is not None:
        parsed_capacity = parse_guest_count(room_capacity, "room_capacity", minimum=1)
        if total_guests > parsed_capacity:
            raise OccupancyValidationError("Guest count exceeds room capacity.")

    return Occupancy(adults=parsed_adults, children=parsed_children, total_guests=total_guests, room_capacity=parsed_capacity)
