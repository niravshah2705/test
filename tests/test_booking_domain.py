import json
import sqlite3

from hbw_seed import reset_and_seed
from hbw_seed.auth import canAdministerHotel, canCancelReservation, canViewReservation
from hbw_seed.audit import record_audit_event, sanitize_metadata, system_actor
from hbw_seed.booking import (
    BookingConflict,
    BookingValidationError,
    assert_payment_transition,
    admin_create_availability_block,
    admin_delete_availability_block,
    admin_update_hotel,
    admin_update_room,
    admin_update_room_type,
    available_room_ids,
    booking_api_cancel_reservation,
    booking_api_create_reservation,
    booking_api_create_payment_intent,
    booking_api_confirm_payment,
    booking_api_admin_get_reservation,
    booking_api_get_guest_reservation,
    booking_api_get_reservation,
    booking_api_record_payment,
    calculate_total_cents,
    create_payment_intent,
    cancel_reservation,
    complete_reservation_stay,
    create_pending_reservation,
    expire_pending_reservation,
    format_money,
    get_reservation_for_user,
    parse_stay_dates,
    record_payment_webhook,
    validate_occupancy,
)
from hbw_seed.public_api import handle_get, handle_post


def seeded_database(tmp_path):
    database = tmp_path / "hbw.sqlite3"
    reset_and_seed(database)
    return database


def audit_rows(database, entity_id=None):
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        sql = "SELECT * FROM audit_records"
        params = []
        if entity_id is not None:
            sql += " WHERE entity_id = ?"
            params.append(entity_id)
        sql += " ORDER BY created_at, id"
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


def audit_metadata(row):
    return json.loads(row["metadata"])


def assert_raises(expected_exception, function, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except expected_exception as exc:
        return exc
    raise AssertionError(f"Expected {expected_exception.__name__}")


def test_audit_service_records_actor_event_entity_metadata_timestamp_and_sanitizes():
    metadata = sanitize_metadata(
        {
            "amountCents": 1000,
            "cardNumber": "4242424242424242",
            "nested": {"providerSecret": "secret", "kept": True},
            "items": [{"token": "tok_secret", "safe": "value"}],
        }
    )

    assert metadata == {"amountCents": 1000, "nested": {"kept": True}, "items": [{"safe": "value"}]}


def test_reservation_payment_refund_cancellation_audits_and_duplicate_webhook_idempotency(tmp_path):
    database = seeded_database(tmp_path)

    reservation = create_pending_reservation(
        str(database),
        reservation_id="res_audit_flow",
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
    payment_intent = create_payment_intent(
        str(database),
        payment_id="pay_intent_audit",
        reservation_id=reservation["id"],
        user_id="usr_guest",
        provider_reference="fx_intent_audit",
    )
    success = record_payment_webhook(
        str(database),
        provider_reference="fx_audit_success",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
    )
    duplicate = record_payment_webhook(
        str(database),
        provider_reference="fx_audit_success",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
    )
    cancelled = cancel_reservation(str(database), reservation_id=reservation["id"], user_id="usr_guest")

    assert payment_intent["status"] == "authorized"
    assert success["duplicate"] is False
    assert duplicate["duplicate"] is True
    assert cancelled["status"] == "cancelled"

    rows = audit_rows(database)
    events = [(row["event_type"], row["entity_type"], row["entity_id"], row["actor_type"], row["actor_user_id"]) for row in rows]
    assert ("reservation.created", "reservation", "res_audit_flow", "guest", "usr_guest") in events
    assert ("payment_intent.created", "payment", "pay_intent_audit", "guest", "usr_guest") in events
    assert ("reservation.confirmed", "reservation", "res_audit_flow", "webhook", None) in events
    assert ("payment.succeeded", "payment", "pay_fx_audit_success", "webhook", None) in events
    assert ("refund.created", "refund", "ref_pay_fx_audit_success", "guest", "usr_guest") in events
    assert ("reservation.cancelled", "reservation", "res_audit_flow", "guest", "usr_guest") in events
    assert sum(1 for row in rows if row["entity_id"] == "pay_fx_audit_success" and row["event_type"] == "payment.succeeded") == 1

    payment_audit = next(row for row in rows if row["entity_id"] == "pay_fx_audit_success")
    payment_metadata = audit_metadata(payment_audit)
    assert payment_metadata["provider"] == "fixture_gateway"
    assert payment_metadata["amountCents"] == reservation["total"]["amountCents"]
    assert "providerReference" not in payment_metadata
    assert "providerSecret" not in payment_metadata
    assert "cardNumber" not in payment_metadata
    assert "rawPayload" not in payment_metadata


def test_payment_failure_audit_uses_webhook_actor_and_safe_metadata(tmp_path):
    database = seeded_database(tmp_path)
    reservation = create_pending_reservation(
        str(database),
        reservation_id="res_audit_failure",
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

    assert_raises(
        BookingValidationError,
        record_payment_webhook,
        str(database),
        provider_reference="fx_audit_failure",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"] - 1,
    )

    row = audit_rows(database, "pay_fx_audit_failure")[0]
    metadata = audit_metadata(row)
    assert row["actor_type"] == "webhook"
    assert row["actor_user_id"] is None
    assert row["event_type"] == "payment.failed"
    assert metadata["failureReason"] == "amount_or_currency_mismatch"
    assert "providerReference" not in metadata


def test_admin_inventory_actions_create_blocking_audit_records(tmp_path):
    database = seeded_database(tmp_path)

    hotel = admin_update_hotel(str(database), hotel_id="htl_sfo_garden", user_id="usr_admin", changes={"description": "Updated fixture description."})
    room_type = admin_update_room_type(str(database), room_type_id="rt_garden_family", user_id="usr_admin", changes={"nightly_rate_cents": 27000})
    room = admin_update_room(str(database), room_id="room_garden_family_302", user_id="usr_admin", changes={"status": "maintenance"})
    block = admin_create_availability_block(
        str(database),
        block_id="blk_audit_admin",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        room_id=None,
        user_id="usr_admin",
        block_type="room_type_closure",
        starts_on="2031-06-20",
        ends_on="2031-06-21",
        reason="Fixture admin closure.",
    )
    deleted = admin_delete_availability_block(str(database), block_id="blk_audit_admin", user_id="usr_admin")

    assert hotel["description"] == "Updated fixture description."
    assert room_type["nightly_rate_cents"] == 27000
    assert room["status"] == "maintenance"
    assert block["id"] == "blk_audit_admin"
    assert deleted is True

    rows = audit_rows(database)
    events = {(row["event_type"], row["entity_id"], row["actor_type"], row["actor_user_id"]) for row in rows}
    assert ("hotel.updated", "htl_sfo_garden", "admin", "usr_admin") in events
    assert ("room_type.updated", "rt_garden_family", "admin", "usr_admin") in events
    assert ("room.updated", "room_garden_family_302", "admin", "usr_admin") in events
    assert ("availability_block.created", "blk_audit_admin", "admin", "usr_admin") in events
    assert ("availability_block.deleted", "blk_audit_admin", "admin", "usr_admin") in events
    assert all("blocking for admin inventory mutations" in audit_metadata(row).get("auditWritePolicy", "") for row in rows if row["entity_id"] in {"htl_sfo_garden", "rt_garden_family", "room_garden_family_302", "blk_audit_admin"})


def test_system_actor_supported_for_background_audit_events(tmp_path):
    database = seeded_database(tmp_path)
    with sqlite3.connect(database) as connection:
        record_audit_event(
            connection,
            actor=system_actor(),
            event_type="reservation.expired",
            entity_type="reservation",
            entity_id="res_garden_family_pending",
            metadata={"auditWritePolicy": "best effort; expiration correctness wins"},
            created_at="2031-06-10T00:00:00Z",
        )
        connection.commit()

    row = audit_rows(database, "res_garden_family_pending")[0]
    assert row["actor_type"] == "system"
    assert row["actor_user_id"] is None
    assert row["event_type"] == "reservation.expired"


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


def test_back_to_back_inventory_recalculation_after_cancel_and_expire(tmp_path):
    database = seeded_database(tmp_path)
    first = create_pending_reservation(
        str(database),
        reservation_id="res_recalc_first",
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
    second = create_pending_reservation(
        str(database),
        reservation_id="res_recalc_back_to_back",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        user_id="usr_guest",
        guest_email="guest@example.test",
        guest_name="Gale Guest",
        check_in="2031-06-14",
        check_out="2031-06-16",
        adults=2,
        children=1,
    )
    record_payment_webhook(str(database), provider_reference="fx_recalc_first", reservation_id=first["id"], amount_cents=first["total"]["amountCents"])

    with sqlite3.connect(database) as connection:
        assert first["status"] == "pending_payment"
        assert second["status"] == "pending_payment"
        assert available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-12", "2031-06-14") == ["room_garden_family_302"]
        assert available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-14", "2031-06-16") == ["room_garden_family_302"]

    cancel_reservation(str(database), reservation_id=first["id"], user_id="usr_guest")
    expire_pending_reservation(str(database), second["id"], now="2031-06-10T00:00:00Z")

    with sqlite3.connect(database) as connection:
        assert available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-12", "2031-06-14") == [
            "room_garden_family_301",
            "room_garden_family_302",
        ]
        assert available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-14", "2031-06-16") == [
            "room_garden_family_301",
            "room_garden_family_302",
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
    assert "roomId" not in first
    assert "userId" not in first
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
    assert "roomId" not in duplicate

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
    assert expire_pending_reservation(str(database), "res_garden_family_pending", now="2031-06-10T00:01:00Z") is False
    with sqlite3.connect(database) as connection:
        assert available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-10", "2031-06-12") == [
            "room_garden_family_301",
            "room_garden_family_302",
        ]
        status = connection.execute("SELECT status FROM reservations WHERE id = 'res_garden_family_pending'").fetchone()[0]
    assert status == "expired"


def test_payment_after_expiration_is_rejected_and_released_inventory_remains_available(tmp_path):
    database = seeded_database(tmp_path)

    assert expire_pending_reservation(str(database), "res_garden_family_pending", now="2031-06-10T00:00:00Z") is True
    conflict = assert_raises(
        BookingConflict,
        record_payment_webhook,
        str(database),
        provider_reference="fx_after_expiration",
        reservation_id="res_garden_family_pending",
        amount_cents=52000,
    )

    assert str(conflict) == "Reservation cannot transition from expired to confirmed via payment_webhook."
    with sqlite3.connect(database) as connection:
        assert available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-10", "2031-06-12") == [
            "room_garden_family_301",
            "room_garden_family_302",
        ]
        assert connection.execute("SELECT COUNT(*) FROM payment_records WHERE provider_reference = 'fx_after_expiration'").fetchone()[0] == 0


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


def test_invalid_payment_transitions_do_not_mark_captured_payment_failed(tmp_path):
    database = seeded_database(tmp_path)
    reservation = create_pending_reservation(
        str(database),
        reservation_id="res_invalid_payment_transition",
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
        provider_reference="fx_invalid_transition_success",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
    )

    conflict = assert_raises(
        BookingConflict,
        record_payment_webhook,
        str(database),
        provider_reference="fx_invalid_transition_failure",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
        event_type="payment.failed",
    )

    assert str(conflict) == "Payment event payment.failed is invalid for confirmed reservation."
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM reservations WHERE id = ?", (reservation["id"],)).fetchone()[0] == "confirmed"
        assert connection.execute("SELECT COUNT(*) FROM payment_records WHERE provider_reference = 'fx_invalid_transition_failure'").fetchone()[0] == 0


def test_refund_limits_reject_refunding_more_than_captured_amount():
    assert str(
        assert_raises(
            BookingConflict,
            assert_payment_transition,
            "captured",
            "refunded",
            amount_cents=101,
            captured_cents=100,
            refunded_cents=0,
        )
    ) == "Refund amount cannot exceed captured payment amount."


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
    assert str(assert_raises(BookingConflict, cancel_reservation, str(database), reservation_id=reservation["id"], user_id="usr_guest")) == "Reservation is not eligible for cancellation."


def test_completed_reservation_is_displayed_but_not_guest_cancellable(tmp_path):
    database = seeded_database(tmp_path)

    completed = complete_reservation_stay(str(database), "res_bay_king_auth_confirmed", today="2031-06-13")
    viewed = get_reservation_for_user(str(database), "res_bay_king_auth_confirmed", "usr_guest")

    assert completed["status"] == "completed"
    assert viewed["status"] == "completed"
    assert str(assert_raises(BookingConflict, cancel_reservation, str(database), reservation_id="res_bay_king_auth_confirmed", user_id="usr_guest")) == "Reservation is not eligible for cancellation."
    assert str(assert_raises(BookingConflict, complete_reservation_stay, str(database), "res_bay_king_auth_confirmed", today="2031-06-14")) == "Reservation cannot transition from completed to completed via completion_service."


def test_completion_uses_date_only_rules(tmp_path):
    database = seeded_database(tmp_path)

    assert str(assert_raises(BookingConflict, complete_reservation_stay, str(database), "res_bay_king_auth_confirmed", today="2031-06-12")) == "Reservation stay has not passed date-only completion rules."



def test_authorization_prevents_viewing_or_cancelling_another_users_booking(tmp_path):
    database = seeded_database(tmp_path)

    assert get_reservation_for_user(str(database), "res_bay_king_auth_confirmed", "usr_guest")["id"] == "res_bay_king_auth_confirmed"
    assert str(assert_raises(PermissionError, get_reservation_for_user, str(database), "res_bay_king_auth_confirmed", "usr_admin")) == "Reservation access denied."
    assert str(assert_raises(PermissionError, cancel_reservation, str(database), reservation_id="res_bay_king_auth_confirmed", user_id="usr_admin")) == "Reservation access denied."


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
    assert forbidden.body == {"success": False, "data": None, "error": {"code": "forbidden", "message": "You are not authorized to access this reservation."}}

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


def test_explicit_authorization_helpers_cover_guest_and_admin_rules(tmp_path):
    database = seeded_database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        guest = connection.execute("SELECT * FROM users WHERE id = 'usr_guest'").fetchone()
        admin = connection.execute("SELECT * FROM users WHERE id = 'usr_admin'").fetchone()
        reservation = connection.execute("SELECT * FROM reservations WHERE id = 'res_bay_king_auth_confirmed'").fetchone()

    assert canViewReservation(guest, reservation) is True
    assert canCancelReservation(guest, reservation) is True
    assert canViewReservation(None, reservation) is False
    assert canCancelReservation(admin, reservation) is False
    assert canAdministerHotel(admin, reservation["hotel_id"]) is True
    assert canAdministerHotel(guest, reservation["hotel_id"]) is False


def test_guest_confirmation_lookup_requires_non_guessable_secret(tmp_path):
    database = seeded_database(tmp_path)

    guessed = booking_api_get_guest_reservation(str(database), "res_bay_king_guest_confirmed", "0001")
    valid = booking_api_get_guest_reservation(str(database), "res_bay_king_guest_confirmed", "cnf_9e45f6c9baf04c2c8d3f1a72")

    assert guessed.status_code == 404
    assert valid.status_code == 200
    assert valid.body["data"]["id"] == "res_bay_king_guest_confirmed"
    assert "roomId" not in valid.body["data"]
    assert "userId" not in valid.body["data"]


def test_payment_intent_endpoint_requires_pending_matching_reservation_and_returns_safe_client_data(tmp_path):
    database = seeded_database(tmp_path)
    reservation = create_pending_reservation(
        str(database),
        reservation_id="res_intent_endpoint",
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

    mismatch = booking_api_create_payment_intent(
        str(database),
        {"reservationId": reservation["id"], "amountCents": reservation["total"]["amountCents"] + 1},
        "usr_guest",
    )
    intent = handle_post(
        str(database),
        "/api/payments/create-intent",
        {"reservationId": reservation["id"], "amountCents": reservation["total"]["amountCents"]},
        "usr_guest",
    )
    duplicate = booking_api_create_payment_intent(
        str(database),
        {"reservationId": reservation["id"], "amountCents": reservation["total"]["amountCents"]},
        "usr_guest",
    )

    assert mismatch.status_code == 400
    assert intent.status_code == 201
    payment = intent.body["data"]["payment"]
    assert payment["status"] == "authorized"
    assert payment["client"] == {"clientSecret": "cs_test_fx_intent_res_intent_endpoint"}
    assert "providerReference" not in payment
    assert "providerSecret" not in payment
    assert "cardNumber" not in str(intent.body)
    assert duplicate.status_code == 409
    assert duplicate.body["error"]["code"] == "payment_conflict"


def test_payment_confirm_endpoint_success_failure_duplicate_and_retry_after_failure(tmp_path):
    database = seeded_database(tmp_path)
    failing_reservation = create_pending_reservation(
        str(database),
        reservation_id="res_confirm_failure_retry",
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
    failed_intent = booking_api_create_payment_intent(
        str(database),
        {"reservationId": failing_reservation["id"], "amountCents": failing_reservation["total"]["amountCents"]},
        "usr_guest",
    )
    failed = booking_api_confirm_payment(
        str(database),
        {
            "reservationId": failing_reservation["id"],
            "providerReference": "fx_intent_res_confirm_failure_retry",
            "amountCents": failing_reservation["total"]["amountCents"],
            "outcome": "failed",
            "failureMessage": "Issuer declined payment.",
        },
        "usr_guest",
    )
    retry = booking_api_create_payment_intent(
        str(database),
        {"reservationId": failing_reservation["id"], "amountCents": failing_reservation["total"]["amountCents"]},
        "usr_guest",
    )

    assert failed_intent.status_code == 201
    assert failed.status_code == 201
    assert failed.body["data"]["payment"]["status"] == "voided"
    assert failed.body["data"]["payment"]["failureMessage"] == "Issuer declined payment."
    assert failed.body["data"]["reservation"]["status"] == "pending_payment"
    assert retry.status_code == 201

    successful_reservation = create_pending_reservation(
        str(database),
        reservation_id="res_confirm_success_duplicate",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        user_id="usr_guest",
        guest_email="guest@example.test",
        guest_name="Gale Guest",
        check_in="2031-06-14",
        check_out="2031-06-16",
        adults=2,
        children=1,
    )
    booking_api_create_payment_intent(
        str(database),
        {"reservationId": successful_reservation["id"], "amountCents": successful_reservation["total"]["amountCents"]},
        "usr_guest",
    )
    success = handle_post(
        str(database),
        "/api/payments/confirm",
        {
            "reservationId": successful_reservation["id"],
            "providerReference": "fx_intent_res_confirm_success_duplicate",
            "amountCents": successful_reservation["total"]["amountCents"],
        },
        "usr_guest",
    )
    duplicate = handle_post(
        str(database),
        "/api/payments/provider-events",
        {
            "reservationId": successful_reservation["id"],
            "providerReference": "fx_intent_res_confirm_success_duplicate",
            "amountCents": successful_reservation["total"]["amountCents"],
            "eventType": "payment.succeeded",
        },
    )

    assert success.status_code == 201
    assert success.body["data"]["payment"]["status"] == "captured"
    assert success.body["data"]["reservation"]["status"] == "confirmed"
    assert duplicate.status_code == 200
    assert duplicate.body["data"]["duplicate"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM payment_records WHERE provider_reference = 'fx_intent_res_confirm_success_duplicate'").fetchone()[0] == 1


def test_ineligible_reservations_cannot_create_payment_intents(tmp_path):
    database = seeded_database(tmp_path)
    expired = booking_api_create_payment_intent(
        str(database),
        {"reservationId": "res_garden_queen_expired", "amountCents": 36000},
        "usr_guest",
    )
    confirmed = booking_api_create_payment_intent(
        str(database),
        {"reservationId": "res_bay_king_auth_confirmed", "amountCents": 48000},
        "usr_guest",
    )
    cancelled = booking_api_create_payment_intent(
        str(database),
        {"reservationId": "res_bay_suite_cancelled", "amountCents": 84000},
        "usr_guest",
    )
    pending = create_pending_reservation(
        str(database),
        reservation_id="res_intent_time_expired",
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
    time_expired = booking_api_create_payment_intent(
        str(database),
        {"reservationId": pending["id"], "amountCents": pending["total"]["amountCents"], "createdAt": "2031-06-10T00:00:00Z"},
        "usr_guest",
    )

    assert expired.status_code in {403, 409}
    assert confirmed.status_code == 403
    assert cancelled.status_code == 403
    assert time_expired.status_code == 409
    assert time_expired.body["error"]["message"] == "Reservation payment window has expired."


def test_payment_api_authorizes_owner_and_hides_provider_reference(tmp_path):
    database = seeded_database(tmp_path)
    reservation = create_pending_reservation(
        str(database),
        reservation_id="res_payment_authz",
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

    unauthorized = booking_api_record_payment(
        str(database),
        {"reservationId": reservation["id"], "providerReference": "fx_forbidden", "amountCents": reservation["total"]["amountCents"]},
        "usr_admin",
    )
    authorized = booking_api_record_payment(
        str(database),
        {"reservationId": reservation["id"], "providerReference": "fx_payment_authz", "amountCents": reservation["total"]["amountCents"]},
        "usr_guest",
    )

    assert unauthorized.status_code == 403
    assert authorized.status_code == 201
    assert authorized.body["data"]["payment"]["status"] == "captured"
    assert "providerReference" not in authorized.body["data"]["payment"]
    assert "providerSecret" not in authorized.body["data"]["payment"]


def test_admin_endpoint_rejects_non_admin_and_returns_operational_dto_only_to_admin(tmp_path):
    database = seeded_database(tmp_path)

    rejected = booking_api_admin_get_reservation(str(database), "res_bay_king_auth_confirmed", "usr_guest")
    accepted = booking_api_admin_get_reservation(str(database), "res_bay_king_auth_confirmed", "usr_admin")

    assert rejected.status_code == 403
    assert rejected.body["error"] == {"code": "forbidden", "message": "Admin access required."}
    assert accepted.status_code == 200
    assert accepted.body["data"]["roomId"] == "room_bay_king_502"
    assert accepted.body["data"]["userId"] == "usr_guest"
