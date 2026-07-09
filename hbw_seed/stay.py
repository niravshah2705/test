"""Shared date-only stay utilities.

These helpers intentionally operate on ``datetime.date`` values and ISO date-only
strings. They never convert through timestamps, datetimes, or local time zones,
so daylight-saving transitions cannot alter night counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

MAX_STAY_NIGHTS = 30


class StayValidationError(ValueError):
    """Raised when date-only stay values are invalid."""


@dataclass(frozen=True)
class StayDates:
    check_in: str
    check_out: str
    nights: int

    @property
    def check_in_date(self) -> date:
        return parse_date_only(self.check_in)

    @property
    def check_out_date(self) -> date:
        return parse_date_only(self.check_out)


def parse_date_only(value: str | date) -> date:
    """Parse a YYYY-MM-DD value without accepting timestamps."""

    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise StayValidationError("Dates must use YYYY-MM-DD format.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise StayValidationError("Dates must use YYYY-MM-DD format.") from exc
    if parsed.isoformat() != value:
        raise StayValidationError("Dates must use YYYY-MM-DD format.")
    return parsed


def format_date_only(value: str | date) -> str:
    """Return a canonical YYYY-MM-DD date-only string."""

    return parse_date_only(value).isoformat()


def parse_stay_dates(check_in: str | date, check_out: str | date, *, max_nights: int = MAX_STAY_NIGHTS) -> StayDates:
    """Validate a half-open stay range and return the date-only night count."""

    parsed_check_in = parse_date_only(check_in)
    parsed_check_out = parse_date_only(check_out)
    nights = night_count(parsed_check_in, parsed_check_out, max_nights=max_nights)
    return StayDates(check_in=parsed_check_in.isoformat(), check_out=parsed_check_out.isoformat(), nights=nights)


def night_count(check_in: str | date, check_out: str | date, *, max_nights: int = MAX_STAY_NIGHTS) -> int:
    """Return nights in a check-in-inclusive/check-out-exclusive range."""

    parsed_check_in = parse_date_only(check_in)
    parsed_check_out = parse_date_only(check_out)
    nights = parsed_check_out.toordinal() - parsed_check_in.toordinal()
    if nights <= 0:
        raise StayValidationError("check_out must be after check_in.")
    if max_nights < 1:
        raise StayValidationError("max_nights must be positive.")
    if nights > max_nights:
        raise StayValidationError(f"Stay cannot exceed {max_nights} nights.")
    return nights


def ranges_overlap(
    first_check_in: str | date,
    first_check_out: str | date,
    second_check_in: str | date,
    second_check_out: str | date,
    *,
    max_nights: int = MAX_STAY_NIGHTS,
) -> bool:
    """Return True when two half-open stay ranges overlap.

    Back-to-back ranges where one check-out equals another check-in do not
    overlap.
    """

    first = parse_stay_dates(first_check_in, first_check_out, max_nights=max_nights)
    second = parse_stay_dates(second_check_in, second_check_out, max_nights=max_nights)
    return first.check_in < second.check_out and second.check_in < first.check_out


def stay_payload(check_in: str | date, check_out: str | date, *, max_nights: int = MAX_STAY_NIGHTS) -> dict[str, Any]:
    stay = parse_stay_dates(check_in, check_out, max_nights=max_nights)
    return {"checkIn": stay.check_in, "checkOut": stay.check_out, "nights": stay.nights}
