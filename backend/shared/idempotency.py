"""Reusable idempotency primitives for command handlers.

The types in this module are intentionally storage- and transport-agnostic so
booking, payment, cancellation, webhook, and provider interaction commands can
share one idempotency contract while each service chooses its durable backing
store.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock
from typing import Any, Callable, Dict, Generic, Mapping, MutableMapping, Optional, Protocol, TypeVar
from uuid import uuid4

JsonObject = Dict[str, Any]
TResult = TypeVar("TResult", bound=Mapping[str, Any])


class IdempotencyScope(str, Enum):
    """Command families that participate in idempotency replay."""

    BOOKING = "booking"
    PAYMENT = "payment"
    CANCELLATION = "cancellation"
    WEBHOOK = "webhook"
    PROVIDER_INTERACTION = "provider_interaction"


@dataclass(frozen=True)
class IdempotencyKey:
    """Stable caller-provided key namespaced by command scope and actor.

    The same raw key can be reused by different actors or command families
    without colliding. Empty values are rejected because they cannot safely
    identify a replayable command.
    """

    scope: IdempotencyScope
    key: str
    actor_id: str

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("idempotency key is required")
        if not self.actor_id:
            raise ValueError("idempotency actor_id is required")

    @property
    def storage_key(self) -> str:
        """Return the canonical key used by stores."""

        return f"{self.scope.value}:{self.actor_id}:{self.key}"


class IdempotencyStatus(str, Enum):
    """Lifecycle state for a tracked idempotent command."""

    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class IdempotencyRecord:
    """Stored command outcome for replaying duplicate requests."""

    idempotency_key: IdempotencyKey
    request_fingerprint: str
    status: IdempotencyStatus
    result: Optional[Mapping[str, Any]] = None
    command_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.request_fingerprint:
            raise ValueError("request_fingerprint is required")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("idempotency record timestamps must be timezone-aware")
        if self.status == IdempotencyStatus.COMPLETED and self.result is None:
            raise ValueError("completed idempotency records require a result")


class IdempotencyConflictError(RuntimeError):
    """Raised when a duplicate key is reused with a different request body."""


class IdempotencyInProgressError(RuntimeError):
    """Raised when a duplicate command is already running and has no result yet."""


class IdempotencyStore(Protocol):
    """Storage contract for idempotent command execution.

    Durable implementations should make ``start`` atomic: exactly one caller can
    create a STARTED record for a storage key, and concurrent duplicates observe
    the existing record instead of running side effects twice.
    """

    def get(self, key: IdempotencyKey) -> Optional[IdempotencyRecord]:
        """Return an existing idempotency record, if any."""

    def start(self, key: IdempotencyKey, request_fingerprint: str) -> IdempotencyRecord:
        """Create and return a STARTED record for a new command."""

    def complete(self, key: IdempotencyKey, result: Mapping[str, Any]) -> IdempotencyRecord:
        """Persist the successful command result for future duplicate replay."""

    def fail(self, key: IdempotencyKey, error: Mapping[str, Any]) -> IdempotencyRecord:
        """Persist a terminal failure outcome for future duplicate replay."""


class InMemoryIdempotencyStore(IdempotencyStore):
    """Thread-safe in-memory store useful for tests and local adapters."""

    def __init__(self) -> None:
        self._records: MutableMapping[str, IdempotencyRecord] = {}
        self._lock = RLock()

    def get(self, key: IdempotencyKey) -> Optional[IdempotencyRecord]:
        with self._lock:
            return self._records.get(key.storage_key)

    def start(self, key: IdempotencyKey, request_fingerprint: str) -> IdempotencyRecord:
        with self._lock:
            existing = self._records.get(key.storage_key)
            if existing is not None:
                return existing

            record = IdempotencyRecord(
                idempotency_key=key,
                request_fingerprint=request_fingerprint,
                status=IdempotencyStatus.STARTED,
            )
            self._records[key.storage_key] = record
            return record

    def complete(self, key: IdempotencyKey, result: Mapping[str, Any]) -> IdempotencyRecord:
        with self._lock:
            existing = self._records[key.storage_key]
            completed = IdempotencyRecord(
                idempotency_key=existing.idempotency_key,
                request_fingerprint=existing.request_fingerprint,
                status=IdempotencyStatus.COMPLETED,
                result=dict(result),
                command_id=existing.command_id,
                created_at=existing.created_at,
            )
            self._records[key.storage_key] = completed
            return completed

    def fail(self, key: IdempotencyKey, error: Mapping[str, Any]) -> IdempotencyRecord:
        with self._lock:
            existing = self._records[key.storage_key]
            failed = IdempotencyRecord(
                idempotency_key=existing.idempotency_key,
                request_fingerprint=existing.request_fingerprint,
                status=IdempotencyStatus.FAILED,
                result=dict(error),
                command_id=existing.command_id,
                created_at=existing.created_at,
            )
            self._records[key.storage_key] = failed
            return failed


def run_idempotent(
    store: IdempotencyStore,
    key: IdempotencyKey,
    request_fingerprint: str,
    handler: Callable[[], TResult],
) -> Mapping[str, Any]:
    """Run a command once and replay the original result for duplicates."""

    existing = store.get(key)
    if existing is not None:
        if existing.request_fingerprint != request_fingerprint:
            raise IdempotencyConflictError("idempotency key was reused with a different request")
        if existing.status in {IdempotencyStatus.COMPLETED, IdempotencyStatus.FAILED}:
            return dict(existing.result or {})
        raise IdempotencyInProgressError("idempotent command is already in progress")

    record = store.start(key, request_fingerprint)
    if record.request_fingerprint != request_fingerprint:
        raise IdempotencyConflictError("idempotency key was reused with a different request")
    if record.status in {IdempotencyStatus.COMPLETED, IdempotencyStatus.FAILED}:
        return dict(record.result or {})
    if record.status != IdempotencyStatus.STARTED:
        raise IdempotencyInProgressError("idempotent command is already in progress")

    try:
        result = handler()
    except Exception as exc:
        store.fail(key, {"error": exc.__class__.__name__, "message": str(exc)})
        raise

    return dict(store.complete(key, result).result or {})


@dataclass(frozen=True)
class BookingCommand:
    """Example booking command contract with required idempotency key."""

    booking_id: str
    traveler_id: str
    itinerary: Mapping[str, Any]
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        if self.idempotency_key.scope != IdempotencyScope.BOOKING:
            raise ValueError("booking commands require a booking idempotency key")

    @property
    def request_fingerprint(self) -> str:
        return f"booking:{self.booking_id}:{self.traveler_id}:{sorted(self.itinerary.items())}"


@dataclass(frozen=True)
class PaymentCommand:
    """Example payment command contract with required idempotency key."""

    payment_id: str
    booking_id: str
    amount: str
    currency: str
    idempotency_key: IdempotencyKey

    def __post_init__(self) -> None:
        if self.idempotency_key.scope != IdempotencyScope.PAYMENT:
            raise ValueError("payment commands require a payment idempotency key")

    @property
    def request_fingerprint(self) -> str:
        return f"payment:{self.payment_id}:{self.booking_id}:{self.amount}:{self.currency}"
