"""Flight search application contract for API handlers and server-rendered UI.

This module keeps provider-native flight data behind the provider interface from
``hbw_seed.flights``. It validates traveler input, creates deterministic search
sessions, stores normalized offers in an in-memory repository for local/test
flows, and returns frontend-safe DTOs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Mapping

from .flights import (
    DeterministicMockFlightProvider,
    FlightBookingService,
    FlightOffer,
    FlightProvider,
    FlightProviderError,
    FlightProviderTimeout,
    FlightSearchRequest,
)
from .public_api import ApiResponse, error_response, success_response

MAX_FLIGHT_PASSENGERS = 9
SUPPORTED_CABINS = {"economy", "premium_economy", "business", "first"}
IATA_PATTERN = re.compile(r"^[A-Z]{3}$")
DEFAULT_NOW = datetime(2031, 6, 30, 12, 0, tzinfo=timezone.utc)

AIRPORTS = [
    {"code": "SFO", "name": "San Francisco International", "city": "San Francisco"},
    {"code": "JFK", "name": "John F. Kennedy International", "city": "New York"},
    {"code": "LAX", "name": "Los Angeles International", "city": "Los Angeles"},
    {"code": "ORD", "name": "O'Hare International", "city": "Chicago"},
    {"code": "SEA", "name": "Seattle-Tacoma International", "city": "Seattle"},
]


@dataclass(frozen=True)
class ValidFlightSearch:
    origin: str
    destination: str
    depart_date: str
    return_date: str | None
    adults: int
    children: int
    infants: int
    cabin: str
    trip_type: str
    scenario: str = "success"

    @property
    def passenger_count(self) -> int:
        return self.adults + self.children + self.infants


@dataclass
class FlightSearchSession:
    id: str
    query: dict[str, Any]
    offers: list[dict[str, Any]] = field(default_factory=list)


class FlightSearchRepository:
    """Small persistence boundary for deterministic local flight searches."""

    def __init__(self) -> None:
        self._sessions: dict[str, FlightSearchSession] = {}

    def save(self, session: FlightSearchSession) -> None:
        self._sessions[session.id] = session

    def get(self, session_id: str) -> FlightSearchSession | None:
        return self._sessions.get(session_id)

    def clear(self) -> None:
        self._sessions.clear()


flight_search_repository = FlightSearchRepository()


def handle_flight_search(
    payload: Mapping[str, Any],
    *,
    provider: FlightProvider | None = None,
    repository: FlightSearchRepository = flight_search_repository,
    now: datetime = DEFAULT_NOW,
) -> ApiResponse:
    """Validate input, run provider search, persist normalized offers, and wrap response."""

    validation = validate_flight_search_input(payload)
    if validation["errors"]:
        return error_response(
            400,
            "validation_error",
            "Flight search parameters failed validation.",
            fields=validation["errors"],
        )

    query: ValidFlightSearch = validation["query"]
    service = FlightBookingService(provider or DeterministicMockFlightProvider())
    request = FlightSearchRequest(
        origin=query.origin,
        destination=query.destination,
        depart_date=query.depart_date,
        return_date=query.return_date,
        adults=query.adults,
        children=query.children,
        infants=query.infants,
        cabin=query.cabin,
        scenario=query.scenario,
    )

    try:
        offers = service.searchFlights(request)
    except FlightProviderTimeout:
        return error_response(504, "provider_timeout", "Flight provider timed out. Please retry your search.")
    except FlightProviderError:
        return error_response(503, "provider_unavailable", "Flight provider is temporarily unavailable. Please retry.")

    offer_dtos = [flight_offer_dto(offer, now=now) for offer in offers]
    session = FlightSearchSession(id=_session_id(query), query=flight_query_dto(query), offers=offer_dtos)
    repository.save(session)

    return success_response(
        {
            "sessionId": session.id,
            "query": session.query,
            "offers": offer_dtos,
            "empty": len(offer_dtos) == 0,
        },
        meta={"resultCount": len(offer_dtos), "providerPayloadExposed": False},
    )


def validate_flight_search_input(payload: Mapping[str, Any]) -> dict[str, Any]:
    errors: dict[str, list[str]] = {}
    trip_type = str(payload.get("tripType") or "one_way").strip() or "one_way"
    if trip_type not in {"one_way", "round_trip"}:
        errors["tripType"] = ["Trip type must be one_way or round_trip."]

    origin = _airport_code(payload.get("origin"), "origin", errors)
    destination = _airport_code(payload.get("destination"), "destination", errors)
    if origin and destination and origin == destination:
        errors.setdefault("destination", []).append("Destination must be different from origin.")

    depart_date_raw = str(payload.get("departDate") or "").strip()
    depart = _parse_date(depart_date_raw, "departDate", errors)
    return_date_raw = str(payload.get("returnDate") or "").strip()
    return_date: date | None = None
    if trip_type == "round_trip":
        return_date = _parse_date(return_date_raw, "returnDate", errors)
        if depart and return_date and return_date < depart:
            errors.setdefault("returnDate", []).append("Return date must be on or after departure date.")
    elif return_date_raw:
        return_date = _parse_date(return_date_raw, "returnDate", errors)
        if depart and return_date and return_date < depart:
            errors.setdefault("returnDate", []).append("Return date must be on or after departure date.")

    adults = _integer(payload.get("adults", 1), "adults", minimum=1, errors=errors)
    children = _integer(payload.get("children", 0), "children", minimum=0, errors=errors)
    infants = _integer(payload.get("infants", 0), "infants", minimum=0, errors=errors)
    if adults is not None and children is not None and infants is not None:
        if adults + children + infants > MAX_FLIGHT_PASSENGERS:
            errors["passengers"] = [f"Total passengers must be less than or equal to {MAX_FLIGHT_PASSENGERS}."]
        if infants > adults:
            errors["infants"] = ["Infant count cannot exceed adult count."]

    cabin = str(payload.get("cabin") or "economy").strip()
    if cabin not in SUPPORTED_CABINS:
        errors["cabin"] = ["Cabin must be economy, premium_economy, business, or first."]

    scenario = str(payload.get("scenario") or "success").strip() or "success"

    return {
        "errors": errors,
        "query": None
        if errors
        else ValidFlightSearch(
            origin=origin or "",
            destination=destination or "",
            depart_date=depart_date_raw,
            return_date=return_date_raw if trip_type == "round_trip" else None,
            adults=adults or 1,
            children=children or 0,
            infants=infants or 0,
            cabin=cabin,
            trip_type=trip_type,
            scenario=scenario,
        ),
    }


def flight_query_dto(query: ValidFlightSearch) -> dict[str, Any]:
    return {
        "origin": query.origin,
        "destination": query.destination,
        "departDate": query.depart_date,
        "returnDate": query.return_date,
        "adults": query.adults,
        "children": query.children,
        "infants": query.infants,
        "cabin": query.cabin,
        "tripType": query.trip_type,
        "passengerCount": query.passenger_count,
    }


def flight_offer_dto(offer: FlightOffer, *, now: datetime = DEFAULT_NOW) -> dict[str, Any]:
    itineraries = [itinerary.to_payload() for itinerary in offer.itineraries]
    first_segment = offer.itineraries[0].segments[0]
    last_segment = offer.itineraries[-1].segments[-1]
    total_duration = sum(segment.duration_minutes for itinerary in offer.itineraries for segment in itinerary.segments)
    max_stops = max((max(len(itinerary.segments) - 1, 0) for itinerary in offer.itineraries), default=0)
    expires_at = offer.expires_at or "2031-07-01T07:45:00Z"
    expired = _is_expired(expires_at, now)

    return {
        "id": offer.id,
        "price": offer.total.to_payload(),
        "total": offer.total.to_payload(),
        "currency": offer.total.currency,
        "airline": first_segment.marketing_carrier,
        "departureAirport": first_segment.origin,
        "arrivalAirport": last_segment.destination,
        "departureTime": first_segment.departs_at,
        "arrivalTime": last_segment.arrives_at,
        "durationMinutes": total_duration,
        "stops": max_stops,
        "baggageSummary": _baggage_summary(offer.checked_bags_included),
        "expiresAt": expires_at,
        "isExpired": expired,
        "status": "expired" if expired else offer.status,
        "cabin": offer.cabin,
        "passengerCount": offer.passenger_count,
        "refundable": offer.refundable,
        "itineraries": itineraries,
    }


def airport_suggestions(query: str) -> list[dict[str, str]]:
    needle = query.strip().lower()
    if not needle:
        return AIRPORTS
    return [airport for airport in AIRPORTS if needle in airport["code"].lower() or needle in airport["name"].lower() or needle in airport["city"].lower()]


def _airport_code(value: Any, field: str, errors: dict[str, list[str]]) -> str | None:
    code = str(value or "").strip().upper()
    if not code:
        errors[field] = [f"{field.title()} airport is required."]
        return None
    if not IATA_PATTERN.fullmatch(code):
        errors[field] = ["Airport must be a three-letter IATA code."]
        return None
    return code


def _parse_date(value: str, field: str, errors: dict[str, list[str]]) -> date | None:
    if not value:
        errors[field] = ["Date is required in YYYY-MM-DD format."]
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[field] = ["Date must be a valid YYYY-MM-DD date."]
        return None


def _integer(value: Any, field: str, *, minimum: int, errors: dict[str, list[str]]) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        errors[field] = ["Must be an integer."]
        return None
    if parsed < minimum:
        errors[field] = [f"Must be greater than or equal to {minimum}."]
        return None
    return parsed


def _session_id(query: ValidFlightSearch) -> str:
    parts = [query.origin, query.destination, query.depart_date, query.return_date or "oneway", str(query.passenger_count), query.cabin]
    return "flight_search_" + "_".join(re.sub(r"[^A-Za-z0-9]+", "_", part).strip("_").lower() for part in parts)


def _baggage_summary(checked_bags_included: int | None) -> str:
    if checked_bags_included is None:
        return "Baggage details unavailable"
    if checked_bags_included == 0:
        return "No checked bags included"
    if checked_bags_included == 1:
        return "1 checked bag included"
    return f"{checked_bags_included} checked bags included"


def _is_expired(expires_at: str, now: datetime) -> bool:
    normalized = expires_at.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized) <= now
    except ValueError:
        return False
