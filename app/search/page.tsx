type SearchParams = Record<string, string | string[] | undefined>;

type FlightOfferView = {
  id: string;
  price: { formatted: string; currency: string };
  airline: string;
  departureAirport: string;
  arrivalAirport: string;
  departureTime: string;
  arrivalTime: string;
  durationMinutes: number;
  stops: number;
  baggageSummary: string;
  expiresAt: string;
  isExpired: boolean;
  status: string;
  cabin: string;
};

const DEFAULT_OFFERS: FlightOfferView[] = [
  {
    id: "ofb_flt_oneway",
    price: { formatted: "USD 286.00", currency: "USD" },
    airline: "OA",
    departureAirport: "SFO",
    arrivalAirport: "JFK",
    departureTime: "2031-07-01T08:00:00-07:00",
    arrivalTime: "2031-07-01T16:35:00-04:00",
    durationMinutes: 335,
    stops: 0,
    baggageSummary: "1 checked bag included",
    expiresAt: "2031-07-01T07:45:00Z",
    isExpired: false,
    status: "available",
    cabin: "economy",
  },
  {
    id: "ofb_flt_multisegment",
    price: { formatted: "USD 428.00", currency: "USD" },
    airline: "OA",
    departureAirport: "SFO",
    arrivalAirport: "JFK",
    departureTime: "2031-07-01T06:30:00-07:00",
    arrivalTime: "2031-07-01T17:05:00-04:00",
    durationMinutes: 380,
    stops: 1,
    baggageSummary: "Baggage details unavailable",
    expiresAt: "2031-07-02T07:45:00Z",
    isExpired: false,
    status: "available",
    cabin: "economy",
  },
];

export default function Page({ searchParams = {} }: { searchParams?: SearchParams }) {
  const scenario = value(searchParams.scenario);
  const submitted = value(searchParams.origin) || value(searchParams.destination) || scenario;
  const errors = formErrors(searchParams);
  const hasErrors = Object.keys(errors).length > 0;
  const providerFailure = scenario === "timeout" || scenario === "error";
  const empty = scenario === "no_availability";
  const offers = submitted && !hasErrors && !providerFailure && !empty ? DEFAULT_OFFERS : [];

  return (
    <main>
      <section aria-labelledby="flight-search-heading">
        <h1 id="flight-search-heading">Search flights</h1>
        <p>Compare available itineraries by route, date, passenger mix, and cabin.</p>
        <form action="/search" method="get" aria-describedby="flight-search-help">
          <p id="flight-search-help">Airport fields support autocomplete suggestions through the flight search API.</p>
          <fieldset>
            <legend>Trip type</legend>
            <label><input type="radio" name="tripType" value="one_way" defaultChecked={value(searchParams.tripType) !== "round_trip"} /> One-way</label>
            <label><input type="radio" name="tripType" value="round_trip" defaultChecked={value(searchParams.tripType) === "round_trip"} /> Round trip</label>
          </fieldset>
          <label htmlFor="origin">Origin airport</label>
          <input id="origin" name="origin" list="airport-options" defaultValue={value(searchParams.origin) || "SFO"} aria-invalid={Boolean(errors.origin)} aria-describedby={errors.origin ? "origin-error" : undefined} />
          {errors.origin ? <p id="origin-error" role="alert">{errors.origin}</p> : null}
          <label htmlFor="destination">Destination airport</label>
          <input id="destination" name="destination" list="airport-options" defaultValue={value(searchParams.destination) || "JFK"} aria-invalid={Boolean(errors.destination)} aria-describedby={errors.destination ? "destination-error" : undefined} />
          {errors.destination ? <p id="destination-error" role="alert">{errors.destination}</p> : null}
          <datalist id="airport-options">
            <option value="SFO">San Francisco International</option>
            <option value="JFK">John F. Kennedy International</option>
            <option value="LAX">Los Angeles International</option>
            <option value="ORD">O'Hare International</option>
            <option value="SEA">Seattle-Tacoma International</option>
          </datalist>
          <label htmlFor="departDate">Departure date</label>
          <input id="departDate" name="departDate" type="date" defaultValue={value(searchParams.departDate) || "2031-07-01"} aria-invalid={Boolean(errors.departDate)} aria-describedby={errors.departDate ? "depart-error" : undefined} />
          {errors.departDate ? <p id="depart-error" role="alert">{errors.departDate}</p> : null}
          <label htmlFor="returnDate">Return date for round trips</label>
          <input id="returnDate" name="returnDate" type="date" defaultValue={value(searchParams.returnDate)} aria-invalid={Boolean(errors.returnDate)} aria-describedby={errors.returnDate ? "return-error" : undefined} />
          {errors.returnDate ? <p id="return-error" role="alert">{errors.returnDate}</p> : null}
          <label htmlFor="adults">Adults</label>
          <input id="adults" name="adults" type="number" min="1" defaultValue={value(searchParams.adults) || "1"} />
          <label htmlFor="children">Children</label>
          <input id="children" name="children" type="number" min="0" defaultValue={value(searchParams.children) || "0"} />
          <label htmlFor="infants">Infants</label>
          <input id="infants" name="infants" type="number" min="0" defaultValue={value(searchParams.infants) || "0"} aria-invalid={Boolean(errors.infants)} aria-describedby={errors.infants ? "infants-error" : undefined} />
          {errors.infants ? <p id="infants-error" role="alert">{errors.infants}</p> : null}
          {errors.passengers ? <p role="alert">{errors.passengers}</p> : null}
          <label htmlFor="cabin">Cabin</label>
          <select id="cabin" name="cabin" defaultValue={value(searchParams.cabin) || "economy"}>
            <option value="economy">Economy</option>
            <option value="premium_economy">Premium economy</option>
            <option value="business">Business</option>
            <option value="first">First</option>
          </select>
          <button type="submit">Search flights</button>
        </form>
      </section>

      <section aria-live="polite" aria-busy="false" aria-labelledby="flight-results-heading">
        <h2 id="flight-results-heading">Flight results</h2>
        {submitted ? <p>Loading state: searching flights shows this region as busy while offers load.</p> : <p>Submit a valid search to compare flight offers.</p>}
        {hasErrors ? <div role="alert">Invalid forms show field errors above before searching.</div> : null}
        {providerFailure ? <div role="alert">Flight provider failed. Adjust your search or retry when the provider recovers.</div> : null}
        {empty ? <p>No flights found for this route and date. Try nearby airports or another day.</p> : null}
        {offers.length > 0 ? (
          <ol>
            {offers.map((offer) => (
              <li key={offer.id}>
                <article aria-labelledby={`${offer.id}-title`}>
                  <h3 id={`${offer.id}-title`}>{offer.airline} {offer.departureAirport} to {offer.arrivalAirport}</h3>
                  <p>{offer.price.formatted} {offer.price.currency}</p>
                  <p>Depart {formatLocalTime(offer.departureTime)} · Arrive {formatLocalTime(offer.arrivalTime)}</p>
                  <p>{formatDuration(offer.durationMinutes)} · {offer.stops === 0 ? "Nonstop" : `${offer.stops} stop${offer.stops > 1 ? "s" : ""}`}</p>
                  <p>{offer.baggageSummary}</p>
                  <p>Cabin: {offer.cabin.replace("_", " ")}</p>
                  <p>{offer.isExpired ? "Offer expired; refresh before booking." : `Offer held until ${formatLocalTime(offer.expiresAt)}.`}</p>
                </article>
              </li>
            ))}
          </ol>
        ) : null}
      </section>
    </main>
  );
}

function value(input: string | string[] | undefined): string {
  return Array.isArray(input) ? input[0] || "" : input || "";
}

function formErrors(searchParams: SearchParams): Record<string, string> {
  const errors: Record<string, string> = {};
  const origin = value(searchParams.origin).toUpperCase();
  const destination = value(searchParams.destination).toUpperCase();
  const departDate = value(searchParams.departDate);
  const returnDate = value(searchParams.returnDate);
  if (origin && destination && origin === destination) errors.destination = "Destination must be different from origin.";
  if (departDate && returnDate && returnDate < departDate) errors.returnDate = "Return date must be on or after departure date.";
  const adults = Number(value(searchParams.adults) || "1");
  const children = Number(value(searchParams.children) || "0");
  const infants = Number(value(searchParams.infants) || "0");
  if (adults + children + infants > 9) errors.passengers = "Total passengers must be less than or equal to 9.";
  if (infants > adults) errors.infants = "Infant count cannot exceed adult count.";
  return errors;
}

function formatDuration(minutes: number): string {
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function formatLocalTime(value: string): string {
  return value.replace("T", " ").replace(/(:\d{2})(?:[+-]\d{2}:\d{2}|Z)$/, "");
}
