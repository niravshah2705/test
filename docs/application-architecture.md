# Application architecture

This repository uses a feature-oriented TypeScript application layout for the Hotel Booking Workflow.
The goal is to keep routes, API handlers, domain services, repositories, DTOs, schemas, and UI components
small and replaceable without leaking persistence details into client code.

## Routes

Public pages live in `app/`:

- `app/page.tsx` for home
- `app/search/page.tsx` for search
- `app/hotels/[hotelId]/page.tsx` for hotel detail
- `app/booking/page.tsx` for the primary booking flow
- `app/bookings/page.tsx` for booking collection and confirmation flows
- `app/account/page.tsx` for account flows

Protected administration pages live under the route group `app/(admin)/admin/`. The admin layout calls
`requireAdmin()` before rendering child routes. Add new admin pages under this group so authorization stays
centralized.

## API handlers

API route placeholders live under `app/api/<operation>/route.ts` for search, hotels, availability,
reservations, payments, account, and admin operations. Add handler logic by parsing request input, calling a
public domain service, and mapping the service result to an HTTP response DTO. Handlers should not import
repositories directly.

## Domain modules

Domain modules live under `src/domain/<feature>/` with this shape:

- `index.ts` exposes the public module surface.
- `service.ts` contains application use cases and public service interfaces.
- `repository.ts` contains persistence adapters and stays internal to the module.
- `schemas.ts` contains validation/parsing helpers for module inputs.
- `dto.ts` contains transport shapes returned to routes and APIs.
- `types.ts` contains domain types.

`src/domain/hotel` is the sample module. Its `index.ts` exports service, DTO, schema, and type contracts, but
it intentionally does not export `repository.ts`. New modules should follow that pattern so callers depend on
services rather than storage details.

## Shared libraries

Shared code lives under `src/lib/`:

- `db` for server-only database access
- `auth` for session and authorization helpers
- `validation` for shared validation primitives
- `errors` for application error types
- `date` for date handling
- `money` for currency and amount helpers
- `request` for request/response utilities

`src/lib/db/index.ts` imports `server-only`, which makes accidental client imports fail in a Next.js runtime.
Repositories may import `@/lib/db`; client components and shared UI must not.

## Import aliases

`tsconfig.json` defines aliases:

- `@/*` -> `src/*`
- `@/app/*` -> `app/*`
- `@/domain/*` -> `src/domain/*`
- `@/lib/*` -> `src/lib/*`
- `@/ui/*` -> `src/ui/*`

Prefer aliases over long relative paths across architectural boundaries.

## Adding new functionality

1. Add or update a route in `app/` or an API handler in `app/api/`.
2. Add a domain service method in the relevant `src/domain/<feature>/service.ts`.
3. Keep persistence in `repository.ts`; do not export repositories from `index.ts`.
4. Add validation in `schemas.ts` and external response/request shapes in `dto.ts`.
5. Put reusable client-safe components in `src/ui/`.
6. Use shared helpers from `src/lib/` instead of duplicating cross-cutting concerns.
7. Confirm client components do not import `@/lib/db`, repositories, or ORM clients.

## Privacy and security conventions

User-owned resources must be scoped by the authenticated owner at the repository or service boundary before any DTO is built. Passenger profiles, contact details, documents, reservations, booking drafts, payment actions, and account/session reads should reject cross-user identifiers with a forbidden/not-found response that does not reveal another user's data.

Use DTO mappers for every response. Public and traveler-facing payloads must exclude password hashes, raw payment tokens, provider-native references/payloads, physical room IDs, confirmation secrets, and full identity document numbers. Document storage and validation accepts only tokenized/provider-safe values plus `documentNumberLast4`; raw full document numbers and raw card fields must be rejected without echoing their values.

Audit and log metadata must pass through the shared sanitizer before persistence or emission. Add new sensitive key aliases to the sanitizer when introducing providers or payment/document fields, and prefer allowlisted status/reason fields over raw request, provider error, or provider payload blobs.

Sensitive actions such as sign-in, payment finalization, payment webhooks, booking confirmation polling, saved passenger profile access, cancellation, refunds, and admin reservation search must call an application-level abuse guard/rate guard interface before the irreversible action. The guard should be injected at the service/handler boundary so this repository can test enforcement without provisioning infrastructure.

Password handling must keep hashes server-side only: registration and login responses use safe identity DTOs, tests assert stored passwords are hashed, and current-user/session helpers must never serialize `password_hash`. Payment handling must require tokenized provider references, idempotency keys, ownership checks against the booking/draft, and amount/currency matching before capture or confirmation.
