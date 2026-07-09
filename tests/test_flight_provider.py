import inspect

from hbw_seed.flights import (
    DeterministicMockFlightProvider,
    FlightBookingService,
    FlightOrderRequest,
    FlightProvider,
    FlightProviderTimeout,
    FlightProviderUnavailable,
    FlightSearchRequest,
    ProviderReference,
    map_provider_offer,
    map_provider_order,
)


def test_provider_interface_supports_required_operations():
    required = ["searchFlights", "getOfferDetails", "revalidateOffer", "createOrder", "getOrderStatus"]

    for operation in required:
        assert hasattr(FlightProvider, operation)
        assert hasattr(DeterministicMockFlightProvider, operation)

    service_source = inspect.getsource(FlightBookingService)
    assert "DeterministicMockFlightProvider" not in service_source
    assert "MockFlightProvider" not in service_source


def test_mock_search_returns_one_way_round_trip_multisegment_codeshare_and_missing_baggage():
    service = FlightBookingService(DeterministicMockFlightProvider())

    offers = service.searchFlights(
        FlightSearchRequest(origin="SFO", destination="JFK", depart_date="2031-07-01", return_date="2031-07-08", adults=2)
    )

    assert [offer.id for offer in offers] == ["ofb_flt_oneway", "ofb_flt_multisegment", "ofb_flt_roundtrip"]
    assert len(offers[0].itineraries) == 1
    assert len(offers[2].itineraries) == 2
    assert len(offers[1].itineraries[0].segments) == 2
    assert offers[1].itineraries[0].segments[1].codeshare is True
    assert offers[1].checked_bags_included is None
    assert isinstance(offers[0].provider_reference, ProviderReference)
    segment_payload = offers[0].to_payload()["itineraries"][0]["segments"][0]
    assert segment_payload["marketingCarrierName"] == "Oceanic Air"
    assert segment_payload["originDisplayName"] == "San Francisco (SFO)"
    assert segment_payload["destinationDisplayName"] == "New York (JFK)"
    assert offers[0].to_payload()["total"] == {"amountCents": 57200, "currency": "USD", "formatted": "USD 572.00"}
    assert "provider_reference" not in offers[0].to_payload()
    assert "providerReference" not in offers[0].to_payload()


def test_revalidation_simulates_success_price_change_unavailable_timeout_and_error():
    provider = DeterministicMockFlightProvider()
    service = FlightBookingService(provider)

    available = service.revalidateOffer("ofb_flt_oneway")
    assert available.status == "available"
    assert available.offer.id == "ofb_flt_oneway"

    changed = service.revalidateOffer("ofb_flt_oneway", scenario="price_change")
    assert changed.status == "price_changed"
    assert changed.previous_total.amount_cents == 28600
    assert changed.offer.total.amount_cents == 32800

    unavailable = service.revalidateOffer("ofb_flt_oneway", scenario="unavailable")
    assert unavailable.status == "unavailable"
    assert unavailable.offer is None

    assert_raises(FlightProviderTimeout, service.revalidateOffer, "ofb_flt_oneway", scenario="timeout")
    assert_raises(FlightProviderUnavailable, service.revalidateOffer, "ofb_flt_oneway", scenario="error")
    assert service.searchFlights(FlightSearchRequest("SFO", "JFK", "2031-07-01", scenario="no_availability")) == []


def test_order_creation_and_status_normalize_pending_ticketing_without_provider_leakage():
    service = FlightBookingService(DeterministicMockFlightProvider())

    order = service.createOrder(
        FlightOrderRequest(
            offer_id="ofb_flt_oneway",
            passengers=({"givenName": "Gale", "familyName": "Guest"},),
            contact_email="guest@example.test",
        )
    )
    status = service.getOrderStatus(order.id)

    assert order.status == "ticketing_pending"
    assert order.ticketing_deadline == "2031-07-01T07:45:00Z"
    assert isinstance(order.provider_reference, ProviderReference)
    assert status.status == "ticketing_pending"
    assert order.to_payload() == {
        "id": "ord_ofb_flt_oneway",
        "offerId": "ofb_flt_oneway",
        "status": "ticketing_pending",
        "total": {"amountCents": 28600, "currency": "USD", "formatted": "USD 286.00"},
        "ticketingDeadline": "2031-07-01T07:45:00Z",
    }


def test_mapping_handles_incomplete_optional_fields_and_unexpected_currency():
    raw_offer = {
        "id": "ofb_flt_unexpected_currency",
        "provider": "mock",
        "providerOfferId": "native-123",
        "itineraries": [
            {
                "id": "itin",
                "segments": [
                    {
                        "id": "seg",
                        "marketingCarrier": "OA",
                        "flightNumber": "OA9",
                        "origin": "SFO",
                        "destination": "LAX",
                        "departsAt": "2031-07-01T08:00:00-07:00",
                        "arrivesAt": "2031-07-01T09:30:00-07:00",
                        "durationMinutes": 90,
                    }
                ],
            }
        ],
        "pricing": {"total": {"amount": 9900, "currency": "btc"}},
    }

    offer = map_provider_offer(raw_offer)

    assert offer.total.currency == "USD"
    assert offer.checked_bags_included is None
    assert offer.itineraries[0].segments[0].operating_carrier == "OA"
    assert offer.provider_reference.reference_id == "native-123"


def test_mapping_order_keeps_provider_specific_data_inside_reference():
    raw_order = {
        "id": "ord_native",
        "offerId": "ofb_flt_oneway",
        "provider": "mock",
        "providerOrderId": "native-order-1",
        "providerMeta": {"nativeOrderToken": "secret-ish"},
        "pricing": {"total": {"amount": 12345, "currency": "EUR"}},
        "status": "ticketing_pending",
    }

    order = map_provider_order(raw_order)

    assert order.provider_reference == ProviderReference(
        provider="mock",
        reference_id="native-order-1",
        kind="flight_order",
        attributes={"nativeOrderToken": "secret-ish"},
    )
    assert order.to_payload() == {
        "id": "ord_native",
        "offerId": "ofb_flt_oneway",
        "status": "ticketing_pending",
        "total": {"amountCents": 12345, "currency": "EUR", "formatted": "EUR 123.45"},
        "ticketingDeadline": None,
    }


def test_frontend_safe_modules_do_not_import_provider_adapter_types():
    import hbw_seed.dto as dto
    import hbw_seed.public_api as public_api

    combined = inspect.getsource(dto) + inspect.getsource(public_api)
    assert "DeterministicMockFlightProvider" not in combined
    assert "MockFlightProvider" not in combined
    assert "ProviderReference" not in combined


def assert_raises(expected_exception, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except expected_exception as exc:
        return exc
    raise AssertionError(f"Expected {expected_exception.__name__}")
