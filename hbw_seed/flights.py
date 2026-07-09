"""Provider-neutral flight integration layer with deterministic mock data.

The application service in this module depends on the ``FlightProvider`` protocol
instead of a concrete adapter. Provider-native identifiers and payload fragments
are kept inside ``ProviderReference`` objects so callers can persist normalized
OFB offer/order models without leaking adapter-specific shapes through the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "CAD"}
DEFAULT_CURRENCY = "USD"
MOCK_PROVIDER_NAME = "deterministic_mock_air"

OfferStatus = Literal["available", "price_changed", "unavailable"]
OrderStatus = Literal["ticketing_pending", "ticketed", "failed"]


class FlightProviderError(RuntimeError):
    """Base class for provider failures that callers can handle uniformly."""


class FlightProviderTimeout(FlightProviderError):
    """Raised when the provider times out."""


class FlightProviderUnavailable(FlightProviderError):
    """Raised when the provider returns a retryable error response."""


@dataclass(frozen=True)
class ProviderReference:
    """Opaque provider-owned reference kept behind the domain boundary."""

    provider: str
    reference_id: str
    kind: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Money:
    amount_cents: int
    currency: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "amountCents": self.amount_cents,
            "currency": self.currency,
            "formatted": f"{self.currency} {self.amount_cents / 100:.2f}",
        }


@dataclass(frozen=True)
class FlightSegment:
    id: str
    marketing_carrier: str
    operating_carrier: str
    flight_number: str
    origin: str
    destination: str
    departs_at: str
    arrives_at: str
    duration_minutes: int
    aircraft: str | None = None

    @property
    def codeshare(self) -> bool:
        return self.marketing_carrier != self.operating_carrier

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "marketingCarrier": self.marketing_carrier,
            "operatingCarrier": self.operating_carrier,
            "flightNumber": self.flight_number,
            "origin": self.origin,
            "destination": self.destination,
            "departsAt": self.departs_at,
            "arrivesAt": self.arrives_at,
            "durationMinutes": self.duration_minutes,
            "aircraft": self.aircraft,
            "codeshare": self.codeshare,
        }


@dataclass(frozen=True)
class FlightItinerary:
    id: str
    segments: tuple[FlightSegment, ...]

    def to_payload(self) -> dict[str, Any]:
        return {"id": self.id, "segments": [segment.to_payload() for segment in self.segments]}


@dataclass(frozen=True)
class FlightOffer:
    id: str
    itineraries: tuple[FlightItinerary, ...]
    total: Money
    passenger_count: int
    cabin: str
    refundable: bool
    checked_bags_included: int | None
    provider_reference: ProviderReference
    status: OfferStatus = "available"
    expires_at: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return frontend-safe offer data without provider adapter references."""

        return {
            "id": self.id,
            "itineraries": [itinerary.to_payload() for itinerary in self.itineraries],
            "total": self.total.to_payload(),
            "passengerCount": self.passenger_count,
            "cabin": self.cabin,
            "refundable": self.refundable,
            "checkedBagsIncluded": self.checked_bags_included,
            "status": self.status,
            "expiresAt": self.expires_at,
        }


@dataclass(frozen=True)
class RevalidationResult:
    status: OfferStatus
    offer: FlightOffer | None = None
    previous_total: Money | None = None
    message: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "offer": self.offer.to_payload() if self.offer else None,
            "previousTotal": self.previous_total.to_payload() if self.previous_total else None,
            "message": self.message,
        }


@dataclass(frozen=True)
class FlightOrder:
    id: str
    offer_id: str
    status: OrderStatus
    total: Money
    ticketing_deadline: str | None
    provider_reference: ProviderReference

    def to_payload(self) -> dict[str, Any]:
        """Return frontend-safe booking/order data without provider adapter references."""

        return {
            "id": self.id,
            "offerId": self.offer_id,
            "status": self.status,
            "total": self.total.to_payload(),
            "ticketingDeadline": self.ticketing_deadline,
        }


@dataclass(frozen=True)
class FlightSearchRequest:
    origin: str
    destination: str
    depart_date: str
    return_date: str | None = None
    adults: int = 1
    children: int = 0
    infants: int = 0
    cabin: str = "economy"
    scenario: str = "success"


@dataclass(frozen=True)
class FlightOrderRequest:
    offer_id: str
    passengers: tuple[dict[str, Any], ...]
    contact_email: str
    scenario: str = "success"


@runtime_checkable
class FlightProvider(Protocol):
    """Provider contract used by application services."""

    def searchFlights(self, request: FlightSearchRequest) -> list[dict[str, Any]]:
        ...

    def getOfferDetails(self, offer_id: str) -> dict[str, Any]:
        ...

    def revalidateOffer(self, offer_id: str, *, scenario: str = "success") -> dict[str, Any]:
        ...

    def createOrder(self, request: FlightOrderRequest) -> dict[str, Any]:
        ...

    def getOrderStatus(self, order_id: str) -> dict[str, Any]:
        ...


class FlightBookingService:
    """Application-facing service that depends only on the provider interface."""

    def __init__(self, provider: FlightProvider):
        self.provider = provider

    def searchFlights(self, request: FlightSearchRequest) -> list[FlightOffer]:
        return [map_provider_offer(raw) for raw in self.provider.searchFlights(request)]

    def getOfferDetails(self, offer_id: str) -> FlightOffer:
        return map_provider_offer(self.provider.getOfferDetails(offer_id))

    def revalidateOffer(self, offer_id: str, *, scenario: str = "success") -> RevalidationResult:
        raw = self.provider.revalidateOffer(offer_id, scenario=scenario)
        status = raw["status"]
        if status == "unavailable":
            return RevalidationResult(status="unavailable", message=raw.get("message"))
        offer = map_provider_offer(raw["offer"])
        previous_total = _provider_money(raw["previousTotal"]) if raw.get("previousTotal") else None
        return RevalidationResult(status=status, offer=offer, previous_total=previous_total, message=raw.get("message"))

    def createOrder(self, request: FlightOrderRequest) -> FlightOrder:
        return map_provider_order(self.provider.createOrder(request))

    def getOrderStatus(self, order_id: str) -> FlightOrder:
        return map_provider_order(self.provider.getOrderStatus(order_id))


class DeterministicMockFlightProvider:
    """Deterministic adapter covering core flight-provider success/failure paths."""

    provider_name = MOCK_PROVIDER_NAME

    def searchFlights(self, request: FlightSearchRequest) -> list[dict[str, Any]]:
        if request.scenario == "timeout":
            raise FlightProviderTimeout("Mock provider timed out while searching flights.")
        if request.scenario == "error":
            raise FlightProviderUnavailable("Mock provider returned a retryable error.")
        if request.scenario in {"unavailable", "no_availability"}:
            return []

        offers = [_offer_payload("ofb_flt_oneway", request), _offer_payload("ofb_flt_multisegment", request, missing_baggage=True)]
        if request.return_date:
            offers.append(_offer_payload("ofb_flt_roundtrip", request))
        return offers

    def getOfferDetails(self, offer_id: str) -> dict[str, Any]:
        if offer_id == "ofb_flt_timeout":
            raise FlightProviderTimeout("Mock provider timed out while retrieving offer details.")
        if offer_id == "ofb_flt_unavailable":
            raise FlightProviderUnavailable("Mock provider could not retrieve offer details.")
        return _offer_payload(offer_id, _default_request())

    def revalidateOffer(self, offer_id: str, *, scenario: str = "success") -> dict[str, Any]:
        if scenario == "timeout":
            raise FlightProviderTimeout("Mock provider timed out while revalidating offer.")
        if scenario == "error":
            raise FlightProviderUnavailable("Mock provider returned a retryable revalidation error.")
        if scenario == "unavailable":
            return {"status": "unavailable", "offer": None, "message": "Offer is no longer available."}

        offer = _offer_payload(offer_id, _default_request())
        if scenario == "price_change":
            previous_total = dict(offer["pricing"]["total"])
            offer = dict(offer)
            offer["pricing"] = dict(offer["pricing"])
            offer["pricing"]["total"] = {"amount": previous_total["amount"] + 4200, "currency": previous_total["currency"]}
            return {"status": "price_changed", "offer": offer, "previousTotal": previous_total, "message": "Fare changed during revalidation."}
        return {"status": "available", "offer": offer, "message": "Offer remains available."}

    def createOrder(self, request: FlightOrderRequest) -> dict[str, Any]:
        if request.scenario == "timeout":
            raise FlightProviderTimeout("Mock provider timed out while creating order.")
        if request.scenario == "error":
            raise FlightProviderUnavailable("Mock provider returned a booking error.")
        if request.scenario == "unavailable":
            raise FlightProviderUnavailable("Offer became unavailable before booking.")
        return _order_payload(f"ord_{request.offer_id}", request.offer_id, status="ticketing_pending")

    def getOrderStatus(self, order_id: str) -> dict[str, Any]:
        if order_id == "ord_timeout":
            raise FlightProviderTimeout("Mock provider timed out while retrieving order status.")
        status: OrderStatus = "ticketing_pending" if order_id.endswith("pending") or order_id.startswith("ord_") else "ticketed"
        return _order_payload(order_id, _offer_id_from_order(order_id), status=status)


# Backwards-friendly alias while keeping app services typed against FlightProvider.
MockFlightProvider = DeterministicMockFlightProvider


def map_provider_offer(raw: dict[str, Any]) -> FlightOffer:
    """Normalize a provider-native offer into the OFB flight offer model."""

    provider_reference = _provider_reference(raw["provider"], raw["providerOfferId"], "flight_offer", raw.get("providerMeta"))
    itineraries = tuple(_map_itinerary(itinerary) for itinerary in raw.get("itineraries", []))
    return FlightOffer(
        id=raw["id"],
        itineraries=itineraries,
        total=_provider_money(raw["pricing"]["total"]),
        passenger_count=int(raw.get("passengerCount", 1)),
        cabin=raw.get("cabin") or "economy",
        refundable=bool(raw.get("refundable", False)),
        checked_bags_included=raw.get("baggage", {}).get("checkedBagsIncluded"),
        provider_reference=provider_reference,
        status=raw.get("status", "available"),
        expires_at=raw.get("expiresAt"),
    )


def map_provider_order(raw: dict[str, Any]) -> FlightOrder:
    """Normalize a provider-native order into the OFB booking/order model."""

    return FlightOrder(
        id=raw["id"],
        offer_id=raw["offerId"],
        status=raw.get("status", "ticketing_pending"),
        total=_provider_money(raw["pricing"]["total"]),
        ticketing_deadline=raw.get("ticketingDeadline"),
        provider_reference=_provider_reference(raw["provider"], raw["providerOrderId"], "flight_order", raw.get("providerMeta")),
    )


def _map_itinerary(raw: dict[str, Any]) -> FlightItinerary:
    return FlightItinerary(id=raw["id"], segments=tuple(_map_segment(segment) for segment in raw.get("segments", [])))


def _map_segment(raw: dict[str, Any]) -> FlightSegment:
    return FlightSegment(
        id=raw["id"],
        marketing_carrier=raw["marketingCarrier"],
        operating_carrier=raw.get("operatingCarrier") or raw["marketingCarrier"],
        flight_number=raw["flightNumber"],
        origin=raw["origin"],
        destination=raw["destination"],
        departs_at=raw["departsAt"],
        arrives_at=raw["arrivesAt"],
        duration_minutes=int(raw["durationMinutes"]),
        aircraft=raw.get("aircraft"),
    )


def _provider_reference(provider: str, reference_id: str, kind: str, attributes: dict[str, Any] | None) -> ProviderReference:
    return ProviderReference(provider=provider, reference_id=reference_id, kind=kind, attributes=dict(attributes or {}))


def _provider_money(raw: dict[str, Any]) -> Money:
    currency = str(raw.get("currency") or DEFAULT_CURRENCY).upper()
    if currency not in SUPPORTED_CURRENCIES:
        currency = DEFAULT_CURRENCY
    amount = raw.get("amountCents", raw.get("amount"))
    return Money(amount_cents=int(amount), currency=currency)


def _default_request() -> FlightSearchRequest:
    return FlightSearchRequest(origin="SFO", destination="JFK", depart_date="2031-07-01", return_date="2031-07-08", adults=1)


def _offer_payload(offer_id: str, request: FlightSearchRequest, *, missing_baggage: bool = False) -> dict[str, Any]:
    passenger_count = request.adults + request.children + request.infants
    price = 28600 if offer_id.endswith("oneway") else 42800 if offer_id.endswith("multisegment") else 51200
    itinerary = _roundtrip_itineraries(request) if offer_id.endswith("roundtrip") else [_multisegment_itinerary() if offer_id.endswith("multisegment") else _oneway_itinerary(request)]
    baggage = {} if missing_baggage or offer_id.endswith("multisegment") else {"checkedBagsIncluded": 1}
    currency = "ZZZ" if offer_id.endswith("unexpected_currency") else "USD"
    return {
        "id": offer_id,
        "provider": MOCK_PROVIDER_NAME,
        "providerOfferId": f"mock-offer-{offer_id}",
        "providerMeta": {"fareSource": "fixture", "nativeOfferToken": f"tok_{offer_id}"},
        "itineraries": itinerary,
        "pricing": {"total": {"amount": price * passenger_count, "currency": currency}},
        "passengerCount": passenger_count,
        "cabin": request.cabin,
        "refundable": offer_id.endswith("roundtrip"),
        "baggage": baggage,
        "status": "available",
        "expiresAt": "2031-07-01T07:45:00Z" if offer_id.endswith("oneway") else "2031-07-02T07:45:00Z",
    }


def _oneway_itinerary(request: FlightSearchRequest) -> dict[str, Any]:
    return {
        "id": "itin_outbound",
        "segments": [
            {
                "id": "seg_sfo_jfk",
                "marketingCarrier": "OA",
                "operatingCarrier": "OA",
                "flightNumber": "OA100",
                "origin": request.origin,
                "destination": request.destination,
                "departsAt": f"{request.depart_date}T08:00:00-07:00",
                "arrivesAt": f"{request.depart_date}T16:35:00-04:00",
                "durationMinutes": 335,
                "aircraft": "738",
            }
        ],
    }


def _roundtrip_itineraries(request: FlightSearchRequest) -> list[dict[str, Any]]:
    inbound_date = request.return_date or request.depart_date
    inbound = {
        "id": "itin_inbound",
        "segments": [
            {
                "id": "seg_jfk_sfo",
                "marketingCarrier": "OA",
                "operatingCarrier": "OA",
                "flightNumber": "OA101",
                "origin": request.destination,
                "destination": request.origin,
                "departsAt": f"{inbound_date}T17:00:00-04:00",
                "arrivesAt": f"{inbound_date}T20:40:00-07:00",
                "durationMinutes": 400,
                "aircraft": "739",
            }
        ],
    }
    return [_oneway_itinerary(request), inbound]


def _multisegment_itinerary() -> dict[str, Any]:
    return {
        "id": "itin_multisegment_codeshare",
        "segments": [
            {
                "id": "seg_sfo_ord",
                "marketingCarrier": "OA",
                "operatingCarrier": "OA",
                "flightNumber": "OA210",
                "origin": "SFO",
                "destination": "ORD",
                "departsAt": "2031-07-01T06:30:00-07:00",
                "arrivesAt": "2031-07-01T12:45:00-05:00",
                "durationMinutes": 255,
                "aircraft": "320",
            },
            {
                "id": "seg_ord_jfk_codeshare",
                "marketingCarrier": "OA",
                "operatingCarrier": "PB",
                "flightNumber": "OA9824",
                "origin": "ORD",
                "destination": "JFK",
                "departsAt": "2031-07-01T14:00:00-05:00",
                "arrivesAt": "2031-07-01T17:05:00-04:00",
                "durationMinutes": 125,
                "aircraft": None,
            },
        ],
    }


def _order_payload(order_id: str, offer_id: str, *, status: OrderStatus) -> dict[str, Any]:
    return {
        "id": order_id,
        "offerId": offer_id,
        "provider": MOCK_PROVIDER_NAME,
        "providerOrderId": f"mock-order-{order_id}",
        "providerMeta": {"nativeOrderToken": f"order_tok_{order_id}"},
        "pricing": {"total": {"amount": 28600, "currency": "USD"}},
        "status": status,
        "ticketingDeadline": "2031-07-01T07:45:00Z" if status == "ticketing_pending" else None,
    }


def _offer_id_from_order(order_id: str) -> str:
    if order_id.startswith("ord_"):
        return order_id.removeprefix("ord_")
    return "ofb_flt_oneway"
