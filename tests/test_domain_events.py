import unittest
from datetime import datetime, timezone
from uuid import UUID

from backend.shared.domain_events import (
    ActorType,
    DomainEvent,
    DomainEventType,
    EventActor,
    InProcessDomainEventPublisher,
)


class DomainEventTests(unittest.TestCase):
    def test_event_envelope_serializes_required_fields(self):
        event = DomainEvent(
            event_type=DomainEventType.BOOKING_CREATED,
            aggregate_id="booking_123",
            occurred_at=datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc),
            actor=EventActor(ActorType.USER, "user_456", "Nirav"),
            correlation_id="corr_789",
            payload_version=1,
            payload={"tripType": "round_trip"},
        )

        serialized = event.to_dict()

        self.assertEqual(
            set(serialized.keys()),
            {
                "eventId",
                "type",
                "aggregateId",
                "timestamp",
                "actor",
                "correlationId",
                "payloadVersion",
                "payload",
            },
        )
        self.assertIsInstance(UUID(serialized["eventId"]), UUID)
        self.assertEqual(serialized["type"], "booking.created")
        self.assertEqual(serialized["aggregateId"], "booking_123")
        self.assertEqual(serialized["timestamp"], "2026-07-07T17:00:00Z")
        self.assertEqual(serialized["actor"], {"type": "user", "id": "user_456", "displayName": "Nirav"})
        self.assertEqual(serialized["correlationId"], "corr_789")
        self.assertEqual(serialized["payloadVersion"], 1)
        self.assertEqual(serialized["payload"], {"tripType": "round_trip"})

    def test_event_round_trips_from_serialized_form(self):
        event = DomainEvent(
            event_type=DomainEventType.PAYMENT_CAPTURED,
            aggregate_id="payment_123",
            actor=EventActor(ActorType.SYSTEM, "payments-service"),
            correlation_id="corr_123",
            payload_version=2,
            payload={"amount": "120.00", "currency": "USD"},
        )

        restored = DomainEvent.from_dict(event.to_dict())

        self.assertEqual(restored, event)

    def test_publisher_dispatches_global_and_type_specific_handlers(self):
        publisher = InProcessDomainEventPublisher()
        calls = []
        event = DomainEvent(
            event_type=DomainEventType.PROVIDER_STATUS_CHANGED,
            aggregate_id="provider_123",
            actor=EventActor(ActorType.PROVIDER, "provider_123"),
            correlation_id="corr_provider",
            payload_version=1,
            payload={"status": "degraded"},
        )

        publisher.subscribe_all(lambda published: calls.append(("all", published.event_type)))
        publisher.subscribe(
            DomainEventType.PROVIDER_STATUS_CHANGED,
            lambda published: calls.append(("specific", published.aggregate_id)),
        )

        publisher.publish(event)

        self.assertEqual(
            calls,
            [
                ("all", DomainEventType.PROVIDER_STATUS_CHANGED),
                ("specific", "provider_123"),
            ],
        )

    def test_payload_version_must_be_positive(self):
        with self.assertRaises(ValueError):
            DomainEvent(
                event_type=DomainEventType.AUDIT_RECORDED,
                aggregate_id="audit_123",
                actor=EventActor(ActorType.SYSTEM, "audit-service"),
                correlation_id="corr_audit",
                payload_version=0,
                payload={},
            )


if __name__ == "__main__":
    unittest.main()
