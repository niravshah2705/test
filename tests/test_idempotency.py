import unittest

from backend.shared.idempotency import (
    BookingCommand,
    IdempotencyConflictError,
    IdempotencyKey,
    IdempotencyScope,
    IdempotencyStatus,
    InMemoryIdempotencyStore,
    PaymentCommand,
    run_idempotent,
)


class IdempotencyTests(unittest.TestCase):
    def test_key_is_namespaced_for_storage(self):
        key = IdempotencyKey(IdempotencyScope.BOOKING, "req_123", "user_456")

        self.assertEqual(key.storage_key, "booking:user_456:req_123")

    def test_booking_command_accepts_idempotency_key(self):
        key = IdempotencyKey(IdempotencyScope.BOOKING, "book_once", "user_123")
        command = BookingCommand(
            booking_id="booking_123",
            traveler_id="traveler_456",
            itinerary={"origin": "JFK", "destination": "LAX"},
            idempotency_key=key,
        )

        self.assertEqual(command.idempotency_key, key)
        self.assertIn("booking_123", command.request_fingerprint)

    def test_payment_command_accepts_idempotency_key(self):
        key = IdempotencyKey(IdempotencyScope.PAYMENT, "pay_once", "user_123")
        command = PaymentCommand(
            payment_id="payment_123",
            booking_id="booking_123",
            amount="120.00",
            currency="USD",
            idempotency_key=key,
        )

        self.assertEqual(command.idempotency_key, key)
        self.assertIn("payment_123", command.request_fingerprint)

    def test_duplicate_booking_command_replays_original_result(self):
        store = InMemoryIdempotencyStore()
        command = BookingCommand(
            booking_id="booking_123",
            traveler_id="traveler_456",
            itinerary={"origin": "JFK", "destination": "LAX"},
            idempotency_key=IdempotencyKey(IdempotencyScope.BOOKING, "dup_book", "user_123"),
        )
        calls = []

        def create_booking():
            calls.append("called")
            return {"bookingId": command.booking_id, "status": "created"}

        first = run_idempotent(store, command.idempotency_key, command.request_fingerprint, create_booking)
        second = run_idempotent(store, command.idempotency_key, command.request_fingerprint, create_booking)

        self.assertEqual(first, {"bookingId": "booking_123", "status": "created"})
        self.assertEqual(second, first)
        self.assertEqual(calls, ["called"])
        self.assertEqual(store.get(command.idempotency_key).status, IdempotencyStatus.COMPLETED)

    def test_duplicate_payment_command_replays_original_result(self):
        store = InMemoryIdempotencyStore()
        command = PaymentCommand(
            payment_id="payment_123",
            booking_id="booking_123",
            amount="120.00",
            currency="USD",
            idempotency_key=IdempotencyKey(IdempotencyScope.PAYMENT, "dup_pay", "user_123"),
        )
        calls = []

        def capture_payment():
            calls.append("called")
            return {"paymentId": command.payment_id, "status": "captured", "amount": "120.00"}

        first = run_idempotent(store, command.idempotency_key, command.request_fingerprint, capture_payment)
        second = run_idempotent(store, command.idempotency_key, command.request_fingerprint, capture_payment)

        self.assertEqual(first, {"paymentId": "payment_123", "status": "captured", "amount": "120.00"})
        self.assertEqual(second, first)
        self.assertEqual(calls, ["called"])

    def test_reusing_key_with_different_request_is_rejected(self):
        store = InMemoryIdempotencyStore()
        key = IdempotencyKey(IdempotencyScope.PAYMENT, "same_key", "user_123")

        run_idempotent(store, key, "first-fingerprint", lambda: {"paymentId": "payment_123"})

        with self.assertRaises(IdempotencyConflictError):
            run_idempotent(store, key, "different-fingerprint", lambda: {"paymentId": "payment_999"})


if __name__ == "__main__":
    unittest.main()
