from hbw_seed.public_api import handle_get
from hbw_seed.reference import airline_display, airport_display, reference_seed_summary


def assert_success(response):
    assert response.status_code == 200
    assert response.body["success"] is True
    assert response.body["error"] is None


def test_airport_search_matches_exact_code_and_includes_reference_fields():
    response = handle_get(":memory:", "/api/reference/airports", "query=SFO")

    assert_success(response)
    assert response.body["data"][0] == {
        "code": "SFO",
        "iataCode": "SFO",
        "displayName": "San Francisco International Airport (SFO)",
        "name": "San Francisco International Airport",
        "city": "San Francisco",
        "country": "US",
        "timezone": "America/Los_Angeles",
    }


def test_airport_search_matches_partial_city():
    response = handle_get(":memory:", "/api/reference/airports", "query=Fran")

    assert_success(response)
    assert [airport["code"] for airport in response.body["data"]] == ["SFO"]


def test_airport_search_matches_airport_name():
    response = handle_get(":memory:", "/api/reference/airports", "query=kennedy")

    assert_success(response)
    assert [airport["code"] for airport in response.body["data"]] == ["JFK"]


def test_airport_search_returns_no_results_for_unknown_query():
    response = handle_get(":memory:", "/api/reference/airports", "query=not-a-city")

    assert_success(response)
    assert response.body["data"] == []


def test_airport_search_is_case_insensitive_for_lowercase_input():
    response = handle_get(":memory:", "/api/reference/airports", "query=sfo")

    assert_success(response)
    assert response.body["data"][0]["code"] == "SFO"


def test_airport_and_airline_detail_endpoints_lookup_codes_case_insensitively():
    airport = handle_get(":memory:", "/api/reference/airports/sfo")
    airline = handle_get(":memory:", "/api/reference/airlines/oca")

    assert_success(airport)
    assert airport.body["data"]["timezone"] == "America/Los_Angeles"
    assert_success(airline)
    assert airline.body["data"] == {
        "code": "OA",
        "iataCode": "OA",
        "icaoCode": "OCA",
        "displayName": "Oceanic Air",
    }


def test_unknown_airport_and_airline_codes_fall_back_safely_for_displays():
    assert airport_display("ZZZ") == "ZZZ"
    assert airline_display("ZZ") == "ZZ"


def test_reference_seed_dataset_is_available_for_development_and_tests():
    assert reference_seed_summary()["airports"] >= 8
    assert reference_seed_summary()["airlines"] >= 7
