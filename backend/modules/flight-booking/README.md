# Flight booking module

## Boundary
Owns flight booking orchestration from priced offer through reservation, ticketing, cancellation, and booking status.

## Responsibilities
- Convert priced offers and traveler profiles into booking requests.
- Coordinate payment authorization/capture through `PaymentService`.
- Reserve, ticket, cancel, and retrieve bookings through provider adapter interfaces.
- Request notifications and record audit events.

## Does not own
- Flight shopping/search result generation.
- Traveler profile storage.
- Payment processor DTOs or airline/GDS DTOs.

## Allowed dependencies
- `identity`
- `traveler-profiles`
- `flight-search`
- `payments`
- `provider-adapters`
- `notifications`
- `audit-events`

## Public interface
See `service-interface.md`.
