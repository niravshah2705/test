"""User account and traveler profile domain entities.

These models are storage- and framework-agnostic so booking flows, profile
management controllers, and persistence adapters can share the same contract.
PII is deliberately grouped in dedicated value objects to make masking,
redaction, and restricted persistence boundaries explicit.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from enum import Enum
from threading import RLock
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence
from uuid import uuid4

JsonObject = Dict[str, object]


class TravelerType(str, Enum):
    """Passenger age categories supported by traveler profiles."""

    ADULT = "adult"
    CHILD = "child"
    INFANT = "infant"


class IdentityDocumentType(str, Enum):
    """Structured identity document types accepted by passenger profiles."""

    PASSPORT = "passport"
    NATIONAL_ID = "national_id"
    DRIVERS_LICENSE = "drivers_license"
    OTHER = "other"


class TravelCabinPreference(str, Enum):
    """Common cabin choices that can be saved with a profile."""

    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


@dataclass(frozen=True)
class UserAccount:
    """Non-PII account record that owns traveler profiles."""

    user_id: str = field(default_factory=lambda: f"user_{uuid4().hex}")
    auth_subject_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    disabled_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ValueError("user_id is required")
        _require_aware("created_at", self.created_at)
        if self.disabled_at is not None:
            _require_aware("disabled_at", self.disabled_at)

    @property
    def is_active(self) -> bool:
        return self.disabled_at is None


@dataclass(frozen=True)
class ContactDetails:
    """PII contact fields for a traveler profile.

    Keep this object separate from non-sensitive profile metadata so stores and
    serializers can apply masking or restricted access to one field group.
    """

    email: Optional[str] = None
    phone_number: Optional[str] = None
    address_line1: Optional[str] = None
    address_line2: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    postal_code: Optional[str] = None
    country_code: Optional[str] = None

    def __post_init__(self) -> None:
        if self.email is not None:
            object.__setattr__(self, "email", _normalize_email(self.email))
        if self.country_code is not None:
            object.__setattr__(self, "country_code", self.country_code.strip().upper())

    def masked(self) -> "ContactDetails":
        """Return a copy suitable for non-privileged views."""

        return ContactDetails(
            email=_mask_email(self.email),
            phone_number=_mask_phone(self.phone_number),
            address_line1=_mask_present(self.address_line1),
            address_line2=_mask_present(self.address_line2),
            city=self.city,
            region=self.region,
            postal_code=_mask_present(self.postal_code),
            country_code=self.country_code,
        )

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "email": self.email,
                "phoneNumber": self.phone_number,
                "addressLine1": self.address_line1,
                "addressLine2": self.address_line2,
                "city": self.city,
                "region": self.region,
                "postalCode": self.postal_code,
                "countryCode": self.country_code,
            }
        )


@dataclass(frozen=True)
class PassengerIdentityDocument:
    """Optional structured passenger identity document fields.

    No field other than ``document_type`` is required because providers and trip
    types vary in what identity information they collect.
    """

    document_type: IdentityDocumentType
    document_number: Optional[str] = None
    issuing_country_code: Optional[str] = None
    nationality_country_code: Optional[str] = None
    expires_on: Optional[date] = None
    issued_on: Optional[date] = None
    issuing_authority: Optional[str] = None

    def __post_init__(self) -> None:
        if self.issuing_country_code is not None:
            object.__setattr__(self, "issuing_country_code", self.issuing_country_code.strip().upper())
        if self.nationality_country_code is not None:
            object.__setattr__(self, "nationality_country_code", self.nationality_country_code.strip().upper())
        if self.issued_on is not None and self.expires_on is not None and self.issued_on > self.expires_on:
            raise ValueError("identity document issued_on cannot be after expires_on")

    def masked(self) -> "PassengerIdentityDocument":
        """Return a copy with sensitive document numbers redacted."""

        return replace(self, document_number=_mask_document_number(self.document_number))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "type": self.document_type.value,
                "documentNumber": self.document_number,
                "issuingCountryCode": self.issuing_country_code,
                "nationalityCountryCode": self.nationality_country_code,
                "expiresOn": self.expires_on.isoformat() if self.expires_on else None,
                "issuedOn": self.issued_on.isoformat() if self.issued_on else None,
                "issuingAuthority": self.issuing_authority,
            }
        )


@dataclass(frozen=True)
class TravelerPII:
    """PII bundle isolated from traveler metadata for restricted access."""

    given_name: str
    family_name: str
    date_of_birth: Optional[date] = None
    contact_details: Optional[ContactDetails] = None
    identity_documents: Sequence[PassengerIdentityDocument] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.given_name.strip():
            raise ValueError("given_name is required")
        if not self.family_name.strip():
            raise ValueError("family_name is required")
        object.__setattr__(self, "given_name", self.given_name.strip())
        object.__setattr__(self, "family_name", self.family_name.strip())
        object.__setattr__(self, "identity_documents", tuple(self.identity_documents))

    def masked(self) -> "TravelerPII":
        return TravelerPII(
            given_name=_mask_name(self.given_name),
            family_name=_mask_name(self.family_name),
            date_of_birth=self.date_of_birth,
            contact_details=self.contact_details.masked() if self.contact_details else None,
            identity_documents=tuple(document.masked() for document in self.identity_documents),
        )

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "givenName": self.given_name,
                "familyName": self.family_name,
                "dateOfBirth": self.date_of_birth.isoformat() if self.date_of_birth else None,
                "contactDetails": self.contact_details.to_dict() if self.contact_details else None,
                "identityDocuments": [document.to_dict() for document in self.identity_documents],
            }
        )


@dataclass(frozen=True)
class TravelPreferences:
    """Saved non-sensitive travel preferences for a traveler profile."""

    preferred_cabin: Optional[TravelCabinPreference] = None
    seat_preference: Optional[str] = None
    meal_preference: Optional[str] = None
    loyalty_programs: Mapping[str, str] = field(default_factory=dict)
    accessibility_requests: Sequence[str] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "loyalty_programs", dict(self.loyalty_programs))
        object.__setattr__(self, "accessibility_requests", tuple(self.accessibility_requests))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "preferredCabin": self.preferred_cabin.value if self.preferred_cabin else None,
                "seatPreference": self.seat_preference,
                "mealPreference": self.meal_preference,
                "loyaltyPrograms": dict(self.loyalty_programs),
                "accessibilityRequests": list(self.accessibility_requests),
            }
        )


@dataclass(frozen=True)
class TravelerProfile:
    """Traveler profile owned by exactly one user account."""

    owner_user_id: str
    traveler_type: TravelerType
    pii: TravelerPII
    profile_id: str = field(default_factory=lambda: f"traveler_{uuid4().hex}")
    label: Optional[str] = None
    preferences: TravelPreferences = field(default_factory=TravelPreferences)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id is required")
        if not self.owner_user_id:
            raise ValueError("owner_user_id is required")
        _require_aware("created_at", self.created_at)
        _require_aware("updated_at", self.updated_at)

    def masked(self) -> "TravelerProfile":
        """Return the profile with PII fields masked for unrestricted reads."""

        return replace(self, pii=self.pii.masked())

    def to_dict(self, *, include_pii: bool = False, masked: bool = True) -> JsonObject:
        pii = self.pii if include_pii and not masked else self.pii.masked()
        return _without_none(
            {
                "profileId": self.profile_id,
                "ownerUserId": self.owner_user_id,
                "travelerType": self.traveler_type.value,
                "label": self.label,
                "preferences": self.preferences.to_dict(),
                "pii": pii.to_dict(),
                "createdAt": self.created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "updatedAt": self.updated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            }
        )


class TravelerProfileStore(Protocol):
    """Persistence contract for traveler profiles owned by user accounts."""

    def save(self, profile: TravelerProfile) -> TravelerProfile:
        """Create or replace a traveler profile."""

    def get(self, owner_user_id: str, profile_id: str, *, include_pii: bool = False) -> Optional[TravelerProfile]:
        """Return one owned profile, masking PII unless explicitly requested."""

    def list_for_user(self, owner_user_id: str, *, include_pii: bool = False) -> Sequence[TravelerProfile]:
        """Return all profiles owned by one user account."""


class InMemoryTravelerProfileStore(TravelerProfileStore):
    """Thread-safe profile store for tests and local adapters."""

    def __init__(self, profiles: Iterable[TravelerProfile] = ()) -> None:
        self._profiles: MutableMapping[str, TravelerProfile] = {}
        self._by_owner: MutableMapping[str, List[str]] = {}
        self._lock = RLock()
        for profile in profiles:
            self.save(profile)

    def save(self, profile: TravelerProfile) -> TravelerProfile:
        with self._lock:
            is_new = profile.profile_id not in self._profiles
            self._profiles[profile.profile_id] = profile
            owner_profiles = self._by_owner.setdefault(profile.owner_user_id, [])
            if is_new:
                owner_profiles.append(profile.profile_id)
            elif profile.profile_id not in owner_profiles:
                owner_profiles.append(profile.profile_id)
            return profile

    def get(self, owner_user_id: str, profile_id: str, *, include_pii: bool = False) -> Optional[TravelerProfile]:
        with self._lock:
            profile = self._profiles.get(profile_id)
            if profile is None or profile.owner_user_id != owner_user_id:
                return None
            return profile if include_pii else profile.masked()

    def list_for_user(self, owner_user_id: str, *, include_pii: bool = False) -> Sequence[TravelerProfile]:
        with self._lock:
            profiles = [self._profiles[profile_id] for profile_id in self._by_owner.get(owner_user_id, [])]
            if include_pii:
                return tuple(profiles)
            return tuple(profile.masked() for profile in profiles)


def _require_aware(field_name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("email cannot be blank")
    return normalized


def _mask_name(value: str) -> str:
    return f"{value[:1]}***" if value else "***"


def _mask_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    local, _, domain = value.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def _mask_phone(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(character for character in value if character.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def _mask_document_number(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    if len(stripped) <= 4:
        return "***"
    return f"***{stripped[-4:]}"


def _mask_present(value: Optional[str]) -> Optional[str]:
    return "***" if value else None


def _without_none(values: Mapping[str, object]) -> JsonObject:
    return {key: value for key, value in values.items() if value is not None}
