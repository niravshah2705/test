from hbw_seed.flight_checkout import (
    CheckoutValidationError,
    InMemoryBookingDraftRepository,
    create_booking_draft,
    finalize_booking_payment,
    handle_finalize_booking_payment,
    poll_booking_finalization,
    revalidate_booking_draft,
)

VALID_CONTACT = {"email": "traveler@example.test", "phone": "+14155550123"}
VALID_ADULT = {"legalGivenName": "Gale", "legalFamilyName": "Guest", "dateOfBirth": "1990-04-12", "passengerType": "adult"}


def assert_raises(expected_exception, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except expected_exception as exc:
        return exc
    raise AssertionError(f"Expected {expected_exception.__name__}")


def validated_draft(scenario="success"):
    repository = InMemoryBookingDraftRepository()
    draft = create_booking_draft({"offerId": "ofb_flt_oneway", "contact": VALID_CONTACT, "passengers": [VALID_ADULT]}, repository=repository)
    review = revalidate_booking_draft(draft["id"], repository=repository, scenario=scenario)
    if scenario == "price_change":
        from hbw_seed.flight_checkout import accept_revalidated_price

        review = accept_revalidated_price(draft["id"], repository=repository)
    return repository, repository.get(draft["id"]), review


def payment_payload(draft, **overrides):
    payload = {
        "bookingId": draft["id"],
        "paymentToken": "tok_fixture_1234567890",
        "idempotencyKey": "idem-fixture-0001",
        "amountCents": draft["total"]["amountCents"],
        "currency": draft["total"]["currency"],
    }
    payload.update(overrides)
    return payload


def test_tokenized_payment_success_finalizes_safe_confirmation_without_raw_card_data():
    repository, draft, _ = validated_draft()

    confirmation = finalize_booking_payment(draft["id"], payment_payload(draft), repository=repository)

    assert confirmation["status"] == "ticketing_pending"
    assert confirmation["payment"]["status"] == "captured"
    assert confirmation["payment"]["amount"] == draft["total"]
    assert confirmation["order"]["id"] == "ord_ofb_flt_oneway"
    assert "providerReference" not in str(confirmation)
    assert "tok_fixture_1234567890" not in str(confirmation)
    assert confirmation["passengers"] == [{"name": "Gale Guest", "passengerType": "adult"}]


def test_payment_rejects_raw_card_data_and_before_successful_revalidation():
    repository = InMemoryBookingDraftRepository()
    draft = create_booking_draft({"offerId": "ofb_flt_oneway", "contact": VALID_CONTACT, "passengers": [VALID_ADULT]}, repository=repository)

    blocked = assert_raises(CheckoutValidationError, finalize_booking_payment, draft["id"], payment_payload(draft), repository=repository)
    assert blocked.fields == {"revalidation": ["Successful price revalidation is required before payment."]}

    revalidate_booking_draft(draft["id"], repository=repository)
    raw_card = assert_raises(CheckoutValidationError, finalize_booking_payment, draft["id"], payment_payload(repository.get(draft["id"]), cardNumber="4242424242424242", cvv="123"), repository=repository)
    assert raw_card.fields["cardNumber"] == ["Raw card data must be entered only in provider-hosted tokenized fields."]
    assert raw_card.fields["cvv"] == ["Raw card data must be entered only in provider-hosted tokenized fields."]


def test_amount_currency_mismatch_and_stale_price_are_rejected():
    repository, draft, _ = validated_draft("price_change")

    stale = assert_raises(CheckoutValidationError, finalize_booking_payment, draft["id"], payment_payload(draft, amountCents=28600), repository=repository)
    assert stale.fields == {"amount": ["Payment amount and currency must match the accepted revalidated price."]}

    mismatch = assert_raises(CheckoutValidationError, finalize_booking_payment, draft["id"], payment_payload(draft, idempotencyKey="idem-fixture-0002", currency="EUR"), repository=repository)
    assert mismatch.fields == {"amount": ["Payment amount and currency must match the accepted revalidated price."]}


def test_duplicate_payment_click_does_not_duplicate_charge_or_order():
    repository, draft, _ = validated_draft()

    first = finalize_booking_payment(draft["id"], payment_payload(draft), repository=repository)
    duplicate = finalize_booking_payment(draft["id"], payment_payload(draft), repository=repository)

    saved = repository.get(draft["id"])
    assert duplicate["duplicate"] is True
    assert duplicate["payment"]["id"] == first["payment"]["id"]
    assert len(saved["paymentAttempts"]) == 1
    assert saved["providerOrder"]["id"] == "ord_ofb_flt_oneway"


def test_declined_payment_returns_failure_without_provider_order():
    repository, draft, _ = validated_draft()

    response = handle_finalize_booking_payment(draft["id"], payment_payload(draft, scenario="declined"), repository=repository)

    assert response.status_code == 202
    assert response.body["data"]["status"] == "payment_declined"
    assert response.body["data"]["payment"]["status"] == "declined"
    assert repository.get(draft["id"])["providerOrder"] is None


def test_provider_failure_after_payment_returns_pending_safe_state():
    repository, draft, _ = validated_draft()

    confirmation = finalize_booking_payment(draft["id"], payment_payload(draft, scenario="provider_failure"), repository=repository)

    assert confirmation["status"] == "booking_failed_after_payment"
    assert confirmation["payment"]["status"] == "authorized_booking_failed"
    assert confirmation["order"]["status"] == "failed"
    assert "providerReference" not in str(confirmation)


def test_polling_can_complete_pending_ticketing_status():
    class TicketedProvider:
        def getOrderStatus(self, order_id):
            return {
                "id": order_id,
                "offerId": "ofb_flt_oneway",
                "provider": "deterministic_mock_air",
                "providerOrderId": "native-ticketed",
                "pricing": {"total": {"amount": 28600, "currency": "USD"}},
                "status": "ticketed",
                "ticketingDeadline": "2031-07-01T07:45:00Z",
            }

    repository, draft, _ = validated_draft()
    finalize_booking_payment(draft["id"], payment_payload(draft), repository=repository)

    polled = poll_booking_finalization(draft["id"], repository=repository, provider=TicketedProvider())

    assert polled["status"] == "confirmed"
    assert polled["polling"]["enabled"] is False
    assert repository.get(draft["id"])["status"] == "finalized"
