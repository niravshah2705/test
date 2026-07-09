"""Flight search persistence helpers and frontend-safe DTOs.

The flight tables intentionally normalize display and booking decision fields while
keeping provider revalidation references in a constrained side table that is not
exposed by DTO helpers.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping


class FlightPersistenceError(ValueError):
    """Raised for invalid flight persistence helper inputs."""


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _format_money(amount_cents: int, currency: str) -> dict[str, Any]:
    return {"amountCents": amount_cents, "currency": currency, "formatted": f"{currency} {amount_cents / 100:.2f}"}


def _parse_instant(value: str) -> datetime:
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise FlightPersistenceError("Instant values must include a timezone offset.")
    return parsed.astimezone(timezone.utc)


def is_offer_expired(expires_at: str, *, now: str) -> bool:
    """Return whether an offer has expired at the supplied ISO instant."""

    return _parse_instant(now) >= _parse_instant(expires_at)


def flight_search_request_dto(database_path: str, search_request_id: str) -> dict[str, Any]:
    """Return normalized search request criteria and legs for display/debugging."""

    with _connect(database_path) as connection:
        request = connection.execute(
            "SELECT * FROM flight_search_requests WHERE id = ?",
            (search_request_id,),
        ).fetchone()
        if request is None:
            raise FlightPersistenceError("Flight search request not found.")
        legs = connection.execute(
            """
            SELECT origin_airport_code, destination_airport_code, departure_date
            FROM flight_search_legs
            WHERE search_request_id = ?
            ORDER BY leg_index
            """,
            (search_request_id,),
        ).fetchall()
        passengers = connection.execute(
            """
            SELECT passenger_type, count
            FROM flight_search_passengers
            WHERE search_request_id = ?
            ORDER BY passenger_type
            """,
            (search_request_id,),
        ).fetchall()

    return {
        "id": request["id"],
        "tripType": request["trip_type"],
        "cabin": request["cabin"],
        "fareBrand": request["fare_brand"],
        "currency": request["currency"],
        "requestedAt": request["requested_at"],
        "legs": [
            {
                "origin": row["origin_airport_code"],
                "destination": row["destination_airport_code"],
                "departureDate": row["departure_date"],
            }
            for row in legs
        ],
        "passengers": {row["passenger_type"]: row["count"] for row in passengers},
    }


def flight_offer_dto(
    database_path: str,
    offer_id: str,
    *,
    now: str = "2031-06-01T12:00:00Z",
) -> dict[str, Any]:
    """Return a frontend-safe flight offer without provider raw payloads/references."""

    with _connect(database_path) as connection:
        offer = connection.execute(
            """
            SELECT offer.*, session.search_request_id
            FROM flight_offers AS offer
            JOIN flight_search_sessions AS session ON session.id = offer.search_session_id
            WHERE offer.id = ?
            """,
            (offer_id,),
        ).fetchone()
        if offer is None:
            raise FlightPersistenceError("Flight offer not found.")

        itineraries = connection.execute(
            """
            SELECT *
            FROM flight_offer_itineraries
            WHERE offer_id = ?
            ORDER BY itinerary_index
            """,
            (offer_id,),
        ).fetchall()
        segments = connection.execute(
            """
            SELECT segment.*, marketing.name AS marketing_carrier_name,
                   operating.name AS operating_carrier_name
            FROM flight_offer_segments AS segment
            LEFT JOIN flight_carriers AS marketing ON marketing.code = segment.marketing_carrier_code
            LEFT JOIN flight_carriers AS operating ON operating.code = segment.operating_carrier_code
            WHERE segment.offer_id = ?
            ORDER BY segment.itinerary_index, segment.segment_index
            """,
            (offer_id,),
        ).fetchall()
        fare_details = connection.execute(
            """
            SELECT *
            FROM flight_fare_details
            WHERE offer_id = ?
            ORDER BY passenger_type, cabin, fare_basis_code
            """,
            (offer_id,),
        ).fetchall()
        passenger_pricing = connection.execute(
            """
            SELECT *
            FROM flight_passenger_type_pricing
            WHERE offer_id = ?
            ORDER BY passenger_type
            """,
            (offer_id,),
        ).fetchall()
        baggage = connection.execute(
            """
            SELECT *
            FROM flight_baggage_summaries
            WHERE offer_id = ?
            ORDER BY passenger_type, segment_id
            """,
            (offer_id,),
        ).fetchall()

    segments_by_itinerary: dict[int, list[dict[str, Any]]] = {row["itinerary_index"]: [] for row in itineraries}
    for row in segments:
        segments_by_itinerary.setdefault(row["itinerary_index"], []).append(_segment_dto(row))

    return {
        "id": offer["id"],
        "searchSessionId": offer["search_session_id"],
        "searchRequestId": offer["search_request_id"],
        "tripType": offer["trip_type"],
        "source": offer["source"],
        "status": "expired" if is_offer_expired(offer["expires_at"], now=now) else offer["status"],
        "expiresAt": offer["expires_at"],
        "lastTicketingDate": offer["last_ticketing_date"],
        "price": {
            "base": _format_money(offer["base_amount_cents"], offer["currency"]),
            "taxes": _format_money(offer["tax_amount_cents"], offer["currency"]),
            "total": _format_money(offer["total_amount_cents"], offer["currency"]),
        },
        "refundable": bool(offer["refundable"]),
        "changeable": bool(offer["changeable"]),
        "itineraries": [
            {
                "index": itinerary["itinerary_index"],
                "durationMinutes": itinerary["duration_minutes"],
                "origin": itinerary["origin_airport_code"],
                "destination": itinerary["destination_airport_code"],
                "segments": segments_by_itinerary.get(itinerary["itinerary_index"], []),
            }
            for itinerary in itineraries
        ],
        "fareDetails": [_fare_detail_dto(row) for row in fare_details],
        "passengerPricing": [_passenger_pricing_dto(row) for row in passenger_pricing],
        "baggage": [_baggage_dto(row) for row in baggage],
    }


def list_flight_offer_dtos(
    database_path: str,
    search_session_id: str,
    *,
    now: str = "2031-06-01T12:00:00Z",
) -> list[dict[str, Any]]:
    """Return all frontend-safe offers for a search session."""

    with _connect(database_path) as connection:
        offer_ids = [
            row[0]
            for row in connection.execute(
                """
                SELECT id
                FROM flight_offers
                WHERE search_session_id = ?
                ORDER BY total_amount_cents, id
                """,
                (search_session_id,),
            ).fetchall()
        ]
    return [flight_offer_dto(database_path, offer_id, now=now) for offer_id in offer_ids]


def provider_reference_for_revalidation(database_path: str, offer_id: str) -> dict[str, Any]:
    """Return the constrained provider reference used server-side for revalidation."""

    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT provider, provider_offer_id, provider_search_id, reference_expires_at, revalidation_token_hash
            FROM flight_offer_provider_refs
            WHERE offer_id = ?
            """,
            (offer_id,),
        ).fetchone()
    if row is None:
        raise FlightPersistenceError("Flight offer provider reference not found.")
    return {
        "provider": row["provider"],
        "providerOfferId": row["provider_offer_id"],
        "providerSearchId": row["provider_search_id"],
        "referenceExpiresAt": row["reference_expires_at"],
        "revalidationTokenHash": row["revalidation_token_hash"],
    }


def _segment_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    codeshare = row["marketing_carrier_code"] != row["operating_carrier_code"]
    return {
        "id": row["id"],
        "index": row["segment_index"],
        "origin": row["origin_airport_code"],
        "destination": row["destination_airport_code"],
        "departure": {
            "localDateTime": row["departure_local_datetime"],
            "timezone": row["departure_timezone"],
            "utcOffsetMinutes": row["departure_utc_offset_minutes"],
            "terminal": row["departure_terminal"],
        },
        "arrival": {
            "localDateTime": row["arrival_local_datetime"],
            "timezone": row["arrival_timezone"],
            "utcOffsetMinutes": row["arrival_utc_offset_minutes"],
            "terminal": row["arrival_terminal"],
        },
        "durationMinutes": row["duration_minutes"],
        "overnight": bool(row["overnight"]),
        "flightNumber": row["flight_number"],
        "marketingCarrier": {"code": row["marketing_carrier_code"], "name": row["marketing_carrier_name"]},
        "operatingCarrier": {"code": row["operating_carrier_code"], "name": row["operating_carrier_name"]},
        "aircraft": row["aircraft_code"],
        "bookingClass": row["booking_class"],
        "cabin": row["cabin"],
        "fareBrand": row["fare_brand"],
        "codeshare": codeshare,
    }


def _fare_detail_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "passengerType": row["passenger_type"],
        "cabin": row["cabin"],
        "fareBrand": row["fare_brand"],
        "fareBasisCode": row["fare_basis_code"],
        "bookingClass": row["booking_class"],
        "segmentId": row["segment_id"],
    }


def _passenger_pricing_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "passengerType": row["passenger_type"],
        "count": row["passenger_count"],
        "base": _format_money(row["base_amount_cents"], row["currency"]),
        "taxes": _format_money(row["tax_amount_cents"], row["currency"]),
        "total": _format_money(row["total_amount_cents"], row["currency"]),
    }


def _baggage_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "passengerType": row["passenger_type"],
        "segmentId": row["segment_id"],
        "carryOnPieces": row["carry_on_pieces"],
        "checkedPieces": row["checked_pieces"],
        "checkedWeightKg": row["checked_weight_kg"],
        "description": row["description"],
    }
