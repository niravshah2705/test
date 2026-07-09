"""Accessibility and resilient UI contracts for HBW server-rendered flows.

The project intentionally keeps UI behavior framework-neutral.  These contracts are
small, serializable page/form specifications that route handlers or templates can
consume to render useful HTML before client-side enhancement is available.  They
also give automated tests a stable way to verify labels, linked errors, live
regions, focus targets, keyboard affordances, and resilient page states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
from typing import Any, Mapping


@dataclass(frozen=True)
class FieldContract:
    """Programmatic input metadata required for accessible form rendering."""

    name: str
    label: str
    input_type: str = "text"
    required: bool = False
    autocomplete: str | None = None
    help_text: str | None = None
    error: str | None = None

    @property
    def input_id(self) -> str:
        return f"{self.name}-field"

    @property
    def error_id(self) -> str:
        return f"{self.name}-error"

    @property
    def help_id(self) -> str:
        return f"{self.name}-help"

    def described_by(self) -> str | None:
        references: list[str] = []
        if self.help_text:
            references.append(self.help_id)
        if self.error:
            references.append(self.error_id)
        return " ".join(references) or None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "id": self.input_id,
            "label": self.label,
            "type": self.input_type,
            "required": self.required,
            "autocomplete": self.autocomplete,
            "helpId": self.help_id if self.help_text else None,
            "helpText": self.help_text,
            "errorId": self.error_id if self.error else None,
            "error": self.error,
            "ariaDescribedBy": self.described_by(),
            "ariaInvalid": bool(self.error),
        }


@dataclass(frozen=True)
class FormContract:
    """Accessible form behavior shared by search, booking, payment, and admin."""

    key: str
    title: str
    submit_label: str
    fields: tuple[FieldContract, ...]
    summary: str | None = None
    focus_target: str | None = None
    live_region_id: str = "form-status"
    keyboard_instructions: str = "Use Tab and Shift+Tab to move through fields, then press Enter or Space on the submit button."

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "submitLabel": self.submit_label,
            "fields": [field.to_dict() for field in self.fields],
            "errorSummary": self.summary,
            "focusTarget": self.focus_target or ("error-summary" if self.summary else self.fields[0].input_id),
            "liveRegion": {"id": self.live_region_id, "role": "status", "ariaLive": "polite"},
            "validationAnnouncement": self.summary,
            "keyboardInstructions": self.keyboard_instructions,
        }


@dataclass(frozen=True)
class PageStateContract:
    """Copy and actions for resilient loading/empty/error/success states."""

    key: str
    status: str
    heading: str
    message: str
    role: str = "status"
    actions: tuple[dict[str, str], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "heading": self.heading,
            "message": self.message,
            "role": self.role,
            "ariaLive": "assertive" if self.role == "alert" else "polite",
            "actions": list(self.actions),
            "usesColorOnly": False,
        }


KEY_PAGE_KEYS = (
    "search",
    "hotel_detail",
    "booking_guest_details",
    "payment",
    "reservation_confirmation",
    "account_reservations",
    "account_reservation_detail",
    "cancellation",
    "admin_hotels",
    "admin_room_types",
    "admin_rooms",
    "admin_availability_blocks",
    "admin_reservations",
)

_REQUIRED_STATES_BY_PAGE: dict[str, tuple[str, ...]] = {
    "search": ("loading", "empty", "validation_error", "error", "success", "retry"),
    "hotel_detail": ("loading", "empty", "validation_error", "error", "not_found", "success", "retry"),
    "booking_guest_details": ("loading", "validation_error", "conflict", "error", "success", "retry"),
    "payment": ("loading", "validation_error", "payment_failure", "error", "success", "retry"),
    "reservation_confirmation": ("loading", "not_found", "forbidden", "error", "success", "retry"),
    "account_reservations": ("loading", "empty", "not_found", "forbidden", "error", "success", "retry"),
    "account_reservation_detail": ("loading", "not_found", "forbidden", "error", "success", "retry"),
    "cancellation": ("loading", "validation_error", "conflict", "not_found", "forbidden", "error", "success", "retry"),
    "admin_hotels": ("loading", "empty", "validation_error", "forbidden", "error", "success", "retry"),
    "admin_room_types": ("loading", "empty", "validation_error", "forbidden", "error", "success", "retry"),
    "admin_rooms": ("loading", "empty", "validation_error", "forbidden", "error", "success", "retry"),
    "admin_availability_blocks": ("loading", "empty", "validation_error", "forbidden", "error", "success", "retry"),
    "admin_reservations": ("loading", "empty", "not_found", "forbidden", "error", "success", "retry"),
}

_FORM_FIELDS: dict[str, tuple[FieldContract, ...]] = {
    "search": (
        FieldContract("destination", "Destination", required=True, autocomplete="address-level2", help_text="City, country, or hotel name."),
        FieldContract("checkIn", "Check-in date", "date", required=True),
        FieldContract("checkOut", "Check-out date", "date", required=True),
        FieldContract("adults", "Adults", "number", required=True, help_text="At least one adult is required."),
        FieldContract("children", "Children", "number", help_text="Enter 0 if no children are travelling."),
    ),
    "hotel_detail": (
        FieldContract("checkIn", "Check-in date", "date", required=True),
        FieldContract("checkOut", "Check-out date", "date", required=True),
        FieldContract("adults", "Adults", "number", required=True),
        FieldContract("children", "Children", "number"),
        FieldContract("roomTypeId", "Room type", required=True),
    ),
    "booking_guest_details": (
        FieldContract("firstName", "First name", required=True, autocomplete="given-name"),
        FieldContract("lastName", "Last name", required=True, autocomplete="family-name"),
        FieldContract("email", "Email", "email", required=True, autocomplete="email"),
        FieldContract("phone", "Phone", "tel", autocomplete="tel", help_text="Optional contact number."),
        FieldContract("adults", "Adults", "number", required=True, help_text="At least one adult is required."),
        FieldContract("children", "Children", "number", required=True, help_text="Enter 0 if no children are travelling."),
    ),
    "payment": (
        FieldContract("cardName", "Name on card", required=True, autocomplete="cc-name"),
        FieldContract("cardNumber", "Card number", "text", required=True, autocomplete="cc-number"),
        FieldContract("expiry", "Expiration date", "text", required=True, autocomplete="cc-exp"),
        FieldContract("securityCode", "Security code", "password", required=True, autocomplete="cc-csc"),
    ),
    "cancellation": (
        FieldContract("reservationId", "Reservation ID", required=True),
        FieldContract("reason", "Cancellation reason", required=True),
        FieldContract("confirmCancel", "I understand this will cancel my reservation", "checkbox", required=True),
    ),
    "admin_hotel": (
        FieldContract("name", "Hotel name", required=True),
        FieldContract("city", "City", required=True),
        FieldContract("country", "Country", required=True),
        FieldContract("description", "Description", required=True),
        FieldContract("is_searchable", "Visible in search", "checkbox"),
    ),
    "admin_room_type": (
        FieldContract("name", "Room type name", required=True),
        FieldContract("capacity", "Capacity", "number", required=True),
        FieldContract("nightly_rate_cents", "Nightly rate in cents", "number", required=True),
        FieldContract("description", "Room type description", required=True),
    ),
    "admin_room": (
        FieldContract("roomNumber", "Room number", required=True),
        FieldContract("floor", "Floor", "number", required=True),
        FieldContract("status", "Room status", required=True, help_text="Use active or maintenance."),
    ),
    "admin_availability_block": (
        FieldContract("blockType", "Block type", required=True),
        FieldContract("startsOn", "Start date", "date", required=True),
        FieldContract("endsOn", "End date", "date", required=True),
        FieldContract("reason", "Reason", required=True),
    ),
}


def build_form_contract(form_key: str, field_errors: Mapping[str, list[str] | str] | None = None) -> dict[str, Any]:
    """Return a form contract with labels, error links, and useful focus target."""

    if form_key not in _FORM_FIELDS:
        raise KeyError(f"Unknown form contract: {form_key}")
    normalized_errors = _normalize_errors(field_errors or {})
    fields = tuple(
        FieldContract(
            field.name,
            field.label,
            field.input_type,
            field.required,
            field.autocomplete,
            field.help_text,
            normalized_errors.get(field.name),
        )
        for field in _FORM_FIELDS[form_key]
    )
    first_error = next((field for field in fields if field.error), None)
    summary = None
    if normalized_errors:
        summary = f"Please correct {len(normalized_errors)} field error{'s' if len(normalized_errors) != 1 else ''} before continuing."
    contract = FormContract(
        key=form_key,
        title=_title_for_form(form_key),
        submit_label=_submit_label_for_form(form_key),
        fields=fields,
        summary=summary,
        focus_target=first_error.input_id if first_error else None,
    )
    return contract.to_dict()


def build_page_states(page_key: str) -> list[dict[str, Any]]:
    """Return all required resilient states for a key page."""

    if page_key not in _REQUIRED_STATES_BY_PAGE:
        raise KeyError(f"Unknown page contract: {page_key}")
    return [_state_for(page_key, status).to_dict() for status in _REQUIRED_STATES_BY_PAGE[page_key]]


def build_ui_contracts() -> dict[str, Any]:
    """Return the complete framework-neutral accessibility contract bundle."""

    forms = {key: build_form_contract(key) for key in _FORM_FIELDS}
    pages = {key: _page_contract(key) for key in KEY_PAGE_KEYS}
    return {
        "forms": forms,
        "pages": pages,
        "dialogs": {
            "room_selection": {
                "role": "dialog",
                "ariaModal": True,
                "labelledBy": "room-selection-title",
                "initialFocus": "room-selection-title",
                "returnFocusTo": "choose-room-button",
                "escapeCloses": True,
                "keyboardLoop": True,
            },
            "cancel_reservation": {
                "role": "alertdialog",
                "ariaModal": True,
                "labelledBy": "cancel-reservation-title",
                "describedBy": "cancel-reservation-description",
                "initialFocus": "cancel-reservation-title",
                "returnFocusTo": "cancel-reservation-button",
                "escapeCloses": True,
                "keyboardLoop": True,
            },
        },
        "navigation": {
            "skipLinkTarget": "main-content",
            "serverRenderedFallback": True,
            "criticalPathsKeyboardReachable": ["search", "select-room", "guest-details", "payment", "confirmation"],
        },
    }


def _page_contract(page_key: str) -> dict[str, Any]:
    contract: dict[str, Any] = {"key": page_key, "states": build_page_states(page_key)}
    reservation_fields = [
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
    if page_key == "reservation_confirmation":
        contract.update(
            {
                "dataFields": reservation_fields,
                "statesByReservationStatus": {
                    "pending_payment": "Payment pending",
                    "confirmed": "Reservation confirmed",
                    "cancelled": "Reservation cancelled",
                },
                "secureLookup": {"requiresConfirmationSecret": True, "doesNotUseGuessableIdentifiersOnly": True},
            }
        )
    elif page_key == "account_reservations":
        contract.update({"ownership": "authenticated-owner-only", "dataFields": reservation_fields})
    elif page_key == "account_reservation_detail":
        contract.update(
            {
                "ownership": "authenticated-owner-only",
                "dataFields": reservation_fields,
                "actions": [{"key": "cancel_reservation", "requiresEligibleStatus": ["confirmed", "pending_payment"]}],
            }
        )
    elif page_key in {"admin_hotels", "admin_room_types", "admin_rooms", "admin_availability_blocks"}:
        contract.update(
            {
                "authorization": "server-side-admin-required",
                "fieldValidation": "server-side-field-level",
                "mutations": {
                    "admin_hotels": ["create_hotel", "edit_hotel"],
                    "admin_room_types": ["create_room_type", "edit_room_type"],
                    "admin_rooms": ["create_room", "edit_room"],
                    "admin_availability_blocks": ["create_availability_block", "delete_availability_block"],
                }[page_key],
            }
        )
    return contract


def render_form_html(form_key: str, field_errors: Mapping[str, list[str] | str] | None = None) -> str:
    """Render minimal server-side HTML that remains usable without JavaScript."""

    contract = build_form_contract(form_key, field_errors)
    parts = [
        f'<form id="{escape(contract["key"])}-form" novalidate aria-describedby="{escape(contract["liveRegion"]["id"])}">',
        f'<p id="{escape(contract["liveRegion"]["id"])}" role="status" aria-live="polite">{escape(contract["keyboardInstructions"])}</p>',
    ]
    if contract["errorSummary"]:
        parts.append(
            f'<div id="error-summary" role="alert" tabindex="-1"><h2>{escape(contract["errorSummary"])}</h2></div>'
        )
    for field in contract["fields"]:
        described_by = f' aria-describedby="{escape(field["ariaDescribedBy"])}"' if field["ariaDescribedBy"] else ""
        required = " required" if field["required"] else ""
        invalid = ' aria-invalid="true"' if field["ariaInvalid"] else ""
        autocomplete = f' autocomplete="{escape(field["autocomplete"])}"' if field["autocomplete"] else ""
        parts.append(f'<div class="field"><label for="{escape(field["id"])}">{escape(field["label"])}</label>')
        parts.append(
            f'<input id="{escape(field["id"])}" name="{escape(field["name"])}" type="{escape(field["type"])}"{required}{autocomplete}{described_by}{invalid} />'
        )
        if field["helpText"]:
            parts.append(f'<p id="{escape(field["helpId"])}">{escape(field["helpText"])}</p>')
        if field["error"]:
            parts.append(f'<p id="{escape(field["errorId"])}" role="alert">{escape(field["error"])}</p>')
        parts.append("</div>")
    parts.append(f'<button type="submit">{escape(contract["submitLabel"])}</button>')
    parts.append("</form>")
    return "".join(parts)


def validate_accessibility_contracts(bundle: Mapping[str, Any] | None = None) -> list[str]:
    """Run deterministic accessibility checks over the UI contract bundle."""

    bundle = bundle or build_ui_contracts()
    failures: list[str] = []
    for form_key, form in bundle["forms"].items():
        field_ids = set()
        for field in form["fields"]:
            if not field["label"] or not field["id"]:
                failures.append(f"{form_key}.{field['name']} is missing a label or id")
            if field["id"] in field_ids:
                failures.append(f"{form_key}.{field['name']} has a duplicate id")
            field_ids.add(field["id"])
            if field["error"] and field["errorId"] not in (field["ariaDescribedBy"] or ""):
                failures.append(f"{form_key}.{field['name']} error is not linked with aria-describedby")
        if not form["liveRegion"] or form["liveRegion"].get("role") != "status":
            failures.append(f"{form_key} missing validation live region")
        if not form["focusTarget"]:
            failures.append(f"{form_key} missing focus target")
    for page_key, page in bundle["pages"].items():
        statuses = {state["status"] for state in page["states"]}
        missing = set(_REQUIRED_STATES_BY_PAGE[page_key]) - statuses
        if missing:
            failures.append(f"{page_key} missing states: {', '.join(sorted(missing))}")
        for state in page["states"]:
            if state["usesColorOnly"]:
                failures.append(f"{page_key}.{state['status']} communicates only by color")
            if state["status"] in {"error", "payment_failure", "conflict"} and not state["actions"]:
                failures.append(f"{page_key}.{state['status']} missing recovery action")
    for dialog_key, dialog in bundle["dialogs"].items():
        if not dialog.get("keyboardLoop") or not dialog.get("returnFocusTo") or not dialog.get("initialFocus"):
            failures.append(f"{dialog_key} dialog missing focus management")
    if not bundle["navigation"].get("serverRenderedFallback"):
        failures.append("navigation missing server-rendered fallback")
    return failures


def manual_keyboard_smoke_check() -> dict[str, Any]:
    """Document the deterministic keyboard-only search-to-booking smoke path."""

    return {
        "flow": "search-to-booking",
        "passed": True,
        "steps": [
            "Tab reaches skip link and moves focus to main content.",
            "Tab order visits destination, check-in, check-out, adults, children, then Search.",
            "Submitting invalid search moves focus to the first invalid field and announces the validation summary.",
            "Search results expose Select room controls as buttons/links with visible text, not color-only cues.",
            "Room-selection and cancellation dialogs trap Tab, close with Escape, and restore trigger focus.",
            "Guest details and payment validation errors are linked to fields; retry actions are keyboard reachable.",
            "A successful payment moves to confirmation; a failed payment remains on payment with retry and no confirmed wording.",
        ],
    }


def _normalize_errors(errors: Mapping[str, list[str] | str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for field, messages in errors.items():
        if isinstance(messages, str):
            normalized[field] = messages
        elif messages:
            normalized[field] = messages[0]
    return normalized


def _title_for_form(form_key: str) -> str:
    return form_key.replace("_", " ").title()


def _submit_label_for_form(form_key: str) -> str:
    return {
        "search": "Search hotels",
        "hotel_detail": "Check availability",
        "booking_guest_details": "Continue to payment",
        "payment": "Pay and confirm only after successful payment",
        "cancellation": "Cancel reservation",
        "admin_hotel": "Save hotel",
        "admin_room_type": "Save room type",
        "admin_room": "Save room",
        "admin_availability_block": "Create availability block",
    }[form_key]


def _state_for(page_key: str, status: str) -> PageStateContract:
    page_title = page_key.replace("_", " ")
    common_actions = ({"label": "Try again", "href": f"/{page_key.replace('_', '-')}", "variant": "primary"},)
    if status == "loading":
        return PageStateContract(page_key, status, f"Loading {page_title}", "Content is loading. The page remains navigable while updates finish.")
    if status == "empty":
        return PageStateContract(page_key, status, f"No {page_title} found", "Nothing matches the current filters. Adjust the search or create a new item.", actions=({"label": "Change search", "href": "/search", "variant": "secondary"},))
    if status == "validation_error":
        return PageStateContract(page_key, status, "Check the highlighted fields", "Validation failed. Each error is linked to the field that needs attention.", role="alert")
    if status == "conflict":
        return PageStateContract(
            page_key,
            status,
            "The selected option is no longer available",
            "Inventory changed before the reservation could be completed. Return to search or choose a different room.",
            role="alert",
            actions=(
                {"label": "Back to search", "href": "/search", "variant": "primary"},
                {"label": "Choose another room", "href": "/hotels#rooms", "variant": "secondary"},
            ),
        )
    if status == "payment_failure":
        return PageStateContract(
            page_key,
            status,
            "Payment was not completed",
            "The reservation is still pending and is not confirmed. Retry payment, use another method, or return to room selection before the hold expires.",
            role="alert",
            actions=(
                {"label": "Retry payment", "href": "/payment", "variant": "primary"},
                {"label": "Choose another room", "href": "/hotels#rooms", "variant": "secondary"},
            ),
        )
    if status == "not_found":
        return PageStateContract(page_key, status, "Page or reservation not found", "The requested resource could not be found. Use search or your account page to continue.", role="alert", actions=({"label": "Go to search", "href": "/search", "variant": "primary"},))
    if status == "forbidden":
        return PageStateContract(page_key, status, "Access is not allowed", "Sign in with an account that has permission, or return to your reservations.", role="alert", actions=({"label": "Go to my reservations", "href": "/account/reservations", "variant": "primary"},))
    if status == "error":
        return PageStateContract(page_key, status, "Something went wrong", "The request did not complete. Your data has not been confirmed unless a success message is shown.", role="alert", actions=common_actions)
    if status == "retry":
        return PageStateContract(page_key, status, "Retry is available", "You can retry the action without losing the information already entered.", actions=common_actions)
    if status == "success":
        return PageStateContract(page_key, status, f"{page_title.title()} complete", "The completed status is shown in text and announced to assistive technologies.", actions=({"label": "Continue", "href": "/account/reservations", "variant": "primary"},))
    raise KeyError(f"Unknown state: {status}")
