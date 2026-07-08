import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from backend.shared.profiles import (
    ConsentFlag,
    ConsentFlagType,
    ContactMethod,
    ContactMethodType,
    CustomerProfile,
    InMemoryCustomerProfileRepository,
    LoyaltyIdentifier,
    SavedTraveler,
    SavedTravelerType,
)


class CustomerProfilePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.created_at = datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc)
        self.repository = InMemoryCustomerProfileRepository()

    def make_profile(self, **overrides):
        values = {
            "profile_id": "profile_123",
            "customer_id": " customer_123 ",
            "display_name": " Nirav Shah ",
            "saved_travelers": (
                SavedTraveler(
                    traveler_id="traveler_primary",
                    given_name=" Nirav ",
                    family_name=" Shah ",
                    traveler_type=SavedTravelerType.ADULT,
                    loyalty_identifiers=(LoyaltyIdentifier("aa", " 12345 ", loyalty_id="loyalty_traveler_aa"),),
                    is_default=True,
                ),
            ),
            "loyalty_identifiers": (LoyaltyIdentifier("ua", " 99999 ", loyalty_id="loyalty_profile_ua"),),
            "contact_methods": (
                ContactMethod(ContactMethodType.EMAIL, " Traveler@Example.COM ", contact_id="contact_email", is_primary=True),
                ContactMethod(ContactMethodType.PHONE, "+1 212 555 0199", contact_id="contact_phone", is_primary=True),
            ),
            "consent_flags": (
                ConsentFlag(
                    ConsentFlagType.PRIVACY_POLICY,
                    True,
                    consent_id="consent_privacy",
                    captured_at=self.created_at,
                    source="checkout",
                ),
                ConsentFlag(
                    ConsentFlagType.MARKETING_EMAIL,
                    False,
                    consent_id="consent_marketing",
                    captured_at=self.created_at,
                ),
            ),
            "created_at": self.created_at,
            "updated_at": self.created_at,
        }
        values.update(overrides)
        return CustomerProfile(**values)

    def test_profile_crud_operations_create_read_update_and_delete(self):
        profile = self.repository.create(self.make_profile())

        stored = self.repository.get("profile_123")
        by_customer = self.repository.get_for_customer("customer_123")
        updated = self.repository.update(replace(profile, display_name="Nirav S", updated_at=self.created_at))
        deleted = self.repository.delete("profile_123")

        self.assertEqual(stored, profile)
        self.assertEqual(by_customer, profile)
        self.assertEqual(profile.customer_id, "customer_123")
        self.assertEqual(profile.display_name, "Nirav Shah")
        self.assertEqual(updated.display_name, "Nirav S")
        self.assertTrue(deleted)
        self.assertIsNone(self.repository.get("profile_123"))
        self.assertIsNone(self.repository.get_for_customer("customer_123"))
        self.assertFalse(self.repository.delete("profile_123"))

    def test_saved_traveler_validation_requires_names_child_birthdate_and_unique_loyalty(self):
        with self.assertRaises(ValueError):
            SavedTraveler(given_name=" ", family_name="Shah")
        with self.assertRaises(ValueError):
            SavedTraveler(given_name="Asha", family_name="Shah", traveler_type=SavedTravelerType.CHILD)
        with self.assertRaises(ValueError):
            SavedTraveler(
                given_name="Nirav",
                family_name="Shah",
                loyalty_identifiers=(
                    LoyaltyIdentifier("AA", "12345", loyalty_id="loyalty_1"),
                    LoyaltyIdentifier("aa", "67890", loyalty_id="loyalty_2"),
                ),
            )

        child = SavedTraveler(
            given_name=" Asha ",
            family_name=" Shah ",
            traveler_type=SavedTravelerType.CHILD,
            date_of_birth=date(2018, 5, 1),
        )

        self.assertEqual(child.given_name, "Asha")
        self.assertEqual(child.date_of_birth, date(2018, 5, 1))

    def test_contact_uniqueness_rules_apply_within_and_across_profiles(self):
        with self.assertRaises(ValueError):
            self.make_profile(
                contact_methods=(
                    ContactMethod(ContactMethodType.EMAIL, "traveler@example.com", contact_id="contact_1"),
                    ContactMethod(ContactMethodType.EMAIL, " TRAVELER@example.com ", contact_id="contact_2"),
                )
            )
        with self.assertRaises(ValueError):
            self.make_profile(
                contact_methods=(
                    ContactMethod(ContactMethodType.EMAIL, "one@example.com", contact_id="contact_1", is_primary=True),
                    ContactMethod(ContactMethodType.EMAIL, "two@example.com", contact_id="contact_2", is_primary=True),
                )
            )

        self.repository.create(self.make_profile())
        with self.assertRaises(ValueError):
            self.repository.create(
                self.make_profile(
                    profile_id="profile_456",
                    customer_id="customer_456",
                    contact_methods=(ContactMethod(ContactMethodType.EMAIL, "TRAVELER@example.com", contact_id="contact_other"),),
                )
            )

    def test_booking_profile_data_retrieves_prefill_travelers_loyalty_contacts_and_consents(self):
        profile = self.repository.create(self.make_profile())

        booking_data = self.repository.booking_data(profile.profile_id)

        self.assertEqual(booking_data.profile_id, "profile_123")
        self.assertEqual(booking_data.display_name, "Nirav Shah")
        self.assertEqual(booking_data.primary_email, "traveler@example.com")
        self.assertEqual(booking_data.primary_phone, "+1 212 555 0199")
        self.assertEqual(booking_data.travelers[0].full_name, "Nirav Shah")
        self.assertEqual(booking_data.travelers[0].loyalty_identifiers[0].program_code, "AA")
        self.assertEqual(booking_data.loyalty_identifiers[0].program_code, "UA")
        self.assertEqual(booking_data.consent_flags[ConsentFlagType.PRIVACY_POLICY], True)
        self.assertEqual(booking_data.consent_flags[ConsentFlagType.MARKETING_EMAIL], False)
        self.assertEqual(
            booking_data.to_dict(),
            {
                "profileId": "profile_123",
                "customerId": "customer_123",
                "displayName": "Nirav Shah",
                "primaryEmail": "traveler@example.com",
                "primaryPhone": "+1 212 555 0199",
                "travelers": [
                    {
                        "travelerId": "traveler_primary",
                        "type": "adult",
                        "givenName": "Nirav",
                        "familyName": "Shah",
                        "fullName": "Nirav Shah",
                        "loyaltyIdentifiers": [
                            {
                                "loyaltyId": "loyalty_traveler_aa",
                                "programCode": "AA",
                                "memberId": "12345",
                            }
                        ],
                        "isDefault": True,
                    }
                ],
                "loyaltyIdentifiers": [
                    {"loyaltyId": "loyalty_profile_ua", "programCode": "UA", "memberId": "99999"}
                ],
                "consentFlags": {"privacy_policy": True, "marketing_email": False},
            },
        )


if __name__ == "__main__":
    unittest.main()
