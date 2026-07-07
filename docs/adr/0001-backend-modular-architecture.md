# ADR 0001: Backend modular architecture

## Status
Accepted

## Context
The backend needs explicit module boundaries for identity, traveler profiles, flight search, flight booking, taxi booking, payments, notifications, audit/events, and provider adapters. Without documented boundaries and service interfaces, product workflows can couple directly to provider DTOs or form circular dependencies between booking, payment, notification, and audit concerns.

## Decision
Organize the backend by product capability modules under `backend/modules` and shared provider-neutral contracts under `backend/shared`.

Each module must:

- Document its boundary, responsibilities, non-responsibilities, and allowed dependencies in a module `README.md`.
- Expose a stable service interface in `service-interface.md`.
- Depend on other modules only through documented service interfaces.
- Keep provider DTOs isolated to `backend/modules/provider-adapters/dtos.md`.

The dependency graph is captured in `backend/module-dependencies.json` and validated by `scripts/validate_backend_architecture.py` to prevent circular dependencies and missing module artifacts.

## Consequences

- Product modules can evolve independently while keeping integration seams visible.
- Provider changes are localized to provider adapters and mappers.
- Cross-module workflows require an application composition layer instead of hidden bidirectional imports.
- The architecture has an executable validation check even before concrete backend code exists.

## Alternatives considered

1. **Layer-only architecture (`controllers/services/repositories`)**: rejected because it obscures product ownership and makes provider DTO leakage easier.
2. **Provider-first architecture**: rejected because user-facing travel workflows should not depend on external vendor schemas.
3. **Single shared service module**: rejected because it encourages ambiguous ownership and circular dependencies.
