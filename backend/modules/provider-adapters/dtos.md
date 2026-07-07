# Provider DTO catalog

Provider DTOs describe external wire payloads and SDK response shapes. They are intentionally isolated in the provider adapters module and must not cross into product module service interfaces.

## Flight provider DTO examples

```ts
interface GdsFlightSearchResponseDto {
  rawProvider: 'gds';
  pricedItineraries: unknown[];
  fareRules: unknown[];
}

interface AirlineReservationRequestDto {
  carrierSpecificPassengerPayload: unknown;
  paymentTokenReference: string;
}
```

## Taxi provider DTO examples

```ts
interface TaxiVendorQuoteDto {
  vendorQuoteId: string;
  vehiclePayload: unknown;
  pricingPayload: unknown;
}
```

## Payment provider DTO examples

```ts
interface PaymentProcessorIntentDto {
  processorIntentId: string;
  processorStatus: string;
  rawRiskDecision?: unknown;
}
```

## Notification provider DTO examples

```ts
interface NotificationVendorMessageDto {
  vendorMessageId: string;
  transportStatus: string;
  rawDeliveryPayload?: unknown;
}
```

## Mapping requirement

Adapters must map these DTOs into provider-neutral contracts before returning values to `flight-search`, `flight-booking`, `taxi-booking`, `payments`, or `notifications`.
