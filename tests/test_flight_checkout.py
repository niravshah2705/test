from __future__ import annotations

from datetime import datetime, timezone

from hbw_seed import reset_and_seed
from hbw_seed.flight_checkout import (
    InMemoryBookingDraftRepository,
    build_offer_detail,
    create_booking_draft,
    handle_create_booking_draft,
    handle_offer_detail,
)
from hbw_seed.profiles import ProfileRepository


VALID_CONTACT = {"email": "traveler@example.test", "phone": "+14155550123"}
VALID_ADULT = {
    "legalGivenName": "Gale",
    "legalFamilyName": "Guest",
    "dateOfBirth": "1990-04-12",
    "passengerType": "adult",
}


def seeded_database(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    return database


def test_offer_detail_displays_timeline_fare_baggage_forms_and_available_state():
    detail = build_offer_detail("ofb_flt_multisegment", required_passenger_types=["adult", "child"])

    assert detail["id"] == "ofb_flt_multisegment"
    assert detail["isExpired"] is False
    assert detail["canContinue"] is True
    assert [item["type"] for item in detail["timeline"]] == ["segment", "layover", "segment"]
    assert detail["timeline"][1] == {"type": "layover", "airport": "ORD", "durationMinutes": 75}
    assert detail["fareSummary"]["cabin"] == "economy"
    assert detail["fareSummary"]["taxesAndFees"]["amountCents"] > 0
    assert detail["baggageSummary"] == "Baggage details unavailable"
    assert [form["passengerType"] for form in detail["requiredPassengerForms"]] == ["adult", "child"]


def test_expired_offer_detail_blocks_continue_and_checkout():
    expired_now = datetime(2031, 7, 1, 8, 0, tzinfo=timezone.utc)

    detail_response = handle_offer_detail("ofb_flt_oneway", now=expired_now)
    draft_response = handle_create_booking_draft(
        {"offerId": "ofb_flt_oneway", "contact": VALID_CONTACT, "passengers": [VALID_ADULT]},
        now=expired_now,
    )

    assert detail_response.body["data"]["status"] == "expired"
    assert detail_response.body["data"]["canContinue"] is False
    assert draft_response.status_code == 400
    assert draft_response.body["error"]["fields"] == {"offerId": ["Expired offers cannot continue to checkout."]}


def test_guest_checkout_renders_expected_forms_and_creates_passenger_snapshots():
    repository = InMemoryBookingDraftRepository()

    draft = create_booking_draft(
        {
            "offerId": "ofb_flt_oneway",
            "requiredPassengerTypes": ["adult", "child", "infant"],
            "contact": VALID_CONTACT,
            "passengers": [
                VALID_ADULT,
                {"legalGivenName": "Kira", "legalFamilyName": "Guest", "dateOfBirth": "2020-03-04", "passengerType": "child"},
                {"legalGivenName": "Ivy", "legalFamilyName": "Guest", "dateOfBirth": "2030-10-01", "passengerType": "infant"},
            ],
        },
        repository=repository,
    )

    assert draft["checkoutType"] == "guest"
    assert len(draft["offerSnapshot"]["requiredPassengerForms"]) == 3
    assert [passenger["passengerType"] for passenger in draft["passengerSnapshots"]] == ["adult", "child", "infant"]
    assert repository.get(draft["id"])["passengerSnapshots"][0]["legalName"]["fullName"] == "Gale Guest"


def test_authenticated_checkout_reuses_profiles_and_saved_edits_do_not_mutate_snapshot(tmp_path):
    database = seeded_database(tmp_path)
    profiles = ProfileRepository(str(database))
    profiles.create_contact_detail({"id": "ct_checkout", "user_id": "usr_guest", "email": "old@example.test", "phone": "+14155550000"})
    profiles.create_passenger_profile(
        {
            "id": "px_checkout",
            "user_id": "usr_guest",
            "display_name": "Gale",
            "legal_given_name": "Gale",
            "legal_family_name": "Guest",
            "date_of_birth": "1990-04-12",
            "passenger_type": "adult",
            "gender": "unspecified",
            "contact_detail_id": "ct_checkout",
        }
    )
    repository = InMemoryBookingDraftRepository()

    draft = create_booking_draft(
        {
            "offerId": "ofb_flt_oneway",
            "userId": "usr_guest",
            "contact": VALID_CONTACT,
            "passengers": [{"profileId": "px_checkout", "legalFamilyName": "Updated", "saveToProfile": True}],
        },
        repository=repository,
        profile_repository=profiles,
    )
    profiles.update_passenger_profile("usr_guest", "px_checkout", {"legal_family_name": "EditedLater"})

    assert draft["checkoutType"] == "authenticated"
    assert profiles.get_passenger_profile("usr_guest", "px_checkout")["legalName"]["familyName"] == "EditedLater"
    saved = repository.get(draft["id"])
    assert saved["passengerSnapshots"][0]["passengerProfileId"] == "px_checkout"
    assert saved["passengerSnapshots"][0]["legalName"]["familyName"] == "Updated"


def test_checkout_validation_is_field_level_for_contact_names_age_infants_and_form_count():
    response = handle_create_booking_draft(
        {
            "offerId": "ofb_flt_oneway",
            "requiredPassengerTypes": ["adult", "infant"],
            "contact": {"email": "bad", "phone": "x"},
            "passengers": [
                {"legalGivenName": "TooLongNameTooLongNameTooLongNameTooLongName", "legalFamilyName": "Guest", "dateOfBirth": "2020-01-01", "passengerType": "adult"},
            ],
        }
    )

    fields = response.body["error"]["fields"]
    assert response.status_code == 400
    assert fields["contact.email"] == ["Contact email must be a valid address."]
    assert fields["contact.phone"] == ["Contact phone must be a valid phone number."]
    assert fields["passengers"] == ["Expected 2 passenger form(s)."]
    assert fields["passengers[0].legalGivenName"] == ["Use 1-40 letters, spaces, apostrophes, or hyphens."]
    assert fields["passengers[0].dateOfBirth"] == ["Adult travelers must be at least 18 on the travel date."]


def test_international_itinerary_requires_document_metadata():
    class InternationalProvider:
        def getOfferDetails(self, offer_id):
            return {
                "id": offer_id,
                "provider": "fixture",
                "providerOfferId": "native-intl",
                "itineraries": [
                    {
                        "id": "intl",
                        "segments": [
                            {
                                "id": "seg_jfk_lhr",
                                "marketingCarrier": "OA",
                                "flightNumber": "OA7",
                                "origin": "JFK",
                                "destination": "LHR",
                                "departsAt": "2031-07-01T20:00:00-04:00",
                                "arrivesAt": "2031-07-02T08:00:00+01:00",
                                "durationMinutes": 420,
                            }
                        ],
                    }
                ],
                "pricing": {"total": {"amount": 90000, "currency": "USD"}},
                "passengerCount": 1,
                "cabin": "business",
                "refundable": True,
                "baggage": {"checkedBagsIncluded": 2},
                "status": "available",
                "expiresAt": "2031-07-01T07:45:00Z",
            }

    detail = build_offer_detail("ofb_flt_intl", provider=InternationalProvider())
    invalid = handle_create_booking_draft(
        {"offerId": "ofb_flt_intl", "contact": VALID_CONTACT, "passengers": [VALID_ADULT]},
        provider=InternationalProvider(),
    )
    valid = handle_create_booking_draft(
        {
            "offerId": "ofb_flt_intl",
            "contact": VALID_CONTACT,
            "passengers": [
                {
                    **VALID_ADULT,
                    "gender": "unspecified",
                    "document": {"documentType": "passport", "issuingCountry": "US", "nationalityCountry": "US", "expiresOn": "2035-01-01", "documentNumberLast4": "1a2b"},
                }
            ],
        },
        provider=InternationalProvider(),
    )

    assert detail["documentRequirements"] == {"required": True, "acceptedTypes": ["national_id", "passport"]}
    assert invalid.body["error"]["fields"]["passengers[0].gender"] == ["Gender is required for this itinerary."]
    assert invalid.body["error"]["fields"]["passengers[0].document.documentType"] == ["Document type must be passport or national_id."]
    assert valid.status_code == 201
    assert valid.body["data"]["passengerSnapshots"][0]["document"]["documentNumberLast4"] == "1A2B"


def test_checkout_rejects_full_document_numbers_without_echoing_sensitive_values():
    class InternationalProvider:
        def getOfferDetails(self, offer_id):
            return {
                "id": offer_id,
                "provider": "fixture",
                "providerOfferId": "native-intl",
                "itineraries": [
                    {
                        "id": "intl",
                        "segments": [
                            {
                                "id": "seg_jfk_lhr",
                                "marketingCarrier": "OA",
                                "flightNumber": "OA7",
                                "origin": "JFK",
                                "destination": "LHR",
                                "departsAt": "2031-07-01T20:00:00-04:00",
                                "arrivesAt": "2031-07-02T08:00:00+01:00",
                                "durationMinutes": 420,
                            }
                        ],
                    }
                ],
                "pricing": {"total": {"amount": 90000, "currency": "USD"}},
                "passengerCount": 1,
                "cabin": "business",
                "refundable": True,
                "baggage": {"checkedBagsIncluded": 2},
                "status": "available",
                "expiresAt": "2031-07-01T07:45:00Z",
            }

    response = handle_create_booking_draft(
        {
            "offerId": "ofb_flt_intl",
            "contact": VALID_CONTACT,
            "passengers": [
                {
                    **VALID_ADULT,
                    "gender": "unspecified",
                    "document": {
                        "documentType": "passport",
                        "issuingCountry": "US",
                        "nationalityCountry": "US",
                        "expiresOn": "2035-01-01",
                        "documentNumber": "P123456789",
                    },
                }
            ],
        },
        provider=InternationalProvider(),
    )

    fields = response.body["error"]["fields"]
    assert response.status_code == 400
    assert fields["passengers[0].document.documentNumber"] == ["Full document numbers must be tokenized before submission; send documentNumberLast4 only."]
    assert "P123456789" not in str(response.body)
