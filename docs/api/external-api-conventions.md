# External API Contract Conventions

This document defines the client-facing conventions for public transactional APIs. Endpoint-specific designs must follow these rules unless an API review explicitly documents an exception.

## Contract principles

- Use HTTPS and JSON for all client-facing endpoints.
- Version all public APIs under a stable URL prefix, for example `/v1`.
- Treat published response fields as additive-only within a major version.
- Use UTC timestamps formatted as RFC 3339 strings.
- Use opaque identifiers in URLs and payloads; clients must not parse IDs for meaning.
- Include request correlation support on every endpoint through `X-Correlation-ID`.

## Transactional resources

Transactional resources represent business operations that change state, such as payments, orders, transfers, or ledger entries.

### Resource modeling

- Expose resources as plural nouns: `/v1/transactions`, `/v1/refunds`.
- Use a canonical `id` field for the resource identifier.
- Include `status`, `createdAt`, and `updatedAt` on mutable transactional resources.
- Prefer explicit lifecycle states over boolean flags, for example `pending`, `authorized`, `posted`, `failed`, `cancelled`.
- Return resource representations after create and update operations.
- Avoid destructive deletes for transactional records; use cancellation or reversal endpoints when business rules allow a state transition.

### Methods

| Method | Use |
| --- | --- |
| `POST /resources` | Create a new transactional resource. Requires idempotency support. |
| `GET /resources/{id}` | Retrieve a resource by ID. |
| `GET /resources` | List resources with pagination and filtering. |
| `PATCH /resources/{id}` | Apply partial updates only for mutable fields. |
| `POST /resources/{id}:action` | Execute domain actions such as `cancel`, `capture`, or `reverse`. Requires idempotency support when the action mutates state. |

## Request correlation

Every endpoint design must support request correlation.

- Clients may send `X-Correlation-ID` on any request.
- If the header is absent, the service generates one.
- Responses must include the resolved `X-Correlation-ID` header.
- Error envelopes must echo the same value in `correlationId`.
- Correlation IDs should be logged with request metadata and downstream calls.
- The value should be 1-128 visible ASCII characters. UUIDv4 is recommended.

## Authentication and authorization

- Require `Authorization: Bearer <token>` for protected endpoints.
- Tokens must be validated for issuer, audience, expiry, and signature before application logic runs.
- Use scopes or permissions for authorization, for example `transactions:read` and `transactions:write`.
- Return `401 Unauthorized` when credentials are missing, expired, malformed, or invalid.
- Return `403 Forbidden` when credentials are valid but insufficient for the requested operation.
- Do not expose credential validation details in error messages.

## Idempotency

Idempotency is required for all client-triggered mutating operations that may be retried, including creates and action endpoints.

- Clients send `Idempotency-Key` on `POST` and other non-idempotent mutating requests.
- The key must be unique per client and operation and should be 8-255 characters.
- The service stores the first completed response for the key and returns that response for equivalent retries.
- If the same key is reused with a materially different request body, return `409 Conflict` with code `IDEMPOTENCY_CONFLICT`.
- If the original request is still processing, return `409 Conflict` with code `IDEMPOTENCY_IN_PROGRESS` or the eventual stored response when available.
- Idempotency records should be retained for at least 24 hours unless a specific product contract requires longer.

## Pagination

List endpoints must be paginated. Cursor pagination is the default for transactional resources because ordering must remain stable while data changes.

### Request parameters

- `page[size]`: maximum number of items to return. Default `25`, maximum `100`.
- `page[after]`: opaque cursor for the next page.
- `page[before]`: opaque cursor for the previous page when reverse traversal is supported.

### Response shape

List responses wrap data with pagination metadata:

```json
{
  "data": [],
  "page": {
    "size": 25,
    "nextCursor": "eyJpZCI6...",
    "prevCursor": null,
    "hasMore": false
  }
}
```

- Cursors are opaque and may expire.
- Default ordering for transactional resources is descending `createdAt`, then descending `id` as a tie breaker.
- Do not expose offset-based pagination for high-volume transactional data unless the dataset is bounded and stable.

## Filtering and sorting

- Use bracketed query parameters for filters: `filter[status]=posted`.
- Use RFC 3339 timestamps for temporal filters: `filter[createdAt][gte]=2025-01-01T00:00:00Z`.
- Document allowed filters per endpoint; reject unsupported filters with `400 Bad Request` and code `INVALID_FILTER`.
- Use comma-separated sort fields in `sort`, with `-` for descending: `sort=-createdAt,id`.
- Document allowed sort fields per endpoint; reject unsupported sorts with `400 Bad Request` and code `INVALID_SORT`.
- Filters should be ANDed by default. Any OR semantics must be explicitly modeled and documented.

## Standard error envelope

Errors must use a consistent JSON envelope and include the request correlation ID.

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "The request body failed validation.",
    "correlationId": "7f4f0b1e-4b83-4f8c-9df2-8f1e2cdb6b24",
    "details": [
      {
        "field": "amount.value",
        "issue": "Must be greater than zero."
      }
    ]
  }
}
```

### Error fields

| Field | Required | Description |
| --- | --- | --- |
| `error.code` | Yes | Stable machine-readable error code in `UPPER_SNAKE_CASE`. |
| `error.message` | Yes | Human-readable summary safe to show to client developers. |
| `error.correlationId` | Yes | The resolved correlation ID for support and tracing. |
| `error.details` | No | Structured field or domain-specific diagnostics. |

### Common status codes

| HTTP status | Default code | Use |
| --- | --- | --- |
| `400` | `BAD_REQUEST` | Malformed syntax, unsupported filters, invalid parameters. |
| `401` | `UNAUTHORIZED` | Missing or invalid authentication. |
| `403` | `FORBIDDEN` | Authenticated principal lacks permission. |
| `404` | `NOT_FOUND` | Resource does not exist or is not visible to caller. |
| `409` | `CONFLICT` | State conflict, idempotency conflict, or duplicate resource. |
| `422` | `VALIDATION_FAILED` | Semantically invalid request body. |
| `429` | `RATE_LIMITED` | Rate limit exceeded. Include `Retry-After` when possible. |
| `500` | `INTERNAL_ERROR` | Unexpected server failure. |
| `503` | `SERVICE_UNAVAILABLE` | Temporary dependency or maintenance outage. |

## Versioning

- Put the major API version in the path: `/v1`.
- Make backward-compatible additions within the same major version.
- Do not remove fields, narrow enum values, or change field meanings within a major version.
- Introduce a new major version for breaking changes.
- Include deprecation guidance in documentation before removing supported versions.
- Optional preview behavior should use documented feature flags or media-type parameters, not silent response changes.

## Endpoint design checklist

Every endpoint design must document:

- Authentication and required scopes.
- Required and optional request headers, including `X-Correlation-ID`.
- Idempotency behavior for mutating operations.
- Request body schema and validation rules.
- Success response schema and status code.
- Standard error envelope responses.
- Pagination, filtering, and sorting rules for list endpoints.
- Version path and backward-compatibility expectations.
