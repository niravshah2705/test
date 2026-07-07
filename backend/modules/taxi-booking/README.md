# Taxi booking module

## Boundary
Owns ground transport quote search, taxi reservation, trip lifecycle, cancellation, and taxi booking status.

## Responsibilities
- Request normalized taxi quotes from provider adapters.
- Coordinate payment authorization/capture for taxi reservations.
- Create, cancel, and retrieve taxi bookings.
- Request notifications and record audit events.

## Does not own
- Flight itineraries or airport metadata beyond shared pickup/dropoff inputs.
- Payment processor DTOs or taxi provider DTOs.
- Traveler profile persistence.

## Allowed dependencies
- `identity`
- `traveler-profiles`
- `payments`
- `provider-adapters`
- `notifications`
- `audit-events`

## Public interface
See `service-interface.md`.
