"""Flight catalog persistence models and repositories.

The catalog types in this module are framework-agnostic domain records for
provider-sourced schedules and operational inventory.  In-memory repositories
provide deterministic persistence semantics for tests and local adapters while
making catalog constraints explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timezone
from enum import Enum
from threading import RLock
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple
from uuid import uuid4

JsonObject = Dict[str, object]


class CabinClass(str, Enum):
    """Cabins that can be offered on a scheduled flight."""

    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class OperatingStatus(str, Enum):
    """Operational lifecycle for a scheduled flight instance."""

    SCHEDULED = "scheduled"
    ON_TIME = "on_time"
    DELAYED = "delayed"
    BOARDING = "boarding"
    DEPARTED = "departed"
    ARRIVED = "arrived"
    CANCELLED = "cancelled"
    DIVERTED = "diverted"


@dataclass(frozen=True)
class Airport:
    """Airport identified by a three-letter IATA code."""

    code: str
    name: str
    city: str
    country_code: str
    timezone_name: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _normalize_airport_code(self.code))
        if not self.name.strip():
            raise ValueError("airport name is required")
        if not self.city.strip():
            raise ValueError("airport city is required")
        country_code = self.country_code.strip().upper()
        if len(country_code) != 2 or not country_code.isalpha():
            raise ValueError("airport country_code must be a two-letter ISO code")
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "city", self.city.strip())
        object.__setattr__(self, "country_code", country_code)
        if self.timezone_name is not None:
            timezone_name = self.timezone_name.strip()
            if not timezone_name:
                raise ValueError("airport timezone_name cannot be blank")
            object.__setattr__(self, "timezone_name", timezone_name)

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "code": self.code,
                "name": self.name,
                "city": self.city,
                "countryCode": self.country_code,
                "timezoneName": self.timezone_name,
            }
        )


@dataclass(frozen=True)
class Airline:
    """Operating or marketing airline identified by IATA code."""

    code: str
    name: str
    airline_id: str = field(default_factory=lambda: f"airline_{uuid4().hex}")

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _normalize_airline_code(self.code))
        if not self.airline_id:
            raise ValueError("airline_id is required")
        if not self.name.strip():
            raise ValueError("airline name is required")
        object.__setattr__(self, "name", self.name.strip())

    def to_dict(self) -> JsonObject:
        return {"airlineId": self.airline_id, "code": self.code, "name": self.name}


@dataclass(frozen=True)
class Aircraft:
    """Aircraft equipment type used for schedule planning."""

    model_code: str
    manufacturer: str
    model_name: str
    seat_capacity: Optional[int] = None
    aircraft_id: str = field(default_factory=lambda: f"aircraft_{uuid4().hex}")

    def __post_init__(self) -> None:
        if not self.aircraft_id:
            raise ValueError("aircraft_id is required")
        model_code = self.model_code.strip().upper()
        if not model_code:
            raise ValueError("aircraft model_code is required")
        if not self.manufacturer.strip():
            raise ValueError("aircraft manufacturer is required")
        if not self.model_name.strip():
            raise ValueError("aircraft model_name is required")
        if self.seat_capacity is not None and self.seat_capacity <= 0:
            raise ValueError("aircraft seat_capacity must be positive")
        object.__setattr__(self, "model_code", model_code)
        object.__setattr__(self, "manufacturer", self.manufacturer.strip())
        object.__setattr__(self, "model_name", self.model_name.strip())

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "aircraftId": self.aircraft_id,
                "modelCode": self.model_code,
                "manufacturer": self.manufacturer,
                "modelName": self.model_name,
                "seatCapacity": self.seat_capacity,
            }
        )


@dataclass(frozen=True)
class Route:
    """Directional airport pair served by one or more schedules."""

    origin_airport_code: str
    destination_airport_code: str
    route_id: str = field(default_factory=lambda: f"route_{uuid4().hex}")
    distance_km: Optional[int] = None

    def __post_init__(self) -> None:
        if not self.route_id:
            raise ValueError("route_id is required")
        origin = _normalize_airport_code(self.origin_airport_code)
        destination = _normalize_airport_code(self.destination_airport_code)
        if origin == destination:
            raise ValueError("route origin and destination must differ")
        if self.distance_km is not None and self.distance_km <= 0:
            raise ValueError("route distance_km must be positive")
        object.__setattr__(self, "origin_airport_code", origin)
        object.__setattr__(self, "destination_airport_code", destination)

    @property
    def airport_pair(self) -> Tuple[str, str]:
        return (self.origin_airport_code, self.destination_airport_code)

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "routeId": self.route_id,
                "originAirportCode": self.origin_airport_code,
                "destinationAirportCode": self.destination_airport_code,
                "distanceKm": self.distance_km,
            }
        )


@dataclass(frozen=True)
class FlightSchedule:
    """Published recurring flight schedule for a route."""

    airline_code: str
    flight_number: str
    route_id: str
    departure_time: time
    arrival_time: time
    effective_from: date
    effective_until: date
    operating_days: Sequence[int]
    cabin_classes: Sequence[CabinClass] = field(default_factory=lambda: (CabinClass.ECONOMY,))
    aircraft_id: Optional[str] = None
    schedule_id: str = field(default_factory=lambda: f"schedule_{uuid4().hex}")

    def __post_init__(self) -> None:
        if not self.schedule_id:
            raise ValueError("schedule_id is required")
        if not self.route_id:
            raise ValueError("schedule route_id is required")
        object.__setattr__(self, "airline_code", _normalize_airline_code(self.airline_code))
        flight_number = self.flight_number.strip().upper()
        if not flight_number:
            raise ValueError("flight_number is required")
        if self.effective_from > self.effective_until:
            raise ValueError("schedule effective_from cannot be after effective_until")
        days = tuple(sorted(set(self.operating_days)))
        if not days:
            raise ValueError("schedule operating_days are required")
        if any(day < 0 or day > 6 for day in days):
            raise ValueError("schedule operating_days must use Python weekday values 0..6")
        cabins = tuple(self.cabin_classes)
        if not cabins:
            raise ValueError("schedule cabin_classes are required")
        object.__setattr__(self, "flight_number", flight_number)
        object.__setattr__(self, "operating_days", days)
        object.__setattr__(self, "cabin_classes", cabins)

    @property
    def flight_designator(self) -> str:
        return f"{self.airline_code}{self.flight_number}"

    def operates_on(self, service_date: date) -> bool:
        return self.effective_from <= service_date <= self.effective_until and service_date.weekday() in self.operating_days

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "scheduleId": self.schedule_id,
                "airlineCode": self.airline_code,
                "flightNumber": self.flight_number,
                "flightDesignator": self.flight_designator,
                "routeId": self.route_id,
                "aircraftId": self.aircraft_id,
                "departureTime": self.departure_time.isoformat(),
                "arrivalTime": self.arrival_time.isoformat(),
                "effectiveFrom": self.effective_from.isoformat(),
                "effectiveUntil": self.effective_until.isoformat(),
                "operatingDays": list(self.operating_days),
                "cabinClasses": [cabin.value for cabin in self.cabin_classes],
            }
        )


@dataclass(frozen=True)
class FlightInstance:
    """Concrete dated operation of a flight schedule."""

    schedule_id: str
    service_date: date
    status: OperatingStatus = OperatingStatus.SCHEDULED
    departure_gate: Optional[str] = None
    arrival_gate: Optional[str] = None
    instance_id: str = field(default_factory=lambda: f"flight_instance_{uuid4().hex}")
    status_updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.instance_id:
            raise ValueError("instance_id is required")
        if not self.schedule_id:
            raise ValueError("flight instance schedule_id is required")
        _require_aware("status_updated_at", self.status_updated_at)
        if self.departure_gate is not None:
            departure_gate = self.departure_gate.strip().upper()
            if not departure_gate:
                raise ValueError("departure_gate cannot be blank")
            object.__setattr__(self, "departure_gate", departure_gate)
        if self.arrival_gate is not None:
            arrival_gate = self.arrival_gate.strip().upper()
            if not arrival_gate:
                raise ValueError("arrival_gate cannot be blank")
            object.__setattr__(self, "arrival_gate", arrival_gate)

    @property
    def identity(self) -> Tuple[str, date]:
        return (self.schedule_id, self.service_date)

    def with_status(self, status: OperatingStatus, *, updated_at: Optional[datetime] = None) -> "FlightInstance":
        return replace(self, status=status, status_updated_at=updated_at or datetime.now(timezone.utc))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "instanceId": self.instance_id,
                "scheduleId": self.schedule_id,
                "serviceDate": self.service_date.isoformat(),
                "status": self.status.value,
                "departureGate": self.departure_gate,
                "arrivalGate": self.arrival_gate,
                "statusUpdatedAt": self.status_updated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        )


class FlightCatalogRepository(Protocol):
    """Persistence contract for flight catalog records."""

    def save_airport(self, airport: Airport) -> Airport:
        """Create or replace an airport."""

    def get_airport(self, code: str) -> Optional[Airport]:
        """Return one airport by IATA code."""

    def save_airline(self, airline: Airline) -> Airline:
        """Create or replace an airline."""

    def get_airline(self, code: str) -> Optional[Airline]:
        """Return one airline by IATA code."""

    def save_aircraft(self, aircraft: Aircraft) -> Aircraft:
        """Create or replace aircraft equipment."""

    def get_aircraft(self, aircraft_id: str) -> Optional[Aircraft]:
        """Return one aircraft equipment record."""

    def save_route(self, route: Route) -> Route:
        """Create a route, enforcing directional airport-pair uniqueness."""

    def get_route(self, route_id: str) -> Optional[Route]:
        """Return one route by id."""

    def find_route(self, origin_airport_code: str, destination_airport_code: str) -> Optional[Route]:
        """Return one route by directional airport pair."""

    def save_schedule(self, schedule: FlightSchedule) -> FlightSchedule:
        """Create or replace a published flight schedule."""

    def get_schedule(self, schedule_id: str) -> Optional[FlightSchedule]:
        """Return one schedule by id."""

    def find_schedules_for_route(self, origin_airport_code: str, destination_airport_code: str) -> Sequence[FlightSchedule]:
        """Return schedules serving a directional airport pair."""

    def find_schedules_for_route_date(
        self, origin_airport_code: str, destination_airport_code: str, service_date: date
    ) -> Sequence[FlightSchedule]:
        """Return schedules serving a directional airport pair on a service date."""

    def save_flight_instance(self, instance: FlightInstance) -> FlightInstance:
        """Create a dated flight instance, enforcing schedule/date identity."""

    def get_flight_instance(self, instance_id: str) -> Optional[FlightInstance]:
        """Return one flight instance by id."""

    def find_flight_instance(self, schedule_id: str, service_date: date) -> Optional[FlightInstance]:
        """Return one flight instance by schedule/date identity."""

    def update_flight_status(
        self, schedule_id: str, service_date: date, status: OperatingStatus, *, updated_at: Optional[datetime] = None
    ) -> FlightInstance:
        """Update a flight instance operating status."""


class InMemoryFlightCatalogRepository(FlightCatalogRepository):
    """Thread-safe in-memory flight catalog repository for tests and local use."""

    def __init__(
        self,
        *,
        airports: Iterable[Airport] = (),
        airlines: Iterable[Airline] = (),
        aircraft: Iterable[Aircraft] = (),
        routes: Iterable[Route] = (),
        schedules: Iterable[FlightSchedule] = (),
        flight_instances: Iterable[FlightInstance] = (),
    ) -> None:
        self._airports: MutableMapping[str, Airport] = {}
        self._airlines: MutableMapping[str, Airline] = {}
        self._aircraft: MutableMapping[str, Aircraft] = {}
        self._routes: MutableMapping[str, Route] = {}
        self._routes_by_pair: MutableMapping[Tuple[str, str], str] = {}
        self._schedules: MutableMapping[str, FlightSchedule] = {}
        self._schedules_by_route: MutableMapping[str, List[str]] = {}
        self._instances: MutableMapping[str, FlightInstance] = {}
        self._instances_by_identity: MutableMapping[Tuple[str, date], str] = {}
        self._lock = RLock()
        for airport in airports:
            self.save_airport(airport)
        for airline in airlines:
            self.save_airline(airline)
        for aircraft_record in aircraft:
            self.save_aircraft(aircraft_record)
        for route in routes:
            self.save_route(route)
        for schedule in schedules:
            self.save_schedule(schedule)
        for instance in flight_instances:
            self.save_flight_instance(instance)

    def save_airport(self, airport: Airport) -> Airport:
        with self._lock:
            self._airports[airport.code] = airport
            return airport

    def get_airport(self, code: str) -> Optional[Airport]:
        with self._lock:
            return self._airports.get(_normalize_airport_code(code))

    def save_airline(self, airline: Airline) -> Airline:
        with self._lock:
            self._airlines[airline.code] = airline
            return airline

    def get_airline(self, code: str) -> Optional[Airline]:
        with self._lock:
            return self._airlines.get(_normalize_airline_code(code))

    def save_aircraft(self, aircraft: Aircraft) -> Aircraft:
        with self._lock:
            self._aircraft[aircraft.aircraft_id] = aircraft
            return aircraft

    def get_aircraft(self, aircraft_id: str) -> Optional[Aircraft]:
        with self._lock:
            return self._aircraft.get(aircraft_id)

    def save_route(self, route: Route) -> Route:
        with self._lock:
            existing_route_id = self._routes_by_pair.get(route.airport_pair)
            if existing_route_id is not None and existing_route_id != route.route_id:
                raise ValueError("route already exists for airport pair")
            self._routes[route.route_id] = route
            self._routes_by_pair[route.airport_pair] = route.route_id
            return route

    def get_route(self, route_id: str) -> Optional[Route]:
        with self._lock:
            return self._routes.get(route_id)

    def find_route(self, origin_airport_code: str, destination_airport_code: str) -> Optional[Route]:
        with self._lock:
            route_id = self._routes_by_pair.get(
                (_normalize_airport_code(origin_airport_code), _normalize_airport_code(destination_airport_code))
            )
            return self._routes.get(route_id) if route_id else None

    def save_schedule(self, schedule: FlightSchedule) -> FlightSchedule:
        with self._lock:
            if schedule.route_id not in self._routes:
                raise ValueError("schedule route_id must reference an existing route")
            self._schedules[schedule.schedule_id] = schedule
            route_schedules = self._schedules_by_route.setdefault(schedule.route_id, [])
            if schedule.schedule_id not in route_schedules:
                route_schedules.append(schedule.schedule_id)
            return schedule

    def get_schedule(self, schedule_id: str) -> Optional[FlightSchedule]:
        with self._lock:
            return self._schedules.get(schedule_id)

    def find_schedules_for_route(self, origin_airport_code: str, destination_airport_code: str) -> Sequence[FlightSchedule]:
        with self._lock:
            route = self.find_route(origin_airport_code, destination_airport_code)
            if route is None:
                return tuple()
            return tuple(self._schedules[schedule_id] for schedule_id in self._schedules_by_route.get(route.route_id, []))

    def find_schedules_for_route_date(
        self, origin_airport_code: str, destination_airport_code: str, service_date: date
    ) -> Sequence[FlightSchedule]:
        return tuple(
            schedule
            for schedule in self.find_schedules_for_route(origin_airport_code, destination_airport_code)
            if schedule.operates_on(service_date)
        )

    def save_flight_instance(self, instance: FlightInstance) -> FlightInstance:
        with self._lock:
            if instance.schedule_id not in self._schedules:
                raise ValueError("flight instance schedule_id must reference an existing schedule")
            existing_instance_id = self._instances_by_identity.get(instance.identity)
            if existing_instance_id is not None and existing_instance_id != instance.instance_id:
                raise ValueError("flight instance already exists for schedule/date")
            self._instances[instance.instance_id] = instance
            self._instances_by_identity[instance.identity] = instance.instance_id
            return instance

    def get_flight_instance(self, instance_id: str) -> Optional[FlightInstance]:
        with self._lock:
            return self._instances.get(instance_id)

    def find_flight_instance(self, schedule_id: str, service_date: date) -> Optional[FlightInstance]:
        with self._lock:
            instance_id = self._instances_by_identity.get((schedule_id, service_date))
            return self._instances.get(instance_id) if instance_id else None

    def update_flight_status(
        self, schedule_id: str, service_date: date, status: OperatingStatus, *, updated_at: Optional[datetime] = None
    ) -> FlightInstance:
        with self._lock:
            instance = self.find_flight_instance(schedule_id, service_date)
            if instance is None:
                raise KeyError("flight instance was not found")
            updated = instance.with_status(status, updated_at=updated_at)
            self._instances[updated.instance_id] = updated
            self._instances_by_identity[updated.identity] = updated.instance_id
            return updated


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


def _require_aware(field_name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _without_none(values: Mapping[str, object]) -> JsonObject:
    return {key: value for key, value in values.items() if value is not None}
