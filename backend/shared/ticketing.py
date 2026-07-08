"""Ticket, invoice, document metadata, and issuance persistence records.

The models in this module capture post-booking issuance artifacts independently
from any transport, ORM, GDS, or document storage provider.  The in-memory
repository is used by tests/local adapters and enforces the persistence
invariants expected by ticketing workflows: globally unique ticket numbers,
coupons bound to flight segments, invoices tied to confirmed payments, document
lookup by booking reference, and append-only issuance status history.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from threading import RLock
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Protocol, Sequence, Tuple
from uuid import uuid4

JsonObject = Dict[str, object]


class IssuanceStatus(str, Enum):
    """Canonical lifecycle states for ticket/document issuance."""

    PENDING = "pending"
    ISSUED = "issued"
    FAILED = "failed"
    VOIDED = "voided"
    REFUNDED = "refunded"


class TicketCouponStatus(str, Enum):
    """Provider status for one ticket coupon/flight segment entitlement."""

    OPEN = "open"
    CHECKED_IN = "checked_in"
    FLOWN = "flown"
    EXCHANGED = "exchanged"
    VOIDED = "voided"
    REFUNDED = "refunded"


class InvoicePaymentStatus(str, Enum):
    """Payment confirmation state captured on an invoice record."""

    CONFIRMED = "confirmed"
    CAPTURED = "captured"
    SETTLED = "settled"


class DocumentType(str, Enum):
    """Document metadata categories persisted for booking artifacts."""

    E_TICKET = "e_ticket"
    INVOICE = "invoice"
    RECEIPT = "receipt"
    ITINERARY = "itinerary"
    OTHER = "other"


@dataclass(frozen=True)
class TicketCoupon:
    """Flight coupon associated with exactly one booked itinerary segment."""

    coupon_number: int
    segment_id: str
    origin_airport_code: str
    destination_airport_code: str
    departure_at: datetime
    marketing_airline_code: str
    flight_number: str
    coupon_id: str = field(default_factory=lambda: f"coupon_{uuid4().hex}")
    status: TicketCouponStatus = TicketCouponStatus.OPEN
    fare_basis_code: Optional[str] = None
    booking_class: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.coupon_id:
            raise ValueError("coupon_id is required")
        if self.coupon_number <= 0:
            raise ValueError("coupon_number must be positive")
        object.__setattr__(self, "segment_id", _normalize_required_text("segment_id", self.segment_id))
        object.__setattr__(self, "origin_airport_code", _normalize_airport_code(self.origin_airport_code))
        object.__setattr__(self, "destination_airport_code", _normalize_airport_code(self.destination_airport_code))
        if self.origin_airport_code == self.destination_airport_code:
            raise ValueError("coupon origin and destination must differ")
        _require_aware("departure_at", self.departure_at)
        object.__setattr__(self, "marketing_airline_code", _normalize_airline_code(self.marketing_airline_code))
        object.__setattr__(self, "flight_number", _normalize_required_text("flight_number", self.flight_number).upper())
        if self.fare_basis_code is not None:
            object.__setattr__(self, "fare_basis_code", _normalize_optional_text("fare_basis_code", self.fare_basis_code).upper())
        if self.booking_class is not None:
            object.__setattr__(self, "booking_class", _normalize_optional_text("booking_class", self.booking_class).upper())

    @property
    def flight_designator(self) -> str:
        return f"{self.marketing_airline_code}{self.flight_number}"

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "couponId": self.coupon_id,
                "couponNumber": self.coupon_number,
                "segmentId": self.segment_id,
                "originAirportCode": self.origin_airport_code,
                "destinationAirportCode": self.destination_airport_code,
                "departureAt": _format_datetime(self.departure_at),
                "marketingAirlineCode": self.marketing_airline_code,
                "flightNumber": self.flight_number,
                "flightDesignator": self.flight_designator,
                "status": self.status.value,
                "fareBasisCode": self.fare_basis_code,
                "bookingClass": self.booking_class,
            }
        )


@dataclass(frozen=True)
class IssuanceStatusHistoryEntry:
    """Append-only issuance status transition record."""

    status: IssuanceStatus
    changed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    history_id: str = field(default_factory=lambda: f"issuance_status_{uuid4().hex}")
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
class ElectronicTicketRecord:
    """Persisted e-ticket aggregate for one passenger on one booking."""

    ticket_number: str
    booking_id: str
    booking_reference: str
    passenger_id: str
    validating_airline_code: str
    coupons: Sequence[TicketCoupon]
    ticket_id: str = field(default_factory=lambda: f"ticket_{uuid4().hex}")
    issuance_history: Sequence[IssuanceStatusHistoryEntry] = field(default_factory=tuple)
    issued_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.ticket_id:
            raise ValueError("ticket_id is required")
        object.__setattr__(self, "ticket_number", _normalize_ticket_number(self.ticket_number))
        object.__setattr__(self, "booking_id", _normalize_required_text("booking_id", self.booking_id))
        object.__setattr__(self, "booking_reference", _normalize_booking_reference(self.booking_reference))
        object.__setattr__(self, "passenger_id", _normalize_required_text("passenger_id", self.passenger_id))
        object.__setattr__(self, "validating_airline_code", _normalize_airline_code(self.validating_airline_code))
        object.__setattr__(self, "coupons", tuple(sorted(self.coupons, key=lambda coupon: coupon.coupon_number)))
        _require_aware("created_at", self.created_at)
        _require_aware("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("ticket updated_at cannot be before created_at")
        if self.issued_at is not None:
            _require_aware("issued_at", self.issued_at)
        history = tuple(self.issuance_history) or (IssuanceStatusHistoryEntry(IssuanceStatus.PENDING, changed_at=self.created_at),)
        object.__setattr__(self, "issuance_history", history)
        self.validate()

    @property
    def issuance_status(self) -> IssuanceStatus:
        return self.issuance_history[-1].status

    def validate(self) -> None:
        if not self.coupons:
            raise ValueError("ticket requires at least one coupon")
        _ensure_unique("coupon_id", (coupon.coupon_id for coupon in self.coupons))
        _ensure_unique("coupon_number", (str(coupon.coupon_number) for coupon in self.coupons))
        _ensure_unique("segment_id", (coupon.segment_id for coupon in self.coupons))
        changed_times = [entry.changed_at for entry in self.issuance_history]
        if changed_times != sorted(changed_times):
            raise ValueError("issuance_history must be chronological")

    def with_status(self, entry: IssuanceStatusHistoryEntry, *, updated_at: Optional[datetime] = None) -> "ElectronicTicketRecord":
        return replace(self, issuance_history=(*self.issuance_history, entry), updated_at=updated_at or entry.changed_at)

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "ticketId": self.ticket_id,
                "ticketNumber": self.ticket_number,
                "bookingId": self.booking_id,
                "bookingReference": self.booking_reference,
                "passengerId": self.passenger_id,
                "validatingAirlineCode": self.validating_airline_code,
                "issuanceStatus": self.issuance_status.value,
                "coupons": [coupon.to_dict() for coupon in self.coupons],
                "issuanceHistory": [entry.to_dict() for entry in self.issuance_history],
                "issuedAt": _format_datetime(self.issued_at) if self.issued_at else None,
                "createdAt": _format_datetime(self.created_at),
                "updatedAt": _format_datetime(self.updated_at),
            }
        )


@dataclass(frozen=True)
class ConfirmedPaymentReference:
    """Payment capture/confirmation associated with an invoice."""

    payment_intent_id: str
    payment_reference: str
    amount: Decimal
    currency: str
    status: InvoicePaymentStatus = InvoicePaymentStatus.CONFIRMED
    confirmed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "payment_intent_id", _normalize_required_text("payment_intent_id", self.payment_intent_id))
        object.__setattr__(self, "payment_reference", _normalize_required_text("payment_reference", self.payment_reference))
        object.__setattr__(self, "amount", _normalize_amount("payment amount", self.amount))
        object.__setattr__(self, "currency", _normalize_currency(self.currency))
        _require_aware("confirmed_at", self.confirmed_at)

    def to_dict(self) -> JsonObject:
        return {
            "paymentIntentId": self.payment_intent_id,
            "paymentReference": self.payment_reference,
            "amount": _format_amount(self.amount),
            "currency": self.currency,
            "status": self.status.value,
            "confirmedAt": _format_datetime(self.confirmed_at),
        }


@dataclass(frozen=True)
class InvoiceRecord:
    """Persisted invoice associated with a confirmed booking payment."""

    invoice_number: str
    booking_id: str
    booking_reference: str
    payment: ConfirmedPaymentReference
    total_amount: Decimal
    currency: str
    invoice_id: str = field(default_factory=lambda: f"invoice_{uuid4().hex}")
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    line_items: Sequence[Mapping[str, object]] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.invoice_id:
            raise ValueError("invoice_id is required")
        object.__setattr__(self, "invoice_number", _normalize_required_text("invoice_number", self.invoice_number).upper())
        object.__setattr__(self, "booking_id", _normalize_required_text("booking_id", self.booking_id))
        object.__setattr__(self, "booking_reference", _normalize_booking_reference(self.booking_reference))
        object.__setattr__(self, "total_amount", _normalize_amount("total_amount", self.total_amount))
        object.__setattr__(self, "currency", _normalize_currency(self.currency))
        _require_aware("issued_at", self.issued_at)
        object.__setattr__(self, "line_items", tuple(dict(item) for item in self.line_items))
        if self.payment.status not in {InvoicePaymentStatus.CONFIRMED, InvoicePaymentStatus.CAPTURED, InvoicePaymentStatus.SETTLED}:
            raise ValueError("invoice payment must be confirmed")
        if self.payment.amount != self.total_amount:
            raise ValueError("invoice total_amount must match confirmed payment amount")
        if self.payment.currency != self.currency:
            raise ValueError("invoice currency must match confirmed payment currency")

    def to_dict(self) -> JsonObject:
        return {
            "invoiceId": self.invoice_id,
            "invoiceNumber": self.invoice_number,
            "bookingId": self.booking_id,
            "bookingReference": self.booking_reference,
            "payment": self.payment.to_dict(),
            "totalAmount": _format_amount(self.total_amount),
            "currency": self.currency,
            "issuedAt": _format_datetime(self.issued_at),
            "lineItems": [dict(item) for item in self.line_items],
        }


@dataclass(frozen=True)
class DocumentMetadataRecord:
    """Metadata for a stored document artifact retrievable by booking reference."""

    document_id: str
    booking_id: str
    booking_reference: str
    document_type: DocumentType
    storage_uri: str
    content_type: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    related_ticket_id: Optional[str] = None
    related_invoice_id: Optional[str] = None
    checksum: Optional[str] = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "document_id", _normalize_required_text("document_id", self.document_id))
        object.__setattr__(self, "booking_id", _normalize_required_text("booking_id", self.booking_id))
        object.__setattr__(self, "booking_reference", _normalize_booking_reference(self.booking_reference))
        object.__setattr__(self, "storage_uri", _normalize_required_text("storage_uri", self.storage_uri))
        object.__setattr__(self, "content_type", _normalize_required_text("content_type", self.content_type).lower())
        _require_aware("created_at", self.created_at)
        if self.related_ticket_id is not None:
            object.__setattr__(self, "related_ticket_id", _normalize_optional_text("related_ticket_id", self.related_ticket_id))
        if self.related_invoice_id is not None:
            object.__setattr__(self, "related_invoice_id", _normalize_optional_text("related_invoice_id", self.related_invoice_id))
        if self.checksum is not None:
            object.__setattr__(self, "checksum", _normalize_optional_text("checksum", self.checksum))
        object.__setattr__(self, "metadata", {str(key): value for key, value in self.metadata.items()})

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "documentId": self.document_id,
                "bookingId": self.booking_id,
                "bookingReference": self.booking_reference,
                "documentType": self.document_type.value,
                "storageUri": self.storage_uri,
                "contentType": self.content_type,
                "createdAt": _format_datetime(self.created_at),
                "relatedTicketId": self.related_ticket_id,
                "relatedInvoiceId": self.related_invoice_id,
                "checksum": self.checksum,
                "metadata": dict(self.metadata),
            }
        )


class TicketDocumentRepository(Protocol):
    """Persistence contract for ticketing artifacts."""

    def save_ticket(self, ticket: ElectronicTicketRecord) -> ElectronicTicketRecord:
        """Create or replace an e-ticket record."""

    def get_ticket(self, ticket_id: str) -> Optional[ElectronicTicketRecord]:
        """Return one e-ticket by internal id."""

    def find_ticket_by_number(self, ticket_number: str) -> Optional[ElectronicTicketRecord]:
        """Return one e-ticket by globally unique ticket number."""

    def save_invoice(self, invoice: InvoiceRecord) -> InvoiceRecord:
        """Create or replace an invoice record."""

    def find_invoices_by_payment_intent_id(self, payment_intent_id: str) -> Sequence[InvoiceRecord]:
        """Return invoices associated with one confirmed payment intent."""

    def save_document(self, document: DocumentMetadataRecord) -> DocumentMetadataRecord:
        """Create or replace document metadata."""

    def find_documents_by_booking_reference(self, booking_reference: str) -> Sequence[DocumentMetadataRecord]:
        """Return document metadata for one booking reference."""


class InMemoryTicketDocumentRepository(TicketDocumentRepository):
    """Thread-safe in-memory repository for ticketing artifacts."""

    def __init__(
        self,
        *,
        tickets: Iterable[ElectronicTicketRecord] = (),
        invoices: Iterable[InvoiceRecord] = (),
        documents: Iterable[DocumentMetadataRecord] = (),
    ) -> None:
        self._tickets: MutableMapping[str, ElectronicTicketRecord] = {}
        self._tickets_by_number: MutableMapping[str, str] = {}
        self._invoices: MutableMapping[str, InvoiceRecord] = {}
        self._invoices_by_payment_intent: MutableMapping[str, List[str]] = {}
        self._documents: MutableMapping[str, DocumentMetadataRecord] = {}
        self._documents_by_booking_reference: MutableMapping[str, List[str]] = {}
        self._lock = RLock()
        for ticket in tickets:
            self.save_ticket(ticket)
        for invoice in invoices:
            self.save_invoice(invoice)
        for document in documents:
            self.save_document(document)

    def save_ticket(self, ticket: ElectronicTicketRecord) -> ElectronicTicketRecord:
        ticket.validate()
        with self._lock:
            owner_id = self._tickets_by_number.get(ticket.ticket_number)
            if owner_id is not None and owner_id != ticket.ticket_id:
                raise ValueError("ticket_number already exists")
            previous = self._tickets.get(ticket.ticket_id)
            if previous is not None:
                self._validate_ticket_append_only(previous, ticket)
            self._tickets[ticket.ticket_id] = ticket
            self._tickets_by_number[ticket.ticket_number] = ticket.ticket_id
            return ticket

    def get_ticket(self, ticket_id: str) -> Optional[ElectronicTicketRecord]:
        with self._lock:
            return self._tickets.get(ticket_id)

    def find_ticket_by_number(self, ticket_number: str) -> Optional[ElectronicTicketRecord]:
        with self._lock:
            ticket_id = self._tickets_by_number.get(_normalize_ticket_number(ticket_number))
            return self._tickets.get(ticket_id) if ticket_id else None

    def append_issuance_status(self, ticket_id: str, entry: IssuanceStatusHistoryEntry) -> ElectronicTicketRecord:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise KeyError("ticket was not found")
            if entry.changed_at < ticket.issuance_history[-1].changed_at:
                raise ValueError("issuance history entries must be appended chronologically")
            updated = ticket.with_status(entry)
            self._tickets[ticket_id] = updated
            return updated

    def save_invoice(self, invoice: InvoiceRecord) -> InvoiceRecord:
        with self._lock:
            self._invoices[invoice.invoice_id] = invoice
            invoice_ids = self._invoices_by_payment_intent.setdefault(invoice.payment.payment_intent_id, [])
            if invoice.invoice_id not in invoice_ids:
                invoice_ids.append(invoice.invoice_id)
            return invoice

    def get_invoice(self, invoice_id: str) -> Optional[InvoiceRecord]:
        with self._lock:
            return self._invoices.get(invoice_id)

    def find_invoices_by_payment_intent_id(self, payment_intent_id: str) -> Sequence[InvoiceRecord]:
        with self._lock:
            invoice_ids = self._invoices_by_payment_intent.get(_normalize_required_text("payment_intent_id", payment_intent_id), [])
            return tuple(self._invoices[invoice_id] for invoice_id in invoice_ids)

    def save_document(self, document: DocumentMetadataRecord) -> DocumentMetadataRecord:
        with self._lock:
            self._documents[document.document_id] = document
            document_ids = self._documents_by_booking_reference.setdefault(document.booking_reference, [])
            if document.document_id not in document_ids:
                document_ids.append(document.document_id)
            return document

    def get_document(self, document_id: str) -> Optional[DocumentMetadataRecord]:
        with self._lock:
            return self._documents.get(document_id)

    def find_documents_by_booking_reference(self, booking_reference: str) -> Sequence[DocumentMetadataRecord]:
        with self._lock:
            document_ids = self._documents_by_booking_reference.get(_normalize_booking_reference(booking_reference), [])
            return tuple(self._documents[document_id] for document_id in document_ids)

    @staticmethod
    def _validate_ticket_append_only(previous: ElectronicTicketRecord, updated: ElectronicTicketRecord) -> None:
        if len(updated.issuance_history) < len(previous.issuance_history):
            raise ValueError("issuance_history is append-only")
        if updated.issuance_history[: len(previous.issuance_history)] != previous.issuance_history:
            raise ValueError("issuance_history is append-only")


def _ensure_unique(field_name: str, values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"ticketing {field_name} values must be unique")
        seen.add(value)
    return seen


def _format_amount(value: Decimal) -> str:
    return f"{value:.2f}"


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_airline_code(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 2 or not code.isalnum():
        raise ValueError("airline code must be a two-character IATA code")
    return code


def _normalize_airport_code(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError("airport code must be a three-letter IATA code")
    return code


def _normalize_amount(field_name: str, value: Decimal) -> Decimal:
    try:
        amount = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid amount") from exc
    if amount <= Decimal("0.00"):
        raise ValueError(f"{field_name} must be positive")
    return amount


def _normalize_booking_reference(value: str) -> str:
    reference = value.strip().upper()
    if len(reference) < 5 or not reference.isalnum():
        raise ValueError("booking_reference must be at least five alphanumeric characters")
    return reference


def _normalize_currency(value: str) -> str:
    currency = value.strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("currency must be a three-letter ISO code")
    return currency


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


def _normalize_ticket_number(value: str) -> str:
    ticket_number = value.strip().replace("-", "")
    if len(ticket_number) != 13 or not ticket_number.isdigit():
        raise ValueError("ticket_number must contain 13 digits")
    return ticket_number


def _require_aware(field_name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _without_none(values: Mapping[str, object]) -> JsonObject:
    return {key: value for key, value in values.items() if value is not None}
