"""Customer profile persistence entities and repositories.

The models in this module represent the saved customer profile data needed by
booking flows: account-owned profiles, saved travelers, loyalty identifiers,
contact methods, and consent flags.  They are framework-agnostic domain records
with an in-memory repository implementation suitable for tests and local
adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from threading import RLock
from typing import Dict, Iterable, Mapping, MutableMapping, Optional, Protocol, Sequence
from uuid import uuid4

JsonObject = Dict[str, object]


class ContactMethodType(str, Enum):
    """Supported customer contact channels."""

    EMAIL = "email"
    PHONE = "phone"


class ConsentFlagType(str, Enum):
    """Consent purposes recorded on a customer profile."""

    MARKETING_EMAIL = "marketing_email"
    MARKETING_SMS = "marketing_sms"
    PRIVACY_POLICY = "privacy_policy"
    TERMS_OF_SERVICE = "terms_of_service"


class SavedTravelerType(str, Enum):
    """Passenger age categories for saved traveler records."""

    ADULT = "adult"
    CHILD = "child"
    INFANT = "infant"


@dataclass(frozen=True)
class LoyaltyIdentifier:
    """Loyalty program identifier saved for a customer or traveler."""

    program_code: str
    member_id: str
    loyalty_id: str = field(default_factory=lambda: f"loyalty_{uuid4().hex}")
    provider: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.loyalty_id:
            raise ValueError("loyalty_id is required")
        object.__setattr__(self, "program_code", _normalize_code("program_code", self.program_code))
        object.__setattr__(self, "member_id", _normalize_required_text("member_id", self.member_id))
        if self.provider is not None:
            object.__setattr__(self, "provider", _normalize_optional_text("provider", self.provider))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "loyaltyId": self.loyalty_id,
                "programCode": self.program_code,
                "memberId": self.member_id,
                "provider": self.provider,
            }
        )


@dataclass(frozen=True)
class ContactMethod:
    """Normalized profile contact method with primary flag."""

    method_type: ContactMethodType
    value: str
    contact_id: str = field(default_factory=lambda: f"contact_{uuid4().hex}")
    is_primary: bool = False
    verified_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.contact_id:
            raise ValueError("contact_id is required")
        if self.method_type == ContactMethodType.EMAIL:
            object.__setattr__(self, "value", _normalize_email(self.value))
        elif self.method_type == ContactMethodType.PHONE:
            object.__setattr__(self, "value", _normalize_phone(self.value))
        else:
            raise ValueError("unsupported contact method type")
        if self.verified_at is not None:
            _require_aware("verified_at", self.verified_at)

    @property
    def uniqueness_key(self) -> tuple[ContactMethodType, str]:
        return (self.method_type, self.value)

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "contactId": self.contact_id,
                "type": self.method_type.value,
                "value": self.value,
                "isPrimary": self.is_primary,
                "verifiedAt": _format_datetime(self.verified_at) if self.verified_at else None,
            }
        )


@dataclass(frozen=True)
class ConsentFlag:
    """Timestamped consent decision for a customer profile."""

    consent_type: ConsentFlagType
    granted: bool
    consent_id: str = field(default_factory=lambda: f"consent_{uuid4().hex}")
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.consent_id:
            raise ValueError("consent_id is required")
        _require_aware("captured_at", self.captured_at)
        if self.source is not None:
            object.__setattr__(self, "source", _normalize_optional_text("source", self.source))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "consentId": self.consent_id,
                "type": self.consent_type.value,
                "granted": self.granted,
                "capturedAt": _format_datetime(self.captured_at),
                "source": self.source,
            }
        )


@dataclass(frozen=True)
class SavedTraveler:
    """Reusable traveler details owned by a customer profile."""

    given_name: str
    family_name: str
    traveler_type: SavedTravelerType = SavedTravelerType.ADULT
    traveler_id: str = field(default_factory=lambda: f"traveler_{uuid4().hex}")
    date_of_birth: Optional[date] = None
    loyalty_identifiers: Sequence[LoyaltyIdentifier] = field(default_factory=tuple)
    is_default: bool = False

    def __post_init__(self) -> None:
        if not self.traveler_id:
            raise ValueError("traveler_id is required")
        object.__setattr__(self, "given_name", _normalize_required_text("given_name", self.given_name))
        object.__setattr__(self, "family_name", _normalize_required_text("family_name", self.family_name))
        object.__setattr__(self, "loyalty_identifiers", tuple(self.loyalty_identifiers))
        _validate_unique_loyalty_identifiers(self.loyalty_identifiers)
        if self.traveler_type in {SavedTravelerType.CHILD, SavedTravelerType.INFANT} and self.date_of_birth is None:
            raise ValueError("child and infant travelers require date_of_birth")

    @property
    def full_name(self) -> str:
        return f"{self.given_name} {self.family_name}"

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "travelerId": self.traveler_id,
                "type": self.traveler_type.value,
                "givenName": self.given_name,
                "familyName": self.family_name,
                "fullName": self.full_name,
                "dateOfBirth": self.date_of_birth.isoformat() if self.date_of_birth else None,
                "loyaltyIdentifiers": [identifier.to_dict() for identifier in self.loyalty_identifiers],
                "isDefault": self.is_default,
            }
        )


@dataclass(frozen=True)
class BookingProfileData:
    """Profile projection used to prefill booking flows."""

    profile_id: str
    customer_id: str
    display_name: Optional[str]
    primary_email: Optional[str]
    primary_phone: Optional[str]
    travelers: Sequence[SavedTraveler]
    loyalty_identifiers: Sequence[LoyaltyIdentifier]
    consent_flags: Mapping[ConsentFlagType, bool]

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "profileId": self.profile_id,
                "customerId": self.customer_id,
                "displayName": self.display_name,
                "primaryEmail": self.primary_email,
                "primaryPhone": self.primary_phone,
                "travelers": [traveler.to_dict() for traveler in self.travelers],
                "loyaltyIdentifiers": [identifier.to_dict() for identifier in self.loyalty_identifiers],
                "consentFlags": {key.value: value for key, value in self.consent_flags.items()},
            }
        )


@dataclass(frozen=True)
class CustomerProfile:
    """Persisted customer profile with booking-prefill data."""

    customer_id: str
    profile_id: str = field(default_factory=lambda: f"profile_{uuid4().hex}")
    display_name: Optional[str] = None
    saved_travelers: Sequence[SavedTraveler] = field(default_factory=tuple)
    loyalty_identifiers: Sequence[LoyaltyIdentifier] = field(default_factory=tuple)
    contact_methods: Sequence[ContactMethod] = field(default_factory=tuple)
    consent_flags: Sequence[ConsentFlag] = field(default_factory=tuple)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id is required")
        object.__setattr__(self, "customer_id", _normalize_required_text("customer_id", self.customer_id))
        if self.display_name is not None:
            object.__setattr__(self, "display_name", _normalize_optional_text("display_name", self.display_name))
        object.__setattr__(self, "saved_travelers", tuple(self.saved_travelers))
        object.__setattr__(self, "loyalty_identifiers", tuple(self.loyalty_identifiers))
        object.__setattr__(self, "contact_methods", tuple(self.contact_methods))
        object.__setattr__(self, "consent_flags", tuple(self.consent_flags))
        _require_aware("created_at", self.created_at)
        _require_aware("updated_at", self.updated_at)
        _validate_unique_ids("traveler_id", (traveler.traveler_id for traveler in self.saved_travelers))
        _validate_unique_ids("loyalty_id", (identifier.loyalty_id for identifier in self.loyalty_identifiers))
        _validate_unique_ids("contact_id", (contact.contact_id for contact in self.contact_methods))
        _validate_unique_ids("consent_id", (consent.consent_id for consent in self.consent_flags))
        _validate_unique_loyalty_identifiers(self.loyalty_identifiers)
        _validate_unique_contacts(self.contact_methods)
        _validate_primary_contacts(self.contact_methods)
        _validate_unique_consent_types(self.consent_flags)

    @property
    def primary_email(self) -> Optional[str]:
        return self._primary_contact_value(ContactMethodType.EMAIL)

    @property
    def primary_phone(self) -> Optional[str]:
        return self._primary_contact_value(ContactMethodType.PHONE)

    def with_saved_traveler(self, traveler: SavedTraveler) -> "CustomerProfile":
        travelers = [existing for existing in self.saved_travelers if existing.traveler_id != traveler.traveler_id]
        if traveler.is_default:
            travelers = [replace(existing, is_default=False) for existing in travelers]
        travelers.append(traveler)
        return replace(self, saved_travelers=tuple(travelers), updated_at=datetime.now(timezone.utc))

    def booking_data(self) -> BookingProfileData:
        return BookingProfileData(
            profile_id=self.profile_id,
            customer_id=self.customer_id,
            display_name=self.display_name,
            primary_email=self.primary_email,
            primary_phone=self.primary_phone,
            travelers=self.saved_travelers,
            loyalty_identifiers=self.loyalty_identifiers,
            consent_flags={flag.consent_type: flag.granted for flag in self.consent_flags},
        )

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "profileId": self.profile_id,
                "customerId": self.customer_id,
                "displayName": self.display_name,
                "savedTravelers": [traveler.to_dict() for traveler in self.saved_travelers],
                "loyaltyIdentifiers": [identifier.to_dict() for identifier in self.loyalty_identifiers],
                "contactMethods": [contact.to_dict() for contact in self.contact_methods],
                "consentFlags": [flag.to_dict() for flag in self.consent_flags],
                "createdAt": _format_datetime(self.created_at),
                "updatedAt": _format_datetime(self.updated_at),
            }
        )

    def _primary_contact_value(self, method_type: ContactMethodType) -> Optional[str]:
        primary = next((contact.value for contact in self.contact_methods if contact.method_type == method_type and contact.is_primary), None)
        if primary is not None:
            return primary
        first = next((contact.value for contact in self.contact_methods if contact.method_type == method_type), None)
        return first


class CustomerProfileRepository(Protocol):
    """Persistence contract for customer profiles."""

    def create(self, profile: CustomerProfile) -> CustomerProfile:
        """Persist a new customer profile."""

    def get(self, profile_id: str) -> Optional[CustomerProfile]:
        """Return a profile by id, if present."""

    def get_for_customer(self, customer_id: str) -> Optional[CustomerProfile]:
        """Return the profile owned by a customer, if present."""

    def update(self, profile: CustomerProfile) -> CustomerProfile:
        """Replace an existing customer profile."""

    def delete(self, profile_id: str) -> bool:
        """Delete a profile by id."""

    def booking_data(self, profile_id: str) -> Optional[BookingProfileData]:
        """Return booking-prefill projection for a profile."""


class InMemoryCustomerProfileRepository(CustomerProfileRepository):
    """Thread-safe in-memory customer profile repository."""

    def __init__(self, profiles: Iterable[CustomerProfile] = ()) -> None:
        self._profiles: MutableMapping[str, CustomerProfile] = {}
        self._by_customer: MutableMapping[str, str] = {}
        self._contact_index: MutableMapping[tuple[ContactMethodType, str], str] = {}
        self._lock = RLock()
        for profile in profiles:
            self.create(profile)

    def create(self, profile: CustomerProfile) -> CustomerProfile:
        with self._lock:
            if profile.profile_id in self._profiles:
                raise ValueError("profile_id already exists")
            if profile.customer_id in self._by_customer:
                raise ValueError("customer already has a profile")
            self._ensure_contacts_available(profile)
            self._store(profile)
            return profile

    def get(self, profile_id: str) -> Optional[CustomerProfile]:
        with self._lock:
            return self._profiles.get(profile_id)

    def get_for_customer(self, customer_id: str) -> Optional[CustomerProfile]:
        with self._lock:
            profile_id = self._by_customer.get(_normalize_required_text("customer_id", customer_id))
            return self._profiles.get(profile_id) if profile_id else None

    def update(self, profile: CustomerProfile) -> CustomerProfile:
        with self._lock:
            current = self._profiles.get(profile.profile_id)
            if current is None:
                raise KeyError("profile does not exist")
            if profile.customer_id != current.customer_id:
                raise ValueError("profile customer_id cannot change")
            self._ensure_contacts_available(profile, replacing_profile_id=profile.profile_id)
            self._remove_indexes(current)
            self._store(profile)
            return profile

    def delete(self, profile_id: str) -> bool:
        with self._lock:
            profile = self._profiles.pop(profile_id, None)
            if profile is None:
                return False
            self._by_customer.pop(profile.customer_id, None)
            self._remove_indexes(profile)
            return True

    def booking_data(self, profile_id: str) -> Optional[BookingProfileData]:
        with self._lock:
            profile = self._profiles.get(profile_id)
            return profile.booking_data() if profile else None

    def _store(self, profile: CustomerProfile) -> None:
        self._profiles[profile.profile_id] = profile
        self._by_customer[profile.customer_id] = profile.profile_id
        for contact in profile.contact_methods:
            self._contact_index[contact.uniqueness_key] = profile.profile_id

    def _remove_indexes(self, profile: CustomerProfile) -> None:
        for contact in profile.contact_methods:
            self._contact_index.pop(contact.uniqueness_key, None)

    def _ensure_contacts_available(self, profile: CustomerProfile, *, replacing_profile_id: Optional[str] = None) -> None:
        for contact in profile.contact_methods:
            owner_profile_id = self._contact_index.get(contact.uniqueness_key)
            if owner_profile_id is not None and owner_profile_id != replacing_profile_id:
                raise ValueError("contact method already belongs to another profile")


def _validate_unique_contacts(contacts: Sequence[ContactMethod]) -> None:
    seen: set[tuple[ContactMethodType, str]] = set()
    for contact in contacts:
        if contact.uniqueness_key in seen:
            raise ValueError("contact methods must be unique per type and value")
        seen.add(contact.uniqueness_key)


def _validate_primary_contacts(contacts: Sequence[ContactMethod]) -> None:
    primary_counts: Dict[ContactMethodType, int] = {}
    for contact in contacts:
        if contact.is_primary:
            primary_counts[contact.method_type] = primary_counts.get(contact.method_type, 0) + 1
    if any(count > 1 for count in primary_counts.values()):
        raise ValueError("only one primary contact is allowed per contact type")


def _validate_unique_loyalty_identifiers(identifiers: Sequence[LoyaltyIdentifier]) -> None:
    seen: set[str] = set()
    for identifier in identifiers:
        if identifier.program_code in seen:
            raise ValueError("loyalty program identifiers must be unique by program_code")
        seen.add(identifier.program_code)


def _validate_unique_consent_types(consents: Sequence[ConsentFlag]) -> None:
    seen: set[ConsentFlagType] = set()
    for consent in consents:
        if consent.consent_type in seen:
            raise ValueError("consent flags must be unique by type")
        seen.add(consent.consent_type)


def _validate_unique_ids(field_name: str, values: Iterable[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"{field_name} values must be unique")
        seen.add(value)


def _normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or "@" not in normalized:
        raise ValueError("email must be valid")
    return normalized


def _normalize_phone(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("phone cannot be blank")
    digits = "".join(character for character in normalized if character.isdigit())
    if len(digits) < 7:
        raise ValueError("phone must include at least seven digits")
    return normalized


def _normalize_code(field_name: str, value: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_required_text(field_name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_optional_text(field_name: str, value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    return normalized


def _require_aware(field_name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _without_none(values: Mapping[str, object]) -> JsonObject:
    return {key: value for key, value in values.items() if value is not None}
