from hbw_seed import reset_and_seed
from hbw_seed.hotel_detail_page import render_hotel_detail_html, render_hotel_detail_page


def seeded_database(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    return database


def test_hotel_detail_page_renders_static_content_and_requires_dates_before_booking(tmp_path):
    database = seeded_database(tmp_path)

    response = render_hotel_detail_page(str(database), "bay-view-grand")

    assert response.status_code == 200
    page = response.body["data"]
    assert page["route"] == "/hotels/bay-view-grand"
    assert page["hotel"]["name"] == "Bay View Grand"
    assert page["gallery"][0]["altText"] == "Bay View Grand exterior"
    assert page["hotel"]["description"] == "Waterfront business hotel used for partial availability and sold-out fixtures."
    assert page["hotel"]["locationSummary"] == "100 Market Street, San Francisco, CA · San Francisco, US"
    assert {amenity["name"] for amenity in page["amenities"]} == {"Breakfast", "Pool", "Spa", "Wi-Fi"}
    assert {policy["type"] for policy in page["policies"]} == {"cancellation", "check_in"}
    assert [review["title"] for review in page["reviews"]] == ["Reliable stay"]
    assert page["reviewSummary"] == {"count": 1, "averageRating": 5.0}
    assert page["notice"]["status"] == "dates_required"
    assert {room["name"] for room in page["roomCards"]} == {"Deluxe King", "Executive Suite"}
    assert all(room["totalPrice"] is None for room in page["roomCards"])
    assert all(room["selectRoom"]["enabled"] is False for room in page["roomCards"])


def test_hotel_detail_page_uses_query_parameters_for_availability_prices_and_booking_links(tmp_path):
    database = seeded_database(tmp_path)

    response = render_hotel_detail_page(
        str(database),
        "mission-garden-inn",
        "checkIn=2031-06-10&checkOut=2031-06-12&adults=2&children=1",
    )

    assert response.status_code == 200
    page = response.body["data"]
    assert page["status"] == "success"
    assert page["selector"]["validDates"] is True
    assert page["selector"]["shareableUrl"] == "/hotels/mission-garden-inn?checkIn=2031-06-10&checkOut=2031-06-12&adults=2&children=1"
    assert page["availability"]["available"] is True
    assert [room["roomTypeId"] for room in page["roomCards"]] == ["rt_garden_family"]
    room = page["roomCards"][0]
    assert room["occupancy"] == 4
    assert room["bedDescription"] == "Studio with sofa bed"
    assert room["nightlyPrice"] == {"amountCents": 26000, "currency": "USD", "formatted": "USD 260.00"}
    assert room["totalPrice"] == {"amountCents": 52000, "currency": "USD", "formatted": "USD 520.00"}
    assert room["availabilityLabel"] == "Only 1 room left"
    assert room["selectRoom"] == {
        "enabled": True,
        "label": "Select room",
        "href": "/booking/guest-details?hotelSlug=mission-garden-inn&roomTypeId=rt_garden_family&checkIn=2031-06-10&checkOut=2031-06-12&adults=2&children=1",
    }


def test_hotel_detail_page_handles_sold_out_invalid_query_and_not_found(tmp_path):
    database = seeded_database(tmp_path)

    sold_out = render_hotel_detail_page(
        str(database),
        "bay-view-grand",
        "checkIn=2031-06-10&checkOut=2031-06-12&adults=5&children=0",
    )
    invalid = render_hotel_detail_page(
        str(database),
        "bay-view-grand",
        "checkIn=not-a-date&checkOut=2031-06-10&adults=0&children=13",
    )
    not_found = render_hotel_detail_page(str(database), "missing-hotel")

    assert sold_out.status_code == 200
    assert sold_out.body["data"]["status"] == "sold_out"
    assert sold_out.body["data"]["roomCards"] == []
    assert sold_out.body["data"]["notice"]["heading"] == "No rooms available for these dates"

    assert invalid.status_code == 200
    invalid_page = invalid.body["data"]
    assert invalid_page["status"] == "validation_error"
    assert invalid_page["selector"]["errors"] == {
        "checkIn": ["Date must be a valid YYYY-MM-DD date."],
        "adults": ["Must be greater than or equal to 1."],
    }
    assert invalid_page["selector"]["form"]["errorSummary"] == "Please correct 2 field errors before continuing."
    assert all(room["selectRoom"]["enabled"] is False for room in invalid_page["roomCards"])

    assert not_found.status_code == 404
    assert not_found.body["error"] == {"code": "not_found", "message": "Hotel not found."}


def test_hotel_detail_html_contains_accessible_sections_and_published_reviews_only(tmp_path):
    database = seeded_database(tmp_path)

    response = render_hotel_detail_html(
        str(database),
        "bay-view-grand",
        "checkIn=2031-06-11&checkOut=2031-06-12&adults=4&children=0",
    )

    assert response.status_code == 200
    html = response.body["data"]
    assert '<main id="main-content">' in html
    assert '<h2 id="gallery-heading">Gallery</h2>' in html
    assert '<h2 id="rooms-heading">Rooms</h2>' in html
    assert 'Executive Suite' in html
    assert 'USD 420.00 total' in html
    assert 'href="/booking/guest-details?hotelSlug=bay-view-grand&amp;roomTypeId=rt_bay_suite&amp;checkIn=2031-06-11&amp;checkOut=2031-06-12&amp;adults=4&amp;children=0"' in html
    assert 'Reliable stay' in html
    assert 'Needs moderation' not in html
