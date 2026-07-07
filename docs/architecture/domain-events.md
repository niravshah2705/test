# Internal domain event model

Domain events are the shared coordination contract between booking, payment,
taxi, notification, audit, and provider-status modules. Events are immutable,
serializable envelopes with a versioned payload so they can be dispatched
in-process now and moved to a durable transport later without changing producer
contracts.

## Envelope

Every domain event MUST include these fields:

| Field | Description |
| --- | --- |
| `eventId` | Globally unique event identifier for idempotency and tracing. |
| `type` | Canonical event type, for example `booking.created`. |
| `aggregateId` | Identifier of the aggregate/root entity affected by the event. |
| `timestamp` | UTC ISO-8601 time when the event occurred. |
| `actor` | Principal that caused the event (`type`, `id`, optional `displayName`). |
| `correlationId` | Request/workflow identifier propagated across modules. |
| `payloadVersion` | Positive integer schema version for the payload. |
| `payload` | JSON-compatible event data for the declared type/version. |

Example:

```json
{
  "eventId": "3fb7bf2e-bac4-4d45-a77f-c46272cf5985",
  "type": "payment.captured",
  "aggregateId": "payment_123",
  "timestamp": "2026-07-07T17:21:33.927000Z",
  "actor": { "type": "system", "id": "payments-service" },
  "correlationId": "corr_987",
  "payloadVersion": 1,
  "payload": {
    "bookingId": "booking_456",
    "amount": "120.00",
    "currency": "USD"
  }
}
```

## Event families

The initial canonical types are defined in `backend/shared/domain_events.py`:

- Booking: `booking.created`, `booking.confirmed`, `booking.cancelled`, `booking.failed`
- Payment: `payment.authorized`, `payment.captured`, `payment.failed`, `payment.refunded`
- Taxi: `taxi.requested`, `taxi.assigned`, `taxi.cancelled`, `taxi.completed`
- Notification: `notification.requested`, `notification.sent`, `notification.failed`
- Audit: `audit.recorded`
- Provider status: `provider.status_changed`, `provider.status_degraded`, `provider.status_recovered`

## Publishing

`DomainEventPublisher` is the publishing interface. `InProcessDomainEventPublisher`
provides a synchronous in-memory implementation that supports:

- `subscribe(event_type, handler)` for type-specific handlers.
- `subscribe_all(handler)` for observability/audit handlers that receive all events.
- `publish(event)` and `publish_all(events)` for dispatch.

Handlers run synchronously in registration order. A handler exception is allowed
to propagate so producers can fail fast until durable retry semantics are added.

## Payload versioning rules

1. `payloadVersion` starts at `1` for each event type and MUST be a positive integer.
2. Producers MUST only add optional payload fields within an existing version.
3. Producers MUST NOT remove, rename, or change the meaning/type of an existing payload field within a version.
4. Any breaking payload change MUST introduce the next integer `payloadVersion` for that event type.
5. Consumers MUST ignore unknown optional fields and branch by `type` + `payloadVersion` for required fields.
6. Deprecated payload fields SHOULD remain populated for at least one released replacement version before removal in a later breaking version.
7. Envelope fields are not payload-versioned; changing envelope semantics requires a separate architecture decision.
