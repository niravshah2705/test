# FlightSearchService

```ts
interface FlightSearchService {
  search(request: FlightSearchRequest): Promise<FlightSearchResult>;
  priceOffer(offerId: FlightOfferId): Promise<PricedFlightOffer>;
  getSearch(searchId: FlightSearchId): Promise<FlightSearchResult>;
}
```

## Contracts

```ts
interface FlightSearchRequest {
  originAirportCode: string;
  destinationAirportCode: string;
  departureDate: string;
  returnDate?: string;
  travelers: number;
  cabin?: 'economy' | 'premium-economy' | 'business' | 'first';
}

interface FlightSearchResult {
  id: FlightSearchId;
  offers: FlightOffer[];
  expiresAt: string;
}

interface FlightOffer {
  id: FlightOfferId;
  validatingCarrierCode: string;
  totalPrice: Money;
  segments: FlightSegment[];
}

interface FlightSegment {
  originAirportCode: string;
  destinationAirportCode: string;
  departsAt: string;
  arrivesAt: string;
  carrierCode: string;
  flightNumber: string;
}

interface PricedFlightOffer extends FlightOffer {
  priceGuaranteedUntil: string;
}
```
