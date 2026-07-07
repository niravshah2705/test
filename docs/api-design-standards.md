# REST API Design Standards

These standards define the conventions for public and internal REST APIs in the online hotel booking platform. They apply to routes, request and response shapes, errors, pagination, filtering, sorting, validation, dates and times, money, versioning, and idempotency.

## Guiding principles

- Prefer predictable, resource-oriented URLs over action-oriented URLs.
- Use HTTP semantics consistently for methods, status codes, caching, and idempotency.
- Keep response envelopes consistent so clients can parse success and error responses uniformly.
- Make contracts explicit: every endpoint must document parameters, request body, response body, status codes, validation rules, and examples.
- Preserve backward compatibility within a published API version.

## URL and route rules

- Prefix all versioned REST routes with `/api/v{major}`. Example: `/api/v1/hotels`.
- Use lowercase kebab-case path segments.
- Use plural nouns for collections: `/hotels`, `/bookings`, `/guests`.
- Use stable identifiers in path parameters: `/api/v1/hotels/{hotelId}`.
- Nest resources only when ownership is clear and the nesting remains shallow. Example: `/api/v1/hotels/{hotelId}/rooms`.
- Put optional search, filtering, sorting, pagination, and projection controls in query parameters, not path segments.
- Do not include verbs in paths unless modeling a true non-resource operation that cannot be represented with standard HTTP methods. Prefer `/bookings/{bookingId}/cancellations` over `/bookings/{bookingId}/cancel`.
- Use trailing slashes only for the API root; endpoint URLs must not require a trailing slash.

## HTTP method rules

| Method | Use | Request body | Idempotent |
| --- | --- | --- | --- |
| `GET` | Read a resource or collection | No | Yes |
| `POST` | Create a resource or start a non-idempotent operation | Yes | No, unless protected by an idempotency key |
| `PUT` | Replace an entire resource at a known URL | Yes | Yes |
| `PATCH` | Partially update a resource | Yes | Not assumed; document endpoint behavior |
| `DELETE` | Delete or cancel a resource | Usually no | Yes |
| `HEAD` | Retrieve metadata for a `GET` resource | No | Yes |
| `OPTIONS` | Report supported methods or CORS metadata | No | Yes |

## Status code rules

Use the most specific status code that communicates the result without relying on response-body parsing.

| Status | Meaning |
| --- | --- |
| `200 OK` | Successful read or update with a response body |
| `201 Created` | Resource created; include `Location` header when a canonical URL exists |
| `202 Accepted` | Request accepted for asynchronous processing |
| `204 No Content` | Successful operation with no response body |
| `304 Not Modified` | Conditional `GET` can reuse cached representation |
| `400 Bad Request` | Malformed syntax or unsupported parameter combination |
| `401 Unauthorized` | Authentication is missing or invalid |
| `403 Forbidden` | Authenticated caller lacks permission |
| `404 Not Found` | Resource does not exist or is intentionally hidden |
| `405 Method Not Allowed` | Method is unsupported for the route; include `Allow` header |
| `409 Conflict` | State conflict such as duplicate booking or stale version |
| `412 Precondition Failed` | Conditional header such as `If-Match` failed |
| `422 Unprocessable Entity` | Request is syntactically valid but violates field validation |
| `429 Too Many Requests` | Rate limit exceeded; include `Retry-After` when possible |
| `500 Internal Server Error` | Unexpected server error |
| `503 Service Unavailable` | Temporary outage or dependency unavailability |

## Request envelope rules

- `GET`, `HEAD`, and `DELETE` requests should not require a JSON body.
- `POST`, `PUT`, and `PATCH` requests with JSON bodies must use `Content-Type: application/json`.
- Use camelCase for all JSON field names.
- Top-level request bodies should represent the resource or command directly. Avoid unnecessary wrappers unless the endpoint combines multiple named inputs.
- Clients must send `Accept: application/json` for JSON APIs.
- Use `Idempotency-Key` on unsafe create/payment/booking operations that clients may retry.
- Use `If-Match` with entity tags for updates where lost-update protection matters.

Example create request:

```http
POST /api/v1/bookings HTTP/1.1
Content-Type: application/json
Accept: application/json
Idempotency-Key: 8b63c4a9-8c7e-4ec7-a72c-7a7a6cf3d68e

{
  "hotelId": "hot_123",
  "roomTypeId": "rt_deluxe",
  "guestId": "gst_456",
  "checkInDate": "2025-06-01",
  "checkOutDate": "2025-06-05",
  "currency": "USD"
}
```

## Response envelope rules

Successful single-resource responses must return a `data` object:

```json
{
  "data": {
    "id": "hot_123",
    "name": "Grand Harbor Hotel"
  },
  "meta": {
    "requestId": "req_01HY..."
  }
}
```

Successful collection responses must return a `data` array and `page` metadata:

```json
{
  "data": [],
  "page": {
    "limit": 20,
    "nextCursor": null,
    "hasMore": false
  },
  "meta": {
    "requestId": "req_01HY..."
  }
}
```

- `data` is required for `200` and `201` JSON responses.
- `meta.requestId` should be included in every JSON response for support correlation.
- `204 No Content` responses must not include a body.
- Include hypermedia links only when they are part of the documented endpoint contract.

## Error rules

Error responses must use a consistent JSON envelope with one top-level `error` object.

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "One or more fields failed validation.",
    "details": [
      {
        "field": "checkOutDate",
        "code": "DATE_MUST_BE_AFTER",
        "message": "checkOutDate must be after checkInDate."
      }
    ],
    "requestId": "req_01HY..."
  }
}
```

- `error.code` must be stable, uppercase snake_case, and safe for client logic.
- `error.message` must be human-readable and safe to show to an end user when appropriate.
- `error.details` is optional and should contain field-level or cause-level details.
- Never expose secrets, stack traces, SQL, dependency credentials, or internal hostnames.
- Authentication errors should distinguish only what is safe: use `401` for invalid/missing credentials and `403` for insufficient permission.

## Pagination rules

- Use cursor-based pagination for mutable collections and search endpoints.
- Query parameters:
  - `limit`: maximum items to return; default `20`; maximum `100` unless documented otherwise.
  - `cursor`: opaque cursor returned by the previous response.
- Response page metadata:
  - `limit`: effective page size.
  - `nextCursor`: cursor for the next page, or `null`.
  - `hasMore`: `true` when another page is available.
- Cursors are opaque. Clients must not parse or construct them.
- Offset pagination may be used only for admin/reporting endpoints where stable cursor ordering is not required; document `offset` and `totalCount` explicitly if used.

## Sorting rules

- Use the `sort` query parameter for ordered collections.
- Default sort must be documented per endpoint.
- Prefix descending fields with `-`; ascending fields have no prefix. Example: `sort=price.amount,-rating`.
- Support multiple sort fields only when deterministic tie-breaking is defined.
- Always append a stable final tie-breaker such as `id` server-side.
- Reject unsupported sort fields with `400 Bad Request` and code `INVALID_SORT`.

## Filtering rules

- Use explicit query parameters for common filters. Example: `city=Boston&minPrice=150&amenities=pool,wifi`.
- Use ISO 8601 dates for date filters: `checkInDate=2025-06-01`.
- Use repeated parameters or comma-separated values consistently per endpoint; document the chosen convention.
- Do not accept arbitrary SQL-like filter strings in public APIs.
- Reject unknown filters with `400 Bad Request` and code `UNKNOWN_FILTER` unless the endpoint explicitly allows forward-compatible ignored parameters.
- Validate cross-field combinations, such as `checkOutDate` after `checkInDate`.

## Date and time rules

- Use ISO 8601 / RFC 3339 strings.
- Instants must include an offset and should be normalized to UTC with `Z`. Example: `2025-06-01T15:30:00Z`.
- Calendar-only hotel stay dates must use `YYYY-MM-DD` and must be interpreted in the hotel's local timezone.
- Include timezone identifiers when business rules depend on local time. Example: `America/New_York`.
- Do not use Unix timestamps in public JSON APIs unless an endpoint explicitly documents them for performance reasons.
- Field names should make semantics clear: `createdAt` for instants, `checkInDate` for calendar dates.

## Money and currency rules

Represent money as an object, not a floating-point number.

```json
{
  "amount": "199.99",
  "currency": "USD"
}
```

- `amount` must be a decimal string using the currency's minor-unit precision.
- `currency` must be an ISO 4217 uppercase currency code.
- Do not use binary floating-point values for money in requests or responses.
- Include tax, fee, discount, and total fields separately when pricing transparency matters.
- Document whether prices are per night, per stay, before tax, after tax, refundable, or approximate.
- Reject unsupported currencies with `422 Unprocessable Entity` and code `UNSUPPORTED_CURRENCY`.

## Validation rules

- Validate request syntax, types, formats, ranges, enum values, required fields, and cross-field constraints.
- Return `400 Bad Request` for malformed JSON, invalid query syntax, or unsupported parameter combinations.
- Return `422 Unprocessable Entity` for semantically invalid fields in a syntactically valid request.
- Report all practical field validation errors in `error.details` rather than failing one field at a time.
- Use stable field paths in validation details. Example: `guests[0].email`.
- Trim leading and trailing whitespace for user-entered strings where safe; document case sensitivity for identifiers and codes.
- Enforce server-side validation even when clients also validate.

## Versioning rules

- Use URI major versioning: `/api/v1`, `/api/v2`.
- Backward-compatible additions within a major version are allowed, including new optional response fields, new optional request fields, and new enum values only when clients are documented to ignore unknown values.
- Breaking changes require a new major version. Breaking changes include removing or renaming fields, changing field types, changing required fields, changing error semantics, or changing default behavior.
- Deprecations must be announced with migration guidance and, when practical, response headers:
  - `Deprecation: true`
  - `Sunset: <RFC 1123 date>`
  - `Link: <https://docs.example.com/migrations/v2>; rel="deprecation"`
- Keep supported versions documented with their lifecycle state.

## Idempotency rules

- `GET`, `HEAD`, `PUT`, and `DELETE` must be idempotent by HTTP semantics.
- `POST` endpoints that create bookings, payments, refunds, or other externally visible side effects must accept `Idempotency-Key`.
- Idempotency keys must be unique per caller and operation intent.
- The server must store the key, request fingerprint, response status, and response body for a documented retention window, at least 24 hours for booking and payment operations.
- Reusing a key with the same request fingerprint must return the original result.
- Reusing a key with a different request fingerprint must return `409 Conflict` with code `IDEMPOTENCY_KEY_REUSED`.
- In-progress duplicate requests should return `409 Conflict` or `202 Accepted`, depending on endpoint behavior, and must be documented.

## Sample endpoint specification: hotel search

### Search hotels

Find hotels available for a stay window and optional location, occupancy, amenity, price, and rating filters.

| Field | Value |
| --- | --- |
| URL | `/api/v1/hotels/search` |
| Method | `GET` |
| Auth | Optional for public search; authenticated callers may receive personalized rates where allowed |
| Default sort | `recommended,-rating,price.amount,id` |
| Pagination | Cursor-based using `limit` and `cursor` |
| Idempotency | Not required; `GET` is idempotent |

#### Query parameters

| Name | Type | Required | Rules |
| --- | --- | --- | --- |
| `destinationId` | string | Conditional | Stable destination identifier; required when `latitude`/`longitude` are absent |
| `latitude` | decimal string | Conditional | Required with `longitude`; range `-90` to `90` |
| `longitude` | decimal string | Conditional | Required with `latitude`; range `-180` to `180` |
| `radiusKm` | decimal string | No | Allowed only with coordinates; default `10`; maximum `100` |
| `checkInDate` | date | Yes | `YYYY-MM-DD` in hotel local timezone |
| `checkOutDate` | date | Yes | Must be after `checkInDate`; maximum stay `30` nights |
| `adults` | integer | Yes | Minimum `1`; maximum `8` |
| `children` | integer | No | Default `0`; maximum `8` |
| `rooms` | integer | No | Default `1`; minimum `1`; maximum `4` |
| `currency` | string | No | ISO 4217; default determined by caller locale or destination |
| `minPrice` | decimal string | No | Requires `currency`; inclusive per-night amount before taxes and fees |
| `maxPrice` | decimal string | No | Requires `currency`; inclusive; must be greater than or equal to `minPrice` |
| `amenities` | comma-separated strings | No | Supported values include `wifi`, `pool`, `parking`, `breakfast`, `gym`, `spa` |
| `minRating` | decimal string | No | Range `0` to `5` |
| `refundableOnly` | boolean | No | `true` or `false`; default `false` |
| `sort` | string | No | Supported: `recommended`, `price.amount`, `rating`, `distanceKm`, `name`; prefix with `-` for descending |
| `limit` | integer | No | Default `20`; maximum `100` |
| `cursor` | string | No | Opaque cursor from previous response |

At least one location input is required: either `destinationId` or both `latitude` and `longitude`.

#### Example request

```http
GET /api/v1/hotels/search?destinationId=dst_boston&checkInDate=2025-06-01&checkOutDate=2025-06-05&adults=2&rooms=1&currency=USD&maxPrice=300&amenities=wifi,pool&sort=price.amount,-rating&limit=20 HTTP/1.1
Accept: application/json
```

#### Success response

Status: `200 OK`

```json
{
  "data": [
    {
      "id": "hot_123",
      "name": "Grand Harbor Hotel",
      "location": {
        "addressLine1": "100 Harbor Way",
        "city": "Boston",
        "region": "MA",
        "country": "US",
        "latitude": "42.3601",
        "longitude": "-71.0589",
        "timezone": "America/New_York"
      },
      "rating": "4.6",
      "amenities": ["wifi", "pool", "parking"],
      "lowestAvailableRate": {
        "amount": "249.00",
        "currency": "USD",
        "unit": "night",
        "taxIncluded": false,
        "feesIncluded": false
      },
      "availability": {
        "checkInDate": "2025-06-01",
        "checkOutDate": "2025-06-05",
        "roomsAvailable": 3,
        "refundable": true
      }
    }
  ],
  "page": {
    "limit": 20,
    "nextCursor": "eyJzb3J0IjpbIjI0OS4wMCIsIjQuNiIsImhvdF8xMjMiXX0=",
    "hasMore": true
  },
  "meta": {
    "requestId": "req_01HYZ7ZQ6F3J9R7K9F6C8V2H2A"
  }
}
```

#### Error responses

Missing location input:

Status: `422 Unprocessable Entity`

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "One or more fields failed validation.",
    "details": [
      {
        "field": "destinationId",
        "code": "LOCATION_REQUIRED",
        "message": "Provide destinationId or latitude and longitude."
      }
    ],
    "requestId": "req_01HYZ80C1PKX8M4G8NZ0EQK7WM"
  }
}
```

Unsupported sort field:

Status: `400 Bad Request`

```json
{
  "error": {
    "code": "INVALID_SORT",
    "message": "Unsupported sort field: popularity.",
    "requestId": "req_01HYZ83N8A2GPY1XDPKH6PK3D7"
  }
}
```

#### Endpoint-specific validation

- `checkOutDate` must be after `checkInDate`.
- Stay length must not exceed 30 nights.
- `destinationId` cannot be combined with `latitude`, `longitude`, or `radiusKm` unless the endpoint explicitly documents precedence.
- `minPrice` and `maxPrice` require `currency`.
- `maxPrice` must be greater than or equal to `minPrice`.
- `children + adults` must be at most `16`.
- Unknown amenities, filters, or sort fields must be rejected with a documented error code.
