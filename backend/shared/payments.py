"""Payment persistence domain records and repositories.

The models in this module capture payment intent state independently from any
payment service provider, ORM, or transport layer.  The in-memory repository is
used by tests/local adapters and enforces the persistence invariants expected by
payment workflows: one idempotency key per operation, append-only status
history, multiple attempts per booking, and immutable external provider
references.
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


class PaymentIntentStatus(str, Enum):
    """Canonical persisted payment intent lifecycle states."""

    REQUIRES_PAYMENT_METHOD = "requires_payment_method"
    REQUIRES_AUTHORIZATION = "requires_authorization"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    PARTIALLY_CAPTURED = "partially_captured"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaymentAttemptStatus(str, Enum):
    """Lifecycle states for one provider payment attempt."""

    STARTED = "started"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PaymentOperation(str, Enum):
    """Idempotent payment operations persisted by the repository."""

    CREATE_INTENT = "create_intent"
    AUTHORIZE = "authorize"
    CAPTURE = "capture"
    REFUND = "refund"
    CANCEL = "cancel"


@dataclass(frozen=True)
class ExternalPaymentReference:
    """Immutable provider reference captured from an external payment system."""

    provider: str
    reference_type: str
    reference_id: str
    raw_response: Mapping[str, object] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _normalize_required_text("provider", self.provider))
        object.__setattr__(self, "reference_type", _normalize_required_text("reference_type", self.reference_type))
        object.__setattr__(self, "reference_id", _normalize_required_text("reference_id", self.reference_id))
        _require_aware("created_at", self.created_at)
        object.__setattr__(self, "raw_response", _freeze_mapping(self.raw_response))

    @property
    def natural_key(self) -> Tuple[str, str, str]:
        return (self.provider, self.reference_type, self.reference_id)

    def to_dict(self) -> JsonObject:
        return {
            "provider": self.provider,
            "referenceType": self.reference_type,
            "referenceId": self.reference_id,
            "rawResponse": dict(self.raw_response),
            "createdAt": _format_datetime(self.created_at),
        }


@dataclass(frozen=True)
class AuthorizationReference:
    """Authorization details returned by the payment provider."""

    authorization_id: str
    provider_reference: ExternalPaymentReference
    authorized_amount: Decimal
    currency: str
    authorized_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorization_id", _normalize_required_text("authorization_id", self.authorization_id))
        object.__setattr__(self, "authorized_amount", _normalize_amount("authorized_amount", self.authorized_amount))
        object.__setattr__(self, "currency", _normalize_currency(self.currency))
        _require_aware("authorized_at", self.authorized_at)
        if self.expires_at is not None:
            _require_aware("expires_at", self.expires_at)
            if self.expires_at <= self.authorized_at:
                raise ValueError("authorization expires_at must be after authorized_at")

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "authorizationId": self.authorization_id,
                "providerReference": self.provider_reference.to_dict(),
                "authorizedAmount": _format_amount(self.authorized_amount),
                "currency": self.currency,
                "authorizedAt": _format_datetime(self.authorized_at),
                "expiresAt": _format_datetime(self.expires_at) if self.expires_at else None,
            }
        )


@dataclass(frozen=True)
class CaptureResult:
    """Persisted capture result for an authorization/payment attempt."""

    capture_id: str
    provider_reference: ExternalPaymentReference
    captured_amount: Decimal
    currency: str
    captured_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "capture_id", _normalize_required_text("capture_id", self.capture_id))
        object.__setattr__(self, "captured_amount", _normalize_amount("captured_amount", self.captured_amount))
        object.__setattr__(self, "currency", _normalize_currency(self.currency))
        _require_aware("captured_at", self.captured_at)

    def to_dict(self) -> JsonObject:
        return {
            "captureId": self.capture_id,
            "providerReference": self.provider_reference.to_dict(),
            "capturedAmount": _format_amount(self.captured_amount),
            "currency": self.currency,
            "capturedAt": _format_datetime(self.captured_at),
        }


@dataclass(frozen=True)
class RefundRecord:
    """Persisted refund result associated with a payment intent."""

    refund_id: str
    provider_reference: ExternalPaymentReference
    refunded_amount: Decimal
    currency: str
    reason: Optional[str] = None
    refunded_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "refund_id", _normalize_required_text("refund_id", self.refund_id))
        object.__setattr__(self, "refunded_amount", _normalize_amount("refunded_amount", self.refunded_amount))
        object.__setattr__(self, "currency", _normalize_currency(self.currency))
        if self.reason is not None:
            object.__setattr__(self, "reason", _normalize_optional_text("refund reason", self.reason))
        _require_aware("refunded_at", self.refunded_at)

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "refundId": self.refund_id,
                "providerReference": self.provider_reference.to_dict(),
                "refundedAmount": _format_amount(self.refunded_amount),
                "currency": self.currency,
                "reason": self.reason,
                "refundedAt": _format_datetime(self.refunded_at),
            }
        )


@dataclass(frozen=True)
class PaymentStatusHistoryEntry:
    """Append-only payment intent status transition record."""

    status: PaymentIntentStatus
    changed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    history_id: str = field(default_factory=lambda: f"payment_status_{uuid4().hex}")
    reason: Optional[str] = None
    actor: Optional[str] = None
    attempt_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.history_id:
            raise ValueError("history_id is required")
        _require_aware("changed_at", self.changed_at)
        if self.reason is not None:
            object.__setattr__(self, "reason", _normalize_optional_text("status reason", self.reason))
        if self.actor is not None:
            object.__setattr__(self, "actor", _normalize_optional_text("status actor", self.actor))
        if self.attempt_id is not None:
            object.__setattr__(self, "attempt_id", _normalize_optional_text("status attempt_id", self.attempt_id))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "historyId": self.history_id,
                "status": self.status.value,
                "changedAt": _format_datetime(self.changed_at),
                "reason": self.reason,
                "actor": self.actor,
                "attemptId": self.attempt_id,
            }
        )


@dataclass(frozen=True)
class PaymentIdempotencyKeyRecord:
    """Stored idempotency key scoped to a payment intent operation."""

    operation: PaymentOperation
    idempotency_key: str
    request_fingerprint: str
    payment_intent_id: str
    attempt_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "idempotency_key", _normalize_required_text("idempotency_key", self.idempotency_key))
        object.__setattr__(self, "request_fingerprint", _normalize_required_text("request_fingerprint", self.request_fingerprint))
        object.__setattr__(self, "payment_intent_id", _normalize_required_text("payment_intent_id", self.payment_intent_id))
        if self.attempt_id is not None:
            object.__setattr__(self, "attempt_id", _normalize_optional_text("attempt_id", self.attempt_id))
        _require_aware("created_at", self.created_at)

    @property
    def operation_key(self) -> Tuple[PaymentOperation, str]:
        return (self.operation, self.idempotency_key)

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "operation": self.operation.value,
                "idempotencyKey": self.idempotency_key,
                "requestFingerprint": self.request_fingerprint,
                "paymentIntentId": self.payment_intent_id,
                "attemptId": self.attempt_id,
                "createdAt": _format_datetime(self.created_at),
            }
        )


@dataclass(frozen=True)
class PaymentAttemptRecord:
    """One provider interaction attempt for a payment intent."""

    attempt_id: str
    payment_intent_id: str
    booking_id: str
    provider: str
    status: PaymentAttemptStatus = PaymentAttemptStatus.STARTED
    authorization_reference: Optional[AuthorizationReference] = None
    capture_result: Optional[CaptureResult] = None
    failure_code: Optional[str] = None
    failure_message: Optional[str] = None
    external_references: Sequence[ExternalPaymentReference] = field(default_factory=tuple)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "attempt_id", _normalize_required_text("attempt_id", self.attempt_id))
        object.__setattr__(self, "payment_intent_id", _normalize_required_text("payment_intent_id", self.payment_intent_id))
        object.__setattr__(self, "booking_id", _normalize_required_text("booking_id", self.booking_id))
        object.__setattr__(self, "provider", _normalize_required_text("provider", self.provider))
        _require_aware("created_at", self.created_at)
        _require_aware("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("attempt updated_at cannot be before created_at")
        if self.failure_code is not None:
            object.__setattr__(self, "failure_code", _normalize_optional_text("failure_code", self.failure_code))
        if self.failure_message is not None:
            object.__setattr__(self, "failure_message", _normalize_optional_text("failure_message", self.failure_message))
        references_by_key = {reference.natural_key: reference for reference in self.external_references}
        if self.authorization_reference is not None:
            references_by_key.setdefault(self.authorization_reference.provider_reference.natural_key, self.authorization_reference.provider_reference)
        if self.capture_result is not None:
            references_by_key.setdefault(self.capture_result.provider_reference.natural_key, self.capture_result.provider_reference)
        object.__setattr__(self, "external_references", tuple(references_by_key.values()))

    def with_authorization(self, authorization_reference: AuthorizationReference, *, updated_at: Optional[datetime] = None) -> "PaymentAttemptRecord":
        return replace(
            self,
            status=PaymentAttemptStatus.AUTHORIZED,
            authorization_reference=authorization_reference,
            updated_at=updated_at or authorization_reference.authorized_at,
        )

    def with_capture(self, capture_result: CaptureResult, *, updated_at: Optional[datetime] = None) -> "PaymentAttemptRecord":
        return replace(
            self,
            status=PaymentAttemptStatus.CAPTURED,
            capture_result=capture_result,
            updated_at=updated_at or capture_result.captured_at,
        )

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "attemptId": self.attempt_id,
                "paymentIntentId": self.payment_intent_id,
                "bookingId": self.booking_id,
                "provider": self.provider,
                "status": self.status.value,
                "authorizationReference": self.authorization_reference.to_dict() if self.authorization_reference else None,
                "captureResult": self.capture_result.to_dict() if self.capture_result else None,
                "failureCode": self.failure_code,
                "failureMessage": self.failure_message,
                "externalReferences": [reference.to_dict() for reference in self.external_references],
                "createdAt": _format_datetime(self.created_at),
                "updatedAt": _format_datetime(self.updated_at),
            }
        )


@dataclass(frozen=True)
class PaymentIntentRecord:
    """Aggregate persisted for one booking payment intent."""

    payment_intent_id: str
    booking_id: str
    amount: Decimal
    currency: str
    customer_identifier: Optional[str] = None
    status_history: Sequence[PaymentStatusHistoryEntry] = field(default_factory=tuple)
    attempts: Sequence[PaymentAttemptRecord] = field(default_factory=tuple)
    refunds: Sequence[RefundRecord] = field(default_factory=tuple)
    idempotency_keys: Sequence[PaymentIdempotencyKeyRecord] = field(default_factory=tuple)
    external_references: Sequence[ExternalPaymentReference] = field(default_factory=tuple)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        object.__setattr__(self, "payment_intent_id", _normalize_required_text("payment_intent_id", self.payment_intent_id))
        object.__setattr__(self, "booking_id", _normalize_required_text("booking_id", self.booking_id))
        object.__setattr__(self, "amount", _normalize_amount("amount", self.amount))
        object.__setattr__(self, "currency", _normalize_currency(self.currency))
        if self.customer_identifier is not None:
            object.__setattr__(self, "customer_identifier", _normalize_optional_text("customer_identifier", self.customer_identifier))
        _require_aware("created_at", self.created_at)
        _require_aware("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("payment intent updated_at cannot be before created_at")
        history = tuple(self.status_history) or (
            PaymentStatusHistoryEntry(PaymentIntentStatus.REQUIRES_PAYMENT_METHOD, changed_at=self.created_at),
        )
        object.__setattr__(self, "status_history", history)
        object.__setattr__(self, "attempts", tuple(self.attempts))
        object.__setattr__(self, "refunds", tuple(self.refunds))
        object.__setattr__(self, "idempotency_keys", tuple(self.idempotency_keys))
        object.__setattr__(self, "external_references", tuple(self.external_references))
        self.validate()

    @property
    def status(self) -> PaymentIntentStatus:
        return self.status_history[-1].status

    def validate(self) -> None:
        changed_times = [entry.changed_at for entry in self.status_history]
        if changed_times != sorted(changed_times):
            raise ValueError("status_history must be chronological")
        _ensure_unique("attempt_id", (attempt.attempt_id for attempt in self.attempts))
        _ensure_unique("refund_id", (refund.refund_id for refund in self.refunds))
        _ensure_unique("idempotency key per operation", (f"{record.operation.value}:{record.idempotency_key}" for record in self.idempotency_keys))
        references = list(self.external_references)
        for attempt in self.attempts:
            if attempt.payment_intent_id != self.payment_intent_id:
                raise ValueError("attempt payment_intent_id must reference payment intent")
            if attempt.booking_id != self.booking_id:
                raise ValueError("attempt booking_id must reference payment intent booking")
            references.extend(attempt.external_references)
        for refund in self.refunds:
            references.append(refund.provider_reference)
        _ensure_unique("external payment reference", (":".join(reference.natural_key) for reference in references))
        for key_record in self.idempotency_keys:
            if key_record.payment_intent_id != self.payment_intent_id:
                raise ValueError("idempotency key payment_intent_id must reference payment intent")
            if key_record.attempt_id is not None and key_record.attempt_id not in {attempt.attempt_id for attempt in self.attempts}:
                raise ValueError("idempotency key attempt_id must reference a payment attempt")

    def with_status(self, entry: PaymentStatusHistoryEntry, *, updated_at: Optional[datetime] = None) -> "PaymentIntentRecord":
        return replace(self, status_history=(*self.status_history, entry), updated_at=updated_at or entry.changed_at)

    def with_attempt(self, attempt: PaymentAttemptRecord, *, updated_at: Optional[datetime] = None) -> "PaymentIntentRecord":
        return replace(self, attempts=(*self.attempts, attempt), updated_at=updated_at or attempt.updated_at)

    def with_refund(self, refund: RefundRecord, *, updated_at: Optional[datetime] = None) -> "PaymentIntentRecord":
        return replace(self, refunds=(*self.refunds, refund), updated_at=updated_at or refund.refunded_at)

    def with_idempotency_key(self, key_record: PaymentIdempotencyKeyRecord) -> "PaymentIntentRecord":
        return replace(self, idempotency_keys=(*self.idempotency_keys, key_record), updated_at=max(self.updated_at, key_record.created_at))

    def to_dict(self) -> JsonObject:
        return _without_none(
            {
                "paymentIntentId": self.payment_intent_id,
                "bookingId": self.booking_id,
                "amount": _format_amount(self.amount),
                "currency": self.currency,
                "customerIdentifier": self.customer_identifier,
                "status": self.status.value,
                "statusHistory": [entry.to_dict() for entry in self.status_history],
                "attempts": [attempt.to_dict() for attempt in self.attempts],
                "refunds": [refund.to_dict() for refund in self.refunds],
                "idempotencyKeys": [record.to_dict() for record in self.idempotency_keys],
                "externalReferences": [reference.to_dict() for reference in self.external_references],
                "createdAt": _format_datetime(self.created_at),
                "updatedAt": _format_datetime(self.updated_at),
            }
        )


class PaymentRepository(Protocol):
    """Persistence contract for payment records."""

    def save(self, payment_intent: PaymentIntentRecord) -> PaymentIntentRecord:
        """Create or replace a payment intent record."""

    def get(self, payment_intent_id: str) -> Optional[PaymentIntentRecord]:
        """Return one payment intent by internal id."""

    def find_by_booking_id(self, booking_id: str) -> Sequence[PaymentIntentRecord]:
        """Return payment intents for one booking."""

    def append_status(self, payment_intent_id: str, entry: PaymentStatusHistoryEntry) -> PaymentIntentRecord:
        """Append one immutable status-history entry."""

    def add_attempt(self, payment_intent_id: str, attempt: PaymentAttemptRecord) -> PaymentIntentRecord:
        """Append one payment attempt to an intent."""

    def record_idempotency_key(self, payment_intent_id: str, key_record: PaymentIdempotencyKeyRecord) -> PaymentIntentRecord:
        """Persist an operation-scoped idempotency key."""


class InMemoryPaymentRepository(PaymentRepository):
    """Thread-safe in-memory payment repository for tests and local adapters."""

    def __init__(self, *, payment_intents: Iterable[PaymentIntentRecord] = ()) -> None:
        self._payment_intents: MutableMapping[str, PaymentIntentRecord] = {}
        self._by_booking: MutableMapping[str, List[str]] = {}
        self._idempotency_index: MutableMapping[Tuple[PaymentOperation, str], str] = {}
        self._external_reference_index: MutableMapping[Tuple[str, str, str], str] = {}
        self._lock = RLock()
        for payment_intent in payment_intents:
            self.save(payment_intent)

    def save(self, payment_intent: PaymentIntentRecord) -> PaymentIntentRecord:
        payment_intent.validate()
        with self._lock:
            previous = self._payment_intents.get(payment_intent.payment_intent_id)
            if previous is not None:
                self._validate_append_only(previous, payment_intent)
            for key_record in payment_intent.idempotency_keys:
                owner_id = self._idempotency_index.get(key_record.operation_key)
                if owner_id is not None and owner_id != payment_intent.payment_intent_id:
                    raise ValueError("idempotency key already exists for operation")
            for reference in _all_external_references(payment_intent):
                owner_id = self._external_reference_index.get(reference.natural_key)
                if owner_id is not None and owner_id != payment_intent.payment_intent_id:
                    raise ValueError("external payment reference already exists")
            self._payment_intents[payment_intent.payment_intent_id] = payment_intent
            booking_intents = self._by_booking.setdefault(payment_intent.booking_id, [])
            if payment_intent.payment_intent_id not in booking_intents:
                booking_intents.append(payment_intent.payment_intent_id)
            for key_record in payment_intent.idempotency_keys:
                self._idempotency_index[key_record.operation_key] = payment_intent.payment_intent_id
            for reference in _all_external_references(payment_intent):
                self._external_reference_index[reference.natural_key] = payment_intent.payment_intent_id
            return payment_intent

    def get(self, payment_intent_id: str) -> Optional[PaymentIntentRecord]:
        with self._lock:
            return self._payment_intents.get(payment_intent_id)

    def find_by_booking_id(self, booking_id: str) -> Sequence[PaymentIntentRecord]:
        with self._lock:
            payment_intent_ids = self._by_booking.get(_normalize_required_text("booking_id", booking_id), [])
            return tuple(self._payment_intents[payment_intent_id] for payment_intent_id in payment_intent_ids)

    def append_status(self, payment_intent_id: str, entry: PaymentStatusHistoryEntry) -> PaymentIntentRecord:
        with self._lock:
            payment_intent = self._require_payment_intent(payment_intent_id)
            if entry.changed_at < payment_intent.status_history[-1].changed_at:
                raise ValueError("status history entries must be appended chronologically")
            updated = payment_intent.with_status(entry)
            self._payment_intents[payment_intent_id] = updated
            return updated

    def add_attempt(self, payment_intent_id: str, attempt: PaymentAttemptRecord) -> PaymentIntentRecord:
        with self._lock:
            payment_intent = self._require_payment_intent(payment_intent_id)
            updated = payment_intent.with_attempt(attempt)
            return self.save(updated)

    def add_refund(self, payment_intent_id: str, refund: RefundRecord) -> PaymentIntentRecord:
        with self._lock:
            payment_intent = self._require_payment_intent(payment_intent_id)
            updated = payment_intent.with_refund(refund)
            return self.save(updated)

    def record_idempotency_key(self, payment_intent_id: str, key_record: PaymentIdempotencyKeyRecord) -> PaymentIntentRecord:
        with self._lock:
            payment_intent = self._require_payment_intent(payment_intent_id)
            updated = payment_intent.with_idempotency_key(key_record)
            return self.save(updated)

    def find_by_idempotency_key(self, operation: PaymentOperation, idempotency_key: str) -> Optional[PaymentIntentRecord]:
        with self._lock:
            payment_intent_id = self._idempotency_index.get((operation, _normalize_required_text("idempotency_key", idempotency_key)))
            return self._payment_intents.get(payment_intent_id) if payment_intent_id else None

    def _require_payment_intent(self, payment_intent_id: str) -> PaymentIntentRecord:
        payment_intent = self._payment_intents.get(payment_intent_id)
        if payment_intent is None:
            raise KeyError("payment intent was not found")
        return payment_intent

    @staticmethod
    def _validate_append_only(previous: PaymentIntentRecord, updated: PaymentIntentRecord) -> None:
        if len(updated.status_history) < len(previous.status_history):
            raise ValueError("status_history is append-only")
        if updated.status_history[: len(previous.status_history)] != previous.status_history:
            raise ValueError("status_history is append-only")
        if len(updated.attempts) < len(previous.attempts) or updated.attempts[: len(previous.attempts)] != previous.attempts:
            raise ValueError("payment attempts are append-only")
        if len(updated.refunds) < len(previous.refunds) or updated.refunds[: len(previous.refunds)] != previous.refunds:
            raise ValueError("refunds are append-only")
        if len(updated.idempotency_keys) < len(previous.idempotency_keys) or updated.idempotency_keys[: len(previous.idempotency_keys)] != previous.idempotency_keys:
            raise ValueError("idempotency keys are append-only")
        if len(updated.external_references) < len(previous.external_references) or updated.external_references[: len(previous.external_references)] != previous.external_references:
            raise ValueError("external payment references are append-only")


def _all_external_references(payment_intent: PaymentIntentRecord) -> Sequence[ExternalPaymentReference]:
    references: List[ExternalPaymentReference] = list(payment_intent.external_references)
    for attempt in payment_intent.attempts:
        references.extend(attempt.external_references)
    for refund in payment_intent.refunds:
        references.append(refund.provider_reference)
    return tuple(references)


def _ensure_unique(field_name: str, values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"payment {field_name} values must be unique")
        seen.add(value)
    return seen


def _format_amount(value: Decimal) -> str:
    return f"{value:.2f}"


def _format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _freeze_mapping(values: Mapping[str, object]) -> Mapping[str, object]:
    return {str(key): value for key, value in values.items()}


def _normalize_amount(field_name: str, value: Decimal) -> Decimal:
    try:
        amount = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid amount") from exc
    if amount <= Decimal("0.00"):
        raise ValueError(f"{field_name} must be positive")
    return amount


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


def _require_aware(field_name: str, value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _without_none(values: Mapping[str, object]) -> JsonObject:
    return {key: value for key, value in values.items() if value is not None}
