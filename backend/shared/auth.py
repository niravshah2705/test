"""Authentication and role-based authorization foundation.

The primitives in this module are intentionally framework-agnostic so HTTP
controllers, background services, and provider callback adapters can share the
same contracts while the application chooses concrete routing and storage later.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Optional, Protocol, Sequence, Set, TypeVar
from uuid import uuid4

JsonObject = Dict[str, Any]
TResult = TypeVar("TResult")


class AuthRole(str, Enum):
    """Application roles used by controller and service authorization checks."""

    ANONYMOUS = "anonymous"
    TRAVELER = "traveler"
    SUPPORT_OPERATOR = "support_operator"
    PROVIDER_CALLBACK = "provider_callback"


@dataclass(frozen=True)
class Principal:
    """Authenticated or anonymous actor resolved for the current request."""

    subject_id: str
    roles: frozenset[AuthRole]
    display_name: Optional[str] = None
    email: Optional[str] = None
    provider_id: Optional[str] = None

    def has_role(self, role: AuthRole) -> bool:
        return role in self.roles

    @property
    def is_authenticated(self) -> bool:
        return AuthRole.ANONYMOUS not in self.roles


ANONYMOUS_PRINCIPAL = Principal("anonymous", frozenset({AuthRole.ANONYMOUS}), "Anonymous")


@dataclass(frozen=True)
class RegisteredUser:
    """Stored registered user credential record."""

    user_id: str
    email: str
    password_hash: str
    roles: frozenset[AuthRole]
    display_name: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_principal(self) -> Principal:
        return Principal(
            subject_id=self.user_id,
            roles=self.roles,
            display_name=self.display_name,
            email=self.email,
        )


@dataclass(frozen=True)
class RegistrationRequest:
    """Controller-facing registration input contract."""

    email: str
    password: str
    display_name: Optional[str] = None
    roles: frozenset[AuthRole] = field(default_factory=lambda: frozenset({AuthRole.TRAVELER}))


@dataclass(frozen=True)
class LoginRequest:
    """Controller-facing login input contract."""

    email: str
    password: str


@dataclass(frozen=True)
class AuthSession:
    """Login or callback authentication result returned to callers."""

    principal: Principal
    access_token: str
    expires_at: datetime


class AuthenticationError(RuntimeError):
    """Raised when request credentials are absent, invalid, or expired."""


class AuthorizationError(RuntimeError):
    """Raised when an authenticated principal lacks a required role."""


class RegistrationError(ValueError):
    """Raised when a registration request cannot be accepted."""


class UserStore(Protocol):
    """Persistence contract for registered users."""

    def get_by_email(self, email: str) -> Optional[RegisteredUser]:
        """Return a user by normalized email, if present."""

    def get_by_id(self, user_id: str) -> Optional[RegisteredUser]:
        """Return a user by stable id, if present."""

    def create(self, request: RegistrationRequest, password_hash: str) -> RegisteredUser:
        """Persist a new registered user credential record."""


class InMemoryUserStore(UserStore):
    """Thread-safe in-memory user store for tests and local adapters."""

    def __init__(self) -> None:
        self._by_email: MutableMapping[str, RegisteredUser] = {}
        self._by_id: MutableMapping[str, RegisteredUser] = {}
        self._lock = RLock()

    def get_by_email(self, email: str) -> Optional[RegisteredUser]:
        with self._lock:
            return self._by_email.get(_normalize_email(email))

    def get_by_id(self, user_id: str) -> Optional[RegisteredUser]:
        with self._lock:
            return self._by_id.get(user_id)

    def create(self, request: RegistrationRequest, password_hash: str) -> RegisteredUser:
        normalized_email = _normalize_email(request.email)
        with self._lock:
            if normalized_email in self._by_email:
                raise RegistrationError("email is already registered")

            user = RegisteredUser(
                user_id=f"user_{uuid4().hex}",
                email=normalized_email,
                password_hash=password_hash,
                roles=frozenset(request.roles),
                display_name=request.display_name,
            )
            self._by_email[normalized_email] = user
            self._by_id[user.user_id] = user
            return user


class PasswordHasher:
    """PBKDF2 password hashing helper suitable for persisted credentials."""

    def __init__(self, iterations: int = 120_000) -> None:
        self._iterations = iterations

    def hash(self, password: str) -> str:
        _require_password(password)
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self._iterations)
        return ":".join(
            [
                "pbkdf2_sha256",
                str(self._iterations),
                base64.urlsafe_b64encode(salt).decode("ascii"),
                base64.urlsafe_b64encode(digest).decode("ascii"),
            ]
        )

    def verify(self, password: str, password_hash: str) -> bool:
        try:
            algorithm, iterations, encoded_salt, encoded_digest = password_hash.split(":", 3)
        except ValueError:
            return False
        if algorithm != "pbkdf2_sha256":
            return False

        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)


class TokenSigner:
    """HMAC-signed bearer token issuer and verifier."""

    def __init__(self, secret: str, ttl: timedelta = timedelta(hours=1)) -> None:
        if not secret:
            raise ValueError("token signing secret is required")
        self._secret = secret.encode("utf-8")
        self._ttl = ttl

    def issue(self, principal: Principal, *, now: Optional[datetime] = None) -> AuthSession:
        issued_at = now or datetime.now(timezone.utc)
        expires_at = issued_at + self._ttl
        payload: JsonObject = {
            "sub": principal.subject_id,
            "roles": [role.value for role in sorted(principal.roles, key=lambda item: item.value)],
            "iat": int(issued_at.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        if principal.email is not None:
            payload["email"] = principal.email
        if principal.display_name is not None:
            payload["displayName"] = principal.display_name
        if principal.provider_id is not None:
            payload["providerId"] = principal.provider_id

        encoded_payload = _base64url_json(payload)
        signature = _base64url_bytes(hmac.new(self._secret, encoded_payload.encode("ascii"), hashlib.sha256).digest())
        return AuthSession(principal=principal, access_token=f"{encoded_payload}.{signature}", expires_at=expires_at)

    def verify(self, token: str, *, now: Optional[datetime] = None) -> Principal:
        try:
            encoded_payload, encoded_signature = token.split(".", 1)
        except ValueError as exc:
            raise AuthenticationError("invalid bearer token") from exc

        expected_signature = _base64url_bytes(hmac.new(self._secret, encoded_payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(encoded_signature, expected_signature):
            raise AuthenticationError("invalid bearer token")

        payload = _decode_base64url_json(encoded_payload)
        current_time = now or datetime.now(timezone.utc)
        if int(payload["exp"]) < int(current_time.timestamp()):
            raise AuthenticationError("bearer token expired")

        return Principal(
            subject_id=str(payload["sub"]),
            roles=frozenset(AuthRole(role) for role in payload["roles"]),
            display_name=payload.get("displayName"),
            email=payload.get("email"),
            provider_id=payload.get("providerId"),
        )


class AuthService:
    """Registration, login, and current-user resolution service."""

    def __init__(self, user_store: UserStore, token_signer: TokenSigner, password_hasher: Optional[PasswordHasher] = None) -> None:
        self._user_store = user_store
        self._token_signer = token_signer
        self._password_hasher = password_hasher or PasswordHasher()

    def register(self, request: RegistrationRequest) -> AuthSession:
        normalized_email = _normalize_email(request.email)
        if not normalized_email:
            raise RegistrationError("email is required")
        if AuthRole.ANONYMOUS in request.roles or AuthRole.PROVIDER_CALLBACK in request.roles:
            raise RegistrationError("registered users cannot be anonymous or provider callbacks")

        user = self._user_store.create(
            RegistrationRequest(
                email=normalized_email,
                password=request.password,
                display_name=request.display_name,
                roles=frozenset(request.roles or {AuthRole.TRAVELER}),
            ),
            self._password_hasher.hash(request.password),
        )
        return self._token_signer.issue(user.to_principal())

    def login(self, request: LoginRequest) -> AuthSession:
        user = self._user_store.get_by_email(request.email)
        if user is None or not self._password_hasher.verify(request.password, user.password_hash):
            raise AuthenticationError("invalid email or password")
        return self._token_signer.issue(user.to_principal())

    def resolve_current_user(self, authorization_header: Optional[str]) -> Principal:
        """Resolve a bearer token into the current user or raise unauthorized."""

        if not authorization_header:
            raise AuthenticationError("authorization header is required")
        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationError("bearer authorization header is required")
        return self._token_signer.verify(token)


@dataclass(frozen=True)
class ProviderCallbackCredential:
    """Shared-secret credential used by provider callback adapters."""

    provider_id: str
    secret: str
    roles: frozenset[AuthRole] = field(default_factory=lambda: frozenset({AuthRole.PROVIDER_CALLBACK}))


class ProviderCredentialStore(Protocol):
    """Persistence contract for provider callback shared secrets."""

    def get(self, provider_id: str) -> Optional[ProviderCallbackCredential]:
        """Return provider callback credentials, if configured."""


class InMemoryProviderCredentialStore(ProviderCredentialStore):
    """Thread-safe in-memory provider credential store."""

    def __init__(self, credentials: Iterable[ProviderCallbackCredential] = ()) -> None:
        self._credentials: MutableMapping[str, ProviderCallbackCredential] = {}
        self._lock = RLock()
        for credential in credentials:
            self.upsert(credential)

    def upsert(self, credential: ProviderCallbackCredential) -> None:
        with self._lock:
            self._credentials[credential.provider_id] = credential

    def get(self, provider_id: str) -> Optional[ProviderCallbackCredential]:
        with self._lock:
            return self._credentials.get(provider_id)


class ProviderCallbackAuthenticator:
    """Authenticate provider callbacks with HMAC request signatures.

    The expected signature is HMAC-SHA256 over
    ``{provider_id}.{timestamp}.{raw_body}``, delivered as a hex digest. The
    timestamp tolerance limits replay risk while keeping the strategy simple for
    provider adapters to implement.
    """

    def __init__(self, credential_store: ProviderCredentialStore, tolerance: timedelta = timedelta(minutes=5)) -> None:
        self._credential_store = credential_store
        self._tolerance = tolerance

    def sign(self, provider_id: str, timestamp: str, raw_body: str) -> str:
        credential = self._credential_store.get(provider_id)
        if credential is None:
            raise AuthenticationError("unknown provider")
        return _provider_signature(credential.secret, provider_id, timestamp, raw_body)

    def authenticate(self, provider_id: str, timestamp: str, raw_body: str, signature: str, *, now: Optional[datetime] = None) -> Principal:
        credential = self._credential_store.get(provider_id)
        if credential is None:
            raise AuthenticationError("unknown provider")

        _validate_callback_timestamp(timestamp, now or datetime.now(timezone.utc), self._tolerance)
        expected = _provider_signature(credential.secret, provider_id, timestamp, raw_body)
        if not hmac.compare_digest(signature, expected):
            raise AuthenticationError("invalid provider callback signature")

        return Principal(
            subject_id=f"provider:{provider_id}",
            roles=credential.roles,
            display_name=provider_id,
            provider_id=provider_id,
        )


@dataclass(frozen=True)
class RequestContext:
    """Framework-neutral request context passed to controllers/services."""

    principal: Principal = ANONYMOUS_PRINCIPAL


def require_authenticated(principal: Principal) -> Principal:
    """Require any authenticated non-anonymous principal."""

    if not principal.is_authenticated:
        raise AuthenticationError("authentication required")
    return principal


def require_roles(principal: Principal, *roles: AuthRole) -> Principal:
    """Require a principal to hold at least one of the supplied roles."""

    require_authenticated(principal)
    required = set(roles)
    if required and principal.roles.isdisjoint(required):
        raise AuthorizationError("required role is missing")
    return principal


def authorize(required_roles: Sequence[AuthRole]) -> Callable[[Callable[..., TResult]], Callable[..., TResult]]:
    """Decorator for enforcing role checks at service/controller boundaries.

    Decorated callables must receive a ``RequestContext`` as their first
    argument after ``self`` (for methods) or as their first argument (for
    functions). This keeps the enforcement point explicit and testable without a
    specific web framework.
    """

    def decorator(handler: Callable[..., TResult]) -> Callable[..., TResult]:
        def wrapper(*args: Any, **kwargs: Any) -> TResult:
            context = _extract_context(args, kwargs)
            require_roles(context.principal, *required_roles)
            return handler(*args, **kwargs)

        return wrapper

    return decorator


def _extract_context(args: Sequence[Any], kwargs: Mapping[str, Any]) -> RequestContext:
    context = kwargs.get("context")
    if isinstance(context, RequestContext):
        return context
    for arg in args:
        if isinstance(arg, RequestContext):
            return arg
    raise AuthenticationError("request context is required")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _require_password(password: str) -> None:
    if len(password) < 8:
        raise RegistrationError("password must be at least 8 characters")


def _base64url_bytes(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_json(value: Mapping[str, Any]) -> str:
    return _base64url_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _decode_base64url_json(value: str) -> JsonObject:
    padded = value + "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


def _provider_signature(secret: str, provider_id: str, timestamp: str, raw_body: str) -> str:
    message = f"{provider_id}.{timestamp}.{raw_body}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _validate_callback_timestamp(timestamp: str, now: datetime, tolerance: timedelta) -> None:
    try:
        callback_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthenticationError("invalid provider callback timestamp") from exc
    if callback_time.tzinfo is None:
        raise AuthenticationError("provider callback timestamp must be timezone-aware")
    if abs(now - callback_time.astimezone(timezone.utc)) > tolerance:
        raise AuthenticationError("provider callback timestamp outside tolerance")
