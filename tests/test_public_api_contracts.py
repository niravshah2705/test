import sqlite3

from hbw_seed import reset_and_seed
from hbw_seed.abuse import InMemoryIdempotencyStore, InMemoryRateLimiter, IdempotencyService, RequestContext
from hbw_seed.public_api import MAX_PAGE_SIZE, handle_get, handle_post

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


def test_rate_limited_search_allows_then_uses_shared_error_envelope(tmp_path):
    database = seeded_database(tmp_path)
    limiter = InMemoryRateLimiter()
    context = RequestContext(ip_address="198.51.100.10")

    first = handle_get(str(database), "/api/search/hotels", SEARCH_QUERY, context=context, rate_limiter=limiter)
    assert_enveloped_success(first)
    for _ in range(29):
        handle_get(str(database), "/api/search/hotels", SEARCH_QUERY, context=context, rate_limiter=limiter)

    exceeded = handle_get(str(database), "/api/search/hotels", SEARCH_QUERY, context=context, rate_limiter=limiter)

    assert exceeded.status_code == 429
    assert exceeded.body["success"] is False
    assert exceeded.body["data"] is None
    assert exceeded.body["error"]["code"] == "rate_limit_exceeded"
    assert exceeded.body["error"]["fields"]["policy"] == ["search"]


def test_authenticated_search_uses_user_key_not_shared_ip(tmp_path):
    database = seeded_database(tmp_path)
    limiter = InMemoryRateLimiter()
    query = SEARCH_QUERY
    shared_ip = "203.0.113.44"

    for _ in range(30):
        response = handle_get(
            str(database), query_string=query, path="/api/search/hotels", context=RequestContext(shared_ip, "usr_guest"), rate_limiter=limiter
        )
        assert response.status_code == 200

    second_user = handle_get(
        str(database), "/api/search/hotels", query, context=RequestContext(shared_ip, "usr_admin"), rate_limiter=limiter
    )
    anonymous_shared_ip = handle_get(
        str(database), "/api/search/hotels", query, context=RequestContext(shared_ip), rate_limiter=limiter
    )
    exceeded_guest = handle_get(
        str(database), "/api/search/hotels", query, context=RequestContext(shared_ip, "usr_guest"), rate_limiter=limiter
    )

    assert second_user.status_code == 200
    assert anonymous_shared_ip.status_code == 200
    assert exceeded_guest.status_code == 429


def test_sensitive_mutations_require_idempotency_key_and_replay_existing_result(tmp_path):
    database = seeded_database(tmp_path)
    limiter = InMemoryRateLimiter()
    idempotency = IdempotencyService(InMemoryIdempotencyStore())
    body = {
        "hotelSlug": "mission-garden-inn",
        "roomTypeCode": "rt_garden_family",
        "guestEmail": "retry@example.test",
        "guestName": "Riley Retry",
        "checkIn": "2031-07-01",
        "checkOut": "2031-07-03",
        "adults": 2,
        "children": 0,
    }

    missing_key = handle_post(str(database), "/api/reservations", body, rate_limiter=limiter, idempotency=idempotency)
    created = handle_post(
        str(database),
        "/api/reservations",
        body,
        headers={"Idempotency-Key": "reservation-key-1"},
        context=RequestContext("198.51.100.20", "usr_guest"),
        rate_limiter=limiter,
        idempotency=idempotency,
    )
    replay = handle_post(
        str(database),
        "/api/reservations",
        body,
        headers={"Idempotency-Key": "reservation-key-1"},
        context=RequestContext("198.51.100.20", "usr_guest"),
        rate_limiter=limiter,
        idempotency=idempotency,
    )

    assert missing_key.status_code == 400
    assert missing_key.body["error"]["code"] == "idempotency_key_required"
    assert created.status_code == 201
    assert replay.status_code == 201
    assert replay.body["data"] == created.body["data"]
    assert replay.body["meta"] == {"idempotentReplay": True}


def test_sign_in_confirmation_lookup_reservation_and_payment_are_guarded(tmp_path):
    database = seeded_database(tmp_path)
    limiter = InMemoryRateLimiter()
    idempotency = IdempotencyService(InMemoryIdempotencyStore())

    sign_in = handle_post(
        str(database), "/api/auth/sign-in", {"email": "guest@example.test"}, context=RequestContext("192.0.2.10"), rate_limiter=limiter
    )
    confirmation = handle_get(
        str(database),
        "/api/reservations/confirmation/res_bay_king_auth_confirmed",
        context=RequestContext("192.0.2.11"),
        rate_limiter=limiter,
    )
    reservation = handle_post(
        str(database),
        "/api/reservations",
        {
            "hotelSlug": "mission-garden-inn",
            "roomTypeCode": "rt_garden_family",
            "guestEmail": "pay@example.test",
            "guestName": "Parker Pay",
            "checkIn": "2031-07-05",
            "checkOut": "2031-07-06",
        },
        headers={"Idempotency-Key": "reservation-key-2"},
        rate_limiter=limiter,
        idempotency=idempotency,
    )
    payment = handle_post(
        str(database),
        "/api/payments/intents",
        {"reservationId": reservation.body["data"]["confirmationCode"]},
        headers={"Idempotency-Key": "payment-key-1"},
        rate_limiter=limiter,
        idempotency=idempotency,
    )

    assert sign_in.status_code == 200
    assert confirmation.status_code == 200
    assert reservation.status_code == 201
    assert payment.status_code == 201
    assert payment.body["data"]["paymentIntentId"].startswith("pay_intent_")
