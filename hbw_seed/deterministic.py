"""Deterministic seed data for Hotel Booking Workflow environments.

The module intentionally uses SQLite and fixed identifiers/dates so local
verification and automated tests can reset data and recreate the exact same
hotel, room, availability, reservation, payment, refund, review, and audit
scenarios on every run.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Iterable

FIXTURE_DATES = {
    "search_start": "2031-06-10",
    "search_end": "2031-06-12",
    "single_night_start": "2031-06-10",
    "single_night_end": "2031-06-11",
    "closure_start": "2031-06-10",
    "closure_end": "2031-06-12",
    "expired_hold_start": "2031-06-10",
    "expired_hold_end": "2031-06-12",
}

SCHEMA_STATEMENTS = [
    "PRAGMA foreign_keys = ON",
    "DROP TABLE IF EXISTS flight_baggage_summaries",
    "DROP TABLE IF EXISTS flight_passenger_type_pricing",
    "DROP TABLE IF EXISTS flight_fare_details",
    "DROP TABLE IF EXISTS flight_offer_segments",
    "DROP TABLE IF EXISTS flight_offer_itineraries",
    "DROP TABLE IF EXISTS flight_offer_provider_refs",
    "DROP TABLE IF EXISTS flight_offers",
    "DROP TABLE IF EXISTS flight_search_sessions",
    "DROP TABLE IF EXISTS flight_search_passengers",
    "DROP TABLE IF EXISTS flight_search_legs",
    "DROP TABLE IF EXISTS flight_search_requests",
    "DROP TABLE IF EXISTS flight_carriers",
    "DROP TABLE IF EXISTS audit_records",
    "DROP TABLE IF EXISTS refunds",
    "DROP TABLE IF EXISTS payment_records",
    "DROP TABLE IF EXISTS availability_blocks",
    "DROP TABLE IF EXISTS reservations",
    "DROP TABLE IF EXISTS reviews",
    "DROP TABLE IF EXISTS room_images",
    "DROP TABLE IF EXISTS hotel_images",
    "DROP TABLE IF EXISTS rooms",
    "DROP TABLE IF EXISTS room_types",
    "DROP TABLE IF EXISTS hotel_policies",
    "DROP TABLE IF EXISTS hotel_amenities",
    "DROP TABLE IF EXISTS amenities",
    "DROP TABLE IF EXISTS passenger_documents",
    "DROP TABLE IF EXISTS passenger_profiles",
    "DROP TABLE IF EXISTS contact_details",
    "DROP TABLE IF EXISTS user_profiles",
    "DROP TABLE IF EXISTS hotels",
    "DROP TABLE IF EXISTS users",
    """
    CREATE TABLE users (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('admin', 'guest')),
        is_test_account INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE flight_carriers (
        code TEXT PRIMARY KEY CHECK (length(code) BETWEEN 2 AND 3),
        name TEXT NOT NULL,
        country_code TEXT CHECK (country_code IS NULL OR (length(country_code) = 2 AND country_code = upper(country_code)))
    )
    """,
    """
    CREATE TABLE flight_search_requests (
        id TEXT PRIMARY KEY,
        user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
        trip_type TEXT NOT NULL CHECK (trip_type IN ('one_way', 'round_trip', 'multi_city')),
        cabin TEXT NOT NULL CHECK (cabin IN ('economy', 'premium_economy', 'business', 'first')),
        fare_brand TEXT,
        currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
        requested_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE flight_search_legs (
        id TEXT PRIMARY KEY,
        search_request_id TEXT NOT NULL REFERENCES flight_search_requests(id) ON DELETE CASCADE,
        leg_index INTEGER NOT NULL CHECK (leg_index >= 0),
        origin_airport_code TEXT NOT NULL CHECK (length(origin_airport_code) = 3 AND origin_airport_code = upper(origin_airport_code)),
        destination_airport_code TEXT NOT NULL CHECK (length(destination_airport_code) = 3 AND destination_airport_code = upper(destination_airport_code)),
        departure_date TEXT NOT NULL,
        UNIQUE (search_request_id, leg_index)
    )
    """,
    """
    CREATE TABLE flight_search_passengers (
        id TEXT PRIMARY KEY,
        search_request_id TEXT NOT NULL REFERENCES flight_search_requests(id) ON DELETE CASCADE,
        passenger_type TEXT NOT NULL CHECK (passenger_type IN ('adult', 'child', 'infant')),
        count INTEGER NOT NULL CHECK (count > 0),
        UNIQUE (search_request_id, passenger_type)
    )
    """,
    """
    CREATE TABLE flight_search_sessions (
        id TEXT PRIMARY KEY,
        search_request_id TEXT NOT NULL REFERENCES flight_search_requests(id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'expired')),
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE flight_offers (
        id TEXT PRIMARY KEY,
        search_session_id TEXT NOT NULL REFERENCES flight_search_sessions(id) ON DELETE CASCADE,
        trip_type TEXT NOT NULL CHECK (trip_type IN ('one_way', 'round_trip', 'multi_city')),
        source TEXT NOT NULL CHECK (source IN ('search_result', 'selected_offer')),
        status TEXT NOT NULL CHECK (status IN ('available', 'priced_changed', 'unavailable', 'expired')),
        currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
        base_amount_cents INTEGER NOT NULL CHECK (base_amount_cents >= 0),
        tax_amount_cents INTEGER NOT NULL CHECK (tax_amount_cents >= 0),
        total_amount_cents INTEGER NOT NULL CHECK (total_amount_cents >= 0),
        refundable INTEGER NOT NULL CHECK (refundable IN (0, 1)),
        changeable INTEGER NOT NULL CHECK (changeable IN (0, 1)),
        expires_at TEXT NOT NULL,
        last_ticketing_date TEXT,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE flight_offer_provider_refs (
        offer_id TEXT PRIMARY KEY REFERENCES flight_offers(id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        provider_offer_id TEXT NOT NULL,
        provider_search_id TEXT,
        reference_expires_at TEXT NOT NULL,
        revalidation_token_hash TEXT,
        sanitized_reference_version INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE flight_offer_itineraries (
        id TEXT PRIMARY KEY,
        offer_id TEXT NOT NULL REFERENCES flight_offers(id) ON DELETE CASCADE,
        itinerary_index INTEGER NOT NULL CHECK (itinerary_index >= 0),
        origin_airport_code TEXT NOT NULL CHECK (length(origin_airport_code) = 3 AND origin_airport_code = upper(origin_airport_code)),
        destination_airport_code TEXT NOT NULL CHECK (length(destination_airport_code) = 3 AND destination_airport_code = upper(destination_airport_code)),
        duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
        UNIQUE (offer_id, itinerary_index)
    )
    """,
    """
    CREATE TABLE flight_offer_segments (
        id TEXT PRIMARY KEY,
        offer_id TEXT NOT NULL REFERENCES flight_offers(id) ON DELETE CASCADE,
        itinerary_index INTEGER NOT NULL,
        segment_index INTEGER NOT NULL CHECK (segment_index >= 0),
        origin_airport_code TEXT NOT NULL CHECK (length(origin_airport_code) = 3 AND origin_airport_code = upper(origin_airport_code)),
        destination_airport_code TEXT NOT NULL CHECK (length(destination_airport_code) = 3 AND destination_airport_code = upper(destination_airport_code)),
        departure_local_datetime TEXT NOT NULL,
        departure_timezone TEXT,
        departure_utc_offset_minutes INTEGER,
        departure_terminal TEXT,
        arrival_local_datetime TEXT NOT NULL,
        arrival_timezone TEXT,
        arrival_utc_offset_minutes INTEGER,
        arrival_terminal TEXT,
        duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
        overnight INTEGER NOT NULL CHECK (overnight IN (0, 1)),
        marketing_carrier_code TEXT NOT NULL REFERENCES flight_carriers(code),
        operating_carrier_code TEXT NOT NULL REFERENCES flight_carriers(code),
        flight_number TEXT NOT NULL,
        aircraft_code TEXT,
        booking_class TEXT,
        cabin TEXT NOT NULL CHECK (cabin IN ('economy', 'premium_economy', 'business', 'first')),
        fare_brand TEXT,
        UNIQUE (offer_id, itinerary_index, segment_index),
        FOREIGN KEY (offer_id, itinerary_index) REFERENCES flight_offer_itineraries(offer_id, itinerary_index) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE flight_fare_details (
        id TEXT PRIMARY KEY,
        offer_id TEXT NOT NULL REFERENCES flight_offers(id) ON DELETE CASCADE,
        segment_id TEXT REFERENCES flight_offer_segments(id) ON DELETE CASCADE,
        passenger_type TEXT NOT NULL CHECK (passenger_type IN ('adult', 'child', 'infant')),
        cabin TEXT NOT NULL CHECK (cabin IN ('economy', 'premium_economy', 'business', 'first')),
        fare_brand TEXT,
        fare_basis_code TEXT,
        booking_class TEXT
    )
    """,
    """
    CREATE TABLE flight_passenger_type_pricing (
        id TEXT PRIMARY KEY,
        offer_id TEXT NOT NULL REFERENCES flight_offers(id) ON DELETE CASCADE,
        passenger_type TEXT NOT NULL CHECK (passenger_type IN ('adult', 'child', 'infant')),
        passenger_count INTEGER NOT NULL CHECK (passenger_count > 0),
        currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
        base_amount_cents INTEGER NOT NULL CHECK (base_amount_cents >= 0),
        tax_amount_cents INTEGER NOT NULL CHECK (tax_amount_cents >= 0),
        total_amount_cents INTEGER NOT NULL CHECK (total_amount_cents >= 0),
        UNIQUE (offer_id, passenger_type)
    )
    """,
    """
    CREATE TABLE flight_baggage_summaries (
        id TEXT PRIMARY KEY,
        offer_id TEXT NOT NULL REFERENCES flight_offers(id) ON DELETE CASCADE,
        segment_id TEXT REFERENCES flight_offer_segments(id) ON DELETE CASCADE,
        passenger_type TEXT NOT NULL CHECK (passenger_type IN ('adult', 'child', 'infant')),
        carry_on_pieces INTEGER,
        checked_pieces INTEGER,
        checked_weight_kg INTEGER,
        description TEXT
    )
    """,
    """
    CREATE TABLE user_profiles (
        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        display_name TEXT,
        legal_given_name TEXT NOT NULL,
        legal_middle_name TEXT,
        legal_family_name TEXT NOT NULL,
        date_of_birth TEXT NOT NULL,
        gender TEXT CHECK (gender IS NULL OR gender IN ('female', 'male', 'non_binary', 'unspecified')),
        country_code TEXT NOT NULL CHECK (length(country_code) = 2 AND country_code = upper(country_code))
    )
    """,
    """
    CREATE TABLE contact_details (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        label TEXT NOT NULL,
        email TEXT NOT NULL,
        phone TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE passenger_profiles (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        display_name TEXT,
        legal_given_name TEXT NOT NULL,
        legal_middle_name TEXT,
        legal_family_name TEXT NOT NULL,
        date_of_birth TEXT NOT NULL,
        passenger_type TEXT NOT NULL CHECK (passenger_type IN ('adult', 'child', 'infant')),
        gender TEXT CHECK (gender IS NULL OR gender IN ('female', 'male', 'non_binary', 'unspecified')),
        contact_detail_id TEXT REFERENCES contact_details(id) ON DELETE SET NULL
    )
    """,
    """
    CREATE TABLE passenger_documents (
        id TEXT PRIMARY KEY,
        passenger_profile_id TEXT NOT NULL REFERENCES passenger_profiles(id) ON DELETE CASCADE,
        document_type TEXT NOT NULL CHECK (document_type IN ('passport', 'national_id', 'drivers_license', 'known_traveler', 'redress')),
        issuing_country TEXT NOT NULL CHECK (length(issuing_country) = 2 AND issuing_country = upper(issuing_country)),
        nationality_country TEXT CHECK (nationality_country IS NULL OR (length(nationality_country) = 2 AND nationality_country = upper(nationality_country))),
        expires_on TEXT,
        document_number_last4 TEXT CHECK (document_number_last4 IS NULL OR length(document_number_last4) = 4)
    )
    """,
    """
    CREATE TABLE hotels (
        id TEXT PRIMARY KEY,
        slug TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        city TEXT NOT NULL,
        country TEXT NOT NULL,
        address TEXT NOT NULL,
        star_rating INTEGER NOT NULL,
        is_searchable INTEGER NOT NULL DEFAULT 1,
        description TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE amenities (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE
    )
    """,
    """
    CREATE TABLE hotel_amenities (
        hotel_id TEXT NOT NULL REFERENCES hotels(id) ON DELETE CASCADE,
        amenity_id TEXT NOT NULL REFERENCES amenities(id) ON DELETE CASCADE,
        PRIMARY KEY (hotel_id, amenity_id)
    )
    """,
    """
    CREATE TABLE hotel_policies (
        id TEXT PRIMARY KEY,
        hotel_id TEXT NOT NULL REFERENCES hotels(id) ON DELETE CASCADE,
        policy_type TEXT NOT NULL,
        description TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE room_types (
        id TEXT PRIMARY KEY,
        hotel_id TEXT NOT NULL REFERENCES hotels(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        capacity INTEGER NOT NULL,
        nightly_rate_cents INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        description TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE rooms (
        id TEXT PRIMARY KEY,
        room_type_id TEXT NOT NULL REFERENCES room_types(id) ON DELETE CASCADE,
        room_number TEXT NOT NULL,
        floor INTEGER NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('active', 'maintenance')),
        UNIQUE (room_type_id, room_number)
    )
    """,
    """
    CREATE TABLE hotel_images (
        id TEXT PRIMARY KEY,
        hotel_id TEXT NOT NULL REFERENCES hotels(id) ON DELETE CASCADE,
        url TEXT NOT NULL,
        alt_text TEXT NOT NULL,
        sort_order INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE room_images (
        id TEXT PRIMARY KEY,
        room_type_id TEXT NOT NULL REFERENCES room_types(id) ON DELETE CASCADE,
        url TEXT NOT NULL,
        alt_text TEXT NOT NULL,
        sort_order INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE reviews (
        id TEXT PRIMARY KEY,
        hotel_id TEXT NOT NULL REFERENCES hotels(id) ON DELETE CASCADE,
        user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
        author_name TEXT NOT NULL,
        rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('published', 'unpublished')),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE reservations (
        id TEXT PRIMARY KEY,
        hotel_id TEXT NOT NULL REFERENCES hotels(id) ON DELETE CASCADE,
        room_type_id TEXT NOT NULL REFERENCES room_types(id) ON DELETE CASCADE,
        room_id TEXT REFERENCES rooms(id) ON DELETE SET NULL,
        user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
        guest_email TEXT NOT NULL,
        guest_name TEXT NOT NULL,
        check_in TEXT NOT NULL,
        check_out TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('confirmed', 'pending_payment', 'cancelled', 'expired')),
        checkout_type TEXT NOT NULL CHECK (checkout_type IN ('guest', 'authenticated')),
        total_cents INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        created_at TEXT NOT NULL,
        cancelled_at TEXT,
        expires_at TEXT,
        confirmation_secret TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE availability_blocks (
        id TEXT PRIMARY KEY,
        hotel_id TEXT NOT NULL REFERENCES hotels(id) ON DELETE CASCADE,
        room_type_id TEXT REFERENCES room_types(id) ON DELETE CASCADE,
        room_id TEXT REFERENCES rooms(id) ON DELETE CASCADE,
        block_type TEXT NOT NULL CHECK (block_type IN ('hotel_closure', 'room_type_closure', 'room_maintenance')),
        starts_on TEXT NOT NULL,
        ends_on TEXT NOT NULL,
        reason TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE payment_records (
        id TEXT PRIMARY KEY,
        reservation_id TEXT NOT NULL REFERENCES reservations(id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        provider_reference TEXT NOT NULL,
        amount_cents INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        status TEXT NOT NULL CHECK (status IN ('authorized', 'captured', 'voided', 'refunded')),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE refunds (
        id TEXT PRIMARY KEY,
        payment_record_id TEXT NOT NULL REFERENCES payment_records(id) ON DELETE CASCADE,
        amount_cents INTEGER NOT NULL,
        reason TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('succeeded', 'pending')),
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE audit_records (
        id TEXT PRIMARY KEY,
        actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
        actor_type TEXT NOT NULL CHECK (actor_type IN ('guest', 'admin', 'system', 'webhook')),
        event_type TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        metadata TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
]

USERS = [
    ("usr_admin", "admin@example.test", "Avery Admin", "admin", 1, "2031-01-01T00:00:00Z"),
    ("usr_guest", "guest@example.test", "Gale Guest", "guest", 1, "2031-01-01T00:00:00Z"),
]

FLIGHT_CARRIERS = [
    ("UA", "United Airlines", "US"),
    ("NH", "All Nippon Airways", "JP"),
    ("BA", "British Airways", "GB"),
    ("AA", "American Airlines", "US"),
    ("QF", "Qantas", "AU"),
    ("IB", "Iberia", "ES"),
]

FLIGHT_SEARCH_REQUESTS = [
    ("fsr_one_way_sfo_nrt", "usr_guest", "one_way", "economy", "standard", "USD", "2031-05-01T10:00:00Z"),
    ("fsr_round_trip_sfo_lhr", "usr_guest", "round_trip", "business", "flex", "USD", "2031-05-01T10:05:00Z"),
    ("fsr_multi_city_pacific", None, "multi_city", "premium_economy", None, "USD", "2031-05-01T10:10:00Z"),
]

FLIGHT_SEARCH_LEGS = [
    ("fsl_one_way_out", "fsr_one_way_sfo_nrt", 0, "SFO", "NRT", "2031-07-10"),
    ("fsl_round_out", "fsr_round_trip_sfo_lhr", 0, "SFO", "LHR", "2031-08-01"),
    ("fsl_round_return", "fsr_round_trip_sfo_lhr", 1, "LHR", "SFO", "2031-08-15"),
    ("fsl_multi_one", "fsr_multi_city_pacific", 0, "SFO", "HND", "2031-09-01"),
    ("fsl_multi_two", "fsr_multi_city_pacific", 1, "HND", "SYD", "2031-09-07"),
    ("fsl_multi_three", "fsr_multi_city_pacific", 2, "SYD", "LAX", "2031-09-20"),
]

FLIGHT_SEARCH_PASSENGERS = [
    ("fsp_one_adult", "fsr_one_way_sfo_nrt", "adult", 1),
    ("fsp_round_adult", "fsr_round_trip_sfo_lhr", "adult", 2),
    ("fsp_round_child", "fsr_round_trip_sfo_lhr", "child", 1),
    ("fsp_multi_adult", "fsr_multi_city_pacific", "adult", 1),
]

FLIGHT_SEARCH_SESSIONS = [
    ("fss_one_way_active", "fsr_one_way_sfo_nrt", "fixture_air", "active", "2031-05-01T10:00:03Z", "2031-05-01T10:20:03Z"),
    ("fss_round_active", "fsr_round_trip_sfo_lhr", "fixture_air", "active", "2031-05-01T10:05:03Z", "2031-05-01T10:25:03Z"),
    ("fss_multi_expired", "fsr_multi_city_pacific", "fixture_air", "expired", "2031-05-01T10:10:03Z", "2031-05-01T10:30:03Z"),
]

FLIGHT_OFFERS = [
    ("fo_one_way_codeshare", "fss_one_way_active", "one_way", "search_result", "available", "USD", 62000, 8100, 70100, 0, 1, "2031-05-01T10:15:03Z", "2031-06-10", "2031-05-01T10:00:05Z"),
    ("fo_round_trip_business", "fss_round_active", "round_trip", "selected_offer", "available", "USD", 855000, 92000, 947000, 1, 1, "2031-05-01T10:23:03Z", "2031-07-01", "2031-05-01T10:05:05Z"),
    ("fo_multi_city_expired", "fss_multi_expired", "multi_city", "selected_offer", "available", "USD", 212000, 34000, 246000, 0, 0, "2031-05-01T10:20:03Z", None, "2031-05-01T10:10:05Z"),
]

FLIGHT_OFFER_PROVIDER_REFS = [
    ("fo_one_way_codeshare", "fixture_air", "fx_offer_ow_001", "fx_search_ow_001", "2031-05-01T10:15:03Z", "sha256:oneway", 1),
    ("fo_round_trip_business", "fixture_air", "fx_offer_rt_002", "fx_search_rt_002", "2031-05-01T10:23:03Z", "sha256:round", 1),
    ("fo_multi_city_expired", "fixture_air", "fx_offer_mc_003", "fx_search_mc_003", "2031-05-01T10:20:03Z", "sha256:multi", 1),
]

FLIGHT_OFFER_ITINERARIES = [
    ("foi_one_way_0", "fo_one_way_codeshare", 0, "SFO", "NRT", 660),
    ("foi_round_0", "fo_round_trip_business", 0, "SFO", "LHR", 620),
    ("foi_round_1", "fo_round_trip_business", 1, "LHR", "SFO", 670),
    ("foi_multi_0", "fo_multi_city_expired", 0, "SFO", "HND", 650),
    ("foi_multi_1", "fo_multi_city_expired", 1, "HND", "SYD", 590),
    ("foi_multi_2", "fo_multi_city_expired", 2, "SYD", "LAX", 820),
]

FLIGHT_OFFER_SEGMENTS = [
    ("fos_one_way_codeshare_0", "fo_one_way_codeshare", 0, 0, "SFO", "NRT", "2031-07-10T11:30:00", "America/Los_Angeles", -420, "G", "2031-07-11T14:30:00", "Asia/Tokyo", 540, "1", 660, 1, "UA", "NH", "837", "789", "K", "economy", "standard"),
    ("fos_round_out_0", "fo_round_trip_business", 0, 0, "SFO", "LHR", "2031-08-01T19:15:00", "America/Los_Angeles", -420, "I", "2031-08-02T13:35:00", "Europe/London", 60, "5", 620, 1, "BA", "BA", "286", "388", "J", "business", "flex"),
    ("fos_round_return_0", "fo_round_trip_business", 1, 0, "LHR", "SFO", "2031-08-15T15:10:00", "Europe/London", 60, "5", "2031-08-15T18:20:00", "America/Los_Angeles", -420, "I", 670, 0, "BA", "AA", "285", "777", "J", "business", "flex"),
    ("fos_multi_0", "fo_multi_city_expired", 0, 0, "SFO", "HND", "2031-09-01T12:10:00", "America/Los_Angeles", -420, None, "2031-09-02T15:00:00", "Asia/Tokyo", 540, None, 650, 1, "UA", "NH", "875", None, "W", "premium_economy", None),
    ("fos_multi_1", "fo_multi_city_expired", 1, 0, "HND", "SYD", "2031-09-07T22:00:00", "Asia/Tokyo", 540, "3", "2031-09-08T08:50:00", "Australia/Sydney", 600, "1", 590, 1, "QF", "QF", "26", "333", "T", "premium_economy", None),
    ("fos_multi_2", "fo_multi_city_expired", 2, 0, "SYD", "LAX", "2031-09-20T10:20:00", "Australia/Sydney", 600, "1", "2031-09-20T06:00:00", "America/Los_Angeles", -420, "B", 820, 0, "QF", "AA", "11", "388", "T", "premium_economy", None),
]

FLIGHT_FARE_DETAILS = [
    ("ffd_one_adult", "fo_one_way_codeshare", "fos_one_way_codeshare_0", "adult", "economy", "standard", "KFIXOW", "K"),
    ("ffd_round_adult_out", "fo_round_trip_business", "fos_round_out_0", "adult", "business", "flex", "JFIXRT", "J"),
    ("ffd_round_child_out", "fo_round_trip_business", "fos_round_out_0", "child", "business", "flex", "JFIXCH", "J"),
    ("ffd_round_adult_ret", "fo_round_trip_business", "fos_round_return_0", "adult", "business", "flex", "JFIXRT", "J"),
    ("ffd_round_child_ret", "fo_round_trip_business", "fos_round_return_0", "child", "business", "flex", "JFIXCH", "J"),
    ("ffd_multi_adult", "fo_multi_city_expired", None, "adult", "premium_economy", None, None, "T"),
]

FLIGHT_PASSENGER_TYPE_PRICING = [
    ("fptp_one_adult", "fo_one_way_codeshare", "adult", 1, "USD", 62000, 8100, 70100),
    ("fptp_round_adult", "fo_round_trip_business", "adult", 2, "USD", 620000, 70000, 690000),
    ("fptp_round_child", "fo_round_trip_business", "child", 1, "USD", 235000, 22000, 257000),
    ("fptp_multi_adult", "fo_multi_city_expired", "adult", 1, "USD", 212000, 34000, 246000),
]

FLIGHT_BAGGAGE_SUMMARIES = [
    ("fbs_one_adult", "fo_one_way_codeshare", "fos_one_way_codeshare_0", "adult", 1, 1, 23, "One carry-on and one checked bag up to 23 kg."),
    ("fbs_round_adult", "fo_round_trip_business", None, "adult", 2, 2, 32, "Two carry-ons and two checked bags up to 32 kg each."),
    ("fbs_round_child", "fo_round_trip_business", None, "child", 1, 1, 23, "Child allowance includes one checked bag."),
    ("fbs_multi_adult", "fo_multi_city_expired", None, "adult", None, None, None, "Provider did not include baggage allowance."),
]

HOTELS = [
    (
        "htl_sfo_bay",
        "bay-view-grand",
        "Bay View Grand",
        "San Francisco",
        "US",
        "100 Market Street, San Francisco, CA",
        5,
        1,
        "Waterfront business hotel used for partial availability and sold-out fixtures.",
    ),
    (
        "htl_sfo_garden",
        "mission-garden-inn",
        "Mission Garden Inn",
        "San Francisco",
        "US",
        "45 Valencia Street, San Francisco, CA",
        4,
        1,
        "Boutique hotel used for room-type closure and guest checkout fixtures.",
    ),
    (
        "htl_nyc_loft",
        "central-loft-hotel",
        "Central Loft Hotel",
        "New York",
        "US",
        "12 West 31st Street, New York, NY",
        4,
        1,
        "City hotel used for hotel-level closure fixtures.",
    ),
]

AMENITIES = [
    ("amn_wifi", "Wi-Fi"),
    ("amn_breakfast", "Breakfast"),
    ("amn_pool", "Pool"),
    ("amn_parking", "Parking"),
    ("amn_spa", "Spa"),
    ("amn_gym", "Fitness Center"),
]

HOTEL_AMENITIES = [
    ("htl_sfo_bay", "amn_wifi"),
    ("htl_sfo_bay", "amn_breakfast"),
    ("htl_sfo_bay", "amn_pool"),
    ("htl_sfo_bay", "amn_spa"),
    ("htl_sfo_garden", "amn_wifi"),
    ("htl_sfo_garden", "amn_breakfast"),
    ("htl_sfo_garden", "amn_parking"),
    ("htl_nyc_loft", "amn_wifi"),
    ("htl_nyc_loft", "amn_gym"),
    ("htl_nyc_loft", "amn_parking"),
]

POLICIES = [
    ("pol_bay_cancel", "htl_sfo_bay", "cancellation", "Free cancellation until 48 hours before check-in."),
    ("pol_bay_checkin", "htl_sfo_bay", "check_in", "Check-in after 15:00; checkout by 11:00."),
    ("pol_garden_cancel", "htl_sfo_garden", "cancellation", "One-night penalty inside 24 hours."),
    ("pol_garden_pets", "htl_sfo_garden", "pets", "Service animals welcome; pets by approval."),
    ("pol_loft_cancel", "htl_nyc_loft", "cancellation", "Non-refundable promotional rates are clearly marked."),
    ("pol_loft_checkin", "htl_nyc_loft", "check_in", "Mobile check-in available after 16:00."),
]

ROOM_TYPES = [
    ("rt_bay_king", "htl_sfo_bay", "Deluxe King", 2, 24000, "USD", "King room with bay views."),
    ("rt_bay_suite", "htl_sfo_bay", "Executive Suite", 4, 42000, "USD", "Suite with separate living area."),
    ("rt_garden_queen", "htl_sfo_garden", "Garden Queen", 2, 18000, "USD", "Quiet queen room facing the courtyard."),
    ("rt_garden_family", "htl_sfo_garden", "Family Studio", 4, 26000, "USD", "Studio with sofa bed and kitchenette."),
    ("rt_loft_queen", "htl_nyc_loft", "Loft Queen", 2, 21000, "USD", "High-ceiling queen room."),
    ("rt_loft_double", "htl_nyc_loft", "Double Double", 4, 30000, "USD", "Two double beds for groups."),
]

ROOMS = [
    ("room_bay_king_501", "rt_bay_king", "501", 5, "active"),
    ("room_bay_king_502", "rt_bay_king", "502", 5, "active"),
    ("room_bay_suite_601", "rt_bay_suite", "601", 6, "active"),
    ("room_bay_suite_602", "rt_bay_suite", "602", 6, "active"),
    ("room_garden_queen_201", "rt_garden_queen", "201", 2, "active"),
    ("room_garden_queen_202", "rt_garden_queen", "202", 2, "active"),
    ("room_garden_family_301", "rt_garden_family", "301", 3, "active"),
    ("room_garden_family_302", "rt_garden_family", "302", 3, "active"),
    ("room_loft_queen_801", "rt_loft_queen", "801", 8, "active"),
    ("room_loft_queen_802", "rt_loft_queen", "802", 8, "active"),
    ("room_loft_double_901", "rt_loft_double", "901", 9, "active"),
    ("room_loft_double_902", "rt_loft_double", "902", 9, "active"),
]

HOTEL_IMAGES = [
    ("img_bay_1", "htl_sfo_bay", "https://fixtures.example.test/hotels/bay-view-grand/exterior.jpg", "Bay View Grand exterior", 1),
    ("img_bay_2", "htl_sfo_bay", "https://fixtures.example.test/hotels/bay-view-grand/lobby.jpg", "Bay View Grand lobby", 2),
    ("img_garden_1", "htl_sfo_garden", "https://fixtures.example.test/hotels/mission-garden-inn/courtyard.jpg", "Mission Garden Inn courtyard", 1),
    ("img_loft_1", "htl_nyc_loft", "https://fixtures.example.test/hotels/central-loft-hotel/roof.jpg", "Central Loft Hotel roof deck", 1),
]

ROOM_IMAGES = [
    ("img_rt_bay_king", "rt_bay_king", "https://fixtures.example.test/rooms/deluxe-king.jpg", "Deluxe King bed", 1),
    ("img_rt_bay_suite", "rt_bay_suite", "https://fixtures.example.test/rooms/executive-suite.jpg", "Executive Suite living room", 1),
    ("img_rt_garden_queen", "rt_garden_queen", "https://fixtures.example.test/rooms/garden-queen.jpg", "Garden Queen room", 1),
    ("img_rt_garden_family", "rt_garden_family", "https://fixtures.example.test/rooms/family-studio.jpg", "Family Studio room", 1),
    ("img_rt_loft_queen", "rt_loft_queen", "https://fixtures.example.test/rooms/loft-queen.jpg", "Loft Queen room", 1),
    ("img_rt_loft_double", "rt_loft_double", "https://fixtures.example.test/rooms/double-double.jpg", "Double Double room", 1),
]

REVIEWS = [
    ("rev_bay_pub", "htl_sfo_bay", "usr_guest", "Gale Guest", 5, "Reliable stay", "Great bay views and fast check-in.", "published", "2031-02-01T12:00:00Z"),
    ("rev_bay_unpub", "htl_sfo_bay", "usr_guest", "Gale Guest", 3, "Needs moderation", "Fixture review that should not be public.", "unpublished", "2031-02-02T12:00:00Z"),
    ("rev_garden_pub", "htl_sfo_garden", None, "Pat Fixture", 4, "Quiet courtyard", "Useful guest-checkout review fixture.", "published", "2031-02-03T12:00:00Z"),
    ("rev_loft_pub", "htl_nyc_loft", "usr_guest", "Gale Guest", 4, "Central location", "Easy walk to transit.", "published", "2031-02-04T12:00:00Z"),
]

RESERVATIONS = [
    # Sold-out Deluxe King: both physical rooms confirmed for the search window.
    ("res_bay_king_guest_confirmed", "htl_sfo_bay", "rt_bay_king", "room_bay_king_501", None, "walkup@example.test", "Wanda Walkup", "2031-06-10", "2031-06-12", "confirmed", "guest", 48000, "USD", "2031-03-01T10:00:00Z", None, None, "cnf_9e45f6c9baf04c2c8d3f1a72"),
    ("res_bay_king_auth_confirmed", "htl_sfo_bay", "rt_bay_king", "room_bay_king_502", "usr_guest", "guest@example.test", "Gale Guest", "2031-06-10", "2031-06-12", "confirmed", "authenticated", 48000, "USD", "2031-03-01T11:00:00Z", None, None, "cnf_1f2b7a8d0e6946bea6f9c831"),
    # Partial availability: one suite occupied, one suite still bookable.
    ("res_bay_suite_confirmed", "htl_sfo_bay", "rt_bay_suite", "room_bay_suite_601", "usr_guest", "guest@example.test", "Gale Guest", "2031-06-10", "2031-06-12", "confirmed", "authenticated", 84000, "USD", "2031-03-02T10:00:00Z", None, None, "cnf_7b0d19f643524acdb122d6aa"),
    # Pending payment holds availability until checkout flow succeeds or expires.
    ("res_garden_family_pending", "htl_sfo_garden", "rt_garden_family", "room_garden_family_301", "usr_guest", "guest@example.test", "Gale Guest", "2031-06-10", "2031-06-12", "pending_payment", "authenticated", 52000, "USD", "2031-03-03T10:00:00Z", None, "2031-06-09T23:59:00Z", "cnf_a6d0c3e91f8b41eba5d7290f"),
    # Cancelled and expired reservations should not consume availability.
    ("res_bay_suite_cancelled", "htl_sfo_bay", "rt_bay_suite", "room_bay_suite_602", None, "cancelled@example.test", "Casey Cancelled", "2031-06-10", "2031-06-12", "cancelled", "guest", 84000, "USD", "2031-03-04T10:00:00Z", "2031-03-05T10:00:00Z", None, "cnf_5c52f2f723cb4de585ed2479"),
    ("res_garden_queen_expired", "htl_sfo_garden", "rt_garden_queen", "room_garden_queen_202", None, "expired@example.test", "Elliot Expired", "2031-06-10", "2031-06-12", "expired", "guest", 36000, "USD", "2031-03-05T10:00:00Z", None, "2031-03-05T10:15:00Z", "cnf_c8a413dc9f9f4777a0e8ab36"),
    # Confirmed reservation on dates not covered by the main search window.
    ("res_loft_double_future", "htl_nyc_loft", "rt_loft_double", "room_loft_double_901", "usr_guest", "guest@example.test", "Gale Guest", "2031-07-01", "2031-07-03", "confirmed", "authenticated", 60000, "USD", "2031-03-06T10:00:00Z", None, None, "cnf_ee92874cf51b427bb68bb5de"),
]

AVAILABILITY_BLOCKS = [
    ("blk_loft_hotel_closed", "htl_nyc_loft", None, None, "hotel_closure", "2031-06-10", "2031-06-12", "Annual building systems test; all room types unavailable."),
    ("blk_garden_queen_closed", "htl_sfo_garden", "rt_garden_queen", None, "room_type_closure", "2031-06-10", "2031-06-12", "Courtyard plumbing work; Garden Queen unavailable."),
    ("blk_bay_suite_maint", "htl_sfo_bay", "rt_bay_suite", "room_bay_suite_602", "room_maintenance", "2031-06-10", "2031-06-11", "HVAC maintenance blocks one suite for one night."),
]

PAYMENTS = [
    ("pay_bay_king_guest", "res_bay_king_guest_confirmed", "fixture_gateway", "fx_auth_guest_0001", 48000, "USD", "captured", "2031-03-01T10:05:00Z"),
    ("pay_bay_king_auth", "res_bay_king_auth_confirmed", "fixture_gateway", "fx_auth_user_0002", 48000, "USD", "captured", "2031-03-01T11:05:00Z"),
    ("pay_bay_suite_confirmed", "res_bay_suite_confirmed", "fixture_gateway", "fx_auth_user_0003", 84000, "USD", "captured", "2031-03-02T10:05:00Z"),
    ("pay_garden_family_pending", "res_garden_family_pending", "fixture_gateway", "fx_pending_user_0004", 52000, "USD", "authorized", "2031-03-03T10:05:00Z"),
    ("pay_bay_suite_cancelled", "res_bay_suite_cancelled", "fixture_gateway", "fx_refund_guest_0005", 84000, "USD", "refunded", "2031-03-04T10:05:00Z"),
]

REFUNDS = [
    ("ref_bay_suite_cancelled", "pay_bay_suite_cancelled", 84000, "Guest cancelled within deterministic fixture window.", "succeeded", "2031-03-05T10:05:00Z"),
]

AUDITS = [
    ("aud_seed_run", "usr_admin", "admin", "seed.reset", "seed_dataset", "deterministic_hbw", '{"source":"NIR-510"}', "2031-03-01T00:00:00Z"),
    ("aud_hotel_closure", "usr_admin", "admin", "availability_block.created", "availability_block", "blk_loft_hotel_closed", '{"scenario":"hotel-level closure","auditWritePolicy":"blocking for admin inventory mutations"}', "2031-03-01T00:01:00Z"),
    ("aud_room_type_closure", "usr_admin", "admin", "availability_block.created", "availability_block", "blk_garden_queen_closed", '{"scenario":"room-type closure","auditWritePolicy":"blocking for admin inventory mutations"}', "2031-03-01T00:02:00Z"),
    ("aud_refund", "usr_admin", "admin", "refund.created", "refund", "ref_bay_suite_cancelled", '{"scenario":"cancelled reservation refund","auditWritePolicy":"best effort; cancellation correctness wins"}', "2031-03-05T10:06:00Z"),
]


def _insert_many(connection: sqlite3.Connection, table: str, rows: Iterable[tuple]) -> None:
    rows = list(rows)
    if not rows:
        return
    placeholders = ", ".join("?" for _ in rows[0])
    connection.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)


def reset_and_seed(database_path: str | Path) -> dict[str, int]:
    """Reset ``database_path`` and load the deterministic fixture set.

    Args:
        database_path: SQLite database path. Parent directories are created when
            needed. Use ``:memory:`` for in-memory test databases.

    Returns:
        A table-name to row-count summary for quick smoke assertions.
    """

    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    try:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)

        _insert_many(connection, "users", USERS)
        _insert_many(connection, "flight_carriers", FLIGHT_CARRIERS)
        _insert_many(connection, "flight_search_requests", FLIGHT_SEARCH_REQUESTS)
        _insert_many(connection, "flight_search_legs", FLIGHT_SEARCH_LEGS)
        _insert_many(connection, "flight_search_passengers", FLIGHT_SEARCH_PASSENGERS)
        _insert_many(connection, "flight_search_sessions", FLIGHT_SEARCH_SESSIONS)
        _insert_many(connection, "flight_offers", FLIGHT_OFFERS)
        _insert_many(connection, "flight_offer_provider_refs", FLIGHT_OFFER_PROVIDER_REFS)
        _insert_many(connection, "flight_offer_itineraries", FLIGHT_OFFER_ITINERARIES)
        _insert_many(connection, "flight_offer_segments", FLIGHT_OFFER_SEGMENTS)
        _insert_many(connection, "flight_fare_details", FLIGHT_FARE_DETAILS)
        _insert_many(connection, "flight_passenger_type_pricing", FLIGHT_PASSENGER_TYPE_PRICING)
        _insert_many(connection, "flight_baggage_summaries", FLIGHT_BAGGAGE_SUMMARIES)
        _insert_many(connection, "hotels", HOTELS)
        _insert_many(connection, "amenities", AMENITIES)
        _insert_many(connection, "hotel_amenities", HOTEL_AMENITIES)
        _insert_many(connection, "hotel_policies", POLICIES)
        _insert_many(connection, "room_types", ROOM_TYPES)
        _insert_many(connection, "rooms", ROOMS)
        _insert_many(connection, "hotel_images", HOTEL_IMAGES)
        _insert_many(connection, "room_images", ROOM_IMAGES)
        _insert_many(connection, "reviews", REVIEWS)
        _insert_many(connection, "reservations", RESERVATIONS)
        _insert_many(connection, "availability_blocks", AVAILABILITY_BLOCKS)
        _insert_many(connection, "payment_records", PAYMENTS)
        _insert_many(connection, "refunds", REFUNDS)
        _insert_many(connection, "audit_records", AUDITS)
        connection.commit()

        tables = [
            "users",
            "flight_carriers",
            "flight_search_requests",
            "flight_search_legs",
            "flight_search_passengers",
            "flight_search_sessions",
            "flight_offers",
            "flight_offer_provider_refs",
            "flight_offer_itineraries",
            "flight_offer_segments",
            "flight_fare_details",
            "flight_passenger_type_pricing",
            "flight_baggage_summaries",
            "user_profiles",
            "contact_details",
            "passenger_profiles",
            "passenger_documents",
            "hotels",
            "amenities",
            "room_types",
            "rooms",
            "reviews",
            "reservations",
            "availability_blocks",
            "payment_records",
            "refunds",
            "audit_records",
        ]
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in tables
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset and load deterministic HBW seed data.")
    parser.add_argument(
        "database",
        nargs="?",
        default="./tmp/hbw_seed.sqlite3",
        help="SQLite database path to reset and seed (default: ./tmp/hbw_seed.sqlite3)",
    )
    args = parser.parse_args()
    counts = reset_and_seed(args.database)
    print(f"Seeded deterministic HBW fixtures into {args.database}")
    for table, count in counts.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
