# API and message contract conventions

This document defines the baseline contract for public APIs, service-to-service APIs,
webhooks, and asynchronous messages. Service-specific contracts can add fields, but
MUST preserve these conventions so clients, operators, and downstream consumers can
handle success, pagination, validation, idempotent retries, tracing, and failures in a
consistent way.

## Contract principles

1. Contracts are explicit, versioned, and backwards compatible by default.
2. JSON field names use lower camel case. Identifiers are opaque strings such as
   `booking_123`; clients MUST NOT parse embedded meaning from identifiers.
3. Timestamps use UTC ISO-8601 strings with a trailing `Z`.
4. Monetary amounts are decimal strings paired with a three-letter ISO-4217
   `currency` value.
5. Unknown optional response fields MUST be ignored by clients. Servers MUST NOT
   remove, rename, or change the meaning of an existing field without a new version.
6. Every request, response, event, and log entry carries the same correlation
   identifier for one logical workflow.

## Request conventions

### Headers

| Header | Required | Description |
| --- | --- | --- |
| `Authorization` | External APIs only | Bearer token or service credential for the caller. |
| `Content-Type: application/json` | Requests with bodies | JSON body encoding. |
| `Accept: application/json` | Yes | Response media type. |
| `X-Correlation-Id` | Yes | Caller-provided workflow identifier. Generate one at the edge if absent for legacy callers. |
| `Idempotency-Key` | Mutating commands with side effects | Caller-provided retry token scoped by actor, endpoint, and normalized request fingerprint. |
| `If-Match` | Conditional updates | Entity version or ETag used to prevent lost updates. |

### Body shape

Command requests SHOULD wrap domain input in `data` and optional `metadata`:

```json
{
  "data": {
    "holdId": "hold_456",
    "quoteId": "quote_789",
    "travelerIds": ["traveler_123"],
    "contactEmail": "customer@example.com"
  },
  "metadata": {
    "requestedBy": "user_123",
    "clientRequestId": "mobile-ios-01J2W5H2ZK"
  }
}
```

Rules:

- Required fields are documented per endpoint and validated before side effects run.
- Optional fields default only when the default is documented and stable.
- PATCH-like updates MUST distinguish omitted fields from explicit `null`.
- Bulk commands MUST define whether partial success is allowed; otherwise they are
  atomic and fail as a whole.

## Response conventions

All JSON responses use an envelope with `data`, `meta`, or `error`.

| Field | Applies to | Description |
| --- | --- | --- |
| `data` | Success | Resource object, command result, or array of resources. |
| `meta` | Success and replay responses | Request diagnostics such as `correlationId`, timestamps, pagination, and idempotency status. |
| `error` | Failures | Standard error object described below. |

Success responses SHOULD use the most specific HTTP status:

- `200 OK` for reads, updates, and idempotency replays of a completed result.
- `201 Created` for newly created resources and include `Location` when applicable.
- `202 Accepted` for asynchronous commands that have been accepted but not completed.
- `204 No Content` only when the client needs no response body.

### Success example

`201 Created`

```json
{
  "data": {
    "id": "booking_123",
    "type": "booking",
    "status": "held",
    "holdId": "hold_456",
    "quoteId": "quote_789",
    "expiresAt": "2026-07-07T18:00:00Z",
    "links": {
      "self": "/bookings/booking_123",
      "confirm": "/bookings/booking_123/confirm"
    }
  },
  "meta": {
    "correlationId": "corr_01J2W5H2ZK9Y7Q6X4P8N3M2L1A",
    "requestId": "req_01J2W5H3A1B2C3D4E5F6G7H8I9",
    "generatedAt": "2026-07-07T17:21:33Z",
    "apiVersion": "2026-07-01"
  }
}
```

## Pagination conventions

Collection endpoints MUST be cursor paginated unless a service-specific contract
explicitly justifies a different model.

Request parameters:

| Parameter | Description |
| --- | --- |
| `page[size]` | Requested page size. Default `25`; maximum `100`. |
| `page[after]` | Cursor returned as `nextCursor` from the previous page. |
| `page[before]` | Cursor for reverse traversal when supported. |

Response metadata:

```json
{
  "data": [
    { "id": "booking_123", "type": "booking", "status": "held" }
  ],
  "meta": {
    "correlationId": "corr_01J2W5H2ZK9Y7Q6X4P8N3M2L1A",
    "pagination": {
      "pageSize": 25,
      "nextCursor": "eyJjcmVhdGVkQXQiOiIyMDI2LTA3LTA3VDE3OjIxOjMzWiJ9",
      "previousCursor": null,
      "hasMore": true
    }
  }
}
```

Rules:

- Default ordering MUST be deterministic and documented for each collection.
- Cursors are opaque; clients MUST NOT decode or construct them.
- If total counts are expensive or unstable, omit them rather than returning stale
  values. If returned, include them as `meta.pagination.totalCount`.

## Filtering, sorting, and sparse fields

Filtering parameters use bracket notation and documented operators:

| Pattern | Example | Description |
| --- | --- | --- |
| Equality | `filter[status]=held` | Exact match. |
| Inclusion | `filter[status][in]=held,confirming` | Match any value. |
| Range | `filter[createdAt][gte]=2026-07-01T00:00:00Z` | Date, numeric, or amount ranges. |
| Search | `filter[q]=smith` | Service-defined text search. |

Sorting uses comma-separated field names with `-` for descending order:

```text
GET /bookings?filter[status][in]=held,confirming&sort=-createdAt,id&page[size]=25
```

Rules:

- Unsupported filters or sort fields return `400` validation errors.
- Services MUST document which fields are filterable and sortable.
- Sparse fieldsets MAY be supported with `fields[booking]=id,status,expiresAt`.

## Validation conventions

Validation happens before side effects and before idempotency records are completed.
A validation failure returns `400 Bad Request` for malformed syntax or unsupported
query parameters, and `422 Unprocessable Entity` when JSON is syntactically valid
but domain input is invalid.

### Validation error example

`422 Unprocessable Entity`

```json
{
  "error": {
    "code": "validation_failed",
    "message": "The request contains invalid fields.",
    "target": "data",
    "details": [
      {
        "code": "required",
        "field": "data.travelerIds",
        "message": "At least one traveler is required."
      },
      {
        "code": "format",
        "field": "data.contactEmail",
        "message": "Must be a valid email address."
      }
    ]
  },
  "meta": {
    "correlationId": "corr_01J2W5H2ZK9Y7Q6X4P8N3M2L1A",
    "requestId": "req_01J2W5H3A1B2C3D4E5F6G7H8I9",
    "generatedAt": "2026-07-07T17:21:33Z"
  }
}
```

## Idempotency conventions

Mutating commands that can create external or persistent side effects MUST require
`Idempotency-Key`. The key is scoped by authenticated actor, endpoint/command, and a
normalized request fingerprint, consistent with the cross-cutting idempotency model.

Duplicate behavior:

1. Same key and same fingerprint after completion returns the original response with
   replay metadata and does not execute side effects again.
2. Same key and different fingerprint returns `409 Conflict` with
   `idempotency_key_reused`.
3. Same key while the original request is still running returns `409 Conflict` or
   `202 Accepted` with a retry hint, as documented by the endpoint.
4. Replay windows are service-defined but MUST be at least as long as the external
   provider retry window for commands touching providers.

### Idempotency replay example

`200 OK`

```json
{
  "data": {
    "id": "booking_123",
    "type": "booking",
    "status": "held",
    "holdId": "hold_456",
    "quoteId": "quote_789",
    "expiresAt": "2026-07-07T18:00:00Z"
  },
  "meta": {
    "correlationId": "corr_01J2W5H2ZK9Y7Q6X4P8N3M2L1A",
    "requestId": "req_01J2W5H7R9S8T7U6V5W4X3Y2Z1",
    "generatedAt": "2026-07-07T17:22:10Z",
    "idempotency": {
      "key": "create-booking-01J2W5H2ZK",
      "status": "replayed",
      "originalRequestId": "req_01J2W5H3A1B2C3D4E5F6G7H8I9",
      "originalCompletedAt": "2026-07-07T17:21:34Z"
    }
  }
}
```

## Correlation identifier conventions

`X-Correlation-Id` connects API requests, asynchronous messages, domain events,
logs, metrics, and provider calls.

Rules:

- Edge services accept caller-provided correlation identifiers when they match the
  documented safe character set `[A-Za-z0-9._:-]{8,128}`; otherwise they generate a
  new identifier and record the rejected value in secure logs only.
- Internal callers MUST propagate the incoming correlation identifier unchanged.
- Responses MUST echo the effective identifier in `meta.correlationId` and SHOULD
  also return it as `X-Correlation-Id`.
- Domain events use the existing `correlationId` envelope field.
- Outbound provider calls include the identifier when provider contracts allow it.

## Error response conventions

Errors use one envelope shape across APIs:

| Field | Required | Description |
| --- | --- | --- |
| `error.code` | Yes | Stable machine-readable code in snake case. |
| `error.message` | Yes | Human-readable summary safe for logs and clients. |
| `error.target` | No | Field, resource, or dependency related to the error. |
| `error.details` | No | Array of structured details for validation or dependency failures. |
| `meta.correlationId` | Yes | Effective correlation identifier. |
| `meta.requestId` | Yes | Unique request attempt identifier. |
| `meta.retryable` | Failures | Whether the exact operation may be retried safely. |

Recommended status/code mapping:

| HTTP status | Code examples | Use when |
| --- | --- | --- |
| `400` | `invalid_request`, `unsupported_filter` | Syntax, unsupported query parameter, or malformed JSON. |
| `401` | `unauthenticated` | Missing or invalid authentication. |
| `403` | `forbidden` | Caller is authenticated but lacks permission. |
| `404` | `not_found` | Target resource does not exist or is not visible to caller. |
| `409` | `domain_conflict`, `idempotency_key_reused`, `version_conflict` | Current resource state or duplicate key conflicts with the command. |
| `422` | `validation_failed` | Request is syntactically valid but violates input rules. |
| `429` | `rate_limited` | Caller exceeded a quota. |
| `500` | `internal_error` | Unexpected server failure. |
| `503` | `dependency_unavailable` | Required downstream service or provider is unavailable. |

### Domain conflict example

`409 Conflict`

```json
{
  "error": {
    "code": "domain_conflict",
    "message": "Booking cannot be confirmed from its current state.",
    "target": "booking_123",
    "details": [
      {
        "code": "invalid_state_transition",
        "currentState": "expired",
        "requestedTransition": "confirm"
      }
    ]
  },
  "meta": {
    "correlationId": "corr_01J2W5H2ZK9Y7Q6X4P8N3M2L1A",
    "requestId": "req_01J2W5H8M9N0P1Q2R3S4T5U6V7",
    "generatedAt": "2026-07-07T17:23:00Z",
    "retryable": false
  }
}
```

### Not-found example

`404 Not Found`

```json
{
  "error": {
    "code": "not_found",
    "message": "Booking was not found.",
    "target": "booking_999"
  },
  "meta": {
    "correlationId": "corr_01J2W5H2ZK9Y7Q6X4P8N3M2L1A",
    "requestId": "req_01J2W5H9A8B7C6D5E4F3G2H1I0",
    "generatedAt": "2026-07-07T17:24:00Z",
    "retryable": false
  }
}
```

## Asynchronous message conventions

Domain events follow the existing [Domain event model](domain-events.md). Command or
integration messages that are not domain events SHOULD still use a versioned
envelope:

```json
{
  "messageId": "msg_01J2W5H2ZK9Y7Q6X4P8N3M2L1A",
  "messageType": "payment.capture_requested",
  "messageVersion": 1,
  "correlationId": "corr_01J2W5H2ZK9Y7Q6X4P8N3M2L1A",
  "causationId": "booking_123",
  "idempotencyKey": "capture-payment-01J2W5H2ZK",
  "occurredAt": "2026-07-07T17:21:33Z",
  "producer": "booking-service",
  "payload": {
    "bookingId": "booking_123",
    "paymentId": "payment_456",
    "amount": "120.00",
    "currency": "USD"
  }
}
```

Rules:

- `messageId` is globally unique and used for message de-duplication.
- `messageType` is a stable dotted name such as `payment.capture_requested`.
- `messageVersion` starts at `1` and increments only for breaking payload changes.
- Consumers MUST ignore unknown optional payload fields for the same version.
- Producers MUST include `correlationId`; consumers MUST propagate it into any
  follow-up API calls, events, logs, and messages.

## Versioning and compatibility

- API versions are documented by release date, for example `2026-07-01`, and are
  returned in success metadata when the endpoint is versioned.
- Backwards-compatible changes include adding optional fields, new enum values only
  when clients are documented to treat unknown values as opaque, and new endpoints.
- Breaking changes include removing fields, changing field types or meanings,
  making optional fields required, changing default sort order, or changing error
  codes for the same condition.
- Deprecated fields SHOULD include a replacement and removal date in endpoint
  documentation and remain populated through the deprecation window.
