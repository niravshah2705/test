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
    assert second_counts["rooms"] == 12
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
            WHERE is_searchable = 1
            GROUP BY city
            ORDER BY city
            """
        ).fetchall()

    assert rows == [("New York", 1), ("San Francisco", 2)]


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
