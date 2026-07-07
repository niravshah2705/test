"""Fare and pricing persistence models and repositories.

The pricing records in this module are framework-agnostic domain entities for
fare classes, base fares, taxes, fees, discounts, currencies, and validity
windows.  In-memory repositories provide deterministic persistence semantics for
repository tests and local adapters while keeping fare lookup constraints
explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from threading import RLock
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence
from uuid import uuid4

from backend.shared.flight_catalog import CabinClass
from backend.shared.travelers import TravelerType

JsonObject = Dict[str, object]


class PassengerType(str, Enum):
    """Passenger categories used by fare filing and pricing."""

    ADULT = "adult"
    CHILD = "child"
    INFANT = "infant"


class PriceAdjustmentType(str, Enum):
    """How a tax, fee, or discount adjusts a base fare."""

    FIXED = "fixed"
    PERCENTAGE = "percentage"


@dataclass(frozen=True)
class Currency:
    """ISO-4217 currency supported by fare pricing."""

    code: str
    name: str
    minor_units: int = 2

    def __post_init__(self) -> None:
        code = self.code.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("currency code must be a three-letter ISO-4217 code")
        if not self.name.strip():
            raise ValueError("currency name is required")
        if self.minor_units < 0:
            raise ValueError("currency minor_units cannot be negative")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "name", self.name.strip())

    def to_dict(self) -> JsonObject:
        return {"code": self.code, "name": self.name, "minorUnits": self.minor_units}


@dataclass(frozen=True)
class FareValidityWindow:
    """Travel and sale date boundaries that make a fare active."""

    travel_from: date
    travel_until: date
    sale_from: date
    sale_until: date

    def __post_init__(self) -> None:
        if self.travel_from > self.travel_until:
            raise ValueError("travel_from cannot be after travel_until")
        if self.sale_from > self.sale_until:
            raise ValueError("sale_from cannot be after sale_until")

    def is_active(self, *, travel_date: date, sale_date: date) -> bool:
        return self.travel_from <= travel_date <= self.travel_until and self.sale_from <= sale_date <= self.sale_until

    def to_dict(self) -> JsonObject:
        return {
            "travelFrom": self.travel_from.isoformat(),
            "travelUntil": self.travel_until.isoformat(),
            "saleFrom": self.sale_from.isoformat(),
            "saleUntil": self.sale_until.isoformat(),
        }


@dataclass(frozen=True)
class FareClass:
    """Fare filing class scoped to a flight cabin."""

    code: str
    cabin: CabinClass
    name: str
    fare_class_id: str = field(default_factory=lambda: f"fare_class_{uuid4().hex}")
    refundable: bool = False
    changeable: bool = False

    def __post_init__(self) -> None:
        if not self.fare_class_id:
            raise ValueError("fare_class_id is required")
        code = self.code.strip().upper()
        if not code or not code.isalnum():
            raise ValueError("fare class code is required")
        if not self.name.strip():
            raise ValueError("fare class name is required")
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "name", self.name.strip())

    def to_dict(self) -> JsonObject:
        return {
            "fareClassId": self.fare_class_id,
            "code": self.code,
            "cabin": self.cabin.value,
            "name": self.name,
            "refundable": self.refundable,
            "changeable": self.changeable,
        }


@dataclass(frozen=True)
class BaseFare:
    """Published base fare for a flight, cabin, passenger type, and currency."""

    flight_id: str
    fare_class_id: str
    cabin: CabinClass
    passenger_type: PassengerType
    amount: Decimal
    currency_code: str
    validity: FareValidityWindow
    base_fare_id: str = field(default_factory=lambda: f"base_fare_{uuid4().hex}")
    priority: int = 0

    def __post_init__(self) -> None:
        if not self.base_fare_id:
            raise ValueError("base_fare_id is required")
        if not self.flight_id:
            raise ValueError("flight_id is required")
        if not self.fare_class_id:
            raise ValueError("base fare fare_class_id is required")
        object.__setattr__(self, "amount", _normalize_money(self.amount, "amount"))
        object.__setattr__(self, "currency_code", _normalize_currency_code(self.currency_code))

    def is_active(self, *, travel_date: date, sale_date: date) -> bool:
        return self.validity.is_active(travel_date=travel_date, sale_date=sale_date)

    def to_dict(self) -> JsonObject:
        return {
            "baseFareId": self.base_fare_id,
            "flightId": self.flight_id,
            "fareClassId": self.fare_class_id,
            "cabin": self.cabin.value,
            "passengerType": self.passenger_type.value,
            "amount": str(self.amount),
            "currencyCode": self.currency_code,
            "validity": self.validity.to_dict(),
            "priority": self.priority,
        }


@dataclass(frozen=True)
class Tax:
    """Tax applicable to a base fare in a specific currency and validity window."""

    code: str
    name: str
    amount: Decimal
    currency_code: str
    validity: FareValidityWindow
    tax_id: str = field(default_factory=lambda: f"tax_{uuid4().hex}")
    adjustment_type: PriceAdjustmentType = PriceAdjustmentType.FIXED

    def __post_init__(self) -> None:
        _validate_adjustment(self.tax_id, "tax_id", self.code, self.name, self.amount, self.currency_code, self.adjustment_type)
        object.__setattr__(self, "code", self.code.strip().upper())
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "amount", _normalize_money(self.amount, "amount"))
        object.__setattr__(self, "currency_code", _normalize_currency_code(self.currency_code))

    def to_dict(self) -> JsonObject:
        return _adjustment_to_dict(
            record_id_name="taxId",
            record_id=self.tax_id,
            code=self.code,
            name=self.name,
            amount=self.amount,
            currency_code=self.currency_code,
            adjustment_type=self.adjustment_type,
            validity=self.validity,
        )


@dataclass(frozen=True)
class Fee:
    """Provider or service fee applicable to a base fare."""

    code: str
    name: str
    amount: Decimal
    currency_code: str
    validity: FareValidityWindow
    fee_id: str = field(default_factory=lambda: f"fee_{uuid4().hex}")
    adjustment_type: PriceAdjustmentType = PriceAdjustmentType.FIXED

    def __post_init__(self) -> None:
        _validate_adjustment(self.fee_id, "fee_id", self.code, self.name, self.amount, self.currency_code, self.adjustment_type)
        object.__setattr__(self, "code", self.code.strip().upper())
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "amount", _normalize_money(self.amount, "amount"))
        object.__setattr__(self, "currency_code", _normalize_currency_code(self.currency_code))

    def to_dict(self) -> JsonObject:
        return _adjustment_to_dict(
            record_id_name="feeId",
            record_id=self.fee_id,
            code=self.code,
            name=self.name,
            amount=self.amount,
            currency_code=self.currency_code,
            adjustment_type=self.adjustment_type,
            validity=self.validity,
        )


@dataclass(frozen=True)
class Discount:
    """Discount applicable to a base fare."""

    code: str
    name: str
    amount: Decimal
    currency_code: str
    validity: FareValidityWindow
    discount_id: str = field(default_factory=lambda: f"discount_{uuid4().hex}")
    adjustment_type: PriceAdjustmentType = PriceAdjustmentType.FIXED

    def __post_init__(self) -> None:
        _validate_adjustment(
            self.discount_id, "discount_id", self.code, self.name, self.amount, self.currency_code, self.adjustment_type
        )
        object.__setattr__(self, "code", self.code.strip().upper())
        object.__setattr__(self, "name", self.name.strip())
        object.__setattr__(self, "amount", _normalize_money(self.amount, "amount"))
        object.__setattr__(self, "currency_code", _normalize_currency_code(self.currency_code))

    def to_dict(self) -> JsonObject:
        return _adjustment_to_dict(
            record_id_name="discountId",
            record_id=self.discount_id,
            code=self.code,
            name=self.name,
            amount=self.amount,
            currency_code=self.currency_code,
            adjustment_type=self.adjustment_type,
            validity=self.validity,
        )


class FarePricingRepository(Protocol):
    """Persistence contract for fare pricing records."""

    def save_currency(self, currency: Currency) -> Currency:
        """Create or replace a currency."""

    def get_currency(self, code: str) -> Optional[Currency]:
        """Return one currency by ISO code."""

    def save_fare_class(self, fare_class: FareClass) -> FareClass:
        """Create or replace a fare class, enforcing code/cabin uniqueness."""

    def get_fare_class(self, fare_class_id: str) -> Optional[FareClass]:
        """Return one fare class by id."""

    def find_fare_class(self, code: str, cabin: CabinClass) -> Optional[FareClass]:
        """Return one fare class by code and cabin."""

    def save_base_fare(self, base_fare: BaseFare) -> BaseFare:
        """Create or replace a base fare."""

    def get_base_fare(self, base_fare_id: str) -> Optional[BaseFare]:
        """Return one base fare by id."""

    def find_active_base_fares(
        self,
        *,
        flight_id: str,
        cabin: CabinClass,
        passenger_type: PassengerType,
        travel_date: date,
        sale_date: date,
        currency_code: Optional[str] = None,
    ) -> Sequence[BaseFare]:
        """Return active base fares matching lookup constraints."""

    def save_tax(self, tax: Tax) -> Tax:
        """Create or replace a tax."""

    def save_fee(self, fee: Fee) -> Fee:
        """Create or replace a fee."""

    def save_discount(self, discount: Discount) -> Discount:
        """Create or replace a discount."""


class InMemoryFarePricingRepository(FarePricingRepository):
    """Thread-safe in-memory fare pricing repository for tests and local use."""

    def __init__(
        self,
        *,
        currencies: Iterable[Currency] = (),
        fare_classes: Iterable[FareClass] = (),
        base_fares: Iterable[BaseFare] = (),
        taxes: Iterable[Tax] = (),
        fees: Iterable[Fee] = (),
        discounts: Iterable[Discount] = (),
    ) -> None:
        self._currencies: MutableMapping[str, Currency] = {}
        self._fare_classes: MutableMapping[str, FareClass] = {}
        self._fare_classes_by_identity: MutableMapping[tuple[str, CabinClass], str] = {}
        self._base_fares: MutableMapping[str, BaseFare] = {}
        self._base_fares_by_flight: MutableMapping[str, List[str]] = {}
        self._taxes: MutableMapping[str, Tax] = {}
        self._fees: MutableMapping[str, Fee] = {}
        self._discounts: MutableMapping[str, Discount] = {}
        self._lock = RLock()
        for currency in currencies:
            self.save_currency(currency)
        for fare_class in fare_classes:
            self.save_fare_class(fare_class)
        for base_fare in base_fares:
            self.save_base_fare(base_fare)
        for tax in taxes:
            self.save_tax(tax)
        for fee in fees:
            self.save_fee(fee)
        for discount in discounts:
            self.save_discount(discount)

    def save_currency(self, currency: Currency) -> Currency:
        with self._lock:
            self._currencies[currency.code] = currency
            return currency

    def get_currency(self, code: str) -> Optional[Currency]:
        with self._lock:
            return self._currencies.get(_normalize_currency_code(code))

    def save_fare_class(self, fare_class: FareClass) -> FareClass:
        with self._lock:
            identity = (fare_class.code, fare_class.cabin)
            existing_fare_class_id = self._fare_classes_by_identity.get(identity)
            if existing_fare_class_id is not None and existing_fare_class_id != fare_class.fare_class_id:
                raise ValueError("fare class already exists for code/cabin")
            self._fare_classes[fare_class.fare_class_id] = fare_class
            self._fare_classes_by_identity[identity] = fare_class.fare_class_id
            return fare_class

    def get_fare_class(self, fare_class_id: str) -> Optional[FareClass]:
        with self._lock:
            return self._fare_classes.get(fare_class_id)

    def find_fare_class(self, code: str, cabin: CabinClass) -> Optional[FareClass]:
        with self._lock:
            fare_class_id = self._fare_classes_by_identity.get((code.strip().upper(), cabin))
            return self._fare_classes.get(fare_class_id) if fare_class_id else None

    def save_base_fare(self, base_fare: BaseFare) -> BaseFare:
        with self._lock:
            if base_fare.currency_code not in self._currencies:
                raise ValueError("base fare currency_code must reference an existing currency")
            fare_class = self._fare_classes.get(base_fare.fare_class_id)
            if fare_class is None:
                raise ValueError("base fare fare_class_id must reference an existing fare class")
            if fare_class.cabin != base_fare.cabin:
                raise ValueError("base fare cabin must match fare class cabin")
            self._base_fares[base_fare.base_fare_id] = base_fare
            flight_fares = self._base_fares_by_flight.setdefault(base_fare.flight_id, [])
            if base_fare.base_fare_id not in flight_fares:
                flight_fares.append(base_fare.base_fare_id)
            return base_fare

    def get_base_fare(self, base_fare_id: str) -> Optional[BaseFare]:
        with self._lock:
            return self._base_fares.get(base_fare_id)

    def find_active_base_fares(
        self,
        *,
        flight_id: str,
        cabin: CabinClass,
        passenger_type: PassengerType,
        travel_date: date,
        sale_date: date,
        currency_code: Optional[str] = None,
    ) -> Sequence[BaseFare]:
        requested_currency = _normalize_currency_code(currency_code) if currency_code is not None else None
        with self._lock:
            fares = (
                self._base_fares[base_fare_id]
                for base_fare_id in self._base_fares_by_flight.get(flight_id, [])
                if base_fare_id in self._base_fares
            )
            matches = [
                fare
                for fare in fares
                if fare.cabin == cabin
                and fare.passenger_type == passenger_type
                and (requested_currency is None or fare.currency_code == requested_currency)
                and fare.is_active(travel_date=travel_date, sale_date=sale_date)
            ]
            return tuple(sorted(matches, key=lambda fare: (-fare.priority, fare.amount, fare.base_fare_id)))

    def save_tax(self, tax: Tax) -> Tax:
        with self._lock:
            self._require_currency(tax.currency_code, "tax currency_code must reference an existing currency")
            self._taxes[tax.tax_id] = tax
            return tax

    def save_fee(self, fee: Fee) -> Fee:
        with self._lock:
            self._require_currency(fee.currency_code, "fee currency_code must reference an existing currency")
            self._fees[fee.fee_id] = fee
            return fee

    def save_discount(self, discount: Discount) -> Discount:
        with self._lock:
            self._require_currency(discount.currency_code, "discount currency_code must reference an existing currency")
            self._discounts[discount.discount_id] = discount
            return discount

    def get_tax(self, tax_id: str) -> Optional[Tax]:
        with self._lock:
            return self._taxes.get(tax_id)

    def get_fee(self, fee_id: str) -> Optional[Fee]:
        with self._lock:
            return self._fees.get(fee_id)

    def get_discount(self, discount_id: str) -> Optional[Discount]:
        with self._lock:
            return self._discounts.get(discount_id)

    def _require_currency(self, currency_code: str, message: str) -> None:
        if currency_code not in self._currencies:
            raise ValueError(message)


def passenger_type_from_traveler_type(traveler_type: TravelerType) -> PassengerType:
    """Map traveler profile age categories to pricing passenger types."""

    return PassengerType(traveler_type.value)


def _validate_adjustment(
    record_id: str,
    record_id_field: str,
    code: str,
    name: str,
    amount: Decimal,
    currency_code: str,
    adjustment_type: PriceAdjustmentType,
) -> None:
    if not record_id:
        raise ValueError(f"{record_id_field} is required")
    normalized_code = code.strip().upper()
    if not normalized_code:
        raise ValueError("adjustment code is required")
    if not name.strip():
        raise ValueError("adjustment name is required")
    _normalize_money(amount, "amount")
    _normalize_currency_code(currency_code)
    if adjustment_type == PriceAdjustmentType.PERCENTAGE and amount > Decimal("100"):
        raise ValueError("percentage adjustment amount cannot exceed 100")


def _adjustment_to_dict(
    *,
    record_id_name: str,
    record_id: str,
    code: str,
    name: str,
    amount: Decimal,
    currency_code: str,
    adjustment_type: PriceAdjustmentType,
    validity: FareValidityWindow,
) -> JsonObject:
    return {
        record_id_name: record_id,
        "code": code.strip().upper(),
        "name": name.strip(),
        "amount": str(_normalize_money(amount, "amount")),
        "currencyCode": _normalize_currency_code(currency_code),
        "adjustmentType": adjustment_type.value,
        "validity": validity.to_dict(),
    }


def _normalize_currency_code(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError("currency code must be a three-letter ISO-4217 code")
    return code


def _normalize_money(value: Decimal, field_name: str) -> Decimal:
    try:
        amount = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a decimal amount") from exc
    if amount < 0:
        raise ValueError(f"{field_name} cannot be negative")
    return amount
