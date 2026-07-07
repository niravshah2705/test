import unittest
from datetime import datetime, timedelta, timezone

from backend.shared.auth import (
    ANONYMOUS_PRINCIPAL,
    AuthRole,
    AuthService,
    AuthenticationError,
    AuthorizationError,
    InMemoryProviderCredentialStore,
    InMemoryUserStore,
    LoginRequest,
    ProviderCallbackAuthenticator,
    ProviderCallbackCredential,
    RegistrationError,
    RegistrationRequest,
    RequestContext,
    TokenSigner,
    authorize,
    require_roles,
)


class AuthFoundationTests(unittest.TestCase):
    def setUp(self):
        self.user_store = InMemoryUserStore()
        self.signer = TokenSigner("test-secret", ttl=timedelta(minutes=30))
        self.auth_service = AuthService(self.user_store, self.signer)

    def test_registration_interface_creates_traveler_session(self):
        session = self.auth_service.register(
            RegistrationRequest(email=" Traveler@Example.COM ", password="correct horse battery", display_name="Traveler")
        )

        self.assertTrue(session.access_token)
        self.assertEqual(session.principal.email, "traveler@example.com")
        self.assertEqual(session.principal.display_name, "Traveler")
        self.assertEqual(session.principal.roles, frozenset({AuthRole.TRAVELER}))
        self.assertTrue(session.principal.is_authenticated)

    def test_login_interface_returns_registered_user_session(self):
        registered = self.auth_service.register(RegistrationRequest(email="traveler@example.com", password="correct horse battery"))

        logged_in = self.auth_service.login(LoginRequest(email="traveler@example.com", password="correct horse battery"))

        self.assertEqual(logged_in.principal.subject_id, registered.principal.subject_id)
        self.assertEqual(logged_in.principal.roles, frozenset({AuthRole.TRAVELER}))

    def test_duplicate_registration_is_rejected(self):
        self.auth_service.register(RegistrationRequest(email="traveler@example.com", password="correct horse battery"))

        with self.assertRaises(RegistrationError):
            self.auth_service.register(RegistrationRequest(email="TRAVELER@example.com", password="correct horse battery"))

    def test_login_with_bad_password_is_unauthorized(self):
        self.auth_service.register(RegistrationRequest(email="traveler@example.com", password="correct horse battery"))

        with self.assertRaises(AuthenticationError):
            self.auth_service.login(LoginRequest(email="traveler@example.com", password="wrong password"))

    def test_authenticated_endpoint_resolves_current_user_from_bearer_token(self):
        session = self.auth_service.register(RegistrationRequest(email="traveler@example.com", password="correct horse battery"))

        principal = self.auth_service.resolve_current_user(f"Bearer {session.access_token}")

        self.assertEqual(principal.subject_id, session.principal.subject_id)
        self.assertEqual(principal.email, "traveler@example.com")
        self.assertEqual(principal.roles, frozenset({AuthRole.TRAVELER}))

    def test_missing_bearer_token_is_unauthorized(self):
        with self.assertRaises(AuthenticationError):
            self.auth_service.resolve_current_user(None)

    def test_forbidden_role_check_rejects_authenticated_traveler(self):
        session = self.auth_service.register(RegistrationRequest(email="traveler@example.com", password="correct horse battery"))

        with self.assertRaises(AuthorizationError):
            require_roles(session.principal, AuthRole.SUPPORT_OPERATOR)

    def test_authorized_role_check_allows_support_operator(self):
        session = self.auth_service.register(
            RegistrationRequest(
                email="support@example.com",
                password="correct horse battery",
                roles=frozenset({AuthRole.SUPPORT_OPERATOR}),
            )
        )

        self.assertEqual(require_roles(session.principal, AuthRole.SUPPORT_OPERATOR), session.principal)

    def test_controller_boundary_decorator_enforces_roles(self):
        calls = []

        @authorize([AuthRole.SUPPORT_OPERATOR])
        def close_ticket(context: RequestContext, ticket_id: str):
            calls.append(ticket_id)
            return {"ticketId": ticket_id, "status": "closed"}

        support_session = self.auth_service.register(
            RegistrationRequest(
                email="support@example.com",
                password="correct horse battery",
                roles=frozenset({AuthRole.SUPPORT_OPERATOR}),
            )
        )

        response = close_ticket(RequestContext(support_session.principal), "ticket_123")

        self.assertEqual(response, {"ticketId": "ticket_123", "status": "closed"})
        self.assertEqual(calls, ["ticket_123"])
        with self.assertRaises(AuthenticationError):
            close_ticket(RequestContext(ANONYMOUS_PRINCIPAL), "ticket_456")

    def test_provider_callback_authentication_strategy_uses_hmac_signature(self):
        credentials = InMemoryProviderCredentialStore([ProviderCallbackCredential("taxi-co", "provider-secret")])
        authenticator = ProviderCallbackAuthenticator(credentials)
        timestamp = datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
        raw_body = '{"bookingId":"booking_123","status":"accepted"}'
        signature = authenticator.sign("taxi-co", timestamp, raw_body)

        principal = authenticator.authenticate(
            "taxi-co",
            timestamp,
            raw_body,
            signature,
            now=datetime(2026, 7, 7, 17, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(principal.provider_id, "taxi-co")
        self.assertEqual(principal.roles, frozenset({AuthRole.PROVIDER_CALLBACK}))

    def test_provider_callback_bad_signature_is_unauthorized(self):
        credentials = InMemoryProviderCredentialStore([ProviderCallbackCredential("taxi-co", "provider-secret")])
        authenticator = ProviderCallbackAuthenticator(credentials)
        timestamp = datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")

        with self.assertRaises(AuthenticationError):
            authenticator.authenticate(
                "taxi-co",
                timestamp,
                "{}",
                "bad-signature",
                now=datetime(2026, 7, 7, 17, 1, tzinfo=timezone.utc),
            )


if __name__ == "__main__":
    unittest.main()
