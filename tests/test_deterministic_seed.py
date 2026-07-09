import sqlite3

from hbw_seed import reset_and_seed

SEARCH_START = "2031-06-10"
SEARCH_END = "2031-06-12"


def available_room_count(connection, room_type_id, start=SEARCH_START, end=SEARCH_END):
    return connection.execute(
        """
        SELECT COUNT(*)
        FROM rooms AS room
        JOIN room_types AS room_type ON room_type.id = room.room_type_id
        JOIN hotels AS hotel ON hotel.id = room_type.hotel_id
        WHERE room.room_type_id = ?
          AND room.status = 'active'
          AND NOT EXISTS (
            SELECT 1 FROM availability_blocks AS block
            WHERE block.hotel_id = hotel.id
              AND block.starts_on < ?
              AND block.ends_on > ?
              AND (
                block.block_type = 'hotel_closure'
                OR block.room_type_id = room_type.id
                OR block.room_id = room.id
              )
          )
          AND NOT EXISTS (
            SELECT 1 FROM reservations AS reservation
            WHERE reservation.room_id = room.id
              AND reservation.check_in < ?
              AND reservation.check_out > ?
              AND reservation.status IN ('confirmed', 'pending_payment')
          )
        """,
        (room_type_id, end, start, end, start),
    ).fetchone()[0]


def test_seed_can_be_rerun_and_recreates_same_counts(tmp_path):
    database = tmp_path / "hbw.sqlite3"

    first_counts = reset_and_seed(database)
    second_counts = reset_and_seed(database)

    assert first_counts == second_counts
    assert second_counts["users"] == 2
    assert second_counts["hotels"] == 3
    assert second_counts["room_types"] == 6
    assert second_counts["room_type_amenities"] == 8
    assert second_counts["rooms"] == 12
    assert second_counts["images"] == 10
    assert second_counts["hotel_images"] == 4
    assert second_counts["room_images"] == 6
    assert second_counts["reservations"] == 7
    assert second_counts["availability_blocks"] == 3


def test_seeded_hotels_are_searchable_in_multiple_cities(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT city, COUNT(*)
            FROM hotels
            WHERE status = 'active' AND is_searchable = 1
            GROUP BY city
            ORDER BY city
            """
        ).fetchall()

    assert rows == [("New York", 1), ("San Francisco", 2)]


def test_core_inventory_schema_constraints_indexes_and_relations(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        for table in {
            "users",
            "hotels",
            "images",
            "room_types",
            "rooms",
            "amenities",
            "hotel_images",
            "room_images",
            "hotel_amenities",
            "room_type_amenities",
        }:
            assert table in tables

        indexes = {
            row[1]
            for table in ("hotels", "room_types", "rooms")
            for row in connection.execute(f"PRAGMA index_list('{table}')")
        }
        assert {
            "idx_hotels_city",
            "idx_hotels_country",
            "idx_hotels_status",
            "idx_room_types_hotel_id",
            "idx_rooms_room_type_id",
        } <= indexes

        try:
            connection.execute(
                """
                INSERT INTO room_types
                VALUES ('rt_bad_capacity', 'htl_sfo_bay', 'Bad', 0, '1 king bed', 10000, 'USD', 'active', 'bad', '2031-01-01T00:00:00Z', '2031-01-01T00:00:00Z')
                """
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Room type capacity must be positive")

        try:
            connection.execute(
                """
                INSERT INTO room_types
                VALUES ('rt_bad_currency', 'htl_sfo_bay', 'Bad', 1, '1 king bed', 10000, 'usd', 'active', 'bad', '2031-01-01T00:00:00Z', '2031-01-01T00:00:00Z')
                """
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("Currency must follow supported money rules")


def test_orm_style_query_loads_hotel_inventory_media_and_amenities(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    with connection:
        hotel = connection.execute(
            "SELECT * FROM hotels WHERE slug = ?",
            ("bay-view-grand",),
        ).fetchone()
        room_types = connection.execute(
            "SELECT * FROM room_types WHERE hotel_id = ? ORDER BY nightly_rate_cents",
            (hotel["id"],),
        ).fetchall()
        rooms = connection.execute(
            """
            SELECT room.*
            FROM rooms AS room
            JOIN room_types AS room_type ON room_type.id = room.room_type_id
            WHERE room_type.hotel_id = ?
            ORDER BY room.room_number
            """,
            (hotel["id"],),
        ).fetchall()
        images = connection.execute(
            """
            SELECT image.url
            FROM images AS image
            JOIN hotel_images AS hotel_image ON hotel_image.image_id = image.id
            WHERE hotel_image.hotel_id = ?
            ORDER BY image.sort_order
            """,
            (hotel["id"],),
        ).fetchall()
        hotel_amenities = connection.execute(
            """
            SELECT amenity.name
            FROM amenities AS amenity
            JOIN hotel_amenities AS hotel_amenity ON hotel_amenity.amenity_id = amenity.id
            WHERE hotel_amenity.hotel_id = ?
            ORDER BY amenity.name
            """,
            (hotel["id"],),
        ).fetchall()
        room_type_amenities = connection.execute(
            """
            SELECT amenity.name
            FROM amenities AS amenity
            JOIN room_type_amenities AS room_type_amenity ON room_type_amenity.amenity_id = amenity.id
            WHERE room_type_amenity.room_type_id = ?
            ORDER BY amenity.name
            """,
            ("rt_bay_suite",),
        ).fetchall()

    assert hotel["status"] == "active"
    assert [room_type["name"] for room_type in room_types] == ["Deluxe King", "Executive Suite"]
    assert all(room_type["capacity"] > 0 for room_type in room_types)
    assert len(rooms) == 4
    assert len(images) == 2
    assert {row["name"] for row in hotel_amenities} == {"Breakfast", "Pool", "Spa", "Wi-Fi"}
    assert {row["name"] for row in room_type_amenities} == {"Spa", "Wi-Fi"}


def test_fixture_availability_outcomes(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)

    with sqlite3.connect(database) as connection:
        assert available_room_count(connection, "rt_bay_king") == 0  # sold out by confirmed reservations
        assert available_room_count(connection, "rt_bay_suite") == 0  # partially booked plus room maintenance block
        assert available_room_count(connection, "rt_garden_queen") == 0  # room-type closure
        assert available_room_count(connection, "rt_garden_family") == 1  # pending payment leaves one room available
        assert available_room_count(connection, "rt_loft_queen") == 0  # hotel-level closure
        assert available_room_count(connection, "rt_loft_double") == 0  # hotel-level closure


def test_cancelled_and_expired_reservations_do_not_consume_inventory(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)

    with sqlite3.connect(database) as connection:
        cancelled = connection.execute(
            "SELECT status, checkout_type FROM reservations WHERE id = 'res_bay_suite_cancelled'"
        ).fetchone()
        expired = connection.execute(
            "SELECT status, checkout_type FROM reservations WHERE id = 'res_garden_queen_expired'"
        ).fetchone()
        guest_checkout_count = connection.execute(
            "SELECT COUNT(*) FROM reservations WHERE checkout_type = 'guest'"
        ).fetchone()[0]
        authenticated_count = connection.execute(
            "SELECT COUNT(*) FROM reservations WHERE checkout_type = 'authenticated'"
        ).fetchone()[0]
        refund_count = connection.execute("SELECT COUNT(*) FROM refunds").fetchone()[0]

    assert cancelled == ("cancelled", "guest")
    assert expired == ("expired", "guest")
    assert guest_checkout_count >= 3
    assert authenticated_count >= 4
    assert refund_count == 1


def test_no_real_payment_credentials_or_production_secrets(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)

    with sqlite3.connect(database) as connection:
        providers = connection.execute("SELECT DISTINCT provider FROM payment_records").fetchall()
        references = connection.execute("SELECT provider_reference FROM payment_records").fetchall()

    assert providers == [("fixture_gateway",)]
    assert all(reference[0].startswith("fx_") for reference in references)
