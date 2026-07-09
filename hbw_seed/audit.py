"""Structured audit trail helpers for critical Hotel Booking Workflow actions."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping

SYSTEM_ACTOR_TYPE = "system"
GUEST_ACTOR_TYPE = "guest"
ADMIN_ACTOR_TYPE = "admin"
WEBHOOK_ACTOR_TYPE = "webhook"

SENSITIVE_METADATA_KEYS = {
    "cardNumber",
    "card_number",
    "cvc",
    "cvv",
    "providerSecret",
    "provider_secret",
    "rawPayload",
    "raw_payload",
    "requestPayload",
    "request_payload",
    "secret",
    "token",
}


@dataclass(frozen=True)
class AuditActor:
    """Actor identity captured in audit records."""

    actor_type: str
    user_id: str | None = None


def actor_from_user(actor: Mapping[str, Any] | None) -> AuditActor:
    """Map a user row to an audit actor; absent users are treated as guests."""

    if actor is None:
        return AuditActor(GUEST_ACTOR_TYPE, None)
    actor_type = ADMIN_ACTOR_TYPE if actor["role"] == "admin" else GUEST_ACTOR_TYPE
    return AuditActor(actor_type, actor["id"])


def user_actor(user_id: str | None, *, actor_type: str | None = None) -> AuditActor:
    """Build an actor from a known user id when the full user row is unavailable."""

    return AuditActor(actor_type or (GUEST_ACTOR_TYPE if user_id else GUEST_ACTOR_TYPE), user_id)


def system_actor(actor_type: str = SYSTEM_ACTOR_TYPE) -> AuditActor:
    """Build an actor for webhook/background actions without a normal user."""

    return AuditActor(actor_type, None)


class AuditWriteError(RuntimeError):
    """Raised when a blocking audit write cannot be persisted."""


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
) -> bool:
    """Persist a structured audit record with safe JSON metadata.

    Booking correctness is the primary requirement, so runtime callers default to
    best-effort audit writes. Admin inventory mutations opt into blocking writes
    because the audit record is part of the back-office change contract.
    """

    safe_metadata = sanitize_metadata(metadata or {})
    record_id = audit_id or _audit_id(event_type, entity_type, entity_id, created_at)
    try:
        connection.execute(
            """
            INSERT INTO audit_records (id, actor_user_id, actor_type, event_type, entity_type, entity_id, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                actor.user_id,
                actor.actor_type,
                event_type,
                entity_type,
                entity_id,
                json.dumps(safe_metadata, sort_keys=True, separators=(",", ":")),
                created_at,
            ),
        )
        return True
    except Exception as exc:  # pragma: no cover - defensive path exercised by callers via policy
        if block_on_failure:
            raise AuditWriteError("Audit event could not be recorded.") from exc
        return False


def sanitize_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return metadata safe for audit storage without payment secrets or raw payloads."""

    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if key in SENSITIVE_METADATA_KEYS:
            continue
        if isinstance(value, Mapping):
            safe[key] = sanitize_metadata(value)
        elif isinstance(value, list):
            safe[key] = [_sanitize_value(item) for item in value]
        else:
            safe[key] = value
    return safe


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return sanitize_metadata(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _audit_id(event_type: str, entity_type: str, entity_id: str, created_at: str) -> str:
    normalized = f"{event_type}_{entity_type}_{entity_id}_{created_at}"
    return "aud_" + "_".join(part for part in normalized.replace(":", "").replace("-", "").split() if part)
