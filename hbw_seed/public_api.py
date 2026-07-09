"""Public API contract layer for deterministic Hotel Booking Workflow data.

The module is framework-neutral so it can be used by tests, a WSGI adapter, or
server-rendered route handlers without pulling in web framework dependencies.
It exposes stable ``GET`` route contracts for hotel search, hotel detail, and
availability backed by the deterministic SQLite schema.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from math import ceil
from typing import Any
from urllib.parse import parse_qs

from .audit import record_audit_event, system_actor
from .dto import public_hotel_detail_dto, public_hotel_summary_dto, public_room_type_dto
from .reference import get_airline, get_airport, is_plausible_reference_code, search_airports

MAX_PAGE_SIZE = 50
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_GUESTS = 12
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True)
class ApiResponse:
    """HTTP-shaped response returned by public API handlers."""

    status_code: int
    body: dict[str, Any]


def success_response(data: Any, *, status_code: int = 200, meta: dict[str, Any] | None = None) -> ApiResponse:
    """Wrap successful payloads in the shared public response envelope."""

    body: dict[str, Any] = {"success": True, "data": data, "error": None}
    if meta is not None:
        body["meta"] = meta
    return ApiResponse(status_code, body)


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    fields: dict[str, list[str]] | None = None,
) -> ApiResponse:
    """Wrap failures in the shared public response envelope."""

    error: dict[str, Any] = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return ApiResponse(status_code, {"success": False, "data": None, "error": error})


def handle_get(database_path: str, path: str, query_string: str = "") -> ApiResponse:
    """Dispatch a public GET request path to the matching API handler.

    Supported routes:
    - ``/api/search/hotels``
    - ``/api/hotels/:slug``
    - ``/api/hotels/:slug/availability``
    - ``/api/reference/airports?query=...``
    - ``/api/reference/airports/:iataCode``
    - ``/api/reference/airlines/:code``
    """

    query = {key: values[-1] for key, values in parse_qs(query_string, keep_blank_values=True).items()}
    if path == "/api/search/hotels":
        return search_hotels(database_path, query)
    if path == "/api/reference/airports":
        return reference_airport_search(query)

    airport_match = re.fullmatch(r"/api/reference/airports/([^/]+)", path)
    if airport_match:
        return reference_airport_detail(airport_match.group(1))

    airline_match = re.fullmatch(r"/api/reference/airlines/([^/]+)", path)
    if airline_match:
        return reference_airline_detail(airline_match.group(1))

    detail_match = re.fullmatch(r"/api/hotels/([^/]+)", path)
    if detail_match:
        return get_hotel_detail(database_path, detail_match.group(1))

    availability_match = re.fullmatch(r"/api/hotels/([^/]+)/availability", path)
    if availability_match:
        return get_hotel_availability(database_path, availability_match.group(1), query)

    return error_response(404, "not_found", "Endpoint not found.")


def search_hotels(database_path: str, query: dict[str, str]) -> ApiResponse:
    validation = _validate_search_query(query)
    if validation["errors"]:
        return _validation_error(validation["errors"])

    destination = validation["destination"]
    check_in = validation["check_in"]
    check_out = validation["check_out"]
    guests = validation["adults"] + validation["children"]
    page = validation["page"]
    page_size = validation["page_size"]

    search_id = f"search_{re.sub(r'[^a-z0-9]+', '_', destination.lower()).strip('_')}_{check_in}_{check_out}_{guests}"
    with _connect(database_path) as connection:
        candidates = connection.execute(
            """
            SELECT hotel.*
            FROM hotels AS hotel
            WHERE hotel.is_searchable = 1
              AND (LOWER(hotel.city) LIKE ? OR LOWER(hotel.country) LIKE ? OR LOWER(hotel.name) LIKE ?)
            ORDER BY hotel.name
            """,
            (f"%{destination.lower()}%", f"%{destination.lower()}%", f"%{destination.lower()}%"),
        ).fetchall()

        matching_hotels = []
        for hotel in candidates:
            room_types = _public_room_types(connection, hotel["id"], check_in, check_out, guests)
            if not room_types:
                continue
            minimum_price = min((room_type["price"] for room_type in room_types), key=lambda price: price["amountCents"])
            matching_hotels.append(
                public_hotel_summary_dto(
                    hotel,
                    images=_hotel_images(connection, hotel["id"]),
                    amenities=_amenities(connection, hotel["id"]),
                    review_summary=_review_summary(connection, hotel["id"]),
                    minimum_price=minimum_price,
                    available_room_types=len(room_types),
                )
            )

        record_audit_event(
            connection,
            actor=system_actor(),
            event_type="search.performed",
            entity_type="search",
            entity_id=search_id,
            metadata={
                "destination": destination,
                "checkIn": check_in,
                "checkOut": check_out,
                "adults": validation["adults"],
                "children": validation["children"],
                "resultCount": len(matching_hotels),
                "auditWritePolicy": "best effort; search correctness wins",
            },
            created_at="2031-04-01T09:00:00Z",
            search_id=search_id,
        )
        connection.commit()

    total = len(matching_hotels)
    start = (page - 1) * page_size
    end = start + page_size
    data = matching_hotels[start:end]
    meta = {
        "pagination": {
            "page": page,
            "pageSize": page_size,
            "total": total,
            "totalPages": ceil(total / page_size) if total else 0,
        },
        "query": {
            "destination": destination,
            "checkIn": check_in,
            "checkOut": check_out,
            "adults": validation["adults"],
            "children": validation["children"],
        },
    }
    return success_response(data, meta=meta)


def reference_airport_search(query: dict[str, str]) -> ApiResponse:
    return success_response(search_airports(query.get("query")))


def reference_airport_detail(iata_code: str) -> ApiResponse:
    if not is_plausible_reference_code(iata_code):
        return _validation_error({"iataCode": ["Airport code must be 2 to 4 letters or numbers."]})
    airport = get_airport(iata_code)
    if airport is None:
        return error_response(404, "not_found", "Airport not found.")
    return success_response(airport)


def reference_airline_detail(code: str) -> ApiResponse:
    if not is_plausible_reference_code(code):
        return _validation_error({"code": ["Airline code must be 2 to 4 letters or numbers."]})
    airline = get_airline(code)
    if airline is None:
        return error_response(404, "not_found", "Airline not found.")
    return success_response(airline)


def get_hotel_detail(database_path: str, slug: str) -> ApiResponse:
    slug_errors = _validate_slug(slug)
    if slug_errors:
        return _validation_error({"slug": slug_errors})

    with _connect(database_path) as connection:
        hotel = _active_hotel_by_slug(connection, slug)
        if hotel is None:
            return _not_found()

        room_types = _public_room_types(connection, hotel["id"], None, None, 1)
        data = public_hotel_detail_dto(
            hotel,
            images=_hotel_images(connection, hotel["id"]),
            amenities=_amenities(connection, hotel["id"]),
            policies=_policies(connection, hotel["id"]),
            reviews=_reviews(connection, hotel["id"]),
            review_summary=_review_summary(connection, hotel["id"]),
            room_types=room_types,
        )
    return success_response(data)


def get_hotel_availability(database_path: str, slug: str, query: dict[str, str]) -> ApiResponse:
    errors: dict[str, list[str]] = {}
    slug_errors = _validate_slug(slug)
    if slug_errors:
        errors["slug"] = slug_errors

    validation = _validate_stay_and_occupancy(query, require_destination=False)
    errors.update(validation["errors"])
    if errors:
        return _validation_error(errors)

    check_in = validation["check_in"]
    check_out = validation["check_out"]
    guests = validation["adults"] + validation["children"]

    with _connect(database_path) as connection:
        hotel = _active_hotel_by_slug(connection, slug)
        if hotel is None:
            return _not_found()
        room_types = _public_room_types(connection, hotel["id"], check_in, check_out, guests)

    data = {
        "hotelSlug": slug,
        "checkIn": check_in,
        "checkOut": check_out,
        "adults": validation["adults"],
        "children": validation["children"],
        "available": any(room_type["availableRooms"] > 0 for room_type in room_types),
        "roomTypes": room_types,
    }
    return success_response(data)


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _validate_search_query(query: dict[str, str]) -> dict[str, Any]:
    validation = _validate_stay_and_occupancy(query, require_destination=True)
    errors = validation["errors"]
    page, page_errors = _positive_int(query.get("page", str(DEFAULT_PAGE)), "page", minimum=1)
    if page_errors:
        errors["page"] = page_errors
    page_size, page_size_errors = _positive_int(query.get("pageSize", str(DEFAULT_PAGE_SIZE)), "pageSize", minimum=1)
    if page_size_errors:
        errors["pageSize"] = page_size_errors
    elif page_size > MAX_PAGE_SIZE:
        errors["pageSize"] = [f"Must be less than or equal to {MAX_PAGE_SIZE}."]

    validation["page"] = page or DEFAULT_PAGE
    validation["page_size"] = page_size or DEFAULT_PAGE_SIZE
    return validation


def _validate_stay_and_occupancy(query: dict[str, str], *, require_destination: bool) -> dict[str, Any]:
    errors: dict[str, list[str]] = {}
    destination = (query.get("destination") or "").strip()
    if require_destination and not destination:
        errors["destination"] = ["Destination is required."]

    check_in_raw = (query.get("checkIn") or "").strip()
    check_out_raw = (query.get("checkOut") or "").strip()
    check_in = _parse_date(check_in_raw, "checkIn", errors)
    check_out = _parse_date(check_out_raw, "checkOut", errors)
    if check_in and check_out and check_out <= check_in:
        errors.setdefault("checkOut", []).append("Must be after checkIn.")

    adults, adult_errors = _positive_int(query.get("adults", "1"), "adults", minimum=1)
    if adult_errors:
        errors["adults"] = adult_errors
    children, children_errors = _positive_int(query.get("children", "0"), "children", minimum=0)
    if children_errors:
        errors["children"] = children_errors
    if adults is not None and children is not None and adults + children > MAX_GUESTS:
        errors["occupancy"] = [f"Total guests must be less than or equal to {MAX_GUESTS}."]

    return {
        "errors": errors,
        "destination": destination,
        "check_in": check_in_raw,
        "check_out": check_out_raw,
        "adults": adults if adults is not None else 1,
        "children": children if children is not None else 0,
    }


def _parse_date(value: str, field: str, errors: dict[str, list[str]]) -> date | None:
    if not value:
        errors[field] = ["Date is required in YYYY-MM-DD format."]
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[field] = ["Date must be a valid YYYY-MM-DD date."]
        return None


def _positive_int(value: str, field: str, *, minimum: int) -> tuple[int | None, list[str]]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, ["Must be an integer."]
    if parsed < minimum:
        return None, [f"Must be greater than or equal to {minimum}."]
    return parsed, []


def _validate_slug(slug: str) -> list[str]:
    if not slug or not SLUG_PATTERN.fullmatch(slug):
        return ["Slug must contain lowercase letters, numbers, and hyphens only."]
    return []


def _validation_error(fields: dict[str, list[str]]) -> ApiResponse:
    return error_response(400, "validation_error", "Request parameters failed validation.", fields=fields)


def _not_found() -> ApiResponse:
    return error_response(404, "not_found", "Hotel not found.")


def _active_hotel_by_slug(connection: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM hotels WHERE slug = ? AND is_searchable = 1",
        (slug,),
    ).fetchone()


def _hotel_images(connection: sqlite3.Connection, hotel_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT url, alt_text
        FROM hotel_images
        WHERE hotel_id = ?
        ORDER BY sort_order, id
        """,
        (hotel_id,),
    ).fetchall()
    return [{"url": row["url"], "altText": row["alt_text"]} for row in rows]


def _room_images(connection: sqlite3.Connection, room_type_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT url, alt_text
        FROM room_images
        WHERE room_type_id = ?
        ORDER BY sort_order, id
        """,
        (room_type_id,),
    ).fetchall()
    return [{"url": row["url"], "altText": row["alt_text"]} for row in rows]


def _amenities(connection: sqlite3.Connection, hotel_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT amenity.name
        FROM amenities AS amenity
        JOIN hotel_amenities AS hotel_amenity ON hotel_amenity.amenity_id = amenity.id
        WHERE hotel_amenity.hotel_id = ?
        ORDER BY amenity.name
        """,
        (hotel_id,),
    ).fetchall()
    return [{"name": row["name"]} for row in rows]


def _policies(connection: sqlite3.Connection, hotel_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT policy_type, description
        FROM hotel_policies
        WHERE hotel_id = ?
        ORDER BY policy_type, id
        """,
        (hotel_id,),
    ).fetchall()
    return [{"type": row["policy_type"], "description": row["description"]} for row in rows]


def _reviews(connection: sqlite3.Connection, hotel_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT author_name, rating, title, body, created_at
        FROM reviews
        WHERE hotel_id = ? AND status = 'published'
        ORDER BY created_at DESC
        """,
        (hotel_id,),
    ).fetchall()
    return [
        {
            "authorName": row["author_name"],
            "rating": row["rating"],
            "title": row["title"],
            "body": row["body"],
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


def _review_summary(connection: sqlite3.Connection, hotel_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT COUNT(*) AS count, AVG(rating) AS average
        FROM reviews
        WHERE hotel_id = ? AND status = 'published'
        """,
        (hotel_id,),
    ).fetchone()
    average = row["average"]
    return {"count": row["count"], "averageRating": round(average, 2) if average is not None else None}


def _public_room_types(
    connection: sqlite3.Connection,
    hotel_id: str,
    check_in: str | None,
    check_out: str | None,
    guests: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT room_type.*
        FROM room_types AS room_type
        WHERE room_type.hotel_id = ?
          AND room_type.capacity >= ?
          AND EXISTS (
            SELECT 1 FROM rooms AS room
            WHERE room.room_type_id = room_type.id AND room.status = 'active'
          )
        ORDER BY room_type.nightly_rate_cents, room_type.name
        """,
        (hotel_id, guests),
    ).fetchall()

    room_types = []
    for row in rows:
        available_rooms = _available_room_count(connection, hotel_id, row["id"], check_in, check_out)
        if check_in is not None and check_out is not None and available_rooms <= 0:
            continue
        room_types.append(public_room_type_dto(row, images=_room_images(connection, row["id"]), available_rooms=available_rooms))
    return room_types


def _available_room_count(
    connection: sqlite3.Connection,
    hotel_id: str,
    room_type_id: str,
    check_in: str | None,
    check_out: str | None,
) -> int:
    if check_in is None or check_out is None:
        return connection.execute(
            """
            SELECT COUNT(*)
            FROM rooms
            WHERE room_type_id = ? AND status = 'active'
            """,
            (room_type_id,),
        ).fetchone()[0]

    return connection.execute(
        """
        SELECT COUNT(*)
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
        """,
        (room_type_id, hotel_id, check_out, check_in, room_type_id, check_out, check_in),
    ).fetchone()[0]
