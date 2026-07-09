from hbw_seed.flight_checkout import (
    CheckoutValidationError,
    InMemoryBookingDraftRepository,
    accept_revalidated_price,
    booking_review_payload,
    create_booking_draft,
    handle_accept_revalidated_price,
    handle_revalidate_booking_draft,
    revalidate_booking_draft,
)

VALID_CONTACT = {"email": "traveler@example.test", "phone": "+14155550123"}
VALID_ADULT = {
    "legalGivenName": "Gale",
    "legalFamilyName": "Guest",
    "dateOfBirth": "1990-04-12",
    "passengerType": "adult",
}


def draft(repository=None):
    repository = repository or InMemoryBookingDraftRepository()
    return repository, create_booking_draft(
        {"offerId": "ofb_flt_oneway", "contact": VALID_CONTACT, "passengers": [VALID_ADULT]},
        repository=repository,
    )


def assert_raises(expected_exception, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except expected_exception as exc:
        return exc
    raise AssertionError(f"Expected {expected_exception.__name__}")


def test_revalidation_runs_for_valid_draft_and_unchanged_price_allows_payment():
    repository, booking = draft()

    review = revalidate_booking_draft(booking["id"], repository=repository)

    assert review["status"] == "price_validated"
    assert review["paymentAllowed"] is True
    assert review["revalidation"]["status"] == "unchanged"
    assert review["currentTotal"]["amountCents"] == booking["total"]["amountCents"]


def test_changed_price_blocks_payment_until_explicit_acceptance():
    repository, booking = draft()

    review = revalidate_booking_draft(booking["id"], repository=repository, scenario="price_change")
    assert review["status"] == "price_changed"
    assert review["paymentAllowed"] is False
    assert review["revalidation"]["status"] == "price_increased"
    assert review["revalidation"]["latestTotal"]["amountCents"] == 32800
    assert [action["label"] for action in review["actions"]] == ["Accept new price"]

    accepted = accept_revalidated_price(booking["id"], repository=repository)
    assert accepted["status"] == "price_change_accepted"
    assert accepted["paymentAllowed"] is True
    assert accepted["currentTotal"]["amountCents"] == 32800
    assert accepted["revalidation"]["accepted"] is True


def test_decreased_price_also_requires_acceptance_before_payment():
    repository, booking = draft()

    review = revalidate_booking_draft(booking["id"], repository=repository, scenario="price_decrease")

    assert review["status"] == "price_changed"
    assert review["paymentAllowed"] is False
    assert review["revalidation"]["status"] == "price_decreased"
    assert review["revalidation"]["priceDeltaCents"] == -2500


def test_unavailable_and_materially_changed_offers_block_payment_and_send_back_to_search():
    repository, booking = draft()
    unavailable = revalidate_booking_draft(booking["id"], repository=repository, scenario="unavailable")
    assert unavailable["status"] == "unavailable"
    assert unavailable["paymentAllowed"] is False
    assert unavailable["revalidation"]["status"] == "unavailable"
    assert unavailable["actions"] == [{"label": "Choose another offer", "href": "/search", "variant": "primary"}]

    repository2, booking2 = draft()
    changed_flight = revalidate_booking_draft(booking2["id"], repository=repository2, scenario="itinerary_change")
    assert changed_flight["status"] == "unavailable"
    assert changed_flight["paymentAllowed"] is False
    assert changed_flight["revalidation"]["status"] == "material_change"


def test_provider_timeout_is_retryable_and_does_not_corrupt_original_total():
    repository, booking = draft()

    review = revalidate_booking_draft(booking["id"], repository=repository, scenario="timeout")

    assert review["status"] == "revalidation_failed"
    assert review["paymentAllowed"] is False
    assert review["currentTotal"] == booking["total"]
    assert review["revalidation"]["status"] == "retryable_failure"
    assert review["actions"] == [{"label": "Retry price check", "action": "revalidate", "variant": "primary"}]


def test_unexpected_currency_change_blocks_payment_as_mismatch():
    repository, booking = draft()

    review = revalidate_booking_draft(booking["id"], repository=repository, scenario="currency_mismatch")

    assert review["status"] == "unavailable"
    assert review["paymentAllowed"] is False
    assert review["revalidation"]["status"] == "currency_mismatch"
    assert review["revalidation"]["latestTotal"]["currency"] == "EUR"


def test_duplicate_revalidation_requests_are_idempotent_or_serialized():
    repository, booking = draft()

    first = revalidate_booking_draft(booking["id"], repository=repository, scenario="price_change")
    duplicate = revalidate_booking_draft(booking["id"], repository=repository, scenario="price_change")
    assert duplicate == first

    repository2, booking2 = draft()
    repository2.update(booking2["id"], {"status": "revalidating", "revalidation": {"status": "in_progress", "scenario": "success", "message": "Revalidation is already running."}})
    in_progress = revalidate_booking_draft(booking2["id"], repository=repository2, scenario="success")
    assert in_progress["status"] == "revalidating"
    assert in_progress["revalidation"]["status"] == "in_progress"
    assert in_progress["paymentAllowed"] is False


def test_review_handlers_return_enveloped_api_responses():
    repository, booking = draft()

    response = handle_revalidate_booking_draft(booking["id"], repository=repository, scenario="price_change")
    assert response.status_code == 200
    assert response.body["success"] is True
    assert response.body["data"]["paymentAllowed"] is False

    accepted = handle_accept_revalidated_price(booking["id"], repository=repository)
    assert accepted.status_code == 200
    assert accepted.body["data"]["paymentAllowed"] is True


def test_accepting_without_changed_price_returns_validation_error():
    repository, booking = draft()
    revalidate_booking_draft(booking["id"], repository=repository)

    exc = assert_raises(CheckoutValidationError, accept_revalidated_price, booking["id"], repository=repository)
    assert exc.fields == {"revalidation": ["No changed price is waiting for acceptance."]}


def test_booking_review_payload_exposes_not_started_retry_action():
    _, booking = draft()

    review = booking_review_payload(booking)

    assert review["paymentAllowed"] is False
    assert review["revalidation"]["status"] == "not_started"
    assert review["actions"] == [{"label": "Retry price check", "action": "revalidate", "variant": "primary"}]
