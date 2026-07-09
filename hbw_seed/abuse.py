"""Application-level abuse protection primitives for HBW route handlers.

The in-memory limiter and idempotency store are intentionally process-local for
local development and tests. They implement small interfaces so production route
adapters can swap in Redis, a database, or a provider-backed store without
changing endpoint policy or handler code.
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol


@dataclass(frozen=True)
class RequestContext:
    """Caller identity used to build stable abuse-protection keys."""

    ip_address: str = "anonymous"
    user_id: str | None = None


@dataclass(frozen=True)
class RateLimitPolicy:
    """Endpoint rate-limit policy.

    ``fail_open`` is explicit per endpoint. Expensive unauthenticated abuse
    targets fail closed; lower-risk authenticated mutations can fail open if the
    limiter backend is unavailable so legitimate checkout retries are not lost.
    """

    name: str
    limit: int
    window_seconds: int
    key_strategy: str
    fail_open: bool


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int
    key: str
    policy: RateLimitPolicy


class RateLimiter(Protocol):
    def check(self, key: str, policy: RateLimitPolicy, now: float | None = None) -> RateLimitResult:
        ...


class InMemoryRateLimiter:
    """Fixed-window in-memory limiter suitable for tests and local execution."""

    def __init__(self) -> None:
        self._buckets: dict[tuple[str, str], tuple[int, float]] = {}

    def check(self, key: str, policy: RateLimitPolicy, now: float | None = None) -> RateLimitResult:
        current_time = time.time() if now is None else now
        bucket_key = (policy.name, key)
        count, reset_at = self._buckets.get(bucket_key, (0, current_time + policy.window_seconds))
        if current_time >= reset_at:
            count = 0
            reset_at = current_time + policy.window_seconds

        retry_after = max(1, int(reset_at - current_time))
        if count >= policy.limit:
            return RateLimitResult(False, policy.limit, 0, retry_after, key, policy)

        count += 1
        self._buckets[bucket_key] = (count, reset_at)
        return RateLimitResult(True, policy.limit, policy.limit - count, retry_after, key, policy)

    def reset(self) -> None:
        self._buckets.clear()


class IdempotencyStore(Protocol):
    def get(self, namespace: str, key: str, fingerprint: str) -> Any | None:
        ...

    def put(self, namespace: str, key: str, fingerprint: str, response: Any) -> None:
        ...


class InMemoryIdempotencyStore:
    """Process-local idempotency result cache for deterministic tests/local use."""

    def __init__(self) -> None:
        self._responses: dict[tuple[str, str, str], Any] = {}

    def get(self, namespace: str, key: str, fingerprint: str) -> Any | None:
        response = self._responses.get((namespace, key, fingerprint))
        return copy.deepcopy(response) if response is not None else None

    def put(self, namespace: str, key: str, fingerprint: str, response: Any) -> None:
        self._responses[(namespace, key, fingerprint)] = copy.deepcopy(response)

    def reset(self) -> None:
        self._responses.clear()


class IdempotencyService:
    def __init__(self, store: IdempotencyStore | None = None) -> None:
        self.store = store or InMemoryIdempotencyStore()

    def fingerprint(self, body: dict[str, Any]) -> str:
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def run(
        self,
        namespace: str,
        idempotency_key: str,
        body: dict[str, Any],
        operation: Callable[[], Any],
    ) -> tuple[Any, bool]:
        fingerprint = self.fingerprint(body)
        existing = self.store.get(namespace, idempotency_key, fingerprint)
        if existing is not None:
            return existing, True
        response = operation()
        self.store.put(namespace, idempotency_key, fingerprint, response)
        return response, False


DEFAULT_RATE_LIMITER = InMemoryRateLimiter()
DEFAULT_IDEMPOTENCY = IdempotencyService()

ENDPOINT_RATE_LIMIT_POLICIES: dict[str, RateLimitPolicy] = {
    "sign_in": RateLimitPolicy("sign_in", limit=5, window_seconds=60, key_strategy="ip", fail_open=False),
    "search": RateLimitPolicy("search", limit=30, window_seconds=60, key_strategy="user_or_ip", fail_open=True),
    "confirmation_lookup": RateLimitPolicy(
        "confirmation_lookup", limit=8, window_seconds=60, key_strategy="ip", fail_open=False
    ),
    "reservation_create": RateLimitPolicy(
        "reservation_create", limit=10, window_seconds=60, key_strategy="user_or_ip", fail_open=False
    ),
    "payment_intent_create": RateLimitPolicy(
        "payment_intent_create", limit=12, window_seconds=60, key_strategy="composite", fail_open=False
    ),
}


def build_rate_limit_key(policy: RateLimitPolicy, context: RequestContext, discriminator: str | None = None) -> str:
    ip = context.ip_address or "anonymous"
    if policy.key_strategy == "ip":
        return f"ip:{ip}"
    if policy.key_strategy == "user_or_ip":
        return f"user:{context.user_id}" if context.user_id else f"ip:{ip}"
    if policy.key_strategy == "composite":
        principal = f"user:{context.user_id}" if context.user_id else f"ip:{ip}"
        return f"{principal}:{discriminator or 'none'}"
    return f"ip:{ip}"


def require_idempotency_key(headers: dict[str, str]) -> str | None:
    key = headers.get("Idempotency-Key") or headers.get("idempotency-key")
    if key and 8 <= len(key) <= 128:
        return key
    return None


def body_within_limit(body: dict[str, Any], max_bytes: int) -> bool:
    return len(json.dumps(body, sort_keys=True, default=str).encode("utf-8")) <= max_bytes
