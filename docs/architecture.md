# Online Hotel Booking Application Architecture

## Purpose and scope

This document defines the initial application architecture for the online hotel booking platform. It establishes module boundaries, shared domain terminology, and the key request flows that coordinate search, availability, booking, payment, and cancellation behavior across the system.

The architecture is organized around clear ownership boundaries so each module can evolve independently while sharing stable contracts and domain events.

## Architectural overview

The application is split into the following major modules:

| Module | Primary responsibility | Owns | Depends on |
| --- | --- | --- | --- |
| Frontend | Customer, hotel staff, and internal operator user experiences | UI state, client-side routing, form validation, API orchestration | Backend API contracts |
| Backend API | Authenticated HTTP/GraphQL entry point and request orchestration | Public API surface, request validation, authorization checks, response shaping | Domain modules, persistence, external integrations |
| Persistence | Durable storage and data-access abstractions | Schema migrations, repositories, transactions, consistency constraints | Database and storage engines |
| Search | Hotel discovery and ranking | Search index projections, filters, facets, query ranking | Hotel-management content, inventory/rate projections |
| Hotel-management | Hotel, property, room, rate, and inventory administration | Hotel profile data, room types, rate plans, inventory controls | Persistence, notification events |
| Account | Customer and staff identity, profiles, roles, and preferences | User accounts, authentication metadata, authorization roles | Persistence, notification |
| Booking | Reservation lifecycle and booking policy enforcement | Booking records, guest details, stay dates, booking status transitions | Inventory, rates, payment, notification, persistence |
| Payment | Payment authorization, capture, refund, and reconciliation | Payment intents, provider references, payment status, idempotency keys | External payment providers, booking, persistence |
| Notification | User and operator messaging | Email/SMS/push templates, delivery jobs, notification preferences | Account, booking, payment events |

## Module boundaries

### Frontend module

The frontend module presents the customer booking journey and hotel/operator management surfaces. It should not contain business-critical pricing, inventory, booking, or payment rules; those rules belong to backend domain modules.

Responsibilities:

- Render search, hotel details, availability, checkout, account, booking management, and hotel-management interfaces.
- Collect user input and perform client-side validation for usability.
- Call backend APIs using stable request/response contracts.
- Display backend-sourced booking, payment, cancellation, and notification states.
- Preserve idempotency keys for user actions that may be retried, such as booking creation and payment confirmation.

Non-responsibilities:

- Authoritative rate calculation.
- Authoritative inventory reservation.
- Payment state decisions.
- Booking status transitions beyond presenting backend results.

### Backend API module

The backend API module is the boundary between clients and domain capabilities. It validates requests, authenticates callers, authorizes actions, and coordinates domain modules.

Responsibilities:

- Expose versioned APIs for search, availability, booking, payment, cancellation, account, and hotel-management operations.
- Enforce authentication and authorization before domain operations.
- Normalize validation errors and domain failures into client-safe responses.
- Coordinate transactions and idempotent command handling.
- Publish domain events for asynchronous workflows such as notifications and search-index updates.

Non-responsibilities:

- Direct SQL/schema knowledge outside persistence abstractions.
- Direct payment-provider-specific behavior outside the payment module.
- UI presentation behavior.

### Persistence module

The persistence module owns durable storage structure and repository interfaces. Domain modules use persistence through explicit repositories or unit-of-work abstractions instead of reaching directly into tables owned by other modules.

Responsibilities:

- Define migrations and schema constraints for hotels, rooms, rates, inventory, bookings, payments, accounts, notifications, and outbox events.
- Provide repository APIs and transactional boundaries.
- Enforce uniqueness, foreign key, and concurrency constraints needed for correctness.
- Support idempotency records for commands such as booking creation, payment confirmation, and cancellation.
- Maintain an outbox for reliable domain-event publication.

Key consistency rules:

- Inventory allocation and booking creation must share an atomic transaction or compensating reservation protocol.
- Payment status updates must be idempotent by provider event identifier and platform idempotency key.
- Search indexes are projections and must not be the source of truth for booking or inventory decisions.

### Search module

The search module helps users discover hotels that match destination, date, occupancy, price, amenity, and policy constraints. It uses projections built from hotel-management and inventory/rate data.

Responsibilities:

- Maintain searchable hotel and room/rate summaries.
- Execute filtered and ranked hotel queries.
- Return candidate hotels, indicative prices, and availability summaries.
- Distinguish approximate search results from authoritative availability checks.

Non-responsibilities:

- Final inventory allocation.
- Booking creation.
- Payment processing.

### Hotel-management module

The hotel-management module is the operational source of truth for property content and sellable supply configuration.

Responsibilities:

- Manage hotel profiles, addresses, amenities, images, policies, and operational status.
- Manage room types, room attributes, occupancy limits, and physical room counts.
- Manage rate plans, cancellation policies, taxes/fees configuration, and distribution rules.
- Manage inventory calendars, stop-sell rules, minimum/maximum stay rules, and allotments.
- Emit events when content, rates, or inventory change so search projections and booking constraints remain current.

Non-responsibilities:

- Customer booking lifecycle after inventory is allocated.
- Payment provider interactions.
- Customer notification delivery.

### Account module

The account module owns identity and authorization-related data for customers, hotel staff, and internal operators.

Responsibilities:

- Authenticate users and service clients.
- Manage customer profiles, saved guests, preferences, and contact information.
- Manage staff/operator roles and hotel access grants.
- Provide authorization claims to the backend API.
- Store notification preferences used by the notification module.

Non-responsibilities:

- Booking ownership decisions beyond identity/role data.
- Hotel operational data.
- Payment instrument vaulting unless delegated by the payment module/provider.

### Booking module

The booking module owns the reservation lifecycle from quote acceptance through confirmation, modification where supported, cancellation, and post-stay completion.

Responsibilities:

- Validate booking requests against authoritative hotel, room, rate, policy, occupancy, and inventory data.
- Create booking records with guest, stay, pricing snapshot, policy snapshot, and status.
- Reserve or consume inventory using transaction-safe mechanisms.
- Track booking statuses such as `pending_payment`, `confirmed`, `cancelled`, `expired`, and `completed`.
- Coordinate with payment for payment-required bookings.
- Trigger notification events for booking confirmation, cancellation, and payment failures.

Non-responsibilities:

- Payment provider capture/refund mechanics.
- Hotel content administration.
- Search ranking.

### Payment module

The payment module owns platform payment state and provider integration boundaries.

Responsibilities:

- Create payment intents for bookings that require payment.
- Authorize, capture, void, and refund payments through payment providers.
- Store provider references, statuses, amounts, currencies, and idempotency keys.
- Process provider webhooks idempotently.
- Notify booking when payment confirmation, failure, refund, or chargeback events occur.
- Reconcile asynchronous provider state with internal payment records.

Non-responsibilities:

- Booking policy decisions, except exposing payment capability and status.
- Storing raw card data when delegated to compliant provider tokenization.
- Customer-facing notification delivery.

### Notification module

The notification module owns message generation and delivery for customer, staff, and operator events.

Responsibilities:

- Subscribe to booking, payment, account, and hotel-management events.
- Render templates for booking confirmations, payment receipts, cancellations, refund notices, and operational alerts.
- Apply user preferences and delivery-channel rules.
- Queue, retry, and record delivery attempts.
- Expose notification history where appropriate.

Non-responsibilities:

- Deciding booking/payment status.
- Mutating hotel inventory or rates.
- Authenticating users.

## Shared terminology

| Term | Definition | Source of truth |
| --- | --- | --- |
| Hotel | A sellable property that has a name, address, amenities, policies, media, operational status, and one or more room types. | Hotel-management |
| Room | A physical room or a room type/category offered by a hotel. Customer search and booking usually reference room types; hotel operations may track physical rooms for allocation. | Hotel-management |
| Rate | The priced commercial offer for a room over one or more stay dates, including currency, base price, taxes/fees rules, occupancy rules, cancellation policy, meal plan, and restrictions. | Hotel-management / Booking pricing snapshot |
| Inventory | The quantity of rooms or room-type allotments available for sale for each hotel, room type, and date, after accounting for stop-sell rules and consumed bookings. | Hotel-management / Booking allocation |
| Booking | A reservation record for a guest stay at a hotel, including selected room/rate, dates, guests, pricing snapshot, policies, status, and payment requirements. | Booking |
| Payment | The platform record of money movement associated with a booking, including payment intent, authorization, capture, refund, provider reference, amount, currency, and status. | Payment |

## Canonical data ownership

- Hotel-management owns mutable hotel content, room configuration, rate plans, and inventory controls.
- Booking stores immutable snapshots of selected hotel, room, rate, taxes/fees, and cancellation terms at booking time to protect confirmed reservations from later content or price changes.
- Payment stores payment records tied to booking identifiers but does not own the booking lifecycle.
- Search stores denormalized projections optimized for discovery; stale search results must be reconciled through authoritative availability checks before booking.
- Account owns identity, contact, role, and preference data. Booking may snapshot guest/contact details needed to fulfill a reservation.
- Notification stores delivery records and rendered message metadata, not authoritative business state.

## Key request flows

### 1. Search flow

Goal: return hotels matching user criteria with indicative pricing and availability.

1. Frontend sends search criteria to Backend API: destination, dates, occupancy, filters, sort, and pagination.
2. Backend API validates input and forwards the query to Search.
3. Search queries its hotel/rate/inventory projection and ranks matching hotels.
4. Search returns candidate hotels with indicative availability, lowest eligible rate, amenities, policy highlights, and projection freshness metadata.
5. Backend API shapes the response and returns results to Frontend.
6. Frontend renders results and clearly treats prices/availability as subject to confirmation until availability check.

Boundary notes:

- Search may use cached/projected data for performance.
- Booking must not rely on search results as proof of availability.
- Hotel-management and booking/inventory events update search projections asynchronously.

### 2. Availability check flow

Goal: confirm authoritative room/rate availability before checkout or booking creation.

1. Frontend requests availability for a selected hotel, stay dates, occupancy, and optional room/rate filters.
2. Backend API authenticates optional user context, validates dates/occupancy, and calls Booking or a dedicated availability service backed by hotel-management inventory/rate data.
3. Availability logic reads authoritative room configuration, rate rules, inventory calendars, existing consumed allocations, and restrictions.
4. Availability returns eligible room/rate options with final price quote, cancellation policy, taxes/fees breakdown, payment requirements, and quote expiration.
5. Backend API returns the availability response to Frontend.
6. Frontend presents checkout options and includes the quote identifier or pricing snapshot reference in booking creation.

Boundary notes:

- Availability is authoritative for the moment it is calculated but not a permanent hold unless explicitly modeled.
- If a quote expires, booking creation must revalidate availability and price.
- Search projections are not used for final availability decisions.

### 3. Booking creation flow

Goal: create a reservation while preventing oversell and preserving a booking-time commercial snapshot.

1. Frontend submits booking details to Backend API with selected hotel, room/rate, stay dates, guests, contact details, accepted policies, quote reference, and idempotency key.
2. Backend API authenticates the customer when required, validates request structure, and forwards a booking command to Booking.
3. Booking verifies the idempotency key to prevent duplicate reservations from retries.
4. Booking revalidates room/rate eligibility, price, policy acceptance, and inventory using authoritative sources.
5. Booking starts a transaction through Persistence.
6. Booking allocates inventory or records a temporary hold according to inventory policy.
7. Booking creates a booking record with status:
   - `confirmed` when no immediate payment is required, or
   - `pending_payment` when payment must be completed before confirmation.
8. If payment is required, Booking requests Payment to create a payment intent for the booking amount and currency.
9. Persistence commits the booking, inventory allocation/hold, payment-intent reference if present, idempotency record, and outbox events.
10. Backend API returns booking details, status, payment instructions when applicable, and next actions to Frontend.
11. Notification sends confirmation or pending-payment messaging based on emitted events.

Boundary notes:

- Booking owns booking status transitions; Payment owns payment status transitions.
- Inventory allocation must be concurrency-safe.
- Pricing and policies are snapshotted on the booking so later rate changes do not alter the customer agreement.

### 4. Payment confirmation flow

Goal: reflect successful payment in booking state and notify the customer reliably.

1. Frontend completes payment through the provider experience using the payment intent returned during booking creation.
2. Payment provider sends a webhook or confirmation callback to Backend API/Payment.
3. Payment validates provider signature, normalizes the event, and checks provider event idempotency.
4. Payment updates the internal payment record to an authorized/captured/confirmed status according to product policy.
5. Payment emits a `payment_confirmed` domain event with booking identifier, amount, currency, and provider reference.
6. Booking consumes the event and transitions the related booking from `pending_payment` to `confirmed` if the amount, currency, booking status, and payment requirements match.
7. Booking finalizes any temporary inventory hold into consumed inventory if not already consumed.
8. Notification sends booking confirmation and payment receipt messages.
9. Frontend obtains updated booking status through polling, push, or booking detail refresh.

Boundary notes:

- Provider webhooks must be idempotent because providers may retry events.
- Booking should reject payment-confirmation events that do not match expected booking amount/currency.
- Payment failures or expired payment intents should release temporary holds according to booking policy.

### 5. Cancellation flow

Goal: cancel an eligible booking, update inventory/payment records, and notify stakeholders.

1. Frontend or hotel staff requests cancellation through Backend API with booking identifier, caller identity, cancellation reason, and idempotency key.
2. Backend API authenticates and authorizes the caller using Account roles and booking ownership/hotel access rules.
3. Booking loads the booking, policy snapshot, status, stay dates, and payment summary.
4. Booking determines cancellation eligibility, penalty, refundability, and whether hotel/operator approval is required.
5. Booking records the cancellation decision idempotently and transitions the booking to `cancelled` when allowed.
6. Booking releases allocated inventory back to the hotel/room/date inventory pool when policy permits resale.
7. If a refund, void, or cancellation fee capture is required, Booking commands Payment with the calculated amount and reason.
8. Payment processes the refund/void/capture through the provider and records provider status asynchronously if needed.
9. Booking and Payment publish cancellation/refund events through the outbox.
10. Notification sends cancellation confirmation, refund status, and hotel operational alerts.
11. Backend API returns the cancellation result and any pending refund status to Frontend.

Boundary notes:

- Cancellation uses the policy snapshot stored on the booking, not the hotel's current policy.
- Payment refunds may complete asynchronously after the booking is already cancelled.
- Inventory release must be idempotent to avoid adding supply more than once.

## Cross-cutting concerns

### API contracts and idempotency

Commands that create or change business state must require idempotency keys, including booking creation, payment confirmation handling, cancellation, refunds, and inventory administration actions that may be retried. Idempotency records should include caller, command type, request hash, result reference, and expiration.

### Authorization

- Customers may view and manage their own accounts and bookings.
- Hotel staff may manage hotels and bookings only for hotels they are granted access to.
- Operators may perform platform-level support actions according to role.
- Service-to-service calls must use scoped credentials and should not bypass domain authorization rules unless explicitly defined.

### Events and eventual consistency

Domain modules publish durable outbox events after committed state changes. Consumers such as Search and Notification process events asynchronously. User-facing flows that require correctness, such as checkout and cancellation, must read authoritative domain state instead of eventual projections.

### Observability

Each request should carry a correlation identifier across frontend, backend, domain modules, provider calls, persistence operations, outbox events, and notifications. Key metrics include search latency, availability latency, booking conversion, inventory contention, payment success/failure, cancellation rate, refund latency, and notification delivery success.

### Failure handling

- Search projection failures should degrade discovery but not corrupt authoritative booking data.
- Booking creation failures after payment-intent creation must be reconciled by idempotent retry or payment-intent cancellation.
- Payment webhook failures must be retried safely using provider event ids.
- Notification failures must not roll back confirmed bookings or payments.
- Inventory allocation failures must return a clear sold-out or stale-quote response.

## Validation checklist

Use this checklist when reviewing future architecture changes:

- [x] Major modules are identified: frontend, backend, persistence, payment, booking, search, account, notification, and hotel-management.
- [x] Hotel, room, rate, inventory, booking, and payment terminology is defined.
- [x] Search flow is documented.
- [x] Availability check flow is documented.
- [x] Booking creation flow is documented.
- [x] Payment confirmation flow is documented.
- [x] Cancellation flow is documented.
