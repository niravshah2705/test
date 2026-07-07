import unittest
from datetime import date, datetime, timezone

from backend.shared.travelers import (
    ContactDetails,
    IdentityDocumentType,
    InMemoryTravelerProfileStore,
    PassengerIdentityDocument,
    TravelCabinPreference,
    TravelPreferences,
    TravelerPII,
    TravelerProfile,
    TravelerType,
    UserAccount,
)


class TravelerProfileModelTests(unittest.TestCase):
    def test_user_account_owns_multiple_traveler_profiles(self):
        user = UserAccount(user_id="user_123")
        store = InMemoryTravelerProfileStore()
        adult = TravelerProfile(
            owner_user_id=user.user_id,
            traveler_type=TravelerType.ADULT,
            pii=TravelerPII(given_name="Nirav", family_name="Shah"),
            profile_id="traveler_adult",
        )
        child = TravelerProfile(
            owner_user_id=user.user_id,
            traveler_type=TravelerType.CHILD,
            pii=TravelerPII(given_name="Asha", family_name="Shah"),
            profile_id="traveler_child",
        )
        other_user_profile = TravelerProfile(
            owner_user_id="user_other",
            traveler_type=TravelerType.ADULT,
            pii=TravelerPII(given_name="Other", family_name="Traveler"),
            profile_id="traveler_other",
        )

        store.save(adult)
        store.save(child)
        store.save(other_user_profile)

        profiles = store.list_for_user(user.user_id, include_pii=True)
        self.assertEqual([profile.profile_id for profile in profiles], ["traveler_adult", "traveler_child"])
        self.assertIsNone(store.get(user.user_id, "traveler_other", include_pii=True))

    def test_profiles_support_adult_child_and_infant_types(self):
        profiles = [
            TravelerProfile(
                owner_user_id="user_123",
                traveler_type=traveler_type,
                pii=TravelerPII(given_name=traveler_type.value, family_name="Traveler"),
            )
            for traveler_type in (TravelerType.ADULT, TravelerType.CHILD, TravelerType.INFANT)
        ]

        self.assertEqual([profile.traveler_type.value for profile in profiles], ["adult", "child", "infant"])

    def test_identity_document_fields_are_structured_and_optional(self):
        minimal = PassengerIdentityDocument(IdentityDocumentType.PASSPORT)
        detailed = PassengerIdentityDocument(
            document_type=IdentityDocumentType.NATIONAL_ID,
            document_number="A1234567",
            issuing_country_code="us",
            nationality_country_code="in",
            issued_on=date(2020, 1, 1),
            expires_on=date(2030, 1, 1),
            issuing_authority="Government",
        )

        self.assertEqual(minimal.to_dict(), {"type": "passport"})
        self.assertEqual(
            detailed.to_dict(),
            {
                "type": "national_id",
                "documentNumber": "A1234567",
                "issuingCountryCode": "US",
                "nationalityCountryCode": "IN",
                "expiresOn": "2030-01-01",
                "issuedOn": "2020-01-01",
                "issuingAuthority": "Government",
            },
        )

    def test_identity_document_rejects_impossible_date_order(self):
        with self.assertRaises(ValueError):
            PassengerIdentityDocument(
                document_type=IdentityDocumentType.PASSPORT,
                issued_on=date(2030, 1, 1),
                expires_on=date(2020, 1, 1),
            )

    def test_pii_fields_are_isolated_and_masked_by_default(self):
        store = InMemoryTravelerProfileStore()
        profile = TravelerProfile(
            owner_user_id="user_123",
            traveler_type=TravelerType.ADULT,
            pii=TravelerPII(
                given_name="Nirav",
                family_name="Shah",
                date_of_birth=date(1990, 5, 20),
                contact_details=ContactDetails(
                    email=" Traveler@Example.COM ",
                    phone_number="+1 (212) 555-0199",
                    address_line1="123 Main Street",
                    city="New York",
                    country_code="us",
                ),
                identity_documents=(
                    PassengerIdentityDocument(IdentityDocumentType.PASSPORT, document_number="P123456789"),
                ),
            ),
            profile_id="traveler_sensitive",
            created_at=datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc),
        )
        store.save(profile)

        masked = store.get("user_123", "traveler_sensitive")
        restricted = store.get("user_123", "traveler_sensitive", include_pii=True)

        self.assertEqual(masked.pii.given_name, "N***")
        self.assertEqual(masked.pii.family_name, "S***")
        self.assertEqual(masked.pii.contact_details.email, "t***@example.com")
        self.assertEqual(masked.pii.contact_details.phone_number, "***0199")
        self.assertEqual(masked.pii.contact_details.address_line1, "***")
        self.assertEqual(masked.pii.contact_details.country_code, "US")
        self.assertEqual(masked.pii.identity_documents[0].document_number, "***6789")
        self.assertEqual(restricted.pii.given_name, "Nirav")
        self.assertEqual(restricted.pii.contact_details.email, "traveler@example.com")

    def test_serialized_profile_can_include_masked_or_restricted_pii(self):
        profile = TravelerProfile(
            owner_user_id="user_123",
            traveler_type=TravelerType.ADULT,
            pii=TravelerPII(given_name="Nirav", family_name="Shah"),
            preferences=TravelPreferences(
                preferred_cabin=TravelCabinPreference.BUSINESS,
                seat_preference="aisle",
                loyalty_programs={"UA": "12345"},
                accessibility_requests=("wheelchair_assistance",),
            ),
            profile_id="traveler_123",
            label="Me",
            created_at=datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc),
            updated_at=datetime(2026, 7, 7, 17, 1, tzinfo=timezone.utc),
        )

        public_data = profile.to_dict()
        restricted_data = profile.to_dict(include_pii=True, masked=False)

        self.assertEqual(public_data["pii"]["givenName"], "N***")
        self.assertEqual(restricted_data["pii"]["givenName"], "Nirav")
        self.assertEqual(public_data["preferences"]["preferredCabin"], "business")
        self.assertEqual(public_data["preferences"]["loyaltyPrograms"], {"UA": "12345"})
        self.assertEqual(public_data["createdAt"], "2026-07-07T17:00:00Z")


if __name__ == "__main__":
    unittest.main()
