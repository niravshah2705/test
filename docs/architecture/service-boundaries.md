# Service boundaries and domain ownership

This architecture document defines the bounded contexts for the core travel platform. Each service owns its domain data and invariants, exposes explicit inbound contracts, and collaborates with other services through APIs and domain events rather than shared writes.

## Review status

- Status: reviewed for initial service/module decomposition.
- Scope: flight catalog, availability, pricing, booking, payment, ticketing, user profile, and notification responsibilities.
- Related cross-cutting contracts: [Domain event model](domain-events.md) and [Cross-cutting idempotency model](idempotency.md).

## Boundary principles

1. A service is the system of record for its owned entities and is the only component allowed to mutate them directly.
2. Other services access owned state through published APIs, read models, or domain events.
3. Synchronous calls are reserved for request-time decisions that require a current answer; asynchronous events are preferred for propagation, audit, and follow-up workflows.
4. Customer-facing orchestration belongs to the booking service unless a narrower owner is listed below.
5. Excluded responsibilities are explicit so future work can avoid accidental domain leakage.

## Service ownership matrix

| Service/module | Owned entities | Responsibilities | Inbound APIs | Outbound dependencies | Explicitly excluded responsibilities |
| --- | --- | --- | --- | --- | --- |
| Flight catalog | Airline, airport, route, flight schedule, aircraft type, cabin class, fare brand, catalog snapshot | Maintain normalized reference data for carriers, airports, routes, scheduled flights, cabins, fare products, and catalog import freshness; publish catalog changes for search/index consumers. | `GET /catalog/airlines`; `GET /catalog/airports`; `GET /catalog/routes`; `GET /catalog/flights/{flightId}`; admin/import API for catalog feeds; subscribes to provider catalog feed updates. | Airline/global distribution system (GDS) schedule feeds; internal event publisher for `catalog.updated`; object/blob storage for imported feed snapshots. | Real-time seat availability, price calculation, booking creation, payment handling, ticket issuance, customer profile management, and user notifications. |
| Availability | Availability quote, seat inventory view, hold, inventory rule, availability cache entry | Answer real-time availability queries, normalize provider inventory responses, create/release short-lived holds, track stale-cache policy, and emit hold lifecycle events. | `POST /availability/search`; `POST /availability/holds`; `DELETE /availability/holds/{holdId}`; provider inventory webhook for schedule/seat changes. | Flight catalog for schedule/route metadata; airline/GDS inventory APIs; booking service for hold consumption; event publisher for `availability.hold_created`, `availability.hold_released`, and `availability.changed`. | Catalog reference-data ownership, final fare pricing, passenger booking records, payment authorization/capture, ticket document generation, and notification delivery. |
| Pricing | Price quote, fare rule, tax/fee breakdown, promotion application, currency conversion snapshot, refund estimate | Calculate itinerary prices, taxes, fees, discounts, repricing outcomes, and refund estimates; preserve quote audit details used by booking/payment. | `POST /pricing/quotes`; `POST /pricing/reprice`; `POST /pricing/refund-estimates`; admin API for fare-rule and promotion configuration. | Flight catalog for fare-brand/cabin metadata; availability for hold validation; tax/fee providers; foreign-exchange rate provider; promotion/loyalty rules if externalized; event publisher for `pricing.quote_created` and `pricing.quote_expired`. | Seat inventory ownership, booking lifecycle state, payment capture/refunds execution, ticket issuance, customer identity/profile storage, and outbound customer messaging. |
| Booking | Booking, booking segment, traveler assignment, booking status, cancellation request, itinerary snapshot, booking audit trail | Orchestrate customer orders from selected itinerary through confirmation/cancellation; bind travelers to held inventory and accepted price quotes; enforce booking state transitions and idempotent command handling. | `POST /bookings`; `GET /bookings/{bookingId}`; `POST /bookings/{bookingId}/confirm`; `POST /bookings/{bookingId}/cancel`; `GET /users/{userId}/bookings`; consumes availability/pricing/payment/ticketing events that affect booking state. | User profile for saved travelers/contact data; availability for hold consumption/release; pricing for quote validation/reprice; payment for authorization/capture/refund commands; ticketing for ticket issuance/void requests; notification for customer communication requests; event publisher for `booking.created`, `booking.confirmed`, `booking.cancelled`, and `booking.failed`. | Maintaining canonical flight catalog data, calculating fares/taxes, storing card/payment credentials, issuing ticket numbers/documents, managing long-lived user profile preferences, and delivering notifications. |
| Payment | Payment intent, authorization, capture, refund, payment method token reference, payment ledger entry, provider webhook record | Own payment state and money-movement commands; authorize, capture, void, and refund payments; reconcile provider webhooks; expose payment status for booking workflows; maintain idempotent provider interaction records. | `POST /payments/intents`; `POST /payments/{paymentId}/authorize`; `POST /payments/{paymentId}/capture`; `POST /payments/{paymentId}/refunds`; `GET /payments/{paymentId}`; `POST /webhooks/payments/{provider}`. | Booking for payable order context; pricing for amount/currency validation; payment service providers/acquirers; fraud/risk provider when enabled; event publisher for `payment.authorized`, `payment.captured`, `payment.failed`, and `payment.refunded`. | Creating bookings, determining itinerary availability, calculating non-payment prices/taxes, issuing tickets, owning customer profile records beyond token references, and sending customer notifications directly. |
| Ticketing | Ticket order, e-ticket, ticket coupon, passenger name record (PNR) reference, exchange/void request, ticketing provider response | Issue, void, exchange, and retrieve travel documents after booking/payment eligibility; map provider PNR/ticket state to internal ticketing records; emit ticket lifecycle events. | `POST /ticketing/orders`; `GET /ticketing/orders/{ticketOrderId}`; `POST /ticketing/tickets/{ticketId}/void`; `POST /ticketing/tickets/{ticketId}/exchange`; provider ticketing webhooks. | Booking for confirmed itinerary/traveler data; payment for capture confirmation/refund eligibility; airline/GDS ticketing APIs; notification for ticket document delivery requests; event publisher for `ticketing.issued`, `ticketing.failed`, `ticketing.voided`, and `ticketing.exchanged`. | Searching catalog/availability, fare calculation, booking orchestration before confirmation, payment execution, canonical user profile storage, and general marketing or operational notifications. |
| User profile | User account, traveler profile, contact method, document/passport record, preference, consent record, loyalty account reference | Own customer identity-adjacent profile data, saved travelers, contact preferences, travel documents, consent, and loyalty references; provide profile snapshots to booking and notification workflows. | `GET /users/{userId}`; `PATCH /users/{userId}`; `GET /users/{userId}/travelers`; `POST /users/{userId}/travelers`; `PATCH /travelers/{travelerId}`; `GET /users/{userId}/preferences`; profile update events from identity/auth systems. | Authentication/identity provider for account identity; booking for booking-history read models only; notification for contact/preference synchronization; secure document storage/KMS; event publisher for `profile.updated` and `traveler.updated`. | Authentication credential verification, flight catalog ownership, availability/pricing decisions, booking lifecycle orchestration, payment processing, ticket issuance, and sending notifications. |
| Notification | Notification request, template, channel preference snapshot, delivery attempt, suppression record, notification event log | Render and deliver transactional notifications across email/SMS/push/webhook channels; enforce channel preferences and suppression rules; track delivery attempts and failures. | `POST /notifications`; `GET /notifications/{notificationId}`; `POST /webhooks/notifications/{provider}`; subscribes to booking, payment, ticketing, provider-status, and profile events requiring customer/operator messaging. | User profile for current contact methods/preferences; booking/payment/ticketing event streams for trigger context; email/SMS/push providers; template/content store; event publisher for `notification.requested`, `notification.sent`, and `notification.failed`. | Owning booking/payment/ticketing state, making business decisions about whether a booking is valid, calculating prices, storing canonical customer profile data, and changing upstream domain outcomes based solely on delivery status. |

## Collaboration notes

### Booking creation

1. The client searches availability and receives an availability hold identifier.
2. The client requests a pricing quote for the held itinerary.
3. Booking creates a booking using the hold, quote, and traveler snapshot from user profile.
4. Booking asks payment to authorize/capture the accepted amount.
5. After payment success, booking requests ticketing issuance.
6. Ticketing emits issuance status; booking records the resulting state and asks notification to send customer updates.

### Cancellation and refund

1. Booking owns the customer cancellation request and validates booking state.
2. Pricing provides a refund estimate and applicable penalty details.
3. Ticketing voids or exchanges travel documents when required.
4. Payment executes refunds after booking/ticketing eligibility is confirmed.
5. Notification sends outcome messages using profile contact preferences.

### Data access and reporting

- Operational reports should use read models populated from domain events rather than cross-service table reads.
- Services may cache external provider responses only for their own decisions and must publish events when cached state affects downstream workflows.
- Shared libraries may contain cross-cutting primitives such as idempotency keys and event envelopes, but not domain entity ownership or write logic.
