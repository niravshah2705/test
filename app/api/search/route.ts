type FieldErrors = Record<string, string[]>;

type FlightSearchPayload = {
  origin?: string;
  destination?: string;
  departDate?: string;
  returnDate?: string;
  tripType?: string;
  adults?: number | string;
  children?: number | string;
  infants?: number | string;
  cabin?: string;
  scenario?: string;
};

const MAX_PASSENGERS = 9;
const CABINS = new Set(["economy", "premium_economy", "business", "first"]);
const AIRPORT_PATTERN = /^[A-Z]{3}$/;

export async function GET(request: Request) {
  const url = new URL(request.url);
  if (url.searchParams.has("airportQuery")) {
    const query = url.searchParams.get("airportQuery") ?? "";
    return apiSuccess({ airports: airportSuggestions(query) });
  }

  return runSearch(Object.fromEntries(url.searchParams.entries()));
}

export async function POST(request: Request) {
  let payload: FlightSearchPayload;
  try {
    payload = await request.json();
  } catch {
    return apiError(400, "invalid_json", "Request body must be valid JSON.");
  }
  return runSearch(payload);
}

function runSearch(payload: FlightSearchPayload): Response {
  const validation = validateFlightSearch(payload);
  if (Object.keys(validation.errors).length > 0 || validation.query === null) {
    return apiError(400, "validation_error", "Flight search parameters failed validation.", validation.errors);
  }

  if (validation.query.scenario === "timeout") {
    return apiError(504, "provider_timeout", "Flight provider timed out. Please retry your search.");
  }
  if (validation.query.scenario === "error") {
    return apiError(503, "provider_unavailable", "Flight provider is temporarily unavailable. Please retry.");
  }

  const offers = validation.query.scenario === "no_availability" ? [] : buildOffers(validation.query);
  const sessionId = ["flight_search", validation.query.origin, validation.query.destination, validation.query.departDate, validation.query.returnDate ?? "oneway", validation.query.passengerCount, validation.query.cabin]
    .join("_")
    .toLowerCase();

  return apiSuccess(
    { sessionId, query: validation.query, offers, empty: offers.length === 0 },
    { resultCount: offers.length, providerPayloadExposed: false },
  );
}

function validateFlightSearch(payload: FlightSearchPayload): { errors: FieldErrors; query: null | RequiredSearchQuery } {
  const errors: FieldErrors = {};
  const tripType = String(payload.tripType ?? "one_way");
  if (!["one_way", "round_trip"].includes(tripType)) {
    errors.tripType = ["Trip type must be one_way or round_trip."];
  }

  const origin = airportCode(payload.origin, "origin", errors);
  const destination = airportCode(payload.destination, "destination", errors);
  if (origin && destination && origin === destination) {
    errors.destination = [...(errors.destination ?? []), "Destination must be different from origin."];
  }

  const departDate = parseDate(String(payload.departDate ?? ""), "departDate", errors);
  const returnDateRaw = String(payload.returnDate ?? "");
  const returnDate = tripType === "round_trip" || returnDateRaw ? parseDate(returnDateRaw, "returnDate", errors) : null;
  if (departDate && returnDate && returnDate < departDate) {
    errors.returnDate = [...(errors.returnDate ?? []), "Return date must be on or after departure date."];
  }

  const adults = integer(payload.adults ?? 1, "adults", 1, errors);
  const children = integer(payload.children ?? 0, "children", 0, errors);
  const infants = integer(payload.infants ?? 0, "infants", 0, errors);
  if (adults !== null && children !== null && infants !== null) {
    if (adults + children + infants > MAX_PASSENGERS) {
      errors.passengers = [`Total passengers must be less than or equal to ${MAX_PASSENGERS}.`];
    }
    if (infants > adults) {
      errors.infants = ["Infant count cannot exceed adult count."];
    }
  }

  const cabin = String(payload.cabin ?? "economy");
  if (!CABINS.has(cabin)) {
    errors.cabin = ["Cabin must be economy, premium_economy, business, or first."];
  }

  if (Object.keys(errors).length > 0 || !origin || !destination || !departDate || adults === null || children === null || infants === null) {
    return { errors, query: null };
  }

  return {
    errors,
    query: {
      origin,
      destination,
      departDate: isoDate(departDate),
      returnDate: tripType === "round_trip" && returnDate ? isoDate(returnDate) : null,
      tripType,
      adults,
      children,
      infants,
      cabin,
      scenario: String(payload.scenario ?? "success"),
      passengerCount: adults + children + infants,
    },
  };
}

type RequiredSearchQuery = {
  origin: string;
  destination: string;
  departDate: string;
  returnDate: string | null;
  tripType: string;
  adults: number;
  children: number;
  infants: number;
  cabin: string;
  scenario: string;
  passengerCount: number;
};

function buildOffers(query: RequiredSearchQuery) {
  const baseOffer = normalizedOffer({ id: "ofb_flt_oneway", query, price: 28600 * query.passengerCount, stops: 0, durationMinutes: 335, baggageSummary: "1 checked bag included", expiresAt: "2031-07-01T07:45:00Z" });
  const crossingZonesOffer = normalizedOffer({ id: "ofb_flt_multisegment", query, price: 42800 * query.passengerCount, stops: 1, durationMinutes: 380, baggageSummary: "Baggage details unavailable", expiresAt: "2031-07-02T07:45:00Z" });
  return query.tripType === "round_trip"
    ? [baseOffer, crossingZonesOffer, normalizedOffer({ id: "ofb_flt_roundtrip", query, price: 51200 * query.passengerCount, stops: 0, durationMinutes: 735, baggageSummary: "1 checked bag included", expiresAt: "2031-07-02T07:45:00Z" })]
    : [baseOffer, crossingZonesOffer];
}

function normalizedOffer(input: { id: string; query: RequiredSearchQuery; price: number; stops: number; durationMinutes: number; baggageSummary: string; expiresAt: string }) {
  const expired = input.expiresAt <= "2031-06-30T12:00:00Z";
  return {
    id: input.id,
    price: { amountCents: input.price, currency: "USD", formatted: `USD ${(input.price / 100).toFixed(2)}` },
    currency: "USD",
    airline: "OA",
    departureAirport: input.query.origin,
    arrivalAirport: input.query.destination,
    departureTime: `${input.query.departDate}T08:00:00-07:00`,
    arrivalTime: `${input.query.departDate}T16:35:00-04:00`,
    durationMinutes: input.durationMinutes,
    stops: input.stops,
    baggageSummary: input.baggageSummary,
    expiresAt: input.expiresAt,
    isExpired: expired,
    status: expired ? "expired" : "available",
    cabin: input.query.cabin,
    passengerCount: input.query.passengerCount,
  };
}

function airportCode(value: unknown, field: string, errors: FieldErrors): string | null {
  const code = String(value ?? "").trim().toUpperCase();
  if (!code) {
    errors[field] = [`${field[0].toUpperCase()}${field.slice(1)} airport is required.`];
    return null;
  }
  if (!AIRPORT_PATTERN.test(code)) {
    errors[field] = ["Airport must be a three-letter IATA code."];
    return null;
  }
  return code;
}

function parseDate(value: string, field: string, errors: FieldErrors): Date | null {
  if (!value) {
    errors[field] = ["Date is required in YYYY-MM-DD format."];
    return null;
  }
  const date = new Date(`${value}T00:00:00Z`);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(date.getTime())) {
    errors[field] = ["Date must be a valid YYYY-MM-DD date."];
    return null;
  }
  return date;
}

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function integer(value: unknown, field: string, minimum: number, errors: FieldErrors): number | null {
  const parsed = Number(value);
  if (!Number.isInteger(parsed)) {
    errors[field] = ["Must be an integer."];
    return null;
  }
  if (parsed < minimum) {
    errors[field] = [`Must be greater than or equal to ${minimum}.`];
    return null;
  }
  return parsed;
}

function apiSuccess(data: unknown, meta?: Record<string, unknown>): Response {
  return Response.json({ success: true, data, error: null, ...(meta ? { meta } : {}) });
}

function apiError(status: number, code: string, message: string, fields?: FieldErrors): Response {
  return Response.json({ success: false, data: null, error: { code, message, ...(fields ? { fields } : {}) } }, { status });
}

function airportSuggestions(query: string) {
  const airports = [
    { code: "SFO", name: "San Francisco International", city: "San Francisco" },
    { code: "JFK", name: "John F. Kennedy International", city: "New York" },
    { code: "LAX", name: "Los Angeles International", city: "Los Angeles" },
    { code: "ORD", name: "O'Hare International", city: "Chicago" },
    { code: "SEA", name: "Seattle-Tacoma International", city: "Seattle" },
  ];
  const needle = query.trim().toLowerCase();
  return needle ? airports.filter((airport) => [airport.code, airport.name, airport.city].some((value) => value.toLowerCase().includes(needle))) : airports;
}
