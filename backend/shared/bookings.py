"""Booking persistence domain records and repositories.

The models in this module capture persisted booking records independently from
any transport, ORM, or provider integration.  They intentionally keep
itinerary, passenger, contact, baggage, request, and status-history data in
separate value objects while exposing a repository contract that enforces the
core booking invariants needed by booking services.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from threading import RLock
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple
from uuid import uuid4

JsonObject = Dict[str, object]


class BookingStatus(str, Enum):
    """Canonical persisted booking lifecycle states."""

    PENDING = "pending"
    HELD = "held"
    CONFIRMED = "confirmed"
    TICKETED = "ticketed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class PassengerType(str, Enum):
    """Passenger age categories captured on a booking."""

    ADULT = "adult"
    CHILD = "child"
    INFANT = "infant"


class SegmentStatus(str, Enum):
    """Provider status for a booked itinerary segment."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


class BaggageSelectionType(str, Enum):
    """Baggage products selected for a passenger segment."""

    CARRY_ON = "carry_on"
    CHECKED = "checked"
    SPORTS_EQUIPMENT = "sports_equipment"
    OVERSIZED = "oversized"


class SpecialRequestType(str, Enum):
    """Common SSR/request categories stored with a booking."""

    MEAL = "meal"
    SEAT = "seat"
    ACCESSIBILITY = "accessibility"
    ASSISTANCE = "assistance"
    OTHER = "other"


@dataclass(frozen=True)
class BookingContactDetails:
    """Contact details for booking notifications and servicing."""

    email: str
    phone_number: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "email", _normalize_email(self.email))
        if self.phone_number is not None:
            phone_number = self.phone_number.strip()
            if not phone_number:
                raise ValueError("contact phone_number cannot be blank")
            object.__setattr__(self, "phone_number", phone_number)
        if self.given_name is not None:
            object.__setattr__(self, "given_name", _normalize_optional_text("contact given_name", self.given_name))
        if self.family_name is not None:
            object.__setattr__(self, "family_name", _normalize_optional_text("contact family_name", self.family_name))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "email": self.email,
                "phoneNumber": self.phone_number,
                "givenName": self.given_name,
                "familyName": self.family_name,
            }
        )


@dataclass(frozen=True)
class BookingItinerarySegment:
    """Booked flight segment associated with a booking record."""

    origin_airport_code: str
    destination_airport_code: str
    departure_at: datetime
    arrival_at: datetime
    marketing_airline_code: str
    flight_number: str
    segment_id: str = field(default_factory=lambda: f"segment_{uuid4().hex}")
    sequence: int = 1
    operating_airline_code: Optional[str] = None
    cabin_class: Optional[str] = None
    fare_basis_code: Optional[str] = None
    provider_segment_id: Optional[str] = None
    status: SegmentStatus = SegmentStatus.PENDING

    def __post_init__(self) -> None:
        if not self.segment_id:
            raise ValueError("segment_id is required")
        if self.sequence <= 0:
            raise ValueError("segment sequence must be positive")
        object.__setattr__(self, "origin_airport_code", _normalize_airport_code(self.origin_airport_code))
        object.__setattr__(self, "destination_airport_code", _normalize_airport_code(self.destination_airport_code))
        if self.origin_airport_code == self.destination_airport_code:
            raise ValueError("segment origin and destination must differ")
        _require_aware("departure_at", self.departure_at)
        _require_aware("arrival_at", self.arrival_at)
        if self.departure_at >= self.arrival_at:
            raise ValueError("segment departure_at must be before arrival_at")
        object.__setattr__(self, "marketing_airline_code", _normalize_airline_code(self.marketing_airline_code))
        if self.operating_airline_code is not None:
            object.__setattr__(self, "operating_airline_code", _normalize_airline_code(self.operating_airline_code))
        flight_number = self.flight_number.strip().upper()
        if not flight_number:
            raise ValueError("segment flight_number is required")
        object.__setattr__(self, "flight_number", flight_number)
        if self.cabin_class is not None:
            object.__setattr__(self, "cabin_class", _normalize_optional_text("segment cabin_class", self.cabin_class))
        if self.fare_basis_code is not None:
            object.__setattr__(self, "fare_basis_code", _normalize_optional_text("segment fare_basis_code", self.fare_basis_code).upper())
        if self.provider_segment_id is not None:
            object.__setattr__(self, "provider_segment_id", _normalize_optional_text("segment provider_segment_id", self.provider_segment_id))

    @property
    def flight_designator(self) -> str:
        return f"{self.marketing_airline_code}{self.flight_number}"

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "segmentId": self.segment_id,
                "sequence": self.sequence,
                "originAirportCode": self.origin_airport_code,
                "destinationAirportCode": self.destination_airport_code,
                "departureAt": _format_datetime(self.departure_at),
                "arrivalAt": _format_datetime(self.arrival_at),
                "marketingAirlineCode": self.marketing_airline_code,
                "operatingAirlineCode": self.operating_airline_code,
                "flightNumber": self.flight_number,
                "flightDesignator": self.flight_designator,
                "cabinClass": self.cabin_class,
                "fareBasisCode": self.fare_basis_code,
                "providerSegmentId": self.provider_segment_id,
                "status": self.status.value,
            }
        )


@dataclass(frozen=True)
class BookingPassenger:
    """Passenger persisted on a booking."""

    given_name: str
    family_name: str
    passenger_id: str = field(default_factory=lambda: f"passenger_{uuid4().hex}")
    passenger_type: PassengerType = PassengerType.ADULT
    date_of_birth: Optional[date] = None
    traveler_profile_id: Optional[str] = None
    loyalty_programs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.passenger_id:
            raise ValueError("passenger_id is required")
        object.__setattr__(self, "given_name", _normalize_required_text("passenger given_name", self.given_name))
        object.__setattr__(self, "family_name", _normalize_required_text("passenger family_name", self.family_name))
        if self.traveler_profile_id is not None:
            object.__setattr__(self, "traveler_profile_id", _normalize_optional_text("traveler_profile_id", self.traveler_profile_id))
        object.__setattr__(self, "loyalty_programs", {code.strip().upper(): number.strip() for code, number in self.loyalty_programs.items()})
        for code, number in self.loyalty_programs.items():
            if not code or not number:
                raise ValueError("loyalty program codes and numbers cannot be blank")

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.family_name}"

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "passengerId": self.passenger_id,
                "type": self.passenger_type.value,
                "givenName": self.given_name,
                "familyName": self.family_name,
                "fullName": self.full_name,
                "dateOfBirth": self.date_of_birth.isoformat() if self.date_of_birth else None,
                "travelerProfileId": self.traveler_profile_id,
                "loyaltyPrograms": dict(self.loyalty_programs),
            }
        )


@dataclass(frozen=True)
class BaggageSelection:
    """Baggage selection scoped to a passenger and optionally to a segment."""

    passenger_id: str
    selection_type: BaggageSelectionType
    baggage_id: str = field(default_factory=lambda: f"baggage_{uuid4().hex}")
    segment_id: Optional[str] = None
    quantity: int = 1
    weight_kg: Optional[float] = None
    description: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.baggage_id:
            raise ValueError("baggage_id is required")
        if not self.passenger_id:
            raise ValueError("baggage passenger_id is required")
        if self.segment_id is not None and not self.segment_id:
            raise ValueError("baggage segment_id cannot be blank")
        if self.quantity <= 0:
            raise ValueError("baggage quantity must be positive")
        if self.weight_kg is not None and self.weight_kg <= 0:
            raise ValueError("baggage weight_kg must be positive")
        if self.description is not None:
            object.__setattr__(self, "description", _normalize_optional_text("baggage description", self.description))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "baggageId": self.baggage_id,
                "passengerId": self.passenger_id,
                "segmentId": self.segment_id,
                "type": self.selection_type.value,
                "quantity": self.quantity,
                "weightKg": self.weight_kg,
                "description": self.description,
            }
        )


@dataclass(frozen=True)
class SpecialRequest:
    """Special service request scoped to a passenger and optionally segment."""

    passenger_id: str
    request_type: SpecialRequestType
    code: str
    request_id: str = field(default_factory=lambda: f"request_{uuid4().hex}")
    segment_id: Optional[str] = None
    description: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.request_id:
            raise ValueError("request_id is required")
        if not self.passenger_id:
            raise ValueError("request passenger_id is required")
        if self.segment_id is not None and not self.segment_id:
            raise ValueError("request segment_id cannot be blank")
        object.__setattr__(self, "code", _normalize_required_text("request code", self.code).upper())
        if self.description is not None:
            object.__setattr__(self, "description", _normalize_optional_text("request description", self.description))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "requestId": self.request_id,
                "passengerId": self.passenger_id,
                "segmentId": self.segment_id,
                "type": self.request_type.value,
                "code": self.code,
                "description": self.description,
            }
        )


@dataclass(frozen=True)
class BookingStatusHistoryEntry:
    """Append-only booking status transition record."""

    status: BookingStatus
    changed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    history_id: str = field(default_factory=lambda: f"booking_status_{uuid4().hex}")
    reason: Optional[str] = None
    actor: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.history_id:
            raise ValueError("history_id is required")
        _require_aware("changed_at", self.changed_at)
        if self.reason is not None:
            object.__setattr__(self, "reason", _normalize_optional_text("status reason", self.reason))
        if self.actor is not None:
            object.__setattr__(self, "actor", _normalize_optional_text("status actor", self.actor))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "historyId": self.history_id,
                "status": self.status.value,
                "changedAt": _format_datetime(self.changed_at),
                "reason": self.reason,
                "actor": self.actor,
            }
        )


@dataclass(frozen=True)
class BookingRecord:
    """Aggregate persisted for one customer booking."""

    booking_reference: str
    customer_identifier: str
    contact_details: BookingContactDetails
    passengers: Sequence[BookingPassenger]
    booking_id: str = field(default_factory=lambda: f"booking_{uuid4().hex}")
    itinerary_segments: Sequence[BookingItinerarySegment] = field(default_factory=tuple)
    baggage_selections: Sequence[BaggageSelection] = field(default_factory=tuple)
    special_requests: Sequence[SpecialRequest] = field(default_factory=tuple)
    status_history: Sequence[BookingStatusHistoryEntry] = field(default_factory=tuple)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.booking_id:
            raise ValueError("booking_id is required")
        object.__setattr__(self, "booking_reference", _normalize_booking_reference(self.booking_reference))
        object.__setattr__(self, "customer_identifier", _normalize_required_text("customer_identifier", self.customer_identifier))
        _require_aware("created_at", self.created_at)
        _require_aware("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("booking updated_at cannot be before created_at")
        object.__setattr__(self, "passengers", tuple(self.passengers))
        object.__setattr__(self, "itinerary_segments", tuple(sorted(self.itinerary_segments, key=lambda segment: segment.sequence)))
        object.__setattr__(self, "baggage_selections", tuple(self.baggage_selections))
        object.__setattr__(self, "special_requests", tuple(self.special_requests))
        history = tuple(self.status_history) or (BookingStatusHistoryEntry(BookingStatus.PENDING, changed_at=self.created_at),)
        object.__setattr__(self, "status_history", history)
        self.validate()

    @property
    def status(self) -> BookingStatus:
        return self.status_history[-1].status

    def validate(self) -> None:
        if not self.passengers:
            raise ValueError("booking requires at least one passenger")
        passenger_ids = _ensure_unique("passenger_id", (passenger.passenger_id for passenger in self.passengers))
        segment_ids = _ensure_unique("segment_id", (segment.segment_id for segment in self.itinerary_segments))
        _ensure_unique("segment sequence", (str(segment.sequence) for segment in self.itinerary_segments))
        for baggage in self.baggage_selections:
            if baggage.passenger_id not in passenger_ids:
                raise ValueError("baggage passenger_id must reference a booking passenger")
            if baggage.segment_id is not None and baggage.segment_id not in segment_ids:
                raise ValueError("baggage segment_id must reference a booking segment")
        for request in self.special_requests:
            if request.passenger_id not in passenger_ids:
                raise ValueError("request passenger_id must reference a booking passenger")
            if request.segment_id is not None and request.segment_id not in segment_ids:
                raise ValueError("request segment_id must reference a booking segment")
        changed_times = [entry.changed_at for entry in self.status_history]
        if changed_times != sorted(changed_times):
            raise ValueError("status_history must be chronological")

    def with_status(self, entry: BookingStatusHistoryEntry, *, updated_at: Optional[datetime] = None) -> "BookingRecord":
        return replace(
            self,
            status_history=(*self.status_history, entry),
            updated_at=updated_at or entry.changed_at,
        )

    def to_dict(self) -> JsonObject:
        return {
            "bookingId": self.booking_id,
            "bookingReference": self.booking_reference,
            "customerIdentifier": self.customer_identifier,
            "status": self.status.value,
            "contactDetails": self.contact_details.to_dict(),
            "passengers": [passenger.to_dict() for passenger in self.passengers],
            "itinerarySegments": [segment.to_dict() for segment in self.itinerary_segments],
            "baggageSelections": [baggage.to_dict() for baggage in self.baggage_selections],
            "specialRequests": [request.to_dict() for request in self.special_requests],
            "statusHistory": [entry.to_dict() for entry in self.status_history],
            "createdAt": _format_datetime(self.created_at),
            "updatedAt": _format_datetime(self.updated_at),
        }


class BookingRepository(Protocol):
    """Persistence contract for booking records."""

    def save(self, booking: BookingRecord) -> BookingRecord:
        """Create or replace a booking record."""

    def get(self, booking_id: str) -> Optional[BookingRecord]:
        """Return one booking by internal id."""

    def find_by_reference(self, booking_reference: str) -> Optional[BookingRecord]:
        """Return one booking by customer-facing booking reference."""

    def find_by_customer_identifier(self, customer_identifier: str) -> Sequence[BookingRecord]:
        """Return bookings for one customer identifier."""

    def append_status(self, booking_id: str, entry: BookingStatusHistoryEntry) -> BookingRecord:
        """Append one immutable status-history entry to a booking."""


class InMemoryBookingRepository(BookingRepository):
    """Thread-safe in-memory booking repository for tests and local adapters."""

    def __init__(self, *, bookings: Iterable[BookingRecord] = ()) -> None:
        self._bookings: MutableMapping[str, BookingRecord] = {}
        self._bookings_by_reference: MutableMapping[str, str] = {}
        self._bookings_by_customer: MutableMapping[str, List[str]] = {}
        self._lock = RLock()
        for booking in bookings:
            self.save(booking)

    def save(self, booking: BookingRecord) -> BookingRecord:
        booking.validate()
        with self._lock:
            existing_booking_id = self._bookings_by_reference.get(booking.booking_reference)
            if existing_booking_id is not None and existing_booking_id != booking.booking_id:
                raise ValueError("booking_reference already exists")
            previous = self._bookings.get(booking.booking_id)
            if previous is not None and len(booking.status_history) < len(previous.status_history):
                raise ValueError("status_history is append-only")
            if previous is not None and booking.status_history[: len(previous.status_history)] != previous.status_history:
                raise ValueError("status_history is append-only")
            self._bookings[booking.booking_id] = booking
            self._bookings_by_reference[booking.booking_reference] = booking.booking_id
            customer_bookings = self._bookings_by_customer.setdefault(booking.customer_identifier, [])
            if booking.booking_id not in customer_bookings:
                customer_bookings.append(booking.booking_id)
            return booking

    def get(self, booking_id: str) -> Optional[BookingRecord]:
        with self._lock:
            return self._bookings.get(booking_id)

    def find_by_reference(self, booking_reference: str) -> Optional[BookingRecord]:
        with self._lock:
            booking_id = self._bookings_by_reference.get(_normalize_booking_reference(booking_reference))
            return self._bookings.get(booking_id) if booking_id else None

    def find_by_customer_identifier(self, customer_identifier: str) -> Sequence[BookingRecord]:
        with self._lock:
            booking_ids = self._bookings_by_customer.get(_normalize_required_text("customer_identifier", customer_identifier), [])
            return tuple(self._bookings[booking_id] for booking_id in booking_ids)

    def append_status(self, booking_id: str, entry: BookingStatusHistoryEntry) -> BookingRecord:
        with self._lock:
            booking = self._bookings.get(booking_id)
            if booking is None:
                raise KeyError("booking was not found")
            if entry.changed_at < booking.status_history[-1].changed_at:
                raise ValueError("status history entries must be appended chronologically")
            updated = booking.with_status(entry)
            self._bookings[booking_id] = updated
            return updated


def _ensure_unique(field_name: str, values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"booking {field_name} values must be unique")
        seen.add(value)
    return seen


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_airport_code(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError("airport code must be a three-letter IATA code")
    return code


def _normalize_airline_code(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 2 or not code.isalnum():
        raise ValueError("airline code must be a two-character IATA code")
    return code


def _normalize_booking_reference(value: str) -> str:
    reference = value.strip().upper()
    if len(reference) < 5 or not reference.isalnum():
        raise ValueError("booking_reference must be at least five alphanumeric characters")
    return reference


def _normalize_email(value: str) -> str:
    email = value.strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("contact email must be valid")
    return email


def _normalize_optional_text(field_name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    return normalized


def _normalize_required_text(field_name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _require_aware(field_name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _without_none(values: Mapping[str, object]) -> JsonObject:
    return {key: value for key, value in values.items() if value is not None}
