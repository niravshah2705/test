import sqlite3
from datetime import date, timedelta

from hbw_seed import reset_and_seed
from hbw_seed.profiles import (
    ProfileAuthorizationError,
    ProfileNotFoundError,
    ProfileRepository,
    ProfileValidationError,
)


def seeded_database(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    return database


def assert_raises(expected_exception, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except expected_exception as exc:
        return exc
    raise AssertionError(f"Expected {expected_exception.__name__}")


def test_profile_schema_supports_users_profiles_contacts_passengers_and_documents(tmp_path):
    database = seeded_database(tmp_path)

    with sqlite3.connect(database) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        passenger_columns = {row[1] for row in connection.execute("PRAGMA table_info(passenger_profiles)")}

    assert {"users", "user_profiles", "contact_details", "passenger_profiles", "passenger_documents"} <= tables
    assert {"user_id", "display_name", "legal_given_name", "legal_family_name", "date_of_birth", "passenger_type", "contact_detail_id"} <= passenger_columns


def test_create_and_list_reusable_passenger_profiles_with_shared_contact(tmp_path):
    database = seeded_database(tmp_path)
    repository = ProfileRepository(str(database))

    user_profile = repository.create_user_profile(
        {
            "user_id": "usr_guest",
            "display_name": "Gale",
            "legal_given_name": "Gale",
            "legal_family_name": "Guest",
            "date_of_birth": "1990-04-12",
            "country_code": "US",
        }
    )
    contact = repository.create_contact_detail(
        {
            "id": "ct_family",
            "user_id": "usr_guest",
            "label": "family travel",
            "email": "family@example.test",
            "phone": "+14155550123",
        }
    )
    traveler = repository.create_passenger_profile(
        {
            "id": "px_self",
            "user_id": "usr_guest",
            "display_name": "Gale",
            "legal_given_name": "Gale",
            "legal_middle_name": "A",
            "legal_family_name": "Guest",
            "date_of_birth": "1990-04-12",
            "passenger_type": "adult",
            "gender": "unspecified",
            "contact_detail_id": contact["id"],
        }
    )
    family = repository.create_passenger_profile(
        {
            "id": "px_family",
            "user_id": "usr_guest",
            "display_name": "Mom",
            "legal_given_name": "Mira",
            "legal_family_name": "Guest",
            "date_of_birth": "1960-08-20",
            "passenger_type": "adult",
            "contact_detail_id": contact["id"],
        }
    )
    document = repository.create_passenger_document(
        {
            "id": "doc_family_passport",
            "user_id": "usr_guest",
            "passenger_profile_id": family["id"],
            "document_type": "passport",
            "issuing_country": "US",
            "nationality_country": "US",
            "expires_on": "2035-01-01",
            "document_number_last4": "1a2b",
        },
        itinerary_requires_expiry=True,
        travel_date="2031-06-10",
    )

    passengers = repository.list_passenger_profiles("usr_guest")
    contacts = repository.list_contact_details("usr_guest")
    documents = repository.list_passenger_documents("usr_guest", "px_family")

    assert user_profile["legalName"]["fullName"] == "Gale Guest"
    assert traveler["legalName"]["fullName"] == "Gale A Guest"
    assert family["displayName"] == "Mom"
    assert family["legalName"]["fullName"] == "Mira Guest"
    assert {passenger["id"] for passenger in passengers} == {"px_self", "px_family"}
    assert {passenger["contactDetailId"] for passenger in passengers} == {"ct_family"}
    assert contacts == [contact]
    assert documents == [document]
    assert document["documentNumberLast4"] == "1A2B"


def test_update_passenger_contact_and_document_details(tmp_path):
    database = seeded_database(tmp_path)
    repository = ProfileRepository(str(database))

    repository.create_contact_detail({"id": "ct_work", "user_id": "usr_guest", "label": "work", "email": "old@example.test", "phone": "+14155550100"})
    repository.create_passenger_profile(
        {
            "id": "px_coworker",
            "user_id": "usr_guest",
            "display_name": "Alex",
            "legal_given_name": "Alexander",
            "legal_family_name": "Worker",
            "date_of_birth": "1985-02-03",
            "passenger_type": "adult",
            "contact_detail_id": "ct_work",
        }
    )
    repository.create_passenger_document(
        {
            "id": "doc_coworker",
            "user_id": "usr_guest",
            "passenger_profile_id": "px_coworker",
            "document_type": "passport",
            "issuing_country": "CA",
            "expires_on": "2032-01-01",
        },
        itinerary_requires_expiry=True,
        travel_date="2031-01-01",
    )

    contact = repository.update_contact_detail("usr_guest", "ct_work", {"email": "new@example.test", "phone": "+14155550199"})
    passenger = repository.update_passenger_profile("usr_guest", "px_coworker", {"display_name": "A. Worker", "legal_given_name": "Alexandra"})
    document = repository.update_passenger_document("usr_guest", "doc_coworker", {"issuing_country": "GB", "expires_on": "2036-05-01"}, itinerary_requires_expiry=True, travel_date="2031-01-01")

    assert contact["email"] == "new@example.test"
    assert passenger["displayName"] == "A. Worker"
    assert passenger["legalName"]["fullName"] == "Alexandra Worker"
    assert document["issuingCountry"] == "GB"
    assert document["expiresOn"] == "2036-05-01"


def test_ownership_checks_block_cross_user_access(tmp_path):
    database = seeded_database(tmp_path)
    repository = ProfileRepository(str(database))

    repository.create_contact_detail({"id": "ct_guest", "user_id": "usr_guest", "label": "guest", "email": "guest@example.test", "phone": "+14155550123"})
    repository.create_passenger_profile(
        {
            "id": "px_guest_child",
            "user_id": "usr_guest",
            "display_name": "Kid",
            "legal_given_name": "Kira",
            "legal_family_name": "Guest",
            "date_of_birth": "2015-03-04",
            "passenger_type": "child",
            "contact_detail_id": "ct_guest",
        }
    )
    repository.create_passenger_document(
        {
            "id": "doc_guest_child",
            "user_id": "usr_guest",
            "passenger_profile_id": "px_guest_child",
            "document_type": "passport",
            "issuing_country": "US",
            "expires_on": "2034-01-01",
        },
        itinerary_requires_expiry=True,
        travel_date="2031-01-01",
    )

    assert_raises(ProfileAuthorizationError, repository.get_passenger_profile, "usr_admin", "px_guest_child")
    assert_raises(ProfileAuthorizationError, repository.update_passenger_profile, "usr_admin", "px_guest_child", {"display_name": "Nope"})
    assert_raises(ProfileAuthorizationError, repository.get_contact_detail, "usr_admin", "ct_guest")
    assert_raises(ProfileAuthorizationError, repository.get_passenger_document, "usr_admin", "doc_guest_child")
    assert_raises(ProfileAuthorizationError, repository.create_passenger_profile, {"id": "px_bad", "user_id": "usr_admin", "legal_given_name": "Bad", "legal_family_name": "Actor", "date_of_birth": "1980-01-01", "passenger_type": "adult", "contact_detail_id": "ct_guest"})
    assert_raises(ProfileNotFoundError, repository.get_passenger_profile, "usr_guest", "px_missing")


def test_validation_covers_required_fields_dates_country_codes_and_document_expiry(tmp_path):
    database = seeded_database(tmp_path)
    repository = ProfileRepository(str(database))
    future_birthdate = (date.today() + timedelta(days=1)).isoformat()

    assert_raises(ProfileValidationError, repository.create_user_profile, {"user_id": "usr_guest", "legal_given_name": "Gale", "date_of_birth": "1990-01-01", "country_code": "US"})
    assert_raises(ProfileValidationError, repository.create_user_profile, {"user_id": "usr_guest", "legal_given_name": "Gale", "legal_family_name": "Guest", "date_of_birth": future_birthdate, "country_code": "US"})
    assert_raises(ProfileValidationError, repository.create_user_profile, {"user_id": "usr_guest", "legal_given_name": "Gale", "legal_family_name": "Guest", "date_of_birth": "1990-01-01", "country_code": "USA"})
    assert_raises(ProfileValidationError, repository.create_contact_detail, {"id": "ct_bad", "user_id": "usr_guest", "email": "not-an-email", "phone": "+14155550123"})
    assert_raises(ProfileValidationError, repository.create_passenger_profile, {"id": "px_bad", "user_id": "usr_guest", "legal_given_name": "Bad", "legal_family_name": "Type", "date_of_birth": "2010-01-01", "passenger_type": "senior"})

    repository.create_passenger_profile(
        {
            "id": "px_doc_validation",
            "user_id": "usr_guest",
            "legal_given_name": "Doc",
            "legal_family_name": "Validation",
            "date_of_birth": "1999-01-01",
            "passenger_type": "adult",
        }
    )
    missing_expiry = {
        "id": "doc_missing_expiry",
        "user_id": "usr_guest",
        "passenger_profile_id": "px_doc_validation",
        "document_type": "passport",
        "issuing_country": "US",
    }
    expired = dict(missing_expiry, id="doc_expired", expires_on="2030-01-01")

    assert_raises(ProfileValidationError, repository.create_passenger_document, missing_expiry, itinerary_requires_expiry=True, travel_date="2031-01-01")
    assert_raises(ProfileValidationError, repository.create_passenger_document, expired, itinerary_requires_expiry=True, travel_date="2031-01-01")
    assert_raises(ProfileValidationError, repository.create_passenger_document, dict(missing_expiry, id="doc_bad_country", issuing_country="usa", expires_on="2032-01-01"), itinerary_requires_expiry=True, travel_date="2031-01-01")
