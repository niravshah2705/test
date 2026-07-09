import sqlite3

from hbw_seed import reset_and_seed
from hbw_seed.booking import (
    BookingConflict,
    BookingValidationError,
    available_room_ids,
    booking_api_cancel_reservation,
    booking_api_create_reservation,
    booking_api_get_reservation,
    calculate_total_cents,
    cancel_reservation,
    create_pending_reservation,
    expire_pending_reservation,
    format_money,
    get_reservation_for_user,
    parse_stay_dates,
    record_payment_webhook,
    validate_occupancy,
)
from hbw_seed.public_api import handle_get


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


def test_unit_date_money_and_occupancy_utilities_validate_booking_rules():
    assert parse_stay_dates("2031-06-10", "2031-06-12").nights == 2
    assert calculate_total_cents(26000, "2031-06-10", "2031-06-12") == 52000
    assert format_money(52000, "USD") == {"amountCents": 52000, "currency": "USD", "formatted": "USD 520.00"}
    assert validate_occupancy(2, 2, 4) == 4

    assert str(assert_raises(BookingValidationError, parse_stay_dates, "2031-06-12", "2031-06-10")) == "check_out must be after check_in."
    assert str(assert_raises(BookingValidationError, format_money, -1, "USD")) == "amount_cents must be non-negative."
    assert str(assert_raises(BookingValidationError, validate_occupancy, 0, 0, 2)) == "At least one adult is required."
    assert str(assert_raises(BookingValidationError, validate_occupancy, 2, 3, 4)) == "Guest count exceeds room capacity."


def test_integration_availability_supports_back_to_back_and_filters_overlaps(tmp_path):
    database = seeded_database(tmp_path)

    with sqlite3.connect(database) as connection:
        assert available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-10", "2031-06-12") == ["room_garden_family_302"]
        assert available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-12", "2031-06-14") == [
            "room_garden_family_301",
            "room_garden_family_302",
        ]
        assert available_room_ids(connection, "htl_sfo_bay", "rt_bay_king", "2031-06-11", "2031-06-12") == []
        assert available_room_ids(connection, "htl_sfo_bay", "rt_bay_king", "2031-06-12", "2031-06-13") == [
            "room_bay_king_501",
            "room_bay_king_502",
        ]


def test_reservation_creation_is_transactional_for_last_room_and_duplicate_request(tmp_path):
    database = seeded_database(tmp_path)

    first = create_pending_reservation(
        str(database),
        reservation_id="res_last_room",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        user_id="usr_guest",
        guest_email="guest@example.test",
        guest_name="Gale Guest",
        check_in="2031-06-10",
        check_out="2031-06-12",
        adults=2,
        children=1,
    )
    assert first["status"] == "pending_payment"
    assert first["roomId"] == "room_garden_family_302"
    assert first["total"] == {"amountCents": 52000, "currency": "USD", "formatted": "USD 520.00"}

    duplicate = create_pending_reservation(
        str(database),
        reservation_id="res_last_room",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        user_id="usr_guest",
        guest_email="guest@example.test",
        guest_name="Gale Guest",
        check_in="2031-06-10",
        check_out="2031-06-12",
        adults=2,
        children=1,
    )
    assert duplicate["duplicateRequest"] is True
    assert duplicate["roomId"] == first["roomId"]

    assert str(
        assert_raises(
            BookingConflict,
            create_pending_reservation,
            str(database),
            reservation_id="res_conflicting_last_room",
            hotel_id="htl_sfo_garden",
            room_type_id="rt_garden_family",
            user_id="usr_guest",
            guest_email="guest@example.test",
            guest_name="Gale Guest",
            check_in="2031-06-10",
            check_out="2031-06-12",
            adults=2,
            children=1,
        )
    ) == "No rooms available for the requested stay."

    with sqlite3.connect(database) as connection:
        count = connection.execute("SELECT COUNT(*) FROM reservations WHERE id LIKE 'res_%last_room%' ").fetchone()[0]
    assert count == 1


def test_expired_pending_reservation_releases_inventory(tmp_path):
    database = seeded_database(tmp_path)

    assert expire_pending_reservation(str(database), "res_garden_family_pending", now="2031-06-10T00:00:00Z") is True
    with sqlite3.connect(database) as connection:
        assert available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-10", "2031-06-12") == [
            "room_garden_family_301",
            "room_garden_family_302",
        ]
        status = connection.execute("SELECT status FROM reservations WHERE id = 'res_garden_family_pending'").fetchone()[0]
    assert status == "expired"


def test_payment_success_failure_duplicate_webhook_and_amount_mismatch(tmp_path):
    database = seeded_database(tmp_path)
    reservation = create_pending_reservation(
        str(database),
        reservation_id="res_payment_flow",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        user_id="usr_guest",
        guest_email="guest@example.test",
        guest_name="Gale Guest",
        check_in="2031-06-12",
        check_out="2031-06-14",
        adults=2,
        children=1,
    )

    mismatch = assert_raises(
        BookingValidationError,
        record_payment_webhook,
        str(database),
        provider_reference="fx_amount_mismatch",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"] - 100,
    )
    assert str(mismatch) == "Payment amount or currency does not match reservation total."

    success = record_payment_webhook(
        str(database),
        provider_reference="fx_success_payment_flow",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
    )
    duplicate = record_payment_webhook(
        str(database),
        provider_reference="fx_success_payment_flow",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
    )

    assert success == {"duplicate": False, "paymentId": "pay_fx_success_payment_flow", "status": "captured"}
    assert duplicate == {"duplicate": True, "paymentId": "pay_fx_success_payment_flow", "status": "captured"}
    with sqlite3.connect(database) as connection:
        reservation_status = connection.execute("SELECT status FROM reservations WHERE id = 'res_payment_flow'").fetchone()[0]
        payment_statuses = connection.execute(
            "SELECT provider_reference, status FROM payment_records WHERE reservation_id = 'res_payment_flow' ORDER BY provider_reference"
        ).fetchall()
    assert reservation_status == "confirmed"
    assert payment_statuses == [("fx_amount_mismatch", "voided"), ("fx_success_payment_flow", "captured")]


def test_cancellation_refunds_payment_and_releases_inventory(tmp_path):
    database = seeded_database(tmp_path)
    reservation = create_pending_reservation(
        str(database),
        reservation_id="res_cancel_flow",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        user_id="usr_guest",
        guest_email="guest@example.test",
        guest_name="Gale Guest",
        check_in="2031-06-12",
        check_out="2031-06-14",
        adults=2,
        children=1,
    )
    record_payment_webhook(
        str(database),
        provider_reference="fx_cancel_flow",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
    )

    cancelled = cancel_reservation(str(database), reservation_id=reservation["id"], user_id="usr_guest")

    assert cancelled["status"] == "cancelled"
    assert cancelled["refund"] == {
        "id": "ref_pay_fx_cancel_flow",
        "amount": {"amountCents": 52000, "currency": "USD", "formatted": "USD 520.00"},
        "status": "succeeded",
    }
    with sqlite3.connect(database) as connection:
        assert "room_garden_family_302" in available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-12", "2031-06-14")
        payment_status = connection.execute("SELECT status FROM payment_records WHERE provider_reference = 'fx_cancel_flow'").fetchone()[0]
    assert payment_status == "refunded"


def test_authorization_prevents_viewing_or_cancelling_another_users_booking(tmp_path):
    database = seeded_database(tmp_path)

    assert get_reservation_for_user(str(database), "res_bay_king_auth_confirmed", "usr_guest")["id"] == "res_bay_king_auth_confirmed"
    assert str(assert_raises(PermissionError, get_reservation_for_user, str(database), "res_bay_king_auth_confirmed", "usr_admin")) == "Reservation belongs to another user."
    assert str(assert_raises(PermissionError, cancel_reservation, str(database), reservation_id="res_bay_king_auth_confirmed", user_id="usr_admin")) == "Reservation belongs to another user."


def test_api_response_contracts_for_success_validation_conflict_and_forbidden(tmp_path):
    database = seeded_database(tmp_path)

    validation = booking_api_create_reservation(str(database), {"reservationId": "res_missing"})
    assert validation.status_code == 400
    assert validation.body["success"] is False
    assert validation.body["error"]["code"] == "validation_error"
    assert validation.body["error"]["fields"]["hotelId"] == ["Field is required."]

    success = booking_api_create_reservation(
        str(database),
        {
            "reservationId": "res_api_success",
            "hotelId": "htl_sfo_garden",
            "roomTypeId": "rt_garden_family",
            "userId": "usr_guest",
            "guestEmail": "guest@example.test",
            "guestName": "Gale Guest",
            "checkIn": "2031-06-12",
            "checkOut": "2031-06-14",
            "adults": 2,
            "children": 1,
        },
    )
    duplicate = booking_api_create_reservation(
        str(database),
        {
            "reservationId": "res_api_success",
            "hotelId": "htl_sfo_garden",
            "roomTypeId": "rt_garden_family",
            "userId": "usr_guest",
            "guestEmail": "guest@example.test",
            "guestName": "Gale Guest",
            "checkIn": "2031-06-12",
            "checkOut": "2031-06-14",
            "adults": 2,
            "children": 1,
        },
    )
    forbidden = booking_api_get_reservation(str(database), "res_api_success", "usr_admin")

    assert success.status_code == 201
    assert success.body["success"] is True
    assert success.body["data"]["status"] == "pending_payment"
    assert duplicate.status_code == 200
    assert duplicate.body["data"]["duplicateRequest"] is True
    assert forbidden.status_code == 403
    assert forbidden.body == {"success": False, "data": None, "error": {"code": "forbidden", "message": "Reservation belongs to another user."}}

    conflict = booking_api_create_reservation(
        str(database),
        {
            "reservationId": "res_api_conflict",
            "hotelId": "htl_sfo_bay",
            "roomTypeId": "rt_bay_king",
            "userId": "usr_guest",
            "guestEmail": "guest@example.test",
            "guestName": "Gale Guest",
            "checkIn": "2031-06-10",
            "checkOut": "2031-06-12",
            "adults": 2,
            "children": 0,
        },
    )
    assert conflict.status_code == 409
    assert conflict.body["error"] == {"code": "inventory_conflict", "message": "No rooms available for the requested stay."}


def test_e2e_guest_search_select_pay_confirm_view_and_cancel_flow(tmp_path):
    database = seeded_database(tmp_path)

    search = handle_get(
        str(database),
        "/api/search/hotels",
        "destination=San%20Francisco&checkIn=2031-06-10&checkOut=2031-06-12&adults=2&children=1&page=1&pageSize=10",
    )
    assert search.status_code == 200
    assert [hotel["slug"] for hotel in search.body["data"]] == ["mission-garden-inn"]

    availability = handle_get(
        str(database),
        "/api/hotels/mission-garden-inn/availability",
        "checkIn=2031-06-10&checkOut=2031-06-12&adults=2&children=1",
    )
    selected_room_type = availability.body["data"]["roomTypes"][0]
    assert selected_room_type["code"] == "rt_garden_family"
    assert selected_room_type["availableRooms"] == 1

    reservation = booking_api_create_reservation(
        str(database),
        {
            "reservationId": "res_e2e_guest",
            "hotelId": "htl_sfo_garden",
            "roomTypeId": selected_room_type["code"],
            "userId": "usr_guest",
            "guestEmail": "guest@example.test",
            "guestName": "Gale Guest",
            "checkIn": "2031-06-10",
            "checkOut": "2031-06-12",
            "adults": 2,
            "children": 1,
        },
    )
    assert reservation.status_code == 201
    assert reservation.body["data"]["status"] == "pending_payment"

    payment = record_payment_webhook(
        str(database),
        provider_reference="fx_e2e_guest",
        reservation_id="res_e2e_guest",
        amount_cents=reservation.body["data"]["total"]["amountCents"],
    )
    assert payment["status"] == "captured"

    confirmation = booking_api_get_reservation(str(database), "res_e2e_guest", "usr_guest")
    assert confirmation.status_code == 200
    assert confirmation.body["data"]["status"] == "confirmed"
    assert booking_api_get_reservation(str(database), "res_e2e_guest", "usr_admin").status_code == 403

    cancellation = booking_api_cancel_reservation(str(database), "res_e2e_guest", "usr_guest")
    assert cancellation.status_code == 200
    assert cancellation.body["data"]["status"] == "cancelled"
    assert cancellation.body["data"]["refund"]["status"] == "succeeded"
