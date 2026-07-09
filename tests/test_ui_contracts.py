from hbw_seed.ui_contracts import (
    KEY_PAGE_KEYS,
    build_form_contract,
    build_page_states,
    build_ui_contracts,
    manual_keyboard_smoke_check,
    render_form_html,
    validate_accessibility_contracts,
)


FORM_KEYS = {
    "search",
    "hotel_detail",
    "booking_guest_details",
    "payment",
    "cancellation",
    "admin_hotel",
    "admin_room_type",
    "admin_room",
    "admin_availability_block",
}


def test_accessibility_contracts_pass_for_key_pages_forms_dialogs_and_navigation():
    bundle = build_ui_contracts()

    assert set(bundle["forms"]) == FORM_KEYS
    assert set(bundle["pages"]) == set(KEY_PAGE_KEYS)
    assert validate_accessibility_contracts(bundle) == []
    assert bundle["navigation"] == {
        "skipLinkTarget": "main-content",
        "serverRenderedFallback": True,
        "criticalPathsKeyboardReachable": ["search", "select-room", "guest-details", "payment", "confirmation"],
    }
    for dialog in bundle["dialogs"].values():
        assert dialog["ariaModal"] is True
        assert dialog["initialFocus"]
        assert dialog["returnFocusTo"]
        assert dialog["keyboardLoop"] is True


def test_booking_guest_details_form_exposes_required_contact_and_occupancy_fields():
    form = build_form_contract("booking_guest_details")
    by_name = {field["name"]: field for field in form["fields"]}

    assert [field["name"] for field in form["fields"]] == ["firstName", "lastName", "email", "phone", "adults", "children"]
    assert {name for name, field in by_name.items() if field["required"]} == {"firstName", "lastName", "email", "adults", "children"}
    assert by_name["phone"]["required"] is False
    assert by_name["email"]["type"] == "email"
    assert by_name["phone"]["autocomplete"] == "tel"


def test_key_forms_have_programmatic_labels_and_field_errors_are_linked():
    form = build_form_contract(
        "search",
        {
            "destination": ["Destination is required."],
            "checkOut": ["Must be after check-in."],
        },
    )

    assert form["errorSummary"] == "Please correct 2 field errors before continuing."
    assert form["focusTarget"] == "destination-field"
    assert form["validationAnnouncement"] == form["errorSummary"]
    by_name = {field["name"]: field for field in form["fields"]}
    assert by_name["destination"]["label"] == "Destination"
    assert by_name["destination"]["errorId"] == "destination-error"
    assert "destination-error" in by_name["destination"]["ariaDescribedBy"]
    assert by_name["destination"]["ariaInvalid"] is True
    assert by_name["checkOut"]["errorId"] == "checkOut-error"
    assert by_name["adults"]["label"] == "Adults"
    assert by_name["children"]["label"] == "Children"


def test_server_rendered_form_html_remains_useful_without_client_enhancement():
    html = render_form_html("payment", {"cardNumber": "Card number is required."})

    assert '<label for="cardNumber-field">Card number</label>' in html
    assert 'id="cardNumber-error" role="alert"' in html
    assert 'aria-describedby="cardNumber-error"' in html
    assert 'aria-invalid="true"' in html
    assert 'role="status" aria-live="polite"' in html
    assert 'type="submit"' in html
    assert "Pay and confirm only after successful payment" in html


def test_resilient_states_cover_loading_empty_errors_conflicts_retries_and_success():
    search_states = {state["status"]: state for state in build_page_states("search")}
    booking_states = {state["status"]: state for state in build_page_states("booking_guest_details")}
    payment_states = {state["status"]: state for state in build_page_states("payment")}
    admin_states = {state["status"]: state for state in build_page_states("admin_reservations")}
    account_detail_states = {state["status"]: state for state in build_page_states("account_reservation_detail")}
    admin_room_type_states = {state["status"]: state for state in build_page_states("admin_room_types")}
    admin_block_states = {state["status"]: state for state in build_page_states("admin_availability_blocks")}

    assert {"loading", "empty", "validation_error", "error", "success", "retry"} <= set(search_states)
    assert booking_states["conflict"]["role"] == "alert"
    assert [action["label"] for action in booking_states["conflict"]["actions"]] == ["Back to search", "Choose another room"]
    assert "not confirmed" in payment_states["payment_failure"]["message"]
    assert [action["label"] for action in payment_states["payment_failure"]["actions"]] == ["Retry payment", "Choose another room"]
    assert admin_states["empty"]["heading"] == "No admin reservations found"
    assert account_detail_states["forbidden"]["role"] == "alert"
    assert admin_room_type_states["validation_error"]["role"] == "alert"
    assert admin_block_states["success"]["actions"][0]["label"] == "Continue"
    for state in [*search_states.values(), *booking_states.values(), *payment_states.values(), *admin_states.values(), *account_detail_states.values(), *admin_room_type_states.values(), *admin_block_states.values()]:
        assert state["usesColorOnly"] is False
        assert state["heading"]
        assert state["message"]


def test_account_confirmation_cancellation_and_admin_authorization_states_are_explicit():
    confirmation = {state["status"] for state in build_page_states("reservation_confirmation")}
    account = {state["status"] for state in build_page_states("account_reservations")}
    cancellation = {state["status"] for state in build_page_states("cancellation")}
    account_detail = {state["status"] for state in build_page_states("account_reservation_detail")}
    admin_hotels = {state["status"] for state in build_page_states("admin_hotels")}
    admin_room_types = {state["status"] for state in build_page_states("admin_room_types")}
    admin_rooms = {state["status"] for state in build_page_states("admin_rooms")}
    admin_blocks = {state["status"] for state in build_page_states("admin_availability_blocks")}

    assert {"loading", "not_found", "forbidden", "error", "success", "retry"} <= confirmation
    assert {"loading", "empty", "not_found", "forbidden", "error", "success", "retry"} <= account
    assert {"loading", "not_found", "forbidden", "error", "success", "retry"} <= account_detail
    assert {"loading", "validation_error", "conflict", "not_found", "forbidden", "error", "success", "retry"} <= cancellation
    assert {"loading", "empty", "validation_error", "forbidden", "error", "success", "retry"} <= admin_hotels
    assert {"loading", "empty", "validation_error", "forbidden", "error", "success", "retry"} <= admin_room_types
    assert {"loading", "empty", "validation_error", "forbidden", "error", "success", "retry"} <= admin_rooms
    assert {"loading", "empty", "validation_error", "forbidden", "error", "success", "retry"} <= admin_blocks


def test_manual_keyboard_smoke_path_is_documented_for_search_to_booking_flow():
    smoke = manual_keyboard_smoke_check()

    assert smoke["flow"] == "search-to-booking"
    assert smoke["passed"] is True
    assert len(smoke["steps"]) >= 6
    assert any("Tab order" in step for step in smoke["steps"])
    assert any("validation" in step.lower() and "announces" in step.lower() for step in smoke["steps"])
    assert any("failed payment" in step.lower() and "no confirmed wording" in step.lower() for step in smoke["steps"])


def test_reservation_and_admin_screen_contracts_expose_required_sections():
    bundle = build_ui_contracts()

    confirmation = bundle["pages"]["reservation_confirmation"]
    account_list = bundle["pages"]["account_reservations"]
    account_detail = bundle["pages"]["account_reservation_detail"]
    admin_hotels = bundle["pages"]["admin_hotels"]
    admin_rooms = bundle["pages"]["admin_rooms"]
    admin_room_types = bundle["pages"]["admin_room_types"]
    admin_blocks = bundle["pages"]["admin_availability_blocks"]
    admin_room_form = build_form_contract("admin_room")

    assert confirmation["dataFields"] == [
        "confirmationCode",
        "hotel",
        "roomType",
        "checkIn",
        "checkOut",
        "guestCount",
        "guestContact",
        "reservationStatus",
        "paymentStatus",
        "cancellationStatus",
        "priceBreakdown",
    ]
    assert set(confirmation["statesByReservationStatus"]) == {"pending_payment", "confirmed", "cancelled"}
    assert confirmation["secureLookup"] == {"requiresConfirmationSecret": True, "doesNotUseGuessableIdentifiersOnly": True}
    assert account_list["ownership"] == "authenticated-owner-only"
    assert account_detail["ownership"] == "authenticated-owner-only"
    assert account_detail["dataFields"] == confirmation["dataFields"]
    assert {action["key"] for action in account_detail["actions"]} == {"cancel_reservation"}
    assert {screen["key"] for screen in [admin_hotels, admin_room_types, admin_rooms, admin_blocks]} == {
        "admin_hotels",
        "admin_room_types",
        "admin_rooms",
        "admin_availability_blocks",
    }
    assert all(screen["authorization"] == "server-side-admin-required" for screen in [admin_hotels, admin_room_types, admin_rooms, admin_blocks])
    assert admin_rooms["mutations"] == ["create_room", "edit_room"]
    assert admin_room_form["submitLabel"] == "Save room"
    assert {field["name"] for field in admin_room_form["fields"]} == {"roomNumber", "floor", "status"}
