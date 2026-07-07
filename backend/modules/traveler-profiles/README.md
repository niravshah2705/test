# Traveler profiles module

## Boundary
Owns traveler profile lifecycle, personal travel preferences, loyalty programs, saved documents, and contact/address data needed for bookings.

## Responsibilities
- Create, update, and read traveler profiles for an authenticated user.
- Validate traveler data before booking workflows use it.
- Manage loyalty and travel document metadata.
- Emit audit events for profile changes.

## Does not own
- Authentication or permissions beyond consuming `AuthContext`.
- Flight/taxi booking state.
- Provider passenger DTOs.

## Allowed dependencies
- `identity`
- `audit-events`

## Public interface
See `service-interface.md`.
