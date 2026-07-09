import json
import sqlite3

from hbw_seed import reset_and_seed
from hbw_seed.auth import canAdministerHotel, canCancelReservation, canViewReservation
from hbw_seed.audit import record_audit_event, sanitize_metadata, system_actor
from hbw_seed.booking import (
    BookingConflict,
    BookingValidationError,
    admin_create_availability_block,
    admin_delete_availability_block,
    admin_update_hotel,
    admin_update_room,
    admin_update_room_type,
    available_room_ids,
    booking_api_cancel_reservation,
    booking_api_create_reservation,
    booking_api_admin_cancel_reservation,
    booking_api_admin_get_reservation,
    booking_api_admin_refund_reservation,
    booking_api_admin_search_reservations,
    booking_api_admin_update_reservation_status,
    booking_api_get_guest_reservation,
    booking_api_get_reservation,
    booking_api_record_payment,
    calculate_total_cents,
    create_payment_intent,
    cancel_reservation,
    create_pending_reservation,
    expire_pending_reservation,
    format_money,
    get_reservation_for_user,
    parse_stay_dates,
    record_payment_webhook,
    validate_occupancy,
)
from hbw_seed.notification import (
    BOOKING_CONFIRMATION,
    BOOKING_FAILED,
    BOOKING_PENDING,
    PAYMENT_FAILED,
    InMemoryNotificationDispatcher,
    create_booking_notification,
    notification_rows,
)
from hbw_seed.public_api import handle_get


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
    with sqlite3.connect(database) as connection:
        assert available_room_ids(connection, "htl_sfo_garden", "rt_garden_family", "2031-06-10", "2031-06-12") == [
            "room_garden_family_301",
            "room_garden_family_302",
        ]
        status = connection.execute("SELECT status FROM reservations WHERE id = 'res_garden_family_pending'").fetchone()[0]
    assert status == "expired"


def test_notification_requests_cover_pending_confirmation_failure_missing_contact_and_sensitive_exclusions(tmp_path):
    database = seeded_database(tmp_path)
    dispatcher = InMemoryNotificationDispatcher()

    reservation = create_pending_reservation(
        str(database),
        reservation_id="res_notification_flow",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        user_id="usr_guest",
        guest_email="notify@example.test",
        guest_name="Nia Notify",
        check_in="2031-06-12",
        check_out="2031-06-14",
        adults=2,
        children=1,
        dispatcher=dispatcher,
    )
    assert [message.kind for message in dispatcher.messages] == [BOOKING_PENDING]

    record_payment_webhook(
        str(database),
        provider_reference="fx_notification_success",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
        dispatcher=dispatcher,
    )
    duplicate = record_payment_webhook(
        str(database),
        provider_reference="fx_notification_success",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
        dispatcher=dispatcher,
    )
    assert duplicate["duplicate"] is True

    payment_failure = create_pending_reservation(
        str(database),
        reservation_id="res_notification_payment_failed",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        user_id="usr_guest",
        guest_email="payfail@example.test",
        guest_name="Pia Payment",
        check_in="2031-06-14",
        check_out="2031-06-15",
        adults=1,
        children=0,
        dispatcher=dispatcher,
    )
    assert_raises(
        BookingValidationError,
        record_payment_webhook,
        str(database),
        provider_reference="fx_notification_amount_mismatch",
        reservation_id=payment_failure["id"],
        amount_cents=payment_failure["total"]["amountCents"] - 1,
        dispatcher=dispatcher,
    )

    missing_contact = create_pending_reservation(
        str(database),
        reservation_id="res_notification_missing_contact",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        user_id="usr_guest",
        guest_email="missing-contact",
        guest_name="Mina Missing",
        check_in="2031-06-15",
        check_out="2031-06-16",
        adults=1,
        children=0,
        dispatcher=dispatcher,
    )
    failed = expire_pending_reservation(str(database), missing_contact["id"], now="2031-06-10T00:00:00Z", dispatcher=dispatcher)
    assert failed is True

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = notification_rows(connection)

    kinds_by_booking = {}
    for row in rows:
        kinds_by_booking.setdefault(row["bookingId"], []).append(row["kind"])
        encoded = json.dumps(row["payload"])
        assert "providerReference" not in encoded
        assert "rawPayload" not in encoded
        assert "cardNumber" not in encoded
        assert "document" not in encoded.lower()
        assert "confirmation_secret" not in encoded
        assert row["payload"]["bookingReference"] == row["bookingId"]
        assert row["payload"]["status"] in {"pending_payment", "confirmed", "expired"}
        assert "hotelName" in row["payload"]["itinerarySummary"]

    assert kinds_by_booking["res_notification_flow"].count(BOOKING_CONFIRMATION) == 1
    assert kinds_by_booking["res_notification_flow"] == [BOOKING_PENDING, BOOKING_CONFIRMATION]
    assert PAYMENT_FAILED in kinds_by_booking["res_notification_payment_failed"]
    assert BOOKING_FAILED in kinds_by_booking["res_notification_missing_contact"]
    missing_rows = [row for row in rows if row["bookingId"] == "res_notification_missing_contact"]
    assert all(row["status"] == "skipped_missing_contact" for row in missing_rows)
    dispatched_kinds = [message.kind for message in dispatcher.messages]
    assert dispatched_kinds.count(BOOKING_CONFIRMATION) == 1
    assert BOOKING_FAILED not in [message.kind for message in dispatcher.messages if message.booking_id == "res_notification_missing_contact"]


def test_create_booking_notification_interface_is_idempotent_for_confirmations(tmp_path):
    database = seeded_database(tmp_path)
    reservation = create_pending_reservation(
        str(database),
        reservation_id="res_direct_notification",
        hotel_id="htl_sfo_garden",
        room_type_id="rt_garden_family",
        user_id="usr_guest",
        guest_email="direct@example.test",
        guest_name="Dina Direct",
        check_in="2031-06-12",
        check_out="2031-06-14",
        adults=1,
        children=0,
    )
    record_payment_webhook(
        str(database),
        provider_reference="fx_direct_notification",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
    )
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        first = create_booking_notification(connection, reservation["id"], BOOKING_CONFIRMATION, created_at="2031-04-01T10:06:00Z")
        second = create_booking_notification(connection, reservation["id"], BOOKING_CONFIRMATION, created_at="2031-04-01T10:07:00Z")
        rows = notification_rows(connection, reservation["id"])
    assert first["duplicate"] is True
    assert second["duplicate"] is True
    assert [row["kind"] for row in rows].count(BOOKING_CONFIRMATION) == 1


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


def test_guest_cancellation_is_idempotent_and_forbidden_access_leaks_no_data(tmp_path):
    database = seeded_database(tmp_path)
    reservation = create_pending_reservation(
        str(database),
        reservation_id="res_guest_cancel_api",
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
        provider_reference="fx_guest_cancel_api",
        reservation_id=reservation["id"],
        amount_cents=reservation["total"]["amountCents"],
    )

    forbidden = booking_api_cancel_reservation(str(database), reservation["id"], "usr_admin")
    first = booking_api_cancel_reservation(str(database), reservation["id"], "usr_guest")
    duplicate = booking_api_cancel_reservation(str(database), reservation["id"], "usr_guest")

    assert forbidden.status_code == 403
    assert forbidden.body == {"success": False, "data": None, "error": {"code": "forbidden", "message": "You are not authorized to cancel this reservation."}}
    assert first.status_code == 200
    assert first.body["data"]["refund"]["amount"]["amountCents"] == 52000
    assert duplicate.status_code == 200
    assert duplicate.body["data"]["duplicateRequest"] is True
    with sqlite3.connect(database) as connection:
        refunds = connection.execute("SELECT COUNT(*) FROM refunds WHERE payment_record_id = 'pay_fx_guest_cancel_api'").fetchone()[0]
    assert refunds == 1


def test_admin_cancellation_search_filters_and_pagination_bounds(tmp_path):
    database = seeded_database(tmp_path)

    rejected = booking_api_admin_search_reservations(str(database), {"hotelId": "htl_sfo_bay"}, "usr_guest")
    too_large = booking_api_admin_search_reservations(str(database), {"pageSize": 51}, "usr_admin")
    filtered = booking_api_admin_search_reservations(
        str(database),
        {"hotelId": "htl_sfo_bay", "status": "confirmed", "guestEmail": "guest@example", "checkInFrom": "2031-06-10", "checkInTo": "2031-06-12", "page": 1, "pageSize": 2},
        "usr_admin",
    )
    cancelled = booking_api_admin_cancel_reservation(str(database), "res_bay_suite_confirmed", "usr_admin")
    duplicate = booking_api_admin_cancel_reservation(str(database), "res_bay_suite_confirmed", "usr_admin")

    assert rejected.status_code == 403
    assert rejected.body == {"success": False, "data": None, "error": {"code": "forbidden", "message": "Admin access required."}}
    assert too_large.status_code == 400
    assert "less than or equal to 50" in too_large.body["error"]["message"]
    assert filtered.status_code == 200
    assert filtered.body["data"]["pagination"] == {"page": 1, "pageSize": 2, "total": 2, "totalPages": 1}
    assert {row["id"] for row in filtered.body["data"]["reservations"]} == {"res_bay_king_auth_confirmed", "res_bay_suite_confirmed"}
    assert cancelled.status_code == 200
    assert cancelled.body["data"]["status"] == "cancelled"
    assert cancelled.body["data"]["refund"]["amount"]["amountCents"] == 84000
    assert duplicate.status_code == 200
    assert duplicate.body["data"]["duplicateRequest"] is True


def test_admin_refund_full_invalid_amount_and_duplicate_request(tmp_path):
    database = seeded_database(tmp_path)

    invalid = booking_api_admin_refund_reservation(str(database), "res_bay_king_auth_confirmed", "usr_admin", {"amountCents": 48001, "refundId": "ref_invalid_over"})
    full = booking_api_admin_refund_reservation(str(database), "res_bay_king_auth_confirmed", "usr_admin", {"amountCents": 48000, "refundId": "ref_admin_full"})
    duplicate = booking_api_admin_refund_reservation(str(database), "res_bay_king_auth_confirmed", "usr_admin", {"amountCents": 48000, "refundId": "ref_admin_full"})

    assert invalid.status_code == 400
    assert invalid.body["error"] == {"code": "validation_error", "message": "Refund amount exceeds captured refundable amount."}
    assert full.status_code == 201
    assert full.body["data"]["refund"] == {"id": "ref_admin_full", "amount": {"amountCents": 48000, "currency": "USD", "formatted": "USD 480.00"}, "status": "succeeded"}
    assert duplicate.status_code == 200
    assert duplicate.body["data"]["duplicateRequest"] is True
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM payment_records WHERE id = 'pay_bay_king_auth'").fetchone()[0] == "refunded"
        assert connection.execute("SELECT COUNT(*) FROM refunds WHERE id = 'ref_admin_full'").fetchone()[0] == 1


def test_admin_status_endpoint_rejects_invalid_transitions(tmp_path):
    database = seeded_database(tmp_path)

    forbidden = booking_api_admin_update_reservation_status(str(database), "res_garden_family_pending", "usr_guest", {"status": "expired"})
    invalid = booking_api_admin_update_reservation_status(str(database), "res_bay_king_auth_confirmed", "usr_admin", {"status": "confirmed"})
    valid = booking_api_admin_update_reservation_status(str(database), "res_garden_family_pending", "usr_admin", {"status": "expired"})

    assert forbidden.status_code == 403
    assert forbidden.body["data"] is None
    assert invalid.status_code == 409
    assert invalid.body["error"] == {"code": "reservation_conflict", "message": "Invalid reservation status transition."}
    assert valid.status_code == 200
    assert valid.body["data"]["status"] == "expired"
