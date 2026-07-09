import sqlite3

from hbw_seed import reset_and_seed
from hbw_seed.public_api import MAX_PAGE_SIZE, handle_get

SEARCH_QUERY = "destination=San%20Francisco&checkIn=2031-06-10&checkOut=2031-06-12&adults=2&children=0&page=1&pageSize=10"

SENSITIVE_KEYS = {
    "id",
    "hotel_id",
    "room_id",
    "room_number",
    "floor",
    "reason",
    "user_id",
    "guest_email",
    "guest_name",
    "provider_reference",
}


def seeded_database(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    return database


def assert_enveloped_success(response):
    assert response.status_code == 200
    assert response.body["success"] is True
    assert response.body["error"] is None
    assert "data" in response.body


def assert_no_sensitive_keys(value):
    if isinstance(value, dict):
        assert not (set(value) & SENSITIVE_KEYS)
        for child in value.values():
            assert_no_sensitive_keys(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_sensitive_keys(child)


def test_get_search_hotels_success_response_shape_and_public_fields(tmp_path):
    database = seeded_database(tmp_path)

    response = handle_get(str(database), "/api/search/hotels", SEARCH_QUERY)

    assert_enveloped_success(response)
    assert response.body["meta"] == {
        "pagination": {"page": 1, "pageSize": 10, "total": 1, "totalPages": 1},
        "query": {
            "destination": "San Francisco",
            "checkIn": "2031-06-10",
            "checkOut": "2031-06-12",
            "adults": 2,
            "children": 0,
        },
    }
    assert response.body["data"] == [
        {
            "slug": "mission-garden-inn",
            "name": "Mission Garden Inn",
            "city": "San Francisco",
            "country": "US",
            "starRating": 4,
            "description": "Boutique hotel used for room-type closure and guest checkout fixtures.",
            "images": [
                {
                    "url": "https://fixtures.example.test/hotels/mission-garden-inn/courtyard.jpg",
                    "altText": "Mission Garden Inn courtyard",
                }
            ],
            "amenities": [{"name": "Breakfast"}, {"name": "Parking"}, {"name": "Wi-Fi"}],
            "reviewSummary": {"count": 1, "averageRating": 4.0},
            "price": {"amountCents": 26000, "currency": "USD", "unit": "night"},
            "availableRoomTypes": 1,
        }
    ]
    assert_no_sensitive_keys(response.body)


def test_get_search_hotels_empty_search_response_shape(tmp_path):
    database = seeded_database(tmp_path)

    response = handle_get(
        str(database),
        "/api/search/hotels",
        "destination=Tokyo&checkIn=2031-06-10&checkOut=2031-06-12&adults=2&children=0",
    )

    assert_enveloped_success(response)
    assert response.body["data"] == []
    assert response.body["meta"]["pagination"] == {"page": 1, "pageSize": 20, "total": 0, "totalPages": 0}


def test_get_search_hotels_invalid_inputs_return_structured_errors(tmp_path):
    database = seeded_database(tmp_path)

    response = handle_get(
        str(database),
        "/api/search/hotels",
        f"destination=&checkIn=2031-06-12&checkOut=2031-06-10&adults=0&children=-1&page=0&pageSize={MAX_PAGE_SIZE + 1}",
    )

    assert response.status_code == 400
    assert response.body["success"] is False
    assert response.body["data"] is None
    assert response.body["error"]["code"] == "validation_error"
    assert response.body["error"]["fields"] == {
        "destination": ["Destination is required."],
        "checkOut": ["Must be after checkIn."],
        "adults": ["Must be greater than or equal to 1."],
        "children": ["Must be greater than or equal to 0."],
        "page": ["Must be greater than or equal to 1."],
        "pageSize": [f"Must be less than or equal to {MAX_PAGE_SIZE}."],
    }


def test_get_hotel_detail_success_hides_unpublished_and_internal_data(tmp_path):
    database = seeded_database(tmp_path)

    response = handle_get(str(database), "/api/hotels/bay-view-grand")

    assert_enveloped_success(response)
    hotel = response.body["data"]
    assert hotel["slug"] == "bay-view-grand"
    assert hotel["address"] == "100 Market Street, San Francisco, CA"
    assert [review["title"] for review in hotel["reviews"]] == ["Reliable stay"]
    assert {room_type["code"] for room_type in hotel["roomTypes"]} == {"rt_bay_king", "rt_bay_suite"}
    assert all(room_type["availableRooms"] == 2 for room_type in hotel["roomTypes"])
    assert_no_sensitive_keys(response.body)


def test_get_hotel_unknown_and_inactive_hotel_return_not_found(tmp_path):
    database = seeded_database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE hotels SET is_searchable = 0 WHERE slug = 'central-loft-hotel'")
        connection.commit()

    unknown = handle_get(str(database), "/api/hotels/not-a-real-hotel")
    inactive = handle_get(str(database), "/api/hotels/central-loft-hotel")

    for response in (unknown, inactive):
        assert response.status_code == 404
        assert response.body == {
            "success": False,
            "data": None,
            "error": {"code": "not_found", "message": "Hotel not found."},
        }


def test_get_hotel_detail_invalid_slug_returns_validation_error(tmp_path):
    database = seeded_database(tmp_path)

    response = handle_get(str(database), "/api/hotels/Bay_View_Grand")

    assert response.status_code == 400
    assert response.body["error"]["code"] == "validation_error"
    assert response.body["error"]["fields"] == {
        "slug": ["Slug must contain lowercase letters, numbers, and hyphens only."]
    }


def test_get_hotel_availability_success_shape_and_hides_block_reasons(tmp_path):
    database = seeded_database(tmp_path)

    response = handle_get(
        str(database),
        "/api/hotels/mission-garden-inn/availability",
        "checkIn=2031-06-10&checkOut=2031-06-12&adults=2&children=1",
    )

    assert_enveloped_success(response)
    assert response.body["data"] == {
        "hotelSlug": "mission-garden-inn",
        "checkIn": "2031-06-10",
        "checkOut": "2031-06-12",
        "adults": 2,
        "children": 1,
        "available": True,
        "roomTypes": [
            {
                "code": "rt_garden_family",
                "name": "Family Studio",
                "capacity": 4,
                "description": "Studio with sofa bed and kitchenette.",
                "price": {"amountCents": 26000, "currency": "USD", "unit": "night"},
                "images": [
                    {
                        "url": "https://fixtures.example.test/rooms/family-studio.jpg",
                        "altText": "Family Studio room",
                    }
                ],
                "availableRooms": 1,
                "remainingQuantity": 1,
                "nightlyRate": {"amountCents": 26000, "currency": "USD", "formatted": "USD 260.00"},
                "totalPreTax": {"amountCents": 52000, "currency": "USD", "formatted": "USD 520.00"},
                "occupancy": {"adults": 2, "children": 1, "totalGuests": 3, "capacity": 4, "compatible": True},
                "unavailableReasons": ["reserved"],
            }
        ],
    }
    assert_no_sensitive_keys(response.body)


def test_get_hotel_availability_validation_and_not_found_cases(tmp_path):
    database = seeded_database(tmp_path)

    invalid = handle_get(
        str(database),
        "/api/hotels/bad_slug/availability",
        "checkIn=not-a-date&checkOut=2031-06-10&adults=13&children=0",
    )
    not_found = handle_get(
        str(database),
        "/api/hotels/unknown-hotel/availability",
        "checkIn=2031-06-10&checkOut=2031-06-12&adults=1&children=0",
    )

    assert invalid.status_code == 400
    assert invalid.body["error"]["fields"] == {
        "slug": ["Slug must contain lowercase letters, numbers, and hyphens only."],
        "checkIn": ["Date must be a valid YYYY-MM-DD date."],
        "occupancy": ["Total guests must be less than or equal to 12."],
    }
    assert not_found.status_code == 404
    assert not_found.body["error"]["code"] == "not_found"
