# Cross-cutting idempotency model

Booking, payment, cancellation, webhook, and provider interaction commands MUST
use a caller-provided idempotency key for any operation that can create external
or persistent side effects. The shared primitives live in
`backend/shared/idempotency.py` so modules can share behavior while using the
storage backend appropriate to each runtime.

## Key model

An `IdempotencyKey` is composed of:

| Field | Description |
| --- | --- |
| `scope` | Command family namespace: `booking`, `payment`, `cancellation`, `webhook`, or `provider_interaction`. |
| `actor_id` | Caller, tenant, provider, or integration identity that owns the key. |
| `key` | Caller-provided retry token for the logical command. |

The canonical storage key is `scope:actor_id:key`. This lets the same raw key be
used safely by different command families or actors without collisions.

## Storage contract

`IdempotencyStore` defines the durable store contract:

- `get(key)` returns an existing record if one exists.
- `start(key, request_fingerprint)` atomically creates a `started` record for a
  new command. Durable implementations MUST guarantee only one caller can create
  a record for a storage key.
- `complete(key, result)` stores the successful command result.
- `fail(key, error)` stores a terminal failure result.

An `IdempotencyRecord` stores the key, request fingerprint, command id, status,
result payload, and timestamps. Production stores should persist completed
records long enough to cover client retry windows and provider webhook retry
windows.

## Duplicate request behavior

1. First request for a key creates a `started` record, runs the command, and
   stores the original result as `completed`.
2. A duplicate request with the same key and same request fingerprint returns the
   stored original result. The command handler is not executed again.
3. A duplicate request with the same key but a different request fingerprint is a
   conflict and MUST be rejected instead of replayed.
4. A duplicate request that arrives while the original command is still
   `started` is considered in progress and SHOULD return a retryable in-progress
   response rather than running side effects twice.
5. A stored terminal failure can be replayed as the original failure result so
   clients observe stable retry behavior.

## Command adoption

- Booking commands accept a booking-scoped `IdempotencyKey`.
- Payment commands accept a payment-scoped `IdempotencyKey`.
- Cancellation commands should use the cancellation scope and include the booking
  or payment cancellation target in the request fingerprint.
- Webhook commands should use the webhook scope and include the provider event id
  and payload version in the request fingerprint.
- Provider interaction commands should use the provider interaction scope and
  include the provider, endpoint/action, and normalized request payload in the
  request fingerprint.

Tests in `tests/test_idempotency.py` verify that duplicate booking and payment
command replays return the original result without invoking command side effects
a second time.
