# Notifications module

## Boundary
Owns notification request orchestration, template selection, delivery status, and notification provider dispatch.

## Responsibilities
- Accept provider-neutral notification requests.
- Render template data into outbound notification messages.
- Dispatch through provider adapter interfaces.
- Track delivery status and emit audit events.

## Does not own
- Booking or payment domain state.
- SMS/email/push vendor DTOs.
- User authentication.

## Allowed dependencies
- `provider-adapters`
- `audit-events`

## Public interface
See `service-interface.md`.
