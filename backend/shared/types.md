# Shared backend types

Shared types are stable, provider-neutral contracts used between backend modules. They must not include wire shapes copied from airlines, GDSs, taxi vendors, payment processors, or notification vendors.

## Core identifiers

```ts
type UserId = string;
type TravelerProfileId = string;
type FlightSearchId = string;
type FlightOfferId = string;
type FlightBookingId = string;
type TaxiQuoteId = string;
type TaxiBookingId = string;
type PaymentIntentId = string;
type NotificationId = string;
type AuditEventId = string;
```

## Shared value objects

```ts
interface Money {
  amountMinor: number;
  currency: string;
}

interface DateRange {
  startsAt: string;
  endsAt: string;
}

interface ContactPoint {
  email?: string;
  phoneNumber?: string;
}

interface PostalAddress {
  line1: string;
  line2?: string;
  city: string;
  region?: string;
  postalCode?: string;
  countryCode: string;
}

interface GeoPoint {
  latitude: number;
  longitude: number;
}
```

## Provider DTO isolation rule

Provider DTOs live only in `backend/modules/provider-adapters/dtos.md`. Product modules exchange normalized shared types and module-specific request/response contracts, never provider payloads.
