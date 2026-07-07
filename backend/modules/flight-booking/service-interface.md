# FlightBookingService

```ts
interface FlightBookingService {
  createBooking(request: CreateFlightBookingRequest, actor: AuthContext): Promise<FlightBooking>;
  ticketBooking(bookingId: FlightBookingId, actor: AuthContext): Promise<FlightBooking>;
  cancelBooking(bookingId: FlightBookingId, actor: AuthContext): Promise<FlightBooking>;
  getBooking(bookingId: FlightBookingId, actor: AuthContext): Promise<FlightBooking>;
}
```

## Contracts

```ts
interface CreateFlightBookingRequest {
  offerId: FlightOfferId;
  travelerProfileIds: TravelerProfileId[];
  paymentIntentId: PaymentIntentId;
  contact: ContactPoint;
}

interface FlightBooking {
  id: FlightBookingId;
  userId: UserId;
  offerId: FlightOfferId;
  travelerProfileIds: TravelerProfileId[];
  status: 'pending-payment' | 'reserved' | 'ticketed' | 'cancelled' | 'failed';
  totalPrice: Money;
  providerReference?: string;
}
```
