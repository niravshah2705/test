# Payments module

## Boundary
Owns payment intent lifecycle, authorization, capture, refunds, and normalized payment status.

## Responsibilities
- Create payment intents for booking workflows.
- Authorize, capture, void, and refund payments through payment provider adapters.
- Store payment status and provider references behind provider-neutral contracts.
- Emit audit events for payment state transitions.

## Does not own
- Flight or taxi booking state machines.
- Payment processor DTOs or webhook raw payloads outside adapter mapping.
- Notification delivery.

## Allowed dependencies
- `identity`
- `provider-adapters`
- `audit-events`

## Public interface
See `service-interface.md`.
