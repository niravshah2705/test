import sqlite3

from hbw_seed import reset_and_seed
from hbw_seed.flights import (
    flight_offer_dto,
    flight_search_request_dto,
    is_offer_expired,
    list_flight_offer_dtos,
    provider_reference_for_revalidation,
)


FLIGHT_TABLES = {
    "flight_search_requests",
    "flight_search_legs",
    "flight_search_passengers",
    "flight_search_sessions",
    "flight_offers",
    "flight_offer_provider_refs",
    "flight_offer_itineraries",
    "flight_offer_segments",
    "flight_fare_details",
    "flight_passenger_type_pricing",
    "flight_baggage_summaries",
    "flight_carriers",
}


def seeded_database(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    return database


def assert_no_provider_payload(value):
    if isinstance(value, dict):
        forbidden = {"provider", "providerOfferId", "providerSearchId", "revalidationTokenHash", "rawPayload"}
        assert forbidden.isdisjoint(value.keys())
        for nested in value.values():
            assert_no_provider_payload(nested)
    elif isinstance(value, list):
        for nested in value:
            assert_no_provider_payload(nested)


def test_flight_schema_creates_normalized_tables_and_seed_counts(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    counts = reset_and_seed(database)

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        offer_columns = {row[1] for row in connection.execute("PRAGMA table_info(flight_offers)")}
        provider_columns = {row[1] for row in connection.execute("PRAGMA table_info(flight_offer_provider_refs)")}
        segment_columns = {row[1] for row in connection.execute("PRAGMA table_info(flight_offer_segments)")}

    assert FLIGHT_TABLES.issubset(tables)
    assert counts["flight_search_requests"] == 3
    assert counts["flight_offers"] == 3
    assert {"base_amount_cents", "tax_amount_cents", "total_amount_cents", "expires_at"}.issubset(offer_columns)
    assert {"provider_offer_id", "provider_search_id", "revalidation_token_hash"}.issubset(provider_columns)
    assert {"departure_local_datetime", "departure_timezone", "departure_utc_offset_minutes"}.issubset(segment_columns)
    assert {"arrival_local_datetime", "arrival_timezone", "arrival_utc_offset_minutes"}.issubset(segment_columns)


def test_search_schema_supports_one_way_round_trip_multi_city_passengers_and_cabin(tmp_path):
    database = seeded_database(tmp_path)

    one_way = flight_search_request_dto(str(database), "fsr_one_way_sfo_nrt")
    round_trip = flight_search_request_dto(str(database), "fsr_round_trip_sfo_lhr")
    multi_city = flight_search_request_dto(str(database), "fsr_multi_city_pacific")

    assert one_way["tripType"] == "one_way"
    assert one_way["cabin"] == "economy"
    assert one_way["legs"] == [{"origin": "SFO", "destination": "NRT", "departureDate": "2031-07-10"}]
    assert one_way["passengers"] == {"adult": 1}

    assert round_trip["tripType"] == "round_trip"
    assert [leg["origin"] for leg in round_trip["legs"]] == ["SFO", "LHR"]
    assert [leg["destination"] for leg in round_trip["legs"]] == ["LHR", "SFO"]
    assert round_trip["passengers"] == {"adult": 2, "child": 1}
    assert round_trip["cabin"] == "business"

    assert multi_city["tripType"] == "multi_city"
    assert len(multi_city["legs"]) == 3
    assert multi_city["fareBrand"] is None


def test_one_way_codeshare_offer_preserves_local_times_offsets_and_separated_provider_ref(tmp_path):
    database = seeded_database(tmp_path)

    offer = flight_offer_dto(str(database), "fo_one_way_codeshare", now="2031-05-01T10:10:00Z")
    provider_ref = provider_reference_for_revalidation(str(database), "fo_one_way_codeshare")
    segment = offer["itineraries"][0]["segments"][0]

    assert offer["tripType"] == "one_way"
    assert offer["status"] == "available"
    assert offer["price"]["total"] == {"amountCents": 70100, "currency": "USD", "formatted": "USD 701.00"}
    assert segment["origin"] == "SFO"
    assert segment["destination"] == "NRT"
    assert segment["departure"] == {
        "localDateTime": "2031-07-10T11:30:00",
        "timezone": "America/Los_Angeles",
        "utcOffsetMinutes": -420,
        "terminal": "G",
    }
    assert segment["arrival"]["localDateTime"] == "2031-07-11T14:30:00"
    assert segment["arrival"]["timezone"] == "Asia/Tokyo"
    assert segment["arrival"]["utcOffsetMinutes"] == 540
    assert segment["overnight"] is True
    assert segment["codeshare"] is True
    assert segment["marketingCarrier"] == {"code": "UA", "name": "United Airlines"}
    assert segment["operatingCarrier"] == {"code": "NH", "name": "All Nippon Airways"}
    assert provider_ref == {
        "provider": "fixture_air",
        "providerOfferId": "fx_offer_ow_001",
        "providerSearchId": "fx_search_ow_001",
        "referenceExpiresAt": "2031-05-01T10:15:03Z",
        "revalidationTokenHash": "sha256:oneway",
    }
    assert_no_provider_payload(offer)


def test_round_trip_offer_supports_multiple_itineraries_passenger_pricing_and_baggage(tmp_path):
    database = seeded_database(tmp_path)

    offer = flight_offer_dto(str(database), "fo_round_trip_business", now="2031-05-01T10:10:00Z")

    assert offer["tripType"] == "round_trip"
    assert offer["source"] == "selected_offer"
    assert len(offer["itineraries"]) == 2
    assert [itinerary["origin"] for itinerary in offer["itineraries"]] == ["SFO", "LHR"]
    assert [itinerary["destination"] for itinerary in offer["itineraries"]] == ["LHR", "SFO"]
    assert all(len(itinerary["segments"]) == 1 for itinerary in offer["itineraries"])
    assert offer["itineraries"][1]["segments"][0]["codeshare"] is True
    assert offer["itineraries"][1]["segments"][0]["arrival"]["localDateTime"] == "2031-08-15T18:20:00"
    assert offer["passengerPricing"] == [
        {
            "passengerType": "adult",
            "count": 2,
            "base": {"amountCents": 620000, "currency": "USD", "formatted": "USD 6200.00"},
            "taxes": {"amountCents": 70000, "currency": "USD", "formatted": "USD 700.00"},
            "total": {"amountCents": 690000, "currency": "USD", "formatted": "USD 6900.00"},
        },
        {
            "passengerType": "child",
            "count": 1,
            "base": {"amountCents": 235000, "currency": "USD", "formatted": "USD 2350.00"},
            "taxes": {"amountCents": 22000, "currency": "USD", "formatted": "USD 220.00"},
            "total": {"amountCents": 257000, "currency": "USD", "formatted": "USD 2570.00"},
        },
    ]
    assert {item["passengerType"] for item in offer["baggage"]} == {"adult", "child"}
    assert {detail["passengerType"] for detail in offer["fareDetails"]} == {"adult", "child"}
    assert_no_provider_payload(offer)


def test_expired_multi_city_offer_handles_missing_optional_provider_data(tmp_path):
    database = seeded_database(tmp_path)

    offer = flight_offer_dto(str(database), "fo_multi_city_expired", now="2031-05-01T10:30:00Z")
    offers = list_flight_offer_dtos(str(database), "fss_multi_expired", now="2031-05-01T10:30:00Z")

    assert is_offer_expired("2031-05-01T10:20:03Z", now="2031-05-01T10:30:00Z") is True
    assert offer["status"] == "expired"
    assert offer["tripType"] == "multi_city"
    assert len(offer["itineraries"]) == 3
    assert offer["itineraries"][0]["segments"][0]["aircraft"] is None
    assert offer["itineraries"][0]["segments"][0]["fareBrand"] is None
    assert offer["baggage"][0]["carryOnPieces"] is None
    assert offer["baggage"][0]["description"] == "Provider did not include baggage allowance."
    assert offers[0]["id"] == "fo_multi_city_expired"
    assert_no_provider_payload(offer)
