# Audit/events module

## Boundary
Owns append-only domain event and audit event recording for compliance, observability, and integration publication.

## Responsibilities
- Record immutable audit events with actor, subject, action, and metadata.
- Publish domain events to downstream integration/event buses.
- Provide query APIs for audit trails.

## Does not own
- Product module state machines.
- Synchronous callbacks into product modules.
- Provider DTO persistence except opaque metadata references when required.

## Allowed dependencies
None.

## Public interface
See `service-interface.md`.
