from __future__ import annotations

from datetime import datetime, timezone

from hbw_seed.flight_search import (
    FlightSearchRepository,
    airport_suggestions,
    handle_flight_search,
    validate_flight_search_input,
)


VALID_SEARCH = {
    "origin": "SFO",
    "destination": "JFK",
    "departDate": "2031-07-01",
    "adults": 1,
    "children": 0,
    "infants": 0,
    "cabin": "economy",
}


def test_flight_search_validation_rejects_route_dates_and_passenger_rules():
    validation = validate_flight_search_input(
        {
            "origin": "SFO",
            "destination": "SFO",
            "departDate": "2031-07-10",
            "returnDate": "2031-07-01",
            "tripType": "round_trip",
            "adults": 1,
            "children": 8,
            "infants": 2,
            "cabin": "private_suite",
        }
    )

    assert validation["query"] is None
    assert validation["errors"]["destination"] == ["Destination must be different from origin."]
    assert validation["errors"]["returnDate"] == ["Return date must be on or after departure date."]
    assert validation["errors"]["passengers"] == ["Total passengers must be less than or equal to 9."]
    assert validation["errors"]["infants"] == ["Infant count cannot exceed adult count."]
    assert validation["errors"]["cabin"] == ["Cabin must be economy, premium_economy, business, or first."]


def test_flight_search_success_creates_session_persists_frontend_safe_offers_for_one_way_and_round_trip():
    repository = FlightSearchRepository()
    one_way = handle_flight_search(VALID_SEARCH, repository=repository)
    round_trip = handle_flight_search(
        {**VALID_SEARCH, "tripType": "round_trip", "returnDate": "2031-07-08", "adults": 2, "infants": 1},
        repository=repository,
    )

    assert one_way.status_code == 200
    assert one_way.body["data"]["query"]["passengerCount"] == 1
    assert len(one_way.body["data"]["offers"]) == 2
    assert round_trip.status_code == 200
    assert round_trip.body["data"]["query"]["returnDate"] == "2031-07-08"
    assert round_trip.body["data"]["query"]["passengerCount"] == 3
    assert len(round_trip.body["data"]["offers"]) == 3

    saved = repository.get(round_trip.body["data"]["sessionId"])
    assert saved is not None
    assert saved.offers == round_trip.body["data"]["offers"]

    offer = round_trip.body["data"]["offers"][0]
    assert offer["price"] == {"amountCents": 85800, "currency": "USD", "formatted": "USD 858.00"}
    assert offer["airline"] == "OA"
    assert offer["departureAirport"] == "SFO"
    assert offer["arrivalAirport"] == "JFK"
    assert offer["departureTime"].endswith("-07:00")
    assert offer["arrivalTime"].endswith("-07:00") or offer["arrivalTime"].endswith("-04:00")
    assert offer["durationMinutes"] > 0
    assert offer["stops"] == 0
    assert offer["baggageSummary"] == "1 checked bag included"
    assert offer["expiresAt"] == "2031-07-01T07:45:00Z"
    assert offer["isExpired"] is False
    assert "provider_reference" not in offer
    assert "providerReference" not in offer
    assert "providerMeta" not in str(round_trip.body)


def test_flight_search_empty_results_are_recoverable_standard_success_shape():
    response = handle_flight_search({**VALID_SEARCH, "scenario": "no_availability"})

    assert response.status_code == 200
    assert response.body["success"] is True
    assert response.body["data"]["offers"] == []
    assert response.body["data"]["empty"] is True
    assert response.body["meta"] == {"resultCount": 0, "providerPayloadExposed": False}


def test_flight_search_provider_failure_uses_standard_recoverable_errors():
    timeout = handle_flight_search({**VALID_SEARCH, "scenario": "timeout"})
    unavailable = handle_flight_search({**VALID_SEARCH, "scenario": "error"})

    assert timeout.status_code == 504
    assert timeout.body == {
        "success": False,
        "data": None,
        "error": {"code": "provider_timeout", "message": "Flight provider timed out. Please retry your search."},
    }
    assert unavailable.status_code == 503
    assert unavailable.body["error"]["code"] == "provider_unavailable"
    assert "retry" in unavailable.body["error"]["message"].lower()


def test_flight_search_marks_expired_offers_and_supports_airport_autocomplete():
    response = handle_flight_search(
        VALID_SEARCH,
        now=datetime(2031, 7, 1, 8, 0, tzinfo=timezone.utc),
    )

    assert response.body["data"]["offers"][0]["isExpired"] is True
    assert response.body["data"]["offers"][0]["status"] == "expired"
    assert airport_suggestions("san") == [{"code": "SFO", "name": "San Francisco International", "city": "San Francisco"}]
