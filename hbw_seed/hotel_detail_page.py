"""Server-rendered hotel detail page contract for deterministic HBW data.

The module keeps the hotel detail page framework-neutral: route handlers can use the
returned page dictionary directly, while tests can assert the important rendering
contract without depending on a web framework or template engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
from typing import Any

from .money import format_money
from .occupancy import MAX_GUESTS
from .stay import MAX_STAY_NIGHTS, StayValidationError, night_count
from urllib.parse import parse_qs, urlencode

from .public_api import ApiResponse, error_response, get_hotel_availability, get_hotel_detail, success_response
from .ui_contracts import build_form_contract

BOOKING_ENTRY_PATH = "/booking/guest-details"


@dataclass(frozen=True)
class HotelDetailPageResponse:
    """HTTP-shaped server-rendered hotel detail page response."""

    status_code: int
    body: dict[str, Any]


def render_hotel_detail_page(database_path: str, slug: str, query_string: str = "") -> HotelDetailPageResponse:
    """Build the server-rendered hotel detail page addressed by hotel slug.

    Static hotel content is always loaded from the detail route. Date and
    occupancy controls are driven by shareable query parameters. Missing dates are
    allowed and keep room information visible, but booking actions are disabled
    until the selected dates are valid. Invalid query parameters are returned as
    validation feedback on a successful page response instead of failing the whole
    page.
    """

    detail_response = get_hotel_detail(database_path, slug)
    if detail_response.status_code == 404:
        return _page_error(404, "not_found", "Hotel not found.")
    if detail_response.status_code != 200:
        return _page_error(detail_response.status_code, detail_response.body["error"]["code"], detail_response.body["error"]["message"], detail_response.body["error"].get("fields"))

    hotel = detail_response.body["data"]
    query = _last_query_values(query_string)
    stay = _parse_page_query(query)
    form_contract = build_form_contract("hotel_detail", stay["errors"])

    room_types = hotel["roomTypes"]
    has_date_query = bool(stay["checkIn"] or stay["checkOut"])
    has_valid_dates = has_date_query and not stay["errors"]
    availability: dict[str, Any] | None = None
    page_status = "success"
    notice: dict[str, Any] | None = None

    if stay["errors"]:
        page_status = "validation_error"
        notice = {
            "status": "validation_error",
            "heading": "Check your dates and guests",
            "message": "Hotel details are still available, but room selection requires valid dates and occupancy.",
            "fields": stay["errors"],
        }
    elif has_valid_dates:
        availability_response = get_hotel_availability(database_path, slug, _availability_query(stay))
        if availability_response.status_code == 200:
            availability = availability_response.body["data"]
            room_types = availability["roomTypes"]
            if not room_types:
                page_status = "sold_out"
                notice = {
                    "status": "sold_out",
                    "heading": "No rooms available for these dates",
                    "message": "Try a different date range or adjust the number of guests.",
                }
        else:
            page_status = availability_response.body["error"]["code"]
            notice = {
                "status": availability_response.body["error"]["code"],
                "heading": "Availability could not be loaded",
                "message": availability_response.body["error"]["message"],
                "fields": availability_response.body["error"].get("fields", {}),
            }
    else:
        notice = {
            "status": "dates_required",
            "heading": "Choose dates to book",
            "message": "Room information is visible now. Add check-in and check-out dates before selecting a room.",
        }

    nights = _nights(stay["checkIn"], stay["checkOut"]) if has_valid_dates else None
    room_cards = [
        _room_card(hotel, room_type, stay, nights=nights, can_book=has_valid_dates and room_type["availableRooms"] > 0)
        for room_type in room_types
    ]

    page = {
        "route": f"/hotels/{hotel['slug']}",
        "status": page_status,
        "hotel": {
            "slug": hotel["slug"],
            "name": hotel["name"],
            "description": hotel["description"],
            "starRating": hotel["starRating"],
            "address": hotel["address"],
            "locationSummary": f"{hotel['address']} · {hotel['city']}, {hotel['country']}",
            "city": hotel["city"],
            "country": hotel["country"],
        },
        "gallery": hotel["images"],
        "amenities": hotel["amenities"],
        "policies": hotel["policies"],
        "reviews": hotel["reviews"],
        "reviewSummary": hotel["reviewSummary"],
        "selector": {
            "query": {
                "checkIn": stay["checkIn"],
                "checkOut": stay["checkOut"],
                "adults": stay["adults"],
                "children": stay["children"],
            },
            "shareableUrl": _shareable_url(hotel["slug"], stay),
            "form": form_contract,
            "errors": stay["errors"],
            "validDates": has_valid_dates,
        },
        "availability": availability,
        "notice": notice,
        "roomCards": room_cards,
    }
    return HotelDetailPageResponse(200, {"success": True, "data": page, "error": None})


def render_hotel_detail_html(database_path: str, slug: str, query_string: str = "") -> ApiResponse:
    """Render minimal accessible HTML for the hotel detail page contract."""

    response = render_hotel_detail_page(database_path, slug, query_string)
    if response.status_code != 200:
        return ApiResponse(response.status_code, response.body)

    page = response.body["data"]
    parts = [
        '<main id="main-content">',
        f'<h1>{escape(page["hotel"]["name"])}</h1>',
        f'<p>{escape(page["hotel"]["locationSummary"])}</p>',
        f'<section aria-labelledby="gallery-heading"><h2 id="gallery-heading">Gallery</h2>',
    ]
    for image in page["gallery"]:
        parts.append(f'<img src="{escape(image["url"])}" alt="{escape(image["altText"])}" />')
    parts.append("</section>")
    parts.append(f'<section aria-labelledby="overview-heading"><h2 id="overview-heading">Overview</h2><p>{escape(page["hotel"]["description"])}</p></section>')
    parts.append('<section aria-labelledby="amenities-heading"><h2 id="amenities-heading">Amenities</h2><ul>')
    parts.extend(f'<li>{escape(amenity["name"])}</li>' for amenity in page["amenities"])
    parts.append("</ul></section>")
    parts.append('<section aria-labelledby="policies-heading"><h2 id="policies-heading">Policies</h2><ul>')
    parts.extend(f'<li><strong>{escape(policy["type"])}</strong>: {escape(policy["description"])}</li>' for policy in page["policies"])
    parts.append("</ul></section>")
    if page["notice"]:
        role = "alert" if page["notice"]["status"] == "validation_error" else "status"
        parts.append(f'<div role="{role}"><h2>{escape(page["notice"]["heading"])}</h2><p>{escape(page["notice"]["message"])}</p></div>')
    parts.append('<section aria-labelledby="rooms-heading"><h2 id="rooms-heading">Rooms</h2>')
    for room in page["roomCards"]:
        parts.append(f'<article data-room-type="{escape(room["roomTypeId"])}"><h3>{escape(room["name"])}</h3>')
        parts.append(f'<p>{escape(room["description"])}</p>')
        parts.append(f'<p>{escape(room["bedDescription"])} · sleeps {room["occupancy"]}</p>')
        parts.append(f'<p>{escape(room["nightlyPrice"]["formatted"]) } per night</p>')
        if room["totalPrice"]:
            parts.append(f'<p>{escape(room["totalPrice"]["formatted"])} total</p>')
        parts.append(f'<p>{escape(room["availabilityLabel"])}</p>')
        if room["selectRoom"]["enabled"]:
            parts.append(f'<a href="{escape(room["selectRoom"]["href"])}">Select room</a>')
        else:
            parts.append(f'<button type="button" disabled>{escape(room["selectRoom"]["label"])}</button>')
        parts.append("</article>")
    parts.append("</section>")
    parts.append('<section aria-labelledby="reviews-heading"><h2 id="reviews-heading">Reviews</h2>')
    for review in page["reviews"]:
        parts.append(f'<article><h3>{escape(review["title"])}</h3><p>{escape(review["body"])}</p><p>{escape(review["authorName"])} · {review["rating"]}/5</p></article>')
    parts.append("</section></main>")
    return success_response("".join(parts))


def _last_query_values(query_string: str) -> dict[str, str]:
    return {key: values[-1] for key, values in parse_qs(query_string, keep_blank_values=True).items()}


def _parse_page_query(query: dict[str, str]) -> dict[str, Any]:
    errors: dict[str, list[str]] = {}
    check_in = (query.get("checkIn") or "").strip()
    check_out = (query.get("checkOut") or "").strip()
    adults_raw = (query.get("adults") or "1").strip()
    children_raw = (query.get("children") or "0").strip()
    has_date_query = bool(check_in or check_out)

    if has_date_query:
        check_in_date = _parse_date(check_in, "checkIn", errors)
        check_out_date = _parse_date(check_out, "checkOut", errors)
        if check_in_date and check_out_date:
            nights = check_out_date.toordinal() - check_in_date.toordinal()
            if nights <= 0:
                errors.setdefault("checkOut", []).append("Must be after checkIn.")
            elif nights > MAX_STAY_NIGHTS:
                errors.setdefault("checkOut", []).append(f"Stay cannot exceed {MAX_STAY_NIGHTS} nights.")
    else:
        check_in_date = None
        check_out_date = None

    adults = _parse_int(adults_raw, "adults", errors, minimum=1)
    children = _parse_int(children_raw, "children", errors, minimum=0)
    if adults is not None and children is not None and adults + children > MAX_GUESTS:
        errors["occupancy"] = [f"Total guests must be less than or equal to {MAX_GUESTS}."]

    return {
        "checkIn": check_in,
        "checkOut": check_out,
        "checkInDate": check_in_date,
        "checkOutDate": check_out_date,
        "adults": adults if adults is not None else 1,
        "children": children if children is not None else 0,
        "errors": errors,
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


def _parse_int(value: str, field: str, errors: dict[str, list[str]], *, minimum: int) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        errors[field] = ["Must be an integer."]
        return None
    if parsed < minimum:
        errors[field] = [f"Must be greater than or equal to {minimum}."]
        return None
    return parsed


def _availability_query(stay: dict[str, Any]) -> dict[str, str]:
    return {
        "checkIn": stay["checkIn"],
        "checkOut": stay["checkOut"],
        "adults": str(stay["adults"]),
        "children": str(stay["children"]),
    }


def _room_card(hotel: dict[str, Any], room_type: dict[str, Any], stay: dict[str, Any], *, nights: int | None, can_book: bool) -> dict[str, Any]:
    nightly_amount = room_type["price"]["amountCents"]
    currency = room_type["price"]["currency"]
    total = _money(nightly_amount * nights, currency) if nights is not None else None
    availability_label = _availability_label(room_type["availableRooms"])
    return {
        "roomTypeId": room_type["code"],
        "name": room_type["name"],
        "description": room_type["description"],
        "occupancy": room_type["capacity"],
        "bedDescription": _bed_description(room_type["name"], room_type["description"]),
        "images": room_type["images"],
        "nightlyPrice": _money(nightly_amount, currency),
        "totalPrice": total,
        "availableRooms": room_type["availableRooms"],
        "availabilityLabel": availability_label,
        "selectRoom": _select_room_action(hotel["slug"], room_type["code"], stay, enabled=can_book),
    }


def _money(amount_cents: int, currency: str) -> dict[str, Any]:
    return format_money(amount_cents, currency)


def _availability_label(available_rooms: int) -> str:
    if available_rooms <= 0:
        return "Sold out for selected dates"
    if available_rooms == 1:
        return "Only 1 room left"
    return f"{available_rooms} rooms left"


def _select_room_action(hotel_slug: str, room_type_id: str, stay: dict[str, Any], *, enabled: bool) -> dict[str, Any]:
    if not enabled:
        return {
            "enabled": False,
            "label": "Select dates to book" if not stay["checkIn"] or not stay["checkOut"] else "Unavailable for selected dates",
            "href": None,
        }
    query = {
        "hotelSlug": hotel_slug,
        "roomTypeId": room_type_id,
        "checkIn": stay["checkIn"],
        "checkOut": stay["checkOut"],
        "adults": str(stay["adults"]),
        "children": str(stay["children"]),
    }
    return {"enabled": True, "label": "Select room", "href": f"{BOOKING_ENTRY_PATH}?{urlencode(query)}"}


def _shareable_url(slug: str, stay: dict[str, Any]) -> str:
    query = {
        "checkIn": stay["checkIn"],
        "checkOut": stay["checkOut"],
        "adults": str(stay["adults"]),
        "children": str(stay["children"]),
    }
    filtered = {key: value for key, value in query.items() if value != ""}
    return f"/hotels/{slug}" + (f"?{urlencode(filtered)}" if filtered else "")


def _nights(check_in: str, check_out: str) -> int:
    try:
        return night_count(check_in, check_out)
    except StayValidationError:
        return 0


def _bed_description(name: str, description: str) -> str:
    text = f"{name} {description}".lower()
    if "king" in text:
        return "1 king bed"
    if "queen" in text:
        return "1 queen bed"
    if "double double" in text:
        return "2 double beds"
    if "suite" in text:
        return "Suite bedding"
    if "studio" in text or "sofa bed" in text:
        return "Studio with sofa bed"
    return "Bed details provided by the hotel"


def _page_error(status_code: int, code: str, message: str, fields: dict[str, list[str]] | None = None) -> HotelDetailPageResponse:
    api_error = error_response(status_code, code, message, fields=fields)
    return HotelDetailPageResponse(api_error.status_code, api_error.body)
