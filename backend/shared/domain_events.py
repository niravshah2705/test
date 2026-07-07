"""Serializable internal domain event primitives.

The event envelope defined here is intentionally transport-agnostic. It can be
used by in-process subscribers today and serialized to JSON for a broker or log
without changing producer contracts later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Callable, Dict, Iterable, List, Mapping, MutableMapping, Optional
from uuid import UUID, uuid4

JsonObject = Dict[str, Any]
DomainEventHandler = Callable[["DomainEvent"], None]


class ActorType(str, Enum):
    """Principal categories that can initiate domain events."""

    USER = "user"
    SYSTEM = "system"
    PROVIDER = "provider"
    SUPPORT = "support"
    SERVICE = "service"


@dataclass(frozen=True)
class EventActor:
    """Actor metadata embedded in every event envelope."""

    actor_type: ActorType
    actor_id: str
    display_name: Optional[str] = None

    def to_dict(self) -> JsonObject:
        data: JsonObject = {
            "type": self.actor_type.value,
            "id": self.actor_id,
        }
        if self.display_name is not None:
            data["displayName"] = self.display_name
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventActor":
        return cls(
            actor_type=ActorType(data["type"]),
            actor_id=str(data["id"]),
            display_name=data.get("displayName"),
        )


class DomainEventType(str, Enum):
    """Canonical internal event types by coordination area."""

    BOOKING_CREATED = "booking.created"
    BOOKING_CONFIRMED = "booking.confirmed"
    BOOKING_CANCELLED = "booking.cancelled"
    BOOKING_FAILED = "booking.failed"

    PAYMENT_AUTHORIZED = "payment.authorized"
    PAYMENT_CAPTURED = "payment.captured"
    PAYMENT_FAILED = "payment.failed"
    PAYMENT_REFUNDED = "payment.refunded"

    TAXI_REQUESTED = "taxi.requested"
    TAXI_ASSIGNED = "taxi.assigned"
    TAXI_CANCELLED = "taxi.cancelled"
    TAXI_COMPLETED = "taxi.completed"

    NOTIFICATION_REQUESTED = "notification.requested"
    NOTIFICATION_SENT = "notification.sent"
    NOTIFICATION_FAILED = "notification.failed"

    AUDIT_RECORDED = "audit.recorded"

    PROVIDER_STATUS_CHANGED = "provider.status_changed"
    PROVIDER_STATUS_DEGRADED = "provider.status_degraded"
    PROVIDER_STATUS_RECOVERED = "provider.status_recovered"


@dataclass(frozen=True)
class DomainEvent:
    """Serializable envelope for internal domain events.

    Required envelope fields:
    - event_id: unique identifier for idempotency and tracing
    - event_type: canonical event name
    - aggregate_id: identifier of the affected aggregate/root entity
    - occurred_at: timezone-aware UTC timestamp
    - actor: initiating principal
    - correlation_id: request/workflow trace identifier
    - payload_version: positive integer schema version for payload
    - payload: versioned JSON-compatible event data
    """

    event_type: DomainEventType
    aggregate_id: str
    actor: EventActor
    correlation_id: str
    payload_version: int
    payload: Mapping[str, Any]
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.aggregate_id:
            raise ValueError("aggregate_id is required")
        if not self.correlation_id:
            raise ValueError("correlation_id is required")
        if self.payload_version < 1:
            raise ValueError("payload_version must be >= 1")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")

    def to_dict(self) -> JsonObject:
        """Return a JSON-compatible dictionary using stable wire keys."""

        return {
            "eventId": str(self.event_id),
            "type": self.event_type.value,
            "aggregateId": self.aggregate_id,
            "timestamp": self.occurred_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "actor": self.actor.to_dict(),
            "correlationId": self.correlation_id,
            "payloadVersion": self.payload_version,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DomainEvent":
        timestamp = str(data["timestamp"])
        if timestamp.endswith("Z"):
            timestamp = f"{timestamp[:-1]}+00:00"

        return cls(
            event_id=UUID(str(data["eventId"])),
            event_type=DomainEventType(data["type"]),
            aggregate_id=str(data["aggregateId"]),
            occurred_at=datetime.fromisoformat(timestamp),
            actor=EventActor.from_dict(data["actor"]),
            correlation_id=str(data["correlationId"]),
            payload_version=int(data["payloadVersion"]),
            payload=dict(data["payload"]),
        )


class DomainEventPublisher:
    """Publisher contract for dispatching internal domain events."""

    def publish(self, event: DomainEvent) -> None:
        raise NotImplementedError

    def publish_all(self, events: Iterable[DomainEvent]) -> None:
        for event in events:
            self.publish(event)


class InProcessDomainEventPublisher(DomainEventPublisher):
    """Synchronous in-memory publisher for local module coordination."""

    def __init__(self) -> None:
        self._handlers: MutableMapping[DomainEventType, List[DomainEventHandler]] = {}
        self._global_handlers: List[DomainEventHandler] = []
        self._lock = RLock()

    def subscribe(self, event_type: DomainEventType, handler: DomainEventHandler) -> None:
        """Register a handler for a specific event type."""

        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

    def subscribe_all(self, handler: DomainEventHandler) -> None:
        """Register a handler that receives every published event."""

        with self._lock:
            self._global_handlers.append(handler)

    def publish(self, event: DomainEvent) -> None:
        """Synchronously dispatch an event to matching and global handlers."""

        with self._lock:
            handlers = [*self._global_handlers, *self._handlers.get(event.event_type, [])]

        for handler in handlers:
            handler(event)
