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
    ("res_garden_family_cancelled", "htl_sfo_garden", "rt_garden_family", "room_garden_family_302", "usr_guest", "guest@example.test", "Gale Guest", "2031-05-01", "2031-05-03", "cancelled", "authenticated", 52000, "USD", "2031-03-04T11:00:00Z", "2031-03-05T11:00:00Z", None, "cnf_2b94a8af8f89429e8a7de092"),
    ("res_loft_guest_confirmed", "htl_nyc_loft", "rt_loft_queen", "room_loft_queen_801", None, "visitor@example.test", "Casey Visitor", "2031-07-05", "2031-07-07", "confirmed", "guest", 42000, "USD", "2031-03-04T12:00:00Z", None, None, "cnf_visitor_safe_lookup"),
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
