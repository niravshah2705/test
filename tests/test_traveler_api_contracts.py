import json
from datetime import datetime, timezone

from hbw_seed import reset_and_seed
from hbw_seed.flight_checkout import InMemoryBookingDraftRepository
from hbw_seed.flights import DeterministicMockFlightProvider, FlightOrderRequest, FlightProviderTimeout
from hbw_seed.profiles import ProfileRepository
from hbw_seed.traveler_api import TravelerApiApplication


SENSITIVE_KEYS = {
    "password",
    "passwordHash",
    "password_hash",
    "paymentToken",
    "providerReference",
    "provider_reference",
    "providerPayload",
    "nativePayload",
    "raw",
    "documentNumber",
    "document_number",
}
SENSITIVE_VALUES = {"CorrectHorse123!", "tok_contract_1234567890", "P123456789"}


def seeded_app(tmp_path, provider=None):
    database = tmp_path / "traveler_api.sqlite3"
    reset_and_seed(database)
    profile_repository = ProfileRepository(str(database))
    return TravelerApiApplication(
        str(database),
        provider=provider or DeterministicMockFlightProvider(),
        booking_repository=InMemoryBookingDraftRepository(),
        profile_repository=profile_repository,
    )


def register_and_login(app, suffix="primary"):
    email = f"traveler-{suffix}@example.test"
    registration = app.request(
        "POST",
        "/api/auth/register",
        json={"id": f"usr_contract_{suffix}", "email": email, "password": "CorrectHorse123!", "fullName": "Contract Traveler"},
    )
    assert_success(registration, status=201)
    login = app.request("POST", "/api/auth/login", json={"email": email, "password": "CorrectHorse123!"})
    assert_success(login)
    return registration.body["data"], login.body["data"]["sessionId"]


def valid_passenger(**overrides):
    passenger = {
        "legalGivenName": "Gale",
        "legalFamilyName": "Guest",
        "dateOfBirth": "1990-04-12",
        "passengerType": "adult",
        "gender": "unspecified",
        "document": {
            "documentType": "passport",
            "issuingCountry": "US",
            "nationalityCountry": "US",
            "expiresOn": "2035-01-01",
            "documentNumber": "P123456789",
            "documentNumberLast4": "6789",
        },
    }
    passenger.update(overrides)
    return passenger


def draft_payload(draft_id="draft_contract", **overrides):
    payload = {
        "draftId": draft_id,
        "offerId": "ofb_flt_oneway",
        "contact": {"email": "traveler@example.test", "phone": "+14155550123"},
        "passengers": [valid_passenger()],
    }
    payload.update(overrides)
    return payload


def payment_payload(booking_id, amount_cents=28600, **overrides):
    payload = {
        "bookingId": booking_id,
        "paymentToken": "tok_contract_1234567890",
        "idempotencyKey": f"idem-{booking_id}-0001",
        "amountCents": amount_cents,
        "currency": "USD",
    }
    payload.update(overrides)
    return payload


def assert_success(response, status=200):
    assert response.status_code == status
    assert response.body["success"] is True
    assert response.body["error"] is None
    assert "data" in response.body
    assert_public_response_is_safe(response.body)


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.body["success"] is False
    assert response.body["data"] is None
    assert response.body["error"]["code"] == code
    assert isinstance(response.body["error"]["message"], str)
    assert response.body["error"]["message"]
    assert_public_response_is_safe(response.body)


def assert_public_response_is_safe(value):
    encoded = json.dumps(value, sort_keys=True)
    for secret in SENSITIVE_VALUES:
        assert secret not in encoded
    _assert_no_sensitive_keys(value)


def _assert_no_sensitive_keys(value):
    if isinstance(value, dict):
        assert not (set(value) & SENSITIVE_KEYS)
        for child in value.values():
            _assert_no_sensitive_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_sensitive_keys(child)


def assert_money(value):
    assert set(value) == {"amountCents", "currency", "formatted"}
    assert isinstance(value["amountCents"], int)
    assert value["currency"] == "USD"


def assert_booking_contract(value, expected_status):
    assert value["bookingId"].startswith("draft_")
    assert value["status"] == expected_status
    assert_money(value["total"])
    assert value["passengerCount"] == 1
    assert value["contact"] == {"email": "traveler@example.test"}
    assert value["passengers"] == [
        {
            "name": "Gale Guest",
            "passengerType": "adult",
            "document": {
                "documentType": "passport",
                "issuingCountry": "US",
                "nationalityCountry": "US",
                "expiresOn": "2035-01-01",
                "documentNumberLast4": "6789",
            },
        }
    ]


def test_traveler_api_happy_path_registration_to_confirmed_booking_contracts(tmp_path):
    app = seeded_app(tmp_path, provider=TicketedProvider())

    user, session_id = register_and_login(app)
    assert set(user) == {"id", "email", "fullName", "role"}

    current_user = app.request("GET", "/api/users/me", session_id=session_id)
    assert_success(current_user)
    assert current_user.body["data"]["user"] == user

    airports = app.request("GET", "/api/airports", query_string="q=san")
    assert_success(airports)
    assert airports.body["data"]["airports"] == [{"code": "SFO", "name": "San Francisco International", "city": "San Francisco"}]

    search = app.request(
        "POST",
        "/api/flights/search",
        json={"origin": "SFO", "destination": "JFK", "departDate": "2031-07-01", "adults": 1, "children": 0, "infants": 0, "cabin": "economy"},
    )
    assert_success(search)
    assert search.body["meta"] == {"resultCount": 2, "providerPayloadExposed": False}
    offer = search.body["data"]["offers"][0]
    assert offer["id"] == "ofb_flt_oneway"
    assert_money(offer["total"])

    detail = app.request("GET", "/api/flights/offers/ofb_flt_oneway")
    assert_success(detail)
    assert detail.body["meta"] == {"providerPayloadExposed": False}
    assert detail.body["data"]["canContinue"] is True
    assert_money(detail.body["data"]["fareSummary"]["total"])

    draft = app.request("POST", "/api/bookings/drafts", session_id=session_id, json=draft_payload())
    assert_success(draft, status=201)
    booking_id = draft.body["data"]["id"]
    assert draft.body["data"]["checkoutType"] == "authenticated"
    assert draft.body["data"]["passengerSnapshots"][0]["document"]["documentNumberLast4"] == "6789"

    passenger_submission = app.request("POST", f"/api/bookings/{booking_id}/passengers", session_id=session_id, json={"passengers": [valid_passenger()]})
    assert_success(passenger_submission)
    assert_booking_contract(passenger_submission.body["data"]["booking"], "draft")

    revalidation = app.request("POST", f"/api/bookings/{booking_id}/revalidate", session_id=session_id, json={"scenario": "success"})
    assert_success(revalidation)
    assert revalidation.body["data"]["paymentAllowed"] is True
    assert revalidation.body["data"]["revalidation"]["status"] == "unchanged"

    payment = app.request("POST", f"/api/bookings/{booking_id}/payments", session_id=session_id, json=payment_payload(booking_id))
    assert_success(payment)
    assert payment.body["data"]["status"] == "confirmed"
    assert payment.body["data"]["payment"] == {"id": f"pay_{booking_id}_idem_{booking_id}_0001", "status": "captured", "amount": {"amountCents": 28600, "currency": "USD", "formatted": "USD 286.00"}}
    assert payment.body["data"]["order"]["status"] == "ticketed"

    finalized = app.request("POST", f"/api/bookings/{booking_id}/finalize", session_id=session_id)
    assert_success(finalized)
    assert_booking_contract(finalized.body["data"]["booking"], "confirmed")
    assert finalized.body["data"]["booking"]["payment"]["status"] == "captured"

    status = app.request("GET", f"/api/bookings/{booking_id}/status", session_id=session_id)
    assert_success(status)
    assert status.body["data"]["status"] == "confirmed"

    history = app.request("GET", "/api/bookings", session_id=session_id)
    assert_success(history)
    assert history.body["data"]["bookings"] == [
        {"bookingId": booking_id, "offerId": "ofb_flt_oneway", "status": "confirmed", "total": {"amountCents": 28600, "currency": "USD", "formatted": "USD 286.00"}, "passengerCount": 1}
    ]

    booking_detail = app.request("GET", f"/api/bookings/{booking_id}", session_id=session_id)
    assert_success(booking_detail)
    assert_booking_contract(booking_detail.body["data"]["booking"], "confirmed")


def test_traveler_api_standard_errors_auth_ownership_and_provider_payment_failures(tmp_path):
    app = seeded_app(tmp_path)
    _, session_id = register_and_login(app)
    _, other_session = register_and_login(app, suffix="other")

    unauthenticated_current_user = app.request("GET", "/api/users/me")
    assert_error(unauthenticated_current_user, 401, "unauthorized")

    invalid_draft = app.request(
        "POST",
        "/api/bookings/drafts",
        session_id=session_id,
        json=draft_payload("draft_invalid", passengers=[valid_passenger(legalGivenName="", document={"documentType": "visa", "issuingCountry": "USA", "expiresOn": "bad-date", "documentNumber": "P123456789"})]),
    )
    assert_error(invalid_draft, 400, "validation_error")
    assert invalid_draft.body["error"]["fields"] == {
        "passengers[0].legalGivenName": ["Use 1-40 letters, spaces, apostrophes, or hyphens."],
        "passengers[0].document.documentType": ["Document type must be passport or national_id."],
        "passengers[0].document.issuingCountry": ["Issuing country must be a two-letter ISO code."],
        "passengers[0].document.nationalityCountry": ["Nationality country must be a two-letter ISO code."],
        "passengers[0].document.expiresOn": ["Document expiry must use YYYY-MM-DD format."],
    }

    expired = app.request(
        "POST",
        "/api/bookings/drafts",
        session_id=session_id,
        json=draft_payload("draft_expired"),
    )
    # Default offer expires after DEFAULT_NOW, so use direct application handler override for the expired-path signal.
    from hbw_seed.flight_checkout import handle_create_booking_draft

    expired = handle_create_booking_draft(
        {**draft_payload("draft_expired"), "userId": "usr_contract_primary"},
        repository=app.booking_repository,
        provider=app.provider,
        now=datetime(2031, 7, 1, 8, 0, tzinfo=timezone.utc),
    )
    assert_error(expired, 400, "validation_error")
    assert expired.body["error"]["fields"] == {"offerId": ["Expired offers cannot continue to checkout."]}

    provider_timeout = app.request(
        "POST",
        "/api/flights/search",
        json={"origin": "SFO", "destination": "JFK", "departDate": "2031-07-01", "adults": 1, "scenario": "timeout"},
    )
    assert_error(provider_timeout, 504, "provider_timeout")

    draft = app.request("POST", "/api/bookings/drafts", session_id=session_id, json=draft_payload("draft_negative"))
    assert_success(draft, status=201)
    booking_id = draft.body["data"]["id"]

    cross_user_detail = app.request("GET", f"/api/bookings/{booking_id}", session_id=other_session)
    assert_error(cross_user_detail, 403, "forbidden")

    revalidation_failure = app.request("POST", f"/api/bookings/{booking_id}/revalidate", session_id=session_id, json={"scenario": "timeout"})
    assert_success(revalidation_failure)
    assert revalidation_failure.body["data"]["status"] == "revalidation_failed"
    assert revalidation_failure.body["data"]["revalidation"]["status"] == "retryable_failure"

    app.request("POST", f"/api/bookings/{booking_id}/revalidate", session_id=session_id, json={"scenario": "success"})
    declined = app.request("POST", f"/api/bookings/{booking_id}/payments", session_id=session_id, json=payment_payload(booking_id, scenario="declined"))
    assert_success(declined, status=202)
    assert declined.body["data"]["status"] == "payment_declined"
    assert declined.body["data"]["payment"]["status"] == "declined"

    raw_card = app.request("POST", f"/api/bookings/{booking_id}/payments", session_id=session_id, json=payment_payload(booking_id, idempotencyKey="idem-raw-card-0002", cardNumber="4111111111111111"))
    assert_error(raw_card, 400, "validation_error")
    assert raw_card.body["error"]["fields"] == {"cardNumber": ["Raw card data must be entered only in provider-hosted tokenized fields."]}

    timeout_app = seeded_app(tmp_path / "timeout", provider=TimeoutOnOrderProvider())
    _, timeout_session = register_and_login(timeout_app, suffix="timeout")
    timeout_app.request("POST", "/api/bookings/drafts", session_id=timeout_session, json=draft_payload("draft_provider_timeout"))
    timeout_app.request("POST", "/api/bookings/draft_provider_timeout/revalidate", session_id=timeout_session, json={"scenario": "success"})
    timeout_payment = timeout_app.request("POST", "/api/bookings/draft_provider_timeout/payments", session_id=timeout_session, json=payment_payload("draft_provider_timeout"))
    assert_success(timeout_payment, status=202)
    assert timeout_payment.body["data"]["status"] == "booking_failed_after_payment"
    assert timeout_payment.body["data"]["payment"]["status"] == "authorized_booking_failed"

    app_ticketed = seeded_app(tmp_path / "ticketed", provider=TicketedProvider())
    _, ticketed_session = register_and_login(app_ticketed, suffix="ticketed")
    app_ticketed.request("POST", "/api/bookings/drafts", session_id=ticketed_session, json=draft_payload("draft_finalized"))
    app_ticketed.request("POST", "/api/bookings/draft_finalized/revalidate", session_id=ticketed_session, json={"scenario": "success"})
    app_ticketed.request("POST", "/api/bookings/draft_finalized/payments", session_id=ticketed_session, json=payment_payload("draft_finalized"))
    already_finalized = app_ticketed.request("POST", "/api/bookings/draft_finalized/payments", session_id=ticketed_session, json=payment_payload("draft_finalized", idempotencyKey="idem-finalized-0002"))
    assert_success(already_finalized)
    assert already_finalized.body["data"]["duplicate"] is True


def test_traveler_api_forbidden_passenger_profile_access_uses_error_envelope(tmp_path):
    app = seeded_app(tmp_path)
    owner, owner_session = register_and_login(app, suffix="owner")
    _, other_session = register_and_login(app, suffix="profile-other")
    app.profile_repository.create_passenger_profile(
        {
            "id": "pax_owner_only",
            "user_id": owner["id"],
            "legal_given_name": "Gale",
            "legal_family_name": "Guest",
            "date_of_birth": "1990-04-12",
            "passenger_type": "adult",
        }
    )

    owner_response = app.request("GET", "/api/passenger-profiles/pax_owner_only", session_id=owner_session)
    assert_success(owner_response)
    assert owner_response.body["data"]["profile"]["id"] == "pax_owner_only"

    forbidden = app.request("GET", "/api/passenger-profiles/pax_owner_only", session_id=other_session)
    assert_error(forbidden, 403, "forbidden")


class TicketedProvider(DeterministicMockFlightProvider):
    def createOrder(self, request: FlightOrderRequest):
        return {
            "id": f"ord_{request.offer_id}_ticketed",
            "offerId": request.offer_id,
            "provider": "deterministic_mock_air",
            "providerOrderId": "native-ticketed-secret",
            "pricing": {"total": {"amount": 28600, "currency": "USD"}},
            "status": "ticketed",
            "ticketingDeadline": None,
        }


class TimeoutOnOrderProvider(DeterministicMockFlightProvider):
    def createOrder(self, request: FlightOrderRequest):
        raise FlightProviderTimeout("Ambiguous provider timeout after payment authorization.")
