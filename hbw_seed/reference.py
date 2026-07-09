"""Airport and airline reference data and lookup APIs.

The reference layer is intentionally framework-neutral: route adapters can call the
functions directly while tests can verify the same payload contracts without a web
framework. Seed data is practical but deterministic for local development and CI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Airport:
    iata_code: str
    name: str
    city: str
    country: str
    timezone: str

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.iata_code})"

    def to_payload(self) -> dict[str, str]:
        return {
            "code": self.iata_code,
            "iataCode": self.iata_code,
            "displayName": self.display_name,
            "name": self.name,
            "city": self.city,
            "country": self.country,
            "timezone": self.timezone,
        }


@dataclass(frozen=True)
class Airline:
    display_name: str
    iata_code: str | None = None
    icao_code: str | None = None

    @property
    def primary_code(self) -> str:
        return self.iata_code or self.icao_code or self.display_name

    def matches_code(self, code: str) -> bool:
        normalized = code.upper()
        return normalized in {candidate for candidate in (self.iata_code, self.icao_code) if candidate}

    def to_payload(self) -> dict[str, str | None]:
        return {
            "code": self.primary_code,
            "iataCode": self.iata_code,
            "icaoCode": self.icao_code,
            "displayName": self.display_name,
        }


AIRPORTS: tuple[Airport, ...] = (
    Airport("SFO", "San Francisco International Airport", "San Francisco", "US", "America/Los_Angeles"),
    Airport("JFK", "John F. Kennedy International Airport", "New York", "US", "America/New_York"),
    Airport("LAX", "Los Angeles International Airport", "Los Angeles", "US", "America/Los_Angeles"),
    Airport("ORD", "O'Hare International Airport", "Chicago", "US", "America/Chicago"),
    Airport("SEA", "Seattle-Tacoma International Airport", "Seattle", "US", "America/Los_Angeles"),
    Airport("LHR", "Heathrow Airport", "London", "GB", "Europe/London"),
    Airport("NRT", "Narita International Airport", "Tokyo", "JP", "Asia/Tokyo"),
    Airport("DXB", "Dubai International Airport", "Dubai", "AE", "Asia/Dubai"),
)

AIRLINES: tuple[Airline, ...] = (
    Airline("Oceanic Air", iata_code="OA", icao_code="OCA"),
    Airline("Pacific Blue", iata_code="PB", icao_code="PBA"),
    Airline("United Airlines", iata_code="UA", icao_code="UAL"),
    Airline("Delta Air Lines", iata_code="DL", icao_code="DAL"),
    Airline("American Airlines", iata_code="AA", icao_code="AAL"),
    Airline("British Airways", iata_code="BA", icao_code="BAW"),
    Airline("Emirates", iata_code="EK", icao_code="UAE"),
)

_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{2,4}$")


def search_airports(query: str | None, *, limit: int = 10) -> list[dict[str, str]]:
    """Return airport matches by IATA code, city, or airport name.

    Search is case-insensitive and returns no rows for a blank query so frontend
    autocomplete controls do not render an unbounded default list.
    """

    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        return []

    matches = [airport for airport in AIRPORTS if _airport_matches(airport, normalized_query)]
    matches.sort(key=lambda airport: _airport_rank(airport, normalized_query))
    return [airport.to_payload() for airport in matches[:limit]]


def get_airport(iata_code: str) -> dict[str, str] | None:
    normalized = iata_code.strip().upper()
    for airport in AIRPORTS:
        if airport.iata_code == normalized:
            return airport.to_payload()
    return None


def get_airline(code: str) -> dict[str, str | None] | None:
    normalized = code.strip().upper()
    for airline in AIRLINES:
        if airline.matches_code(normalized):
            return airline.to_payload()
    return None


def airport_display(iata_code: str) -> str:
    airport = get_airport(iata_code)
    if airport is None:
        return iata_code
    return f"{airport['city']} ({airport['code']})"


def airline_display(code: str) -> str:
    airline = get_airline(code)
    if airline is None:
        return code
    return str(airline["displayName"])


def is_plausible_reference_code(code: str) -> bool:
    return bool(_CODE_PATTERN.fullmatch(code.strip()))


def _airport_matches(airport: Airport, normalized_query: str) -> bool:
    return (
        normalized_query in airport.iata_code.lower()
        or normalized_query in airport.city.lower()
        or normalized_query in airport.name.lower()
    )


def _airport_rank(airport: Airport, normalized_query: str) -> tuple[int, str]:
    code = airport.iata_code.lower()
    city = airport.city.lower()
    name = airport.name.lower()
    if code == normalized_query:
        rank = 0
    elif code.startswith(normalized_query):
        rank = 1
    elif city.startswith(normalized_query):
        rank = 2
    elif name.startswith(normalized_query):
        rank = 3
    else:
        rank = 4
    return (rank, airport.iata_code)


def reference_seed_summary() -> dict[str, Any]:
    """Return deterministic dataset counts for development/test diagnostics."""

    return {"airports": len(AIRPORTS), "airlines": len(AIRLINES)}
