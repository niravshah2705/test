# ProviderAdapterRegistry

```ts
interface ProviderAdapterRegistry {
  flights: FlightProviderAdapter;
  taxis: TaxiProviderAdapter;
  payments: PaymentProviderAdapter;
  notifications: NotificationProviderAdapter;
}
```

## Adapter interfaces

```ts
interface FlightProviderAdapter {
  search(request: FlightSearchRequest): Promise<FlightOffer[]>;
  price(offerId: FlightOfferId): Promise<PricedFlightOffer>;
  reserve(request: CreateFlightBookingRequest): Promise<{ providerReference: string }>;
  ticket(providerReference: string): Promise<void>;
  cancel(providerReference: string): Promise<void>;
}

interface TaxiProviderAdapter {
  quote(request: TaxiQuoteRequest): Promise<TaxiQuote[]>;
  reserve(request: CreateTaxiBookingRequest): Promise<{ providerReference: string }>;
  cancel(providerReference: string): Promise<void>;
}

interface PaymentProviderAdapter {
  authorize(intent: PaymentIntent): Promise<{ providerReference: string }>;
  capture(providerReference: string, amount: Money): Promise<void>;
  refund(providerReference: string, amount?: Money): Promise<void>;
}

interface NotificationProviderAdapter {
  send(request: SendNotificationRequest): Promise<{ providerReference: string }>;
  getStatus(providerReference: string): Promise<NotificationReceipt['status']>;
}
```
