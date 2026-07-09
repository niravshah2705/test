"""DTO mappers that separate public, guest-owned, admin, and payment-safe data."""

from __future__ import annotations

from typing import Any, Mapping

def _format_money(amount_cents: int, currency: str = "USD") -> dict[str, Any]:
    return {"amountCents": amount_cents, "currency": currency, "formatted": f"{currency} {amount_cents / 100:.2f}"}


def public_room_type_dto(row: Mapping[str, Any], *, images: list[dict[str, Any]], available_rooms: int) -> dict[str, Any]:
    """Return public-safe room type data without physical room identifiers."""

    return {
        "code": row["id"],
        "name": row["name"],
        "capacity": row["capacity"],
        "description": row["description"],
        "price": {
            "amountCents": row["nightly_rate_cents"],
            "currency": row["currency"],
            "unit": "night",
        },
        "images": images,
        "availableRooms": available_rooms,
    }


def public_hotel_summary_dto(hotel: Mapping[str, Any], *, images: list[dict[str, Any]], amenities: list[dict[str, Any]], review_summary: dict[str, Any], minimum_price: dict[str, Any], available_room_types: int) -> dict[str, Any]:
    """Return search-result hotel data safe for unauthenticated clients."""

    return {
        "slug": hotel["slug"],
        "name": hotel["name"],
        "city": hotel["city"],
        "country": hotel["country"],
        "starRating": hotel["star_rating"],
        "description": hotel["description"],
        "images": images,
        "amenities": amenities,
        "reviewSummary": review_summary,
        "price": minimum_price,
        "availableRoomTypes": available_room_types,
    }


def public_hotel_detail_dto(hotel: Mapping[str, Any], *, images: list[dict[str, Any]], amenities: list[dict[str, Any]], policies: list[dict[str, Any]], reviews: list[dict[str, Any]], review_summary: dict[str, Any], room_types: list[dict[str, Any]]) -> dict[str, Any]:
    """Return public hotel detail without inactive inventory or operational data."""

    return {
        "slug": hotel["slug"],
        "name": hotel["name"],
        "city": hotel["city"],
        "country": hotel["country"],
        "address": hotel["address"],
        "starRating": hotel["star_rating"],
        "description": hotel["description"],
        "images": images,
        "amenities": amenities,
        "policies": policies,
        "reviews": reviews,
        "reviewSummary": review_summary,
        "roomTypes": room_types,
    }


def guest_reservation_dto(row: Mapping[str, Any], *, duplicate_request: bool = False, refund: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return guest-owned reservation data without user or physical room IDs."""

    payload: dict[str, Any] = {
        "id": row["id"],
        "hotelId": row["hotel_id"],
        "roomTypeId": row["room_type_id"],
        "guestEmail": row["guest_email"],
        "guestName": row["guest_name"],
        "checkIn": row["check_in"],
        "checkOut": row["check_out"],
        "status": row["status"],
        "checkoutType": row["checkout_type"],
        "total": _format_money(row["total_cents"], row["currency"]),
        "expiresAt": row["expires_at"],
        "cancelledAt": row["cancelled_at"],
    }
    if duplicate_request:
        payload["duplicateRequest"] = True
    if refund is not None:
        payload["refund"] = refund
    return payload


def admin_reservation_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return operational reservation data intended only for admins."""

    return {
        "id": row["id"],
        "hotelId": row["hotel_id"],
        "roomTypeId": row["room_type_id"],
        "roomId": row["room_id"],
        "userId": row["user_id"],
        "guestEmail": row["guest_email"],
        "guestName": row["guest_name"],
        "checkIn": row["check_in"],
        "checkOut": row["check_out"],
        "status": row["status"],
        "checkoutType": row["checkout_type"],
        "total": _format_money(row["total_cents"], row["currency"]),
        "expiresAt": row["expires_at"],
        "cancelledAt": row["cancelled_at"],
    }


def payment_safe_dto(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return payment status without provider references or method secrets."""

    return {
        "id": row["id"],
        "reservationId": row["reservation_id"],
        "provider": row["provider"],
        "amount": _format_money(row["amount_cents"], row["currency"]),
        "status": row["status"],
        "createdAt": row["created_at"],
    }
