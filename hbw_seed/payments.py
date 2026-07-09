"""Provider-neutral tokenized payment integration layer.

The application boundary in this module models hosted/tokenized checkout: OFB asks a
provider for client-safe session initialization data, receives opaque payment
tokens from hosted components, and persists only provider references plus safe
metadata. Raw card data is intentionally absent from every request contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

MOCK_PAYMENT_PROVIDER = "deterministic_mock_payments"

PaymentOutcomeStatus = Literal[
    "requires_confirmation",
    "authorized",
    "captured",
    "declined",
    "cancelled",
    "expired",
    "timeout",
    "pending",
    "failed",
]
InternalPaymentStatus = Literal["authorized", "captured", "voided", "refunded"]


class PaymentProviderError(RuntimeError):
    """Base class for provider failures that callers can handle uniformly."""


class PaymentProviderTimeout(PaymentProviderError):
    """Raised when the provider times out."""


class PaymentProviderDeclined(PaymentProviderError):
    """Raised when the payment method is declined."""


class PaymentProviderConflict(PaymentProviderError):
    """Raised for provider-side token/session conflicts."""


@dataclass(frozen=True)
class PaymentProviderReference:
    """Opaque provider-owned reference retained behind the domain boundary."""

    provider: str
    reference_id: str
    kind: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentSessionRequest:
    reservation_id: str
    amount_cents: int
    currency: str = "USD"
    idempotency_key: str | None = None
    scenario: str = "success"


@dataclass(frozen=True)
class PaymentSession:
    id: str
    provider_reference: PaymentProviderReference
    amount_cents: int
    currency: str
    expires_at: str
    client_initialization: dict[str, Any]
    status: PaymentOutcomeStatus = "requires_confirmation"

    def to_client_payload(self) -> dict[str, Any]:
        """Return frontend-safe hosted/tokenized initialization data only."""

        return {
            "id": self.id,
            "provider": self.provider_reference.provider,
            "amount": _money(self.amount_cents, self.currency),
            "expiresAt": self.expires_at,
            "status": self.status,
            "clientInitialization": dict(self.client_initialization),
        }


@dataclass(frozen=True)
class PaymentConfirmationRequest:
    session_id: str
    payment_token: str
    amount_cents: int
    currency: str = "USD"
    idempotency_key: str | None = None
    scenario: str = "success"


@dataclass(frozen=True)
class PaymentOperationResult:
    provider_reference: PaymentProviderReference
    amount_cents: int
    currency: str
    status: PaymentOutcomeStatus
    duplicate: bool = False
    safe_metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    """Provider contract for hosted/tokenized payments; raw card fields are forbidden."""

    def createPaymentSession(self, request: PaymentSessionRequest) -> PaymentSession:
        ...

    def confirmPaymentToken(self, request: PaymentConfirmationRequest) -> PaymentOperationResult:
        ...

    def capturePayment(self, provider_reference: PaymentProviderReference, *, amount_cents: int, currency: str = "USD", scenario: str = "success") -> PaymentOperationResult:
        ...

    def cancelPayment(self, provider_reference: PaymentProviderReference, *, reason: str = "cancelled") -> PaymentOperationResult:
        ...

    def getPaymentStatus(self, provider_reference: PaymentProviderReference) -> PaymentOperationResult:
        ...


class DeterministicMockPaymentProvider:
    """Deterministic adapter covering tokenized checkout success and failure paths."""

    provider_name = MOCK_PAYMENT_PROVIDER

    def __init__(self) -> None:
        self._sessions_by_key: dict[str, PaymentSession] = {}
        self._sessions: dict[str, PaymentSession] = {}
        self._token_uses: dict[str, str] = {}
        self._confirmations_by_key: dict[str, PaymentOperationResult] = {}
        self._operations: dict[str, PaymentOperationResult] = {}

    def createPaymentSession(self, request: PaymentSessionRequest) -> PaymentSession:
        if request.scenario == "timeout":
            raise PaymentProviderTimeout("Mock payment provider timed out while creating session.")
        key = request.idempotency_key or f"session:{request.reservation_id}:{request.amount_cents}:{request.currency}:{request.scenario}"
        if key in self._sessions_by_key:
            return self._sessions_by_key[key]

        session_id = f"ps_{request.reservation_id}_{request.scenario}"
        session = PaymentSession(
            id=session_id,
            provider_reference=PaymentProviderReference(
                provider=self.provider_name,
                reference_id=f"native_{session_id}",
                kind="payment_session",
                attributes={"scenario": request.scenario},
            ),
            amount_cents=request.amount_cents,
            currency=request.currency,
            expires_at="2031-04-01T10:18:00Z",
            client_initialization={
                "mode": "hosted_or_tokenized_component",
                "sessionClientToken": f"client_{session_id}",
                "publishableKey": "pk_test_deterministic_mock_payments",
            },
        )
        self._sessions_by_key[key] = session
        self._sessions[session.id] = session
        return session

    def confirmPaymentToken(self, request: PaymentConfirmationRequest) -> PaymentOperationResult:
        if request.scenario == "timeout":
            raise PaymentProviderTimeout("Mock payment provider timed out while confirming token.")
        if request.idempotency_key and request.idempotency_key in self._confirmations_by_key:
            existing = self._confirmations_by_key[request.idempotency_key]
            duplicate = PaymentOperationResult(
                existing.provider_reference,
                existing.amount_cents,
                existing.currency,
                existing.status,
                duplicate=True,
                safe_metadata=existing.safe_metadata | {"idempotencyReplay": True},
            )
            return duplicate
        session = self._sessions.get(request.session_id)
        if session is None:
            raise PaymentProviderConflict("Payment session not found.")
        if request.scenario == "expired_session" or session.provider_reference.attributes.get("scenario") == "expired_session":
            result = self._result(session, request, "expired", failure_reason="session_expired")
            self._remember_confirmation(request, result)
            return result
        if request.payment_token in self._token_uses:
            raise PaymentProviderConflict("Payment token has already been used.")
        if request.amount_cents != session.amount_cents or request.currency != session.currency:
            self._token_uses[request.payment_token] = request.session_id
            result = self._result(session, request, "failed", failure_reason="amount_or_currency_mismatch")
            self._remember_confirmation(request, result)
            return result
        if request.scenario == "declined":
            self._token_uses[request.payment_token] = request.session_id
            result = self._result(session, request, "declined", failure_reason="payment_declined")
            self._remember_confirmation(request, result)
            return result
        if request.scenario == "delayed_confirmation":
            self._token_uses[request.payment_token] = request.session_id
            result = self._result(session, request, "pending", delayed=True)
            self._remember_confirmation(request, result)
            return result

        self._token_uses[request.payment_token] = request.session_id
        result = self._result(session, request, "authorized")
        self._remember_confirmation(request, result)
        return result

    def capturePayment(self, provider_reference: PaymentProviderReference, *, amount_cents: int, currency: str = "USD", scenario: str = "success") -> PaymentOperationResult:
        if scenario == "timeout":
            raise PaymentProviderTimeout("Mock payment provider timed out while capturing payment.")
        if scenario == "capture_failure":
            result = PaymentOperationResult(provider_reference, amount_cents, currency, "failed", safe_metadata={"failureReason": "capture_failed"})
            self._operations[provider_reference.reference_id] = result
            return result
        prior = self._operations.get(provider_reference.reference_id)
        metadata = (prior.safe_metadata if prior is not None else {}) | {"captureMode": "manual"}
        result = PaymentOperationResult(provider_reference, amount_cents, currency, "captured", safe_metadata=metadata)
        self._operations[provider_reference.reference_id] = result
        return result

    def cancelPayment(self, provider_reference: PaymentProviderReference, *, reason: str = "cancelled") -> PaymentOperationResult:
        result = PaymentOperationResult(provider_reference, 0, "USD", "cancelled", safe_metadata={"reason": reason})
        self._operations[provider_reference.reference_id] = result
        return result

    def getPaymentStatus(self, provider_reference: PaymentProviderReference) -> PaymentOperationResult:
        return self._operations.get(
            provider_reference.reference_id,
            PaymentOperationResult(provider_reference, 0, "USD", "pending", safe_metadata={"statusSource": "mock_default"}),
        )

    def _result(
        self,
        session: PaymentSession,
        request: PaymentConfirmationRequest,
        status: PaymentOutcomeStatus,
        *,
        failure_reason: str | None = None,
        delayed: bool = False,
    ) -> PaymentOperationResult:
        metadata: dict[str, Any] = {
            "sessionId": session.id,
            "tokenized": True,
            "paymentComponent": "hosted_or_tokenized",
        }
        if failure_reason:
            metadata["failureReason"] = failure_reason
        if delayed:
            metadata["confirmation"] = "delayed"
        reference = PaymentProviderReference(
            provider=self.provider_name,
            reference_id=f"auth_{session.id}_{request.payment_token[-6:]}",
            kind="payment_authorization",
            attributes={"sessionReference": session.provider_reference.reference_id},
        )
        result = PaymentOperationResult(reference, request.amount_cents, request.currency, status, safe_metadata=metadata)
        self._operations[reference.reference_id] = result
        return result

    def _remember_confirmation(self, request: PaymentConfirmationRequest, result: PaymentOperationResult) -> None:
        if request.idempotency_key:
            self._confirmations_by_key[request.idempotency_key] = result


def internal_status_for_provider_outcome(status: PaymentOutcomeStatus) -> InternalPaymentStatus:
    """Map provider outcomes onto statuses supported by OFB persistence."""

    if status == "captured":
        return "captured"
    if status in {"authorized", "pending"}:
        return "authorized"
    return "voided"


def payment_record_id(provider_reference: PaymentProviderReference) -> str:
    safe_reference = provider_reference.reference_id.replace("native_", "").replace("auth_", "")
    return f"pay_{safe_reference}"


def _money(amount_cents: int, currency: str) -> dict[str, Any]:
    return {"amountCents": amount_cents, "currency": currency, "formatted": f"{currency} {amount_cents / 100:.2f}"}
