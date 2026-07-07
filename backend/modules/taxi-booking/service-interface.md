# TaxiBookingService

```ts
interface TaxiBookingService {
  quote(request: TaxiQuoteRequest, actor: AuthContext): Promise<TaxiQuote[]>;
  createBooking(request: CreateTaxiBookingRequest, actor: AuthContext): Promise<TaxiBooking>;
  cancelBooking(bookingId: TaxiBookingId, actor: AuthContext): Promise<TaxiBooking>;
  getBooking(bookingId: TaxiBookingId, actor: AuthContext): Promise<TaxiBooking>;
}
```

## Contracts

```ts
interface TaxiQuoteRequest {
  pickup: GeoPoint | PostalAddress;
  dropoff: GeoPoint | PostalAddress;
  pickupAt: string;
  passengerCount: number;
}

interface TaxiQuote {
  id: TaxiQuoteId;
  providerCode: string;
  vehicleClass: string;
  estimatedPrice: Money;
  expiresAt: string;
}

interface CreateTaxiBookingRequest {
  quoteId: TaxiQuoteId;
  travelerProfileId: TravelerProfileId;
  paymentIntentId: PaymentIntentId;
  contact: ContactPoint;
}

interface TaxiBooking {
  id: TaxiBookingId;
  status: 'pending-payment' | 'reserved' | 'driver-assigned' | 'in-progress' | 'completed' | 'cancelled' | 'failed';
  quoteId: TaxiQuoteId;
  providerReference?: string;
}
```
