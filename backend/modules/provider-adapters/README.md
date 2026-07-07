# Provider adapters module

## Boundary
Owns external provider integration clients, provider DTOs, credentials/configuration references, and mapping between provider payloads and backend provider-neutral contracts.

## Responsibilities
- Encapsulate airline/GDS, taxi, payment processor, and notification vendor SDK/API usage.
- Translate provider DTOs into shared or module-owned normalized contracts before returning to product modules.
- Provide stable adapter interfaces that product modules can consume.
- Keep provider-specific error handling and retry semantics behind adapter boundaries.

## Does not own
- Product workflows or domain state machines.
- Traveler profile, booking, payment, or notification persistence decisions.
- Shared backend domain contracts.

## Allowed dependencies
None outside shared provider-neutral types.

## Public interface
See `service-interface.md`.

## Provider DTOs
Provider DTOs are documented in `dtos.md` and must not be imported by product modules.
