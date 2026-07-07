# Flight search module

## Boundary
Owns flight availability search, fare shopping, normalized itinerary results, and pricing snapshot management.

## Responsibilities
- Accept flight search criteria from application APIs.
- Query flight provider adapters through provider-neutral interfaces.
- Normalize provider results into flight search contracts.
- Persist pricing snapshots for booking handoff.

## Does not own
- Ticketing, cancellation, or booking lifecycle.
- Provider flight DTOs or direct provider SDK shapes.
- Payment authorization.

## Allowed dependencies
- `provider-adapters`
- `audit-events`

## Public interface
See `service-interface.md`.
