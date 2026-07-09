from datetime import datetime, timezone

from hbw_seed.flight_checkout import (
    BookingDraftRepository,
    CheckoutValidationError,
    InMemoryBookingDraftRepository,
    accept_revalidated_price,
    create_booking_draft,
    finalize_booking_payment,
    poll_booking_finalization,
    revalidate_booking_draft,
)
from hbw_seed.flights import DeterministicMockFlightProvider, FlightOrderRequest, FlightProviderTimeout

VALID_CONTACT = {"email": "traveler@example.test", "phone": "+14155550123"}
VALID_ADULT = {"legalGivenName": "Gale", "legalFamilyName": "Guest", "dateOfBirth": "1990-04-12", "passengerType": "adult"}


def assert_raises(expected_exception, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except expected_exception as exc:
        return exc
    raise AssertionError(f"Expected {expected_exception.__name__}")


class CountingProvider(DeterministicMockFlightProvider):
    def __init__(self):
        self.revalidation_calls = []
        self.order_calls = []
        self.status_calls = []
        self.fail_first_order = False
        self.pending_statuses = []

    def revalidateOffer(self, offer_id, *, passengers=(), scenario="success"):
        self.revalidation_calls.append((offer_id, scenario, len(passengers)))
        return super().revalidateOffer(offer_id, passengers=passengers, scenario=scenario)

    def createOrder(self, request: FlightOrderRequest):
        self.order_calls.append((request.offer_id, request.scenario, len(request.passengers)))
        if self.fail_first_order and len(self.order_calls) == 1:
            raise FlightProviderTimeout("Ambiguous provider timeout after payment authorization.")
        return super().createOrder(request)

    def getOrderStatus(self, order_id):
        self.status_calls.append(order_id)
        if self.pending_statuses:
            status = self.pending_statuses.pop(0)
            return {
                "id": order_id,
                "offerId": "ofb_flt_oneway",
                "provider": "deterministic_mock_air",
                "providerOrderId": f"native-{status}",
                "pricing": {"total": {"amount": 28600, "currency": "USD"}},
                "status": status,
                "ticketingDeadline": "2031-07-01T07:45:00Z",
            }
        return super().getOrderStatus(order_id)


class TicketedProvider(CountingProvider):
    def createOrder(self, request: FlightOrderRequest):
        self.order_calls.append((request.offer_id, request.scenario, len(request.passengers)))
        return {
            "id": f"ord_{request.offer_id}_ticketed",
            "offerId": request.offer_id,
            "provider": "deterministic_mock_air",
            "providerOrderId": "native-ticketed",
            "pricing": {"total": {"amount": 28600, "currency": "USD"}},
            "status": "ticketed",
            "ticketingDeadline": None,
        }


def draft(repository=None, draft_id="draft_lifecycle", **overrides):
    repository = repository or InMemoryBookingDraftRepository()
    payload = {"draftId": draft_id, "offerId": "ofb_flt_oneway", "contact": VALID_CONTACT, "passengers": [VALID_ADULT]}
    payload.update(overrides)
    return repository, create_booking_draft(payload, repository=repository)


def validated_draft(repository=None, draft_id="draft_lifecycle", scenario="success", provider=None):
    repository, booking = draft(repository, draft_id=draft_id)
    review = revalidate_booking_draft(booking["id"], repository=repository, provider=provider, scenario=scenario)
    if scenario in {"price_change", "price_decrease"}:
        review = accept_revalidated_price(booking["id"], repository=repository)
    return repository, repository.get(booking["id"]), review


def payment_payload(draft, **overrides):
    payload = {
        "bookingId": draft["id"],
        "paymentToken": "tok_lifecycle_1234567890",
        "idempotencyKey": f"idem-{draft['id']}-0001",
        "amountCents": draft["total"]["amountCents"],
        "currency": draft["total"]["currency"],
    }
    payload.update(overrides)
    return payload


def event_types(repository, draft_id):
    return [event["type"] for event in repository.get(draft_id)["auditEvents"]]


def test_state_machine_valid_lifecycle_transitions_and_audit_events_with_sqlite_repository(tmp_path):
    repository = BookingDraftRepository(str(tmp_path / "flight_lifecycle.sqlite3"))
    provider = TicketedProvider()

    repository, booking = draft(repository, draft_id="draft_state_machine")
    assert booking["status"] == "draft"

    review = revalidate_booking_draft(booking["id"], repository=repository, provider=provider)
    assert review["status"] == "price_validated"

    confirmation = finalize_booking_payment(booking["id"], payment_payload(repository.get(booking["id"])), repository=repository, provider=provider)
    assert confirmation["status"] == "confirmed"
    assert repository.get(booking["id"])["status"] == "finalized"

    assert event_types(repository, booking["id"]) == [
        "booking_draft.created",
        "booking_revalidation.completed",
        "booking_payment.authorized",
        "booking_provider_order.created",
    ]
    assert provider.revalidation_calls == [("ofb_flt_oneway", "success", 1)]
    assert provider.order_calls == [("ofb_flt_oneway", "success", 1)]


def test_state_machine_rejects_invalid_transitions_from_each_non_payable_or_terminal_state():
    invalid_statuses = ["draft", "revalidating", "price_changed", "unavailable", "revalidation_failed", "ticketing_pending", "finalized"]
    for status in invalid_statuses:
        repository, booking = draft(draft_id=f"draft_invalid_{status}")
        repository.update(booking["id"], {"status": status, "revalidation": {"status": "price_increased", "latestTotal": booking["total"]}})

        exc = assert_raises(CheckoutValidationError, finalize_booking_payment, booking["id"], payment_payload(repository.get(booking["id"])), repository=repository)

        if status in {"ticketing_pending", "finalized"}:
            assert exc.fields == {"status": ["Booking is already finalized."]}
        else:
            assert exc.fields == {"revalidation": ["Successful price revalidation is required before payment."]}
        assert repository.get(booking["id"])["providerOrder"] is None
        assert repository.get(booking["id"])["paymentAttempts"] == []


def test_revalidation_outcomes_cover_unchanged_changed_unavailable_and_provider_failure():
    scenarios = {
        "success": ("price_validated", "unchanged", True),
        "price_change": ("price_changed", "price_increased", False),
        "price_decrease": ("price_changed", "price_decreased", False),
        "unavailable": ("unavailable", "unavailable", False),
        "itinerary_change": ("unavailable", "material_change", False),
        "error": ("revalidation_failed", "retryable_failure", False),
    }

    for scenario, expected in scenarios.items():
        repository, booking = draft(draft_id=f"draft_reval_{scenario}")
        review = revalidate_booking_draft(booking["id"], repository=repository, scenario=scenario)

        assert (review["status"], review["revalidation"]["status"], review["paymentAllowed"]) == expected
        assert event_types(repository, booking["id"])[-1] in {"booking_revalidation.completed", "booking_revalidation.failed"}


def test_offer_expiry_during_checkout_blocks_draft_after_passenger_entry():
    expired_now = datetime(2031, 7, 1, 8, 0, tzinfo=timezone.utc)

    exc = assert_raises(
        CheckoutValidationError,
        create_booking_draft,
        {"offerId": "ofb_flt_oneway", "contact": VALID_CONTACT, "passengers": [VALID_ADULT]},
        repository=InMemoryBookingDraftRepository(),
        now=expired_now,
    )

    assert exc.fields == {"offerId": ["Expired offers cannot continue to checkout."]}


def test_price_changing_after_passenger_entry_requires_acceptance_then_charges_new_amount():
    repository, stale_draft, review = validated_draft(scenario="price_change")

    assert review["currentTotal"]["amountCents"] == 32800
    stale_exc = assert_raises(CheckoutValidationError, finalize_booking_payment, stale_draft["id"], payment_payload(stale_draft, amountCents=28600), repository=repository)
    assert stale_exc.fields == {"amount": ["Payment amount and currency must match the accepted revalidated price."]}

    fresh_draft = repository.get(stale_draft["id"])
    paid = finalize_booking_payment(fresh_draft["id"], payment_payload(fresh_draft, idempotencyKey="idem-price-change-0002"), repository=repository)
    assert paid["payment"]["amount"]["amountCents"] == 32800
    assert paid["status"] == "ticketing_pending"


def test_payment_finalization_success_failure_duplicate_delayed_and_mismatch_cases():
    success_provider = CountingProvider()
    repository, draft, _ = validated_draft(draft_id="draft_success")
    success = finalize_booking_payment(draft["id"], payment_payload(draft), repository=repository, provider=success_provider)
    duplicate = finalize_booking_payment(draft["id"], payment_payload(draft), repository=repository, provider=success_provider)
    assert success["status"] == "ticketing_pending"
    assert duplicate["duplicate"] is True
    assert len(repository.get(draft["id"])["paymentAttempts"]) == 1
    assert len(success_provider.order_calls) == 1

    declined_repo, declined_draft, _ = validated_draft(draft_id="draft_declined")
    declined = finalize_booking_payment(declined_draft["id"], payment_payload(declined_draft, scenario="declined"), repository=declined_repo)
    assert declined["status"] == "payment_declined"
    assert declined_repo.get(declined_draft["id"])["providerOrder"] is None

    delayed_provider = CountingProvider()
    delayed_provider.pending_statuses = ["ticketing_pending", "ticketed"]
    delayed_repo, delayed_draft, _ = validated_draft(draft_id="draft_delayed")
    delayed = finalize_booking_payment(delayed_draft["id"], payment_payload(delayed_draft), repository=delayed_repo, provider=delayed_provider)
    still_pending = poll_booking_finalization(delayed_draft["id"], repository=delayed_repo, provider=delayed_provider)
    ticketed = poll_booking_finalization(delayed_draft["id"], repository=delayed_repo, provider=delayed_provider)
    assert delayed["status"] == "ticketing_pending"
    assert still_pending["status"] == "ticketing_pending"
    assert ticketed["status"] == "confirmed"

    mismatch_repo, mismatch_draft, _ = validated_draft(draft_id="draft_mismatch")
    mismatch = assert_raises(CheckoutValidationError, finalize_booking_payment, mismatch_draft["id"], payment_payload(mismatch_draft, amountCents=mismatch_draft["total"]["amountCents"] + 1), repository=mismatch_repo)
    assert mismatch.fields == {"amount": ["Payment amount and currency must match the accepted revalidated price."]}
    assert mismatch_repo.get(mismatch_draft["id"])["providerOrder"] is None


def test_provider_failure_and_ambiguous_timeout_are_idempotent_without_duplicate_financial_actions():
    failing_provider = CountingProvider()
    failing_provider.fail_first_order = True
    repository, draft, _ = validated_draft(draft_id="draft_timeout")

    failed = finalize_booking_payment(draft["id"], payment_payload(draft), repository=repository, provider=failing_provider)
    retry_same_key = finalize_booking_payment(draft["id"], payment_payload(draft), repository=repository, provider=failing_provider)
    retry_new_key = finalize_booking_payment(draft["id"], payment_payload(draft, idempotencyKey="idem-timeout-new-0002"), repository=repository, provider=failing_provider)

    saved = repository.get(draft["id"])
    assert failed["status"] == "booking_failed_after_payment"
    assert retry_same_key["duplicate"] is True
    assert retry_new_key["duplicate"] is True
    assert len(saved["paymentAttempts"]) == 1
    assert saved["paymentAttempts"][0]["status"] == "authorized_booking_failed"
    assert len(failing_provider.order_calls) == 1
    assert event_types(repository, draft["id"]).count("booking_provider_order.failed") == 1


def test_refresh_after_terminal_state_does_not_call_provider_or_create_audit_noise():
    provider = TicketedProvider()
    repository, draft, _ = validated_draft(draft_id="draft_terminal")
    finalize_booking_payment(draft["id"], payment_payload(draft), repository=repository, provider=provider)
    before_events = event_types(repository, draft["id"])

    refreshed = poll_booking_finalization(draft["id"], repository=repository, provider=provider)

    assert refreshed["status"] == "confirmed"
    assert provider.status_calls == []
    assert event_types(repository, draft["id"]) == before_events


def test_duplicate_payment_submit_with_different_key_after_terminal_state_does_not_create_second_order_or_payment():
    provider = CountingProvider()
    repository, draft, _ = validated_draft(draft_id="draft_double_submit")

    first = finalize_booking_payment(draft["id"], payment_payload(draft), repository=repository, provider=provider)
    duplicate = finalize_booking_payment(draft["id"], payment_payload(draft, idempotencyKey="idem-second-click-0002", paymentToken="tok_second_12345678"), repository=repository, provider=provider)

    saved = repository.get(draft["id"])
    assert first["payment"]["id"] == duplicate["payment"]["id"]
    assert duplicate["duplicate"] is True
    assert len(saved["paymentAttempts"]) == 1
    assert len(provider.order_calls) == 1
