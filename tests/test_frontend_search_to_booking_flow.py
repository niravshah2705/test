from __future__ import annotations

from hbw_seed.frontend_flow_contracts import (
    VALID_ADULT_PASSENGER,
    VALID_CONTACT,
    VALID_FLIGHT_SEARCH,
    AutocompleteComponent,
    booking_history_component,
    checkout_form_component,
    confirmation_component,
    flight_search_form_component,
    offer_detail_component,
    payment_component,
    render_component_html,
    revalidation_component,
    search_results_component,
)


def test_airport_autocomplete_query_keyboard_selection_and_no_results_are_accessible():
    autocomplete = AutocompleteComponent()

    queried = autocomplete.query("san")
    assert queried["role"] == "combobox"
    assert queried["ariaControls"] == "origin-airport-options"
    assert queried["ariaActiveDescendant"] == "origin-airport-options-SFO"
    assert queried["options"] == [
        {
            "id": "origin-airport-options-SFO",
            "role": "option",
            "code": "SFO",
            "label": "San Francisco (SFO) — San Francisco International",
            "ariaSelected": True,
        }
    ]

    selected = autocomplete.select_with_keyboard("new", ["ArrowDown", "Enter"])
    assert selected["selected"]["code"] == "JFK"
    assert selected["focusTarget"] == "origin-field"
    assert selected["ariaExpanded"] is False
    assert selected["announcement"] == "Selected New York (JFK) — John F. Kennedy International."

    no_results = autocomplete.query("zzz")
    assert no_results["options"] == []
    assert no_results["emptyState"] == {
        "id": "origin-airport-no-results",
        "role": "status",
        "ariaLive": "polite",
        "message": "No airports found for zzz.",
    }


def test_search_form_required_fields_invalid_dates_and_results_error_states():
    form = flight_search_form_component({"tripType": "round_trip", "origin": "", "destination": "SFO", "departDate": "2031-07-10", "returnDate": "2031-07-01", "adults": 0})

    fields = {field["name"]: field for field in form["fields"]}
    assert form["errorSummary"] == "Please correct 3 flight search field errors."
    assert form["focusTarget"] == "flight-origin-field"
    assert fields["origin"]["label"] == "From"
    assert fields["origin"]["error"] == "Origin airport is required."
    assert fields["origin"]["ariaDescribedBy"] == "flight-origin-error"
    assert fields["returnDate"]["error"] == "Return date must be on or after departure date."
    assert fields["adults"]["error"] == "Must be greater than or equal to 1."

    empty = search_results_component({**VALID_FLIGHT_SEARCH, "scenario": "no_availability"})
    assert empty["state"] == "empty"
    assert empty["role"] == "status"
    assert empty["offers"] == []

    provider_unavailable = search_results_component({**VALID_FLIGHT_SEARCH, "scenario": "error"})
    assert provider_unavailable["state"] == "error"
    assert provider_unavailable["role"] == "alert"
    assert "retry" in provider_unavailable["message"].lower()


def test_search_results_and_offer_detail_render_backend_contract_aligned_offer_shapes():
    results = search_results_component(VALID_FLIGHT_SEARCH)

    assert results["state"] == "success"
    assert results["heading"] == "2 flights found"
    first_offer = results["offers"][0]
    assert first_offer["id"] == "ofb_flt_oneway"
    assert first_offer["label"] == "OA flight from SFO to JFK"
    assert first_offer["price"] == {"amountCents": 28600, "currency": "USD", "formatted": "USD 286.00"}
    assert first_offer["selectLabel"] == "Select OA flight"
    assert first_offer["selectDisabled"] is False
    assert first_offer["segments"][0]["flightNumber"] == "OA100"

    detail = offer_detail_component("ofb_flt_multisegment")
    assert detail["heading"] == "Review flight details"
    assert detail["canContinue"] is True
    assert [item["type"] for item in detail["timeline"]] == ["segment", "layover", "segment"]
    assert detail["fareSummary"]["total"] == {"amountCents": 42800, "currency": "USD", "formatted": "USD 428.00"}
    assert detail["requiredPassengerForms"][0]["fields"][:3] == ["legalGivenName", "legalFamilyName", "dateOfBirth"]


def test_checkout_component_covers_passenger_birth_date_and_contact_validation():
    checkout = checkout_form_component(
        {
            "offerId": "ofb_flt_oneway",
            "contact": {"email": "bad", "phone": "x"},
            "passengers": [{**VALID_ADULT_PASSENGER, "dateOfBirth": "2020-01-01"}],
        }
    )

    assert checkout["state"] == "validation_error"
    assert checkout["errorSummary"] == "Please correct 3 checkout field errors."
    assert checkout["focusTarget"] == "contact-email-field"
    assert checkout["fields"] == {
        "contact.email": ["Contact email must be a valid address."],
        "contact.phone": ["Contact phone must be a valid phone number."],
        "passengers[0].dateOfBirth": ["Adult travelers must be at least 18 on the travel date."],
    }
    assert checkout["statusRegion"] == {"id": "checkout-status", "role": "status", "ariaLive": "polite"}


def test_revalidation_components_cover_success_expired_unavailable_price_changed_and_retry_states():
    unchanged = revalidation_component("success")
    assert unchanged["state"] == "unchanged"
    assert unchanged["paymentAllowed"] is True
    assert unchanged["role"] == "status"

    price_changed = revalidation_component("price_change")
    assert price_changed["state"] == "price_increased"
    assert price_changed["paymentAllowed"] is False
    assert price_changed["latestTotal"] == {"amountCents": 32800, "currency": "USD", "formatted": "USD 328.00"}
    assert price_changed["actions"] == [{"label": "Accept new price", "action": "accept_price", "variant": "primary"}]

    unavailable = revalidation_component("unavailable")
    assert unavailable["state"] == "unavailable"
    assert unavailable["actions"] == [{"label": "Choose another offer", "href": "/search", "variant": "primary"}]

    retryable = revalidation_component("timeout")
    assert retryable["state"] == "retryable_failure"
    assert retryable["role"] == "alert"


def test_payment_component_uses_mocked_tokenization_and_covers_success_failure_pending_states():
    pending = payment_component("success")
    assert pending["state"] == "ticketing_pending"
    assert pending["paymentStatus"] == "captured"
    assert pending["tokenization"] == {"mocked": True, "rawCardSubmitted": False}
    assert pending["polling"] == {"enabled": True, "href": f"/api/payments?bookingId={pending['confirmation']['bookingId']}"}
    assert "not confirmed" not in pending["message"].lower()

    failed = payment_component("failure")
    assert failed["state"] == "payment_declined"
    assert failed["paymentStatus"] == "declined"
    assert failed["role"] == "alert"
    assert "not confirmed" in failed["message"].lower()

    accepted_changed_price = payment_component("price_changed_success")
    assert accepted_changed_price["state"] == "ticketing_pending"
    assert accepted_changed_price["confirmation"]["payment"]["amount"] == {"amountCents": 32800, "currency": "USD", "formatted": "USD 328.00"}


def test_confirmation_history_and_forbidden_detail_render_statuses_with_live_regions():
    pending = confirmation_component("pending")
    assert pending["status"] == "ticketing_pending"
    assert pending["heading"] == "Booking pending"
    assert pending["statusLabel"] == "Ticketing pending"

    confirmed = confirmation_component("confirmed")
    assert confirmed["status"] == "confirmed"
    assert confirmed["heading"] == "Booking confirmed"
    assert confirmed["statusLabel"] == "Confirmed"
    assert confirmed["polling"]["enabled"] is False

    empty_history = booking_history_component([])
    assert empty_history["state"] == "empty"
    assert empty_history["heading"] == "No bookings yet"
    assert '<section role="status" aria-live="polite">' in render_component_html(empty_history)

    forbidden = booking_history_component(forbidden_detail=True)
    assert forbidden["state"] == "forbidden"
    assert forbidden["role"] == "alert"
    assert forbidden["detail"]["heading"] == "Access is not allowed"
    assert '<section role="alert" aria-live="assertive">' in render_component_html(forbidden)

    history = booking_history_component([confirmed])
    assert history["items"] == [
        {
            "bookingId": confirmed["bookingId"],
            "status": "confirmed",
            "statusLabel": "Confirmed",
            "href": f"/bookings/{confirmed['bookingId']}",
        }
    ]
