# Backend modular architecture

The backend is organized around product capabilities rather than transport controllers or provider implementations. Each module owns its domain model, application service interface, and inbound/outbound contracts. Runtime composition should wire implementations together through the service interfaces defined here.

## Module inventory

| Module | Boundary | Public service interface |
| --- | --- | --- |
| `identity` | Authentication, authorization context, and user account identity. | `IdentityService` |
| `traveler-profiles` | Traveler profile lifecycle, preferences, loyalty details, and documents. | `TravelerProfileService` |
| `flight-search` | Flight availability, fare search, pricing snapshots, and search result normalization. | `FlightSearchService` |
| `flight-booking` | Flight offer reservation, ticketing workflow, cancellation, and booking status. | `FlightBookingService` |
| `taxi-booking` | Ground transport quotes, reservations, driver/trip lifecycle, and taxi booking status. | `TaxiBookingService` |
| `payments` | Payment intent orchestration, capture, refunds, and payment status. | `PaymentService` |
| `notifications` | Email/SMS/push notification requests and delivery status. | `NotificationService` |
| `audit-events` | Append-only domain audit/event recording and event publication. | `AuditEventService` |
| `provider-adapters` | External provider clients and DTO mapping for airlines, GDS, taxis, payment processors, and notification vendors. | `ProviderAdapterRegistry` |

## Boundary rules

1. Modules expose behavior through `service-interface.md`; consumers depend on those interfaces, not implementation details.
2. Domain-facing contracts use shared backend types from `backend/shared/types.md` plus module-owned types. Provider DTOs stay under `provider-adapters/dtos.md` and are mapped before crossing module boundaries.
3. `provider-adapters` may depend on shared types and provider DTOs; product modules may depend on provider adapter interfaces but must not import provider DTOs.
4. `audit-events` and `notifications` are side-effect modules invoked through their service interfaces; they must not call product modules back synchronously.
5. Dependencies must follow `module-dependencies.json`; bidirectional module imports are not allowed.
6. Cross-module workflows are coordinated by an application layer/composition root, not by hiding direct cycles inside modules.

## Validation

Run the architecture validation script after changing module boundaries:

```sh
python3 scripts/validate_backend_architecture.py
```
