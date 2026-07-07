import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.shared.payments import (
    AuthorizationReference,
    CaptureResult,
    ExternalPaymentReference,
    InMemoryPaymentRepository,
    PaymentAttemptRecord,
    PaymentAttemptStatus,
    PaymentIdempotencyKeyRecord,
    PaymentIntentRecord,
    PaymentIntentStatus,
    PaymentOperation,
    PaymentStatusHistoryEntry,
    RefundRecord,
)


class PaymentRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryPaymentRepository()
        self.created_at = datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc)

    def make_intent(self, **overrides):
        values = {
            "payment_intent_id": "pi_123",
            "booking_id": "booking_123",
            "amount": Decimal("120.00"),
            "currency": "usd",
            "customer_identifier": "customer_123",
            "created_at": self.created_at,
            "updated_at": self.created_at,
        }
        values.update(overrides)
        return PaymentIntentRecord(**values)

    def provider_reference(self, reference_id="auth_ref_123", reference_type="authorization"):
        return ExternalPaymentReference(
            provider="stripe",
            reference_type=reference_type,
            reference_id=reference_id,
            raw_response={"id": reference_id, "status": "succeeded"},
            created_at=self.created_at,
        )

    def test_repository_persists_payment_intent_attempt_authorization_capture_and_refund(self):
        intent = self.repository.save(self.make_intent())
        authorization = AuthorizationReference(
            authorization_id="auth_123",
            provider_reference=self.provider_reference(),
            authorized_amount=Decimal("120"),
            currency="USD",
            authorized_at=self.created_at + timedelta(minutes=1),
            expires_at=self.created_at + timedelta(days=7),
        )
        capture = CaptureResult(
            capture_id="capture_123",
            provider_reference=self.provider_reference("capture_ref_123", "capture"),
            captured_amount=Decimal("120.00"),
            currency="usd",
            captured_at=self.created_at + timedelta(minutes=2),
        )
        attempt = PaymentAttemptRecord(
            attempt_id="attempt_1",
            payment_intent_id=intent.payment_intent_id,
            booking_id=intent.booking_id,
            provider="stripe",
            created_at=self.created_at,
            updated_at=self.created_at,
        ).with_authorization(authorization).with_capture(capture)
        refund = RefundRecord(
            refund_id="refund_123",
            provider_reference=self.provider_reference("refund_ref_123", "refund"),
            refunded_amount=Decimal("10.00"),
            currency="USD",
            reason="customer request",
            refunded_at=self.created_at + timedelta(minutes=3),
        )

        self.repository.add_attempt(intent.payment_intent_id, attempt)
        updated = self.repository.add_refund(intent.payment_intent_id, refund)

        stored = self.repository.get("pi_123")
        self.assertEqual(stored, updated)
        self.assertEqual(stored.currency, "USD")
        self.assertEqual(stored.attempts[0].status, PaymentAttemptStatus.CAPTURED)
        self.assertEqual(stored.attempts[0].authorization_reference.provider_reference.reference_id, "auth_ref_123")
        self.assertEqual(stored.attempts[0].capture_result.provider_reference.reference_id, "capture_ref_123")
        self.assertEqual(stored.refunds[0].provider_reference.reference_id, "refund_ref_123")
        self.assertEqual(stored.to_dict()["amount"], "120.00")
        self.assertEqual(stored.to_dict()["status"], "requires_payment_method")

    def test_unique_idempotency_keys_are_enforced_per_operation(self):
        intent = self.repository.save(self.make_intent())
        create_key = PaymentIdempotencyKeyRecord(
            operation=PaymentOperation.CREATE_INTENT,
            idempotency_key="same-key",
            request_fingerprint="create-body",
            payment_intent_id=intent.payment_intent_id,
            created_at=self.created_at,
        )
        authorize_key = PaymentIdempotencyKeyRecord(
            operation=PaymentOperation.AUTHORIZE,
            idempotency_key="same-key",
            request_fingerprint="authorize-body",
            payment_intent_id=intent.payment_intent_id,
            created_at=self.created_at + timedelta(seconds=1),
        )

        self.repository.record_idempotency_key(intent.payment_intent_id, create_key)
        updated = self.repository.record_idempotency_key(intent.payment_intent_id, authorize_key)

        self.assertEqual(len(updated.idempotency_keys), 2)
        self.assertEqual(
            self.repository.find_by_idempotency_key(PaymentOperation.CREATE_INTENT, "same-key").payment_intent_id,
            intent.payment_intent_id,
        )
        duplicate_authorize_key = PaymentIdempotencyKeyRecord(
            operation=PaymentOperation.AUTHORIZE,
            idempotency_key="same-key",
            request_fingerprint="different-authorize-body",
            payment_intent_id=intent.payment_intent_id,
            created_at=self.created_at + timedelta(seconds=2),
        )
        with self.assertRaises(ValueError):
            self.repository.record_idempotency_key(intent.payment_intent_id, duplicate_authorize_key)

    def test_idempotency_key_cannot_be_reused_for_same_operation_on_another_intent(self):
        first = self.repository.save(self.make_intent(payment_intent_id="pi_123", booking_id="booking_123"))
        second = self.repository.save(self.make_intent(payment_intent_id="pi_456", booking_id="booking_456"))
        key = PaymentIdempotencyKeyRecord(
            operation=PaymentOperation.CAPTURE,
            idempotency_key="capture-once",
            request_fingerprint="capture-body",
            payment_intent_id=first.payment_intent_id,
            created_at=self.created_at,
        )
        self.repository.record_idempotency_key(first.payment_intent_id, key)

        reused_key = PaymentIdempotencyKeyRecord(
            operation=PaymentOperation.CAPTURE,
            idempotency_key="capture-once",
            request_fingerprint="capture-other-body",
            payment_intent_id=second.payment_intent_id,
            created_at=self.created_at,
        )

        with self.assertRaises(ValueError):
            self.repository.record_idempotency_key(second.payment_intent_id, reused_key)

    def test_status_transition_persistence_is_append_only_and_chronological(self):
        intent = self.repository.save(self.make_intent())
        authorized_entry = PaymentStatusHistoryEntry(
            history_id="status_authorized",
            status=PaymentIntentStatus.AUTHORIZED,
            changed_at=self.created_at + timedelta(minutes=1),
            actor="payment-service",
        )
        captured_entry = PaymentStatusHistoryEntry(
            history_id="status_captured",
            status=PaymentIntentStatus.CAPTURED,
            changed_at=self.created_at + timedelta(minutes=2),
            actor="payment-service",
        )

        self.repository.append_status(intent.payment_intent_id, authorized_entry)
        updated = self.repository.append_status(intent.payment_intent_id, captured_entry)

        self.assertEqual(updated.status, PaymentIntentStatus.CAPTURED)
        self.assertEqual([entry.status for entry in updated.status_history], [
            PaymentIntentStatus.REQUIRES_PAYMENT_METHOD,
            PaymentIntentStatus.AUTHORIZED,
            PaymentIntentStatus.CAPTURED,
        ])
        stored = self.repository.get(intent.payment_intent_id)
        with self.assertRaises(ValueError):
            self.repository.save(replace(stored, status_history=stored.status_history[1:]))
        with self.assertRaises(ValueError):
            self.repository.append_status(
                intent.payment_intent_id,
                PaymentStatusHistoryEntry(
                    status=PaymentIntentStatus.FAILED,
                    changed_at=self.created_at - timedelta(minutes=1),
                ),
            )

    def test_multiple_payment_attempts_can_be_persisted_for_one_booking(self):
        intent = self.repository.save(self.make_intent())
        failed_attempt = PaymentAttemptRecord(
            attempt_id="attempt_failed",
            payment_intent_id=intent.payment_intent_id,
            booking_id=intent.booking_id,
            provider="stripe",
            status=PaymentAttemptStatus.FAILED,
            failure_code="card_declined",
            failure_message="Card was declined",
            created_at=self.created_at,
            updated_at=self.created_at + timedelta(seconds=1),
        )
        successful_attempt = PaymentAttemptRecord(
            attempt_id="attempt_success",
            payment_intent_id=intent.payment_intent_id,
            booking_id=intent.booking_id,
            provider="stripe",
            status=PaymentAttemptStatus.AUTHORIZED,
            external_references=(self.provider_reference("auth_ref_456"),),
            created_at=self.created_at + timedelta(minutes=1),
            updated_at=self.created_at + timedelta(minutes=1),
        )

        self.repository.add_attempt(intent.payment_intent_id, failed_attempt)
        updated = self.repository.add_attempt(intent.payment_intent_id, successful_attempt)

        self.assertEqual([attempt.attempt_id for attempt in updated.attempts], ["attempt_failed", "attempt_success"])
        booking_payments = self.repository.find_by_booking_id(intent.booking_id)
        self.assertEqual(booking_payments, (updated,))
        with self.assertRaises(ValueError):
            self.repository.add_attempt(intent.payment_intent_id, replace(successful_attempt, provider="adyen"))

    def test_external_payment_references_are_immutable_after_storage(self):
        intent = self.repository.save(self.make_intent())
        reference = self.provider_reference("auth_ref_immutable")
        attempt = PaymentAttemptRecord(
            attempt_id="attempt_immutable",
            payment_intent_id=intent.payment_intent_id,
            booking_id=intent.booking_id,
            provider="stripe",
            status=PaymentAttemptStatus.AUTHORIZED,
            external_references=(reference,),
            created_at=self.created_at,
            updated_at=self.created_at,
        )
        stored = self.repository.add_attempt(intent.payment_intent_id, attempt)

        mutated_reference = ExternalPaymentReference(
            provider="stripe",
            reference_type="authorization",
            reference_id="auth_ref_changed",
            raw_response={"id": "auth_ref_changed"},
            created_at=self.created_at,
        )
        tampered_attempt = replace(stored.attempts[0], external_references=(mutated_reference,))
        tampered_intent = replace(stored, attempts=(tampered_attempt,))

        with self.assertRaises(ValueError):
            self.repository.save(tampered_intent)
        self.assertEqual(
            self.repository.get(intent.payment_intent_id).attempts[0].external_references[0].reference_id,
            "auth_ref_immutable",
        )

    def test_external_payment_references_must_be_unique_across_intents(self):
        first = self.repository.save(self.make_intent(payment_intent_id="pi_123", booking_id="booking_123"))
        second = self.repository.save(self.make_intent(payment_intent_id="pi_456", booking_id="booking_456"))
        shared_reference = self.provider_reference("provider_ref_shared")
        self.repository.add_attempt(
            first.payment_intent_id,
            PaymentAttemptRecord(
                attempt_id="attempt_1",
                payment_intent_id=first.payment_intent_id,
                booking_id=first.booking_id,
                provider="stripe",
                external_references=(shared_reference,),
                created_at=self.created_at,
                updated_at=self.created_at,
            ),
        )

        with self.assertRaises(ValueError):
            self.repository.add_attempt(
                second.payment_intent_id,
                PaymentAttemptRecord(
                    attempt_id="attempt_2",
                    payment_intent_id=second.payment_intent_id,
                    booking_id=second.booking_id,
                    provider="stripe",
                    external_references=(shared_reference,),
                    created_at=self.created_at,
                    updated_at=self.created_at,
                ),
            )


if __name__ == "__main__":
    unittest.main()
