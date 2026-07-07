# Identity module

## Boundary
Owns authentication, authorization context, user account identity, session lifecycle, and identity claims used by other backend modules.

## Responsibilities
- Verify credentials and federated login assertions.
- Issue and validate application sessions/tokens.
- Resolve the current actor and authorization context.
- Publish identity audit events through `AuditEventService`.

## Does not own
- Traveler profile preferences or travel documents.
- Payment instruments or billing identity beyond stable user references.
- Provider-specific identity payloads.

## Allowed dependencies
- `audit-events`

## Public interface
See `service-interface.md`.
