"""Public API contract layer for deterministic Hotel Booking Workflow data.

The module is framework-neutral so it can be used by tests, a WSGI adapter, or
server-rendered route handlers without pulling in web framework dependencies.
It exposes stable ``GET`` route contracts for hotel search, hotel detail, and
availability backed by the deterministic SQLite schema.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from math import ceil
from typing import Any
from urllib.parse import parse_qs

from .abuse import (
    DEFAULT_IDEMPOTENCY,
    DEFAULT_RATE_LIMITER,
    ENDPOINT_RATE_LIMIT_POLICIES,
    IdempotencyService,
    RateLimiter,
    RequestContext,
    body_within_limit,
    build_rate_limit_key,
    require_idempotency_key,
)

MAX_PAGE_SIZE = 50
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_GUESTS = 12
MAX_MUTATION_BODY_BYTES = 8_192
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


def handle_get(
    database_path: str,
    path: str,
    query_string: str = "",
    *,
    context: RequestContext | None = None,
    rate_limiter: RateLimiter | None = None,
) -> ApiResponse:
    """Dispatch a public GET request path to the matching API handler.

    Supported routes:
    - ``/api/search/hotels``
    - ``/api/hotels/:slug``
    - ``/api/hotels/:slug/availability``
    - ``/api/reservations/confirmation/:code``
    """

    query = {key: values[-1] for key, values in parse_qs(query_string, keep_blank_values=True).items()}
    if path == "/api/search/hotels":
        limited = _rate_limit("search", context=context, rate_limiter=rate_limiter)
        if limited is not None:
            return limited
        return search_hotels(database_path, query)

    confirmation_match = re.fullmatch(r"/api/reservations/confirmation/([^/]+)", path)
    if confirmation_match:
        limited = _rate_limit("confirmation_lookup", context=context, rate_limiter=rate_limiter)
        if limited is not None:
            return limited
        return get_reservation_confirmation(database_path, confirmation_match.group(1))

    detail_match = re.fullmatch(r"/api/hotels/([^/]+)", path)
    if detail_match:
        return get_hotel_detail(database_path, detail_match.group(1))

    availability_match = re.fullmatch(r"/api/hotels/([^/]+)/availability", path)
    if availability_match:
        return get_hotel_availability(database_path, availability_match.group(1), query)

    return error_response(404, "not_found", "Endpoint not found.")


def handle_post(
    database_path: str,
    path: str,
    body: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    context: RequestContext | None = None,
    rate_limiter: RateLimiter | None = None,
    idempotency: IdempotencyService | None = None,
) -> ApiResponse:
    """Dispatch framework-neutral POST routes with abuse guards applied."""

    headers = headers or {}
    if not body_within_limit(body, MAX_MUTATION_BODY_BYTES):
        return error_response(413, "request_too_large", "Request body exceeds the allowed size.")

    if path == "/api/auth/sign-in":
        limited = _rate_limit("sign_in", context=context, rate_limiter=rate_limiter)
        if limited is not None:
            return limited
        return sign_in(database_path, body)

    if path == "/api/reservations":
        return _idempotent_mutation(
            "reservation_create",
            headers,
            body,
            lambda: create_reservation(database_path, body, context=context),
            context=context,
            rate_limiter=rate_limiter,
            idempotency=idempotency,
        )

    if path == "/api/payments/intents":
        reservation_id = str(body.get("reservationId") or "")
        return _idempotent_mutation(
            "payment_intent_create",
            headers,
            body,
            lambda: create_payment_intent(database_path, body),
            context=context,
            rate_limiter=rate_limiter,
            idempotency=idempotency,
            discriminator=reservation_id,
        )

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
                {
                    "slug": hotel["slug"],
                    "name": hotel["name"],
                    "city": hotel["city"],
                    "country": hotel["country"],
                    "starRating": hotel["star_rating"],
                    "description": hotel["description"],
                    "images": _hotel_images(connection, hotel["id"]),
                    "amenities": _amenities(connection, hotel["id"]),
                    "reviewSummary": _review_summary(connection, hotel["id"]),
                    "price": minimum_price,
                    "availableRoomTypes": len(room_types),
                }
            )

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


def get_hotel_detail(database_path: str, slug: str) -> ApiResponse:
    slug_errors = _validate_slug(slug)
    if slug_errors:
        return _validation_error({"slug": slug_errors})

    with _connect(database_path) as connection:
        hotel = _active_hotel_by_slug(connection, slug)
        if hotel is None:
            return _not_found()

        room_types = _public_room_types(connection, hotel["id"], None, None, 1)
        data = {
            "slug": hotel["slug"],
            "name": hotel["name"],
            "city": hotel["city"],
            "country": hotel["country"],
            "address": hotel["address"],
            "starRating": hotel["star_rating"],
            "description": hotel["description"],
            "images": _hotel_images(connection, hotel["id"]),
            "amenities": _amenities(connection, hotel["id"]),
            "policies": _policies(connection, hotel["id"]),
            "reviews": _reviews(connection, hotel["id"]),
            "reviewSummary": _review_summary(connection, hotel["id"]),
            "roomTypes": room_types,
        }
    return success_response(data)


def get_reservation_confirmation(database_path: str, confirmation_code: str) -> ApiResponse:
    if not re.fullmatch(r"[A-Za-z0-9_]{8,64}", confirmation_code):
        return _validation_error({"confirmationCode": ["Confirmation code format is invalid."]})

    with _connect(database_path) as connection:
        reservation = connection.execute(
            """
            SELECT reservation.*, hotel.slug AS hotel_slug, hotel.name AS hotel_name
            FROM reservations AS reservation
            JOIN hotels AS hotel ON hotel.id = reservation.hotel_id
            WHERE reservation.id = ?
            """,
            (confirmation_code,),
        ).fetchone()
        if reservation is None:
            return error_response(404, "not_found", "Reservation not found.")

    return success_response(
        {
            "confirmationCode": reservation["id"],
            "hotelSlug": reservation["hotel_slug"],
            "hotelName": reservation["hotel_name"],
            "checkIn": reservation["check_in"],
            "checkOut": reservation["check_out"],
            "status": reservation["status"],
            "total": {"amountCents": reservation["total_cents"], "currency": reservation["currency"]},
        }
    )


def sign_in(database_path: str, body: dict[str, Any]) -> ApiResponse:
    email = str(body.get("email") or "").strip().lower()
    if not email:
        return _validation_error({"email": ["Email is required."]})
    with _connect(database_path) as connection:
        user = connection.execute("SELECT id, email, full_name, role FROM users WHERE LOWER(email) = ?", (email,)).fetchone()
    if user is None:
        return error_response(401, "invalid_credentials", "Email or password is incorrect.")
    return success_response({"user": {"id": user["id"], "email": user["email"], "fullName": user["full_name"], "role": user["role"]}})


def create_reservation(database_path: str, body: dict[str, Any], *, context: RequestContext | None = None) -> ApiResponse:
    required = ["hotelSlug", "roomTypeCode", "guestEmail", "guestName", "checkIn", "checkOut"]
    errors = {field: ["Field is required."] for field in required if not str(body.get(field) or "").strip()}
    validation = _validate_stay_and_occupancy(
        {
            "checkIn": str(body.get("checkIn") or ""),
            "checkOut": str(body.get("checkOut") or ""),
            "adults": str(body.get("adults", "1")),
            "children": str(body.get("children", "0")),
        },
        require_destination=False,
    )
    errors.update(validation["errors"])
    if errors:
        return _validation_error(errors)

    with _connect(database_path) as connection:
        hotel = _active_hotel_by_slug(connection, str(body["hotelSlug"]))
        if hotel is None:
            return _not_found()
        room_type = connection.execute(
            "SELECT * FROM room_types WHERE id = ? AND hotel_id = ?", (str(body["roomTypeCode"]), hotel["id"])
        ).fetchone()
        if room_type is None:
            return error_response(404, "not_found", "Room type not found.")
        room = _first_available_room(connection, hotel["id"], room_type["id"], validation["check_in"], validation["check_out"])
        if room is None:
            return error_response(409, "inventory_unavailable", "Requested room type is no longer available.")

        nights = (date.fromisoformat(validation["check_out"]) - date.fromisoformat(validation["check_in"])).days
        total_cents = nights * int(room_type["nightly_rate_cents"])
        reservation_fingerprint = hashlib.sha256(
            "|".join(
                str(body[field]) for field in ("hotelSlug", "roomTypeCode", "guestEmail", "checkIn", "checkOut")
            ).encode("utf-8")
        ).hexdigest()[:12]
        reservation_id = f"res_idem_{reservation_fingerprint}"
        user_id = context.user_id if context else None
        checkout_type = "authenticated" if user_id else "guest"
        connection.execute(
            """
            INSERT INTO reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_payment', ?, ?, ?, ?, NULL, ?)
            """,
            (
                reservation_id,
                hotel["id"],
                room_type["id"],
                room["id"],
                user_id,
                str(body["guestEmail"]),
                str(body["guestName"]),
                validation["check_in"],
                validation["check_out"],
                checkout_type,
                total_cents,
                room_type["currency"],
                "2031-03-10T10:00:00Z",
                "2031-03-10T10:15:00Z",
            ),
        )
        connection.commit()
        room_type_currency = room_type["currency"]
    return success_response(
        {
            "confirmationCode": reservation_id,
            "status": "pending_payment",
            "total": {"amountCents": total_cents, "currency": room_type_currency},
        },
        status_code=201,
    )


def create_payment_intent(database_path: str, body: dict[str, Any]) -> ApiResponse:
    reservation_id = str(body.get("reservationId") or "").strip()
    if not reservation_id:
        return _validation_error({"reservationId": ["Reservation id is required."]})
    with _connect(database_path) as connection:
        reservation = connection.execute("SELECT * FROM reservations WHERE id = ?", (reservation_id,)).fetchone()
        if reservation is None:
            return error_response(404, "not_found", "Reservation not found.")
        payment_id = f"pay_intent_{reservation_id}"
        existing = connection.execute("SELECT * FROM payment_records WHERE id = ?", (payment_id,)).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO payment_records VALUES (?, ?, 'fixture_gateway', ?, ?, ?, 'authorized', ?)",
                (
                    payment_id,
                    reservation_id,
                    f"fx_intent_{reservation_id}",
                    reservation["total_cents"],
                    reservation["currency"],
                    "2031-03-10T10:01:00Z",
                ),
            )
            connection.commit()
    return success_response(
        {
            "paymentIntentId": payment_id,
            "reservationId": reservation_id,
            "amount": {"amountCents": reservation["total_cents"], "currency": reservation["currency"]},
            "status": "authorized",
        },
        status_code=201,
    )


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


def _rate_limit(
    endpoint: str,
    *,
    context: RequestContext | None,
    rate_limiter: RateLimiter | None,
    discriminator: str | None = None,
) -> ApiResponse | None:
    policy = ENDPOINT_RATE_LIMIT_POLICIES[endpoint]
    limiter = rate_limiter or DEFAULT_RATE_LIMITER
    request_context = context or RequestContext()
    key = build_rate_limit_key(policy, request_context, discriminator)
    try:
        result = limiter.check(key, policy)
    except Exception:
        if policy.fail_open:
            return None
        return error_response(503, "rate_limiter_unavailable", "Rate limiter is unavailable.")
    if result.allowed:
        return None
    return error_response(
        429,
        "rate_limit_exceeded",
        "Too many requests. Please retry later.",
        fields={"retryAfterSeconds": [str(result.retry_after_seconds)], "policy": [policy.name]},
    )


def _idempotent_mutation(
    endpoint: str,
    headers: dict[str, str],
    body: dict[str, Any],
    operation,
    *,
    context: RequestContext | None,
    rate_limiter: RateLimiter | None,
    idempotency: IdempotencyService | None,
    discriminator: str | None = None,
) -> ApiResponse:
    idempotency_key = require_idempotency_key(headers)
    if idempotency_key is None:
        return error_response(
            400,
            "idempotency_key_required",
            "A valid Idempotency-Key header is required for this mutation.",
        )
    limited = _rate_limit(endpoint, context=context, rate_limiter=rate_limiter, discriminator=discriminator)
    if limited is not None:
        return limited
    service = idempotency or DEFAULT_IDEMPOTENCY
    response, replayed = service.run(endpoint, idempotency_key, body, operation)
    if replayed:
        response.body.setdefault("meta", {})["idempotentReplay"] = True
    return response


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


def _first_available_room(
    connection: sqlite3.Connection,
    hotel_id: str,
    room_type_id: str,
    check_in: str,
    check_out: str,
) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT room.*
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
        ORDER BY room.room_number
        LIMIT 1
        """,
        (room_type_id, hotel_id, check_out, check_in, room_type_id, check_out, check_in),
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
        room_types.append(
            {
                "code": row["id"],
                "name": row["name"],
                "capacity": row["capacity"],
                "description": row["description"],
                "price": {
                    "amountCents": row["nightly_rate_cents"],
                    "currency": row["currency"],
                    "unit": "night",
                },
                "images": _room_images(connection, row["id"]),
                "availableRooms": available_rooms,
            }
        )
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
