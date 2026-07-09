"""Durable sanitized audit trail helpers for Hotel Booking Workflow actions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

USER_ACTOR_TYPE = "user"
SYSTEM_ACTOR_TYPE = "system"
PROVIDER_ACTOR_TYPE = "provider"

SENSITIVE_METADATA_KEYS = {
    "cardNumber",
    "card_number",
    "confirmationSecret",
    "confirmation_secret",
    "cvc",
    "cvv",
    "documentNumber",
    "document_number",
    "documentValue",
    "document_value",
    "fullDocumentNumber",
    "full_document_number",
    "identityDocument",
    "identity_document",
    "pan",
    "password",
    "passwordHash",
    "password_hash",
    "paymentToken",
    "payment_token",
    "providerError",
    "provider_error",
    "providerPayload",
    "provider_payload",
    "providerReference",
    "provider_reference",
    "providerSecret",
    "provider_secret",
    "rawPayload",
    "raw_payload",
    "rawProviderError",
    "raw_provider_error",
    "rawProviderPayload",
    "raw_provider_payload",
    "requestPayload",
    "request_payload",
    "secret",
    "ssn",
    "token",
}
SENSITIVE_METADATA_KEY_FINGERPRINTS = {"".join(character for character in key.lower() if character.isalnum()) for key in SENSITIVE_METADATA_KEYS}

MAX_METADATA_JSON_BYTES = 4096


@dataclass(frozen=True)
class AuditActor:
    """Actor identity captured in audit records."""

    actor_type: str
    user_id: str | None = None
    provider: str | None = None


@dataclass(frozen=True)
class AuditResource:
    """Resource references that make audit records queryable for troubleshooting."""

    resource_type: str
    resource_id: str
    user_id: str | None = None
    search_id: str | None = None
    booking_id: str | None = None
    payment_id: str | None = None
    offer_id: str | None = None


@dataclass(frozen=True)
class AuditEvent:
    """Application-level audit event input."""

    event_type: str
    actor: AuditActor
    resource: AuditResource
    metadata: Mapping[str, Any] | None = None
    occurred_at: str | None = None
    created_at: str | None = None
    id: str | None = None
    critical: bool = False


class AuditWriteError(RuntimeError):
    """Raised when a critical audit write cannot be persisted."""


class AuditEventRepository:
    """SQLite repository for durable audit events and troubleshooting queries."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def create(self, event: AuditEvent) -> dict[str, Any]:
        occurred_at = event.occurred_at or event.created_at
        created_at = event.created_at or event.occurred_at
        if not occurred_at or not created_at:
            raise ValueError("Audit events require occurred_at or created_at.")
        if event.actor.actor_type not in {USER_ACTOR_TYPE, SYSTEM_ACTOR_TYPE, PROVIDER_ACTOR_TYPE}:
            raise ValueError("Unsupported audit actor type.")
        metadata_json = _metadata_json(sanitize_metadata(event.metadata or {}))
        event_id = event.id or _audit_id(event.event_type, event.resource.resource_type, event.resource.resource_id, occurred_at)
        self.connection.execute(
            """
            INSERT INTO audit_events (
                id, event_type, actor_type, actor_user_id, actor_provider,
                resource_type, resource_id, user_id, search_id, booking_id,
                payment_id, offer_id, metadata, occurred_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event.event_type,
                event.actor.actor_type,
                event.actor.user_id,
                event.actor.provider,
                event.resource.resource_type,
                event.resource.resource_id,
                event.resource.user_id,
                event.resource.search_id,
                event.resource.booking_id,
                event.resource.payment_id,
                event.resource.offer_id,
                metadata_json,
                occurred_at,
                created_at,
            ),
        )
        return self._row_by_id(event_id)

    def by_booking_id(self, booking_id: str) -> list[dict[str, Any]]:
        return self._query("booking_id = ?", (booking_id,))

    def by_user_id(self, user_id: str) -> list[dict[str, Any]]:
        return self._query("user_id = ? OR actor_user_id = ?", (user_id, user_id))

    def _query(self, where: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            f"SELECT * FROM audit_events WHERE {where} ORDER BY occurred_at, id",
            params,
        ).fetchall()
        return [_event_payload(row) for row in rows]

    def _row_by_id(self, event_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM audit_events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            raise LookupError("Audit event not found after insert.")
        return _event_payload(row)


class AuditTrailService:
    """Application service that applies failure policy around audit writes."""

    def __init__(self, repository: AuditEventRepository):
        self.repository = repository

    def record(self, event: AuditEvent) -> bool:
        try:
            self.repository.create(event)
            return True
        except Exception as exc:  # pragma: no cover - defensive path exercised with bad schema tests
            if event.critical:
                raise AuditWriteError("Audit event could not be recorded.") from exc
            return False


def actor_from_user(actor: Mapping[str, Any] | None) -> AuditActor:
    """Map a user row to an audit actor; absent users are treated as system actors."""

    if actor is None:
        return system_actor()
    return AuditActor(USER_ACTOR_TYPE, actor["id"])


def user_actor(user_id: str | None, *, actor_type: str | None = None) -> AuditActor:
    """Build a user actor from a known user id when the full user row is unavailable."""

    if actor_type == PROVIDER_ACTOR_TYPE:
        return provider_actor(None)
    if actor_type == SYSTEM_ACTOR_TYPE or user_id is None:
        return system_actor()
    return AuditActor(USER_ACTOR_TYPE, user_id)


def system_actor(actor_type: str = SYSTEM_ACTOR_TYPE) -> AuditActor:
    """Build an actor for background or provider-originated actions."""

    if actor_type in {"webhook", PROVIDER_ACTOR_TYPE}:
        return provider_actor("webhook")
    return AuditActor(SYSTEM_ACTOR_TYPE, None)


def provider_actor(provider: str | None) -> AuditActor:
    """Build an actor for external provider callbacks."""

    return AuditActor(PROVIDER_ACTOR_TYPE, None, provider)


def record_audit_event(
    connection: sqlite3.Connection,
    *,
    actor: AuditActor,
    event_type: str,
    entity_type: str,
    entity_id: str,
    metadata: Mapping[str, Any] | None = None,
    created_at: str,
    audit_id: str | None = None,
    block_on_failure: bool = False,
    user_id: str | None = None,
    search_id: str | None = None,
    booking_id: str | None = None,
    payment_id: str | None = None,
    offer_id: str | None = None,
) -> bool:
    """Persist a sanitized audit event using best-effort or critical policy.

    Non-critical audit writes return ``False`` on persistence failure so user
    actions continue. Critical callers opt in with ``block_on_failure=True`` and
    receive ``AuditWriteError`` if durable storage fails.
    """

    resource = _resource_from_legacy(entity_type, entity_id, metadata or {}, user_id, search_id, booking_id, payment_id, offer_id)
    return AuditTrailService(AuditEventRepository(connection)).record(
        AuditEvent(
            id=audit_id,
            event_type=event_type,
            actor=actor,
            resource=resource,
            metadata=metadata,
            occurred_at=created_at,
            created_at=created_at,
            critical=block_on_failure,
        )
    )


def query_audit_events_by_booking_id(database_path: str, booking_id: str) -> list[dict[str, Any]]:
    """Return audit events related to a booking/reservation for troubleshooting."""

    with _connect(database_path) as connection:
        return AuditEventRepository(connection).by_booking_id(booking_id)


def query_audit_events_by_user_id(database_path: str, user_id: str) -> list[dict[str, Any]]:
    """Return audit events related to or performed by a user."""

    with _connect(database_path) as connection:
        return AuditEventRepository(connection).by_user_id(user_id)


def sanitize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return metadata safe for audit storage without payment, document, or raw provider secrets."""

    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if _is_sensitive_metadata_key(str(key)):
            continue
        safe[key] = _sanitize_value(value)
    return safe


def _is_sensitive_metadata_key(key: str) -> bool:
    fingerprint = "".join(character for character in key.lower() if character.isalnum())
    return fingerprint in SENSITIVE_METADATA_KEY_FINGERPRINTS


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return sanitize_metadata(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:50]]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value[:50]]
    return value


def _metadata_json(metadata: Mapping[str, Any]) -> str:
    encoded = json.dumps(metadata, sort_keys=True, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) <= MAX_METADATA_JSON_BYTES:
        return encoded
    truncated = dict(metadata)
    truncated["truncated"] = True
    truncated["_auditMetadataNotice"] = "metadata exceeded audit storage limit and was truncated"
    while len(json.dumps(truncated, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")) > MAX_METADATA_JSON_BYTES and len(truncated) > 2:
        key = next(key for key in truncated if key not in {"truncated", "_auditMetadataNotice"})
        truncated.pop(key)
    return json.dumps(truncated, sort_keys=True, separators=(",", ":"), default=str)


def _resource_from_legacy(
    entity_type: str,
    entity_id: str,
    metadata: Mapping[str, Any],
    user_id: str | None,
    search_id: str | None,
    booking_id: str | None,
    payment_id: str | None,
    offer_id: str | None,
) -> AuditResource:
    resource_type = "booking" if entity_type == "reservation" else entity_type
    inferred_booking_id = booking_id or (entity_id if entity_type == "reservation" else _metadata_str(metadata, "reservationId") or _metadata_str(metadata, "bookingId"))
    inferred_payment_id = payment_id or (entity_id if entity_type == "payment" else _metadata_str(metadata, "paymentId"))
    inferred_user_id = user_id or _metadata_str(metadata, "userId")
    return AuditResource(
        resource_type=resource_type,
        resource_id=entity_id,
        user_id=inferred_user_id,
        search_id=search_id or (entity_id if entity_type == "search" else _metadata_str(metadata, "searchId")),
        booking_id=inferred_booking_id,
        payment_id=inferred_payment_id,
        offer_id=offer_id or (entity_id if entity_type == "offer" else _metadata_str(metadata, "offerId")),
    )


def _metadata_str(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    return str(value)


def _event_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "eventType": row["event_type"],
        "actor": {"type": row["actor_type"], "userId": row["actor_user_id"], "provider": row["actor_provider"]},
        "resource": {
            "type": row["resource_type"],
            "id": row["resource_id"],
            "userId": row["user_id"],
            "searchId": row["search_id"],
            "bookingId": row["booking_id"],
            "paymentId": row["payment_id"],
            "offerId": row["offer_id"],
        },
        "metadata": json.loads(row["metadata"]),
        "occurredAt": row["occurred_at"],
        "createdAt": row["created_at"],
    }


def _connect(database_path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    return connection


def _audit_id(event_type: str, entity_type: str, entity_id: str, created_at: str) -> str:
    normalized = f"{event_type}_{entity_type}_{entity_id}_{created_at}"
    return "aud_" + "_".join(part for part in normalized.replace(":", "").replace("-", "").split() if part)
