"""Framework-neutral frontend component and flow contracts for flight search-to-booking UX.

The repository does not ship a browser test runner, so these contracts model the
component inputs, accessible rendering metadata, and flow states that the OFB web
interface consumes.  Fixtures are deliberately built from the same application
handlers used by API tests so frontend coverage stays aligned with backend DTOs.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import Any, Mapping

from .flight_checkout import (
    InMemoryBookingDraftRepository,
    accept_revalidated_price,
    build_offer_detail,
    create_booking_draft,
    finalize_booking_payment,
    handle_create_booking_draft,
    handle_finalize_booking_payment,
    poll_booking_finalization,
    revalidate_booking_draft,
)
from .flight_search import airport_suggestions, handle_flight_search, validate_flight_search_input
from .ui_contracts import build_page_states

VALID_FLIGHT_SEARCH = {
    "origin": "SFO",
    "destination": "JFK",
    "departDate": "2031-07-01",
    "adults": 1,
    "children": 0,
    "infants": 0,
    "cabin": "economy",
}

VALID_CONTACT = {"email": "traveler@example.test", "phone": "+14155550123"}
VALID_ADULT_PASSENGER = {
    "legalGivenName": "Gale",
    "legalFamilyName": "Guest",
    "dateOfBirth": "1990-04-12",
    "passengerType": "adult",
}


@dataclass(frozen=True)
class AutocompleteComponent:
    """Accessible airport autocomplete behavior contract."""

    field_id: str = "origin-field"
    listbox_id: str = "origin-airport-options"
    no_results_id: str = "origin-airport-no-results"

    def query(self, text: str) -> dict[str, Any]:
        options = airport_suggestions(text)
        active = options[0]["code"] if options else None
        return {
            "role": "combobox",
            "inputId": self.field_id,
            "listboxId": self.listbox_id,
            "ariaExpanded": bool(text),
            "ariaControls": self.listbox_id,
            "ariaAutocomplete": "list",
            "ariaActiveDescendant": f"{self.listbox_id}-{active}" if active else None,
            "options": [self._option(option, selected=index == 0) for index, option in enumerate(options)],
            "emptyState": None
            if options
            else {
                "id": self.no_results_id,
                "role": "status",
                "ariaLive": "polite",
                "message": f"No airports found for {text}.",
            },
            "keyboardHelp": "Type an airport, use ArrowDown and ArrowUp to review options, Enter to select, and Escape to close.",
        }

    def select_with_keyboard(self, text: str, key_sequence: list[str]) -> dict[str, Any]:
        state = self.query(text)
        options = state["options"]
        highlighted = 0 if options else -1
        selected = None
        closed = False
        for key in key_sequence:
            if key == "ArrowDown" and options:
                highlighted = min(highlighted + 1, len(options) - 1)
            elif key == "ArrowUp" and options:
                highlighted = max(highlighted - 1, 0)
            elif key == "Enter" and highlighted >= 0:
                selected = options[highlighted]
                closed = True
            elif key == "Escape":
                closed = True
        return {
            "selected": selected,
            "focusTarget": self.field_id,
            "ariaExpanded": False if closed else state["ariaExpanded"],
            "announcement": f"Selected {selected['label']}." if selected else state["emptyState"]["message"] if state["emptyState"] else None,
        }

    def _option(self, airport: Mapping[str, str], *, selected: bool) -> dict[str, Any]:
        return {
            "id": f"{self.listbox_id}-{airport['code']}",
            "role": "option",
            "code": airport["code"],
            "label": f"{airport['city']} ({airport['code']}) — {airport['name']}",
            "ariaSelected": selected,
        }


def flight_search_form_component(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return form validation UI metadata backed by search input validation."""

    validation = validate_flight_search_input(payload)
    errors = validation["errors"]
    fields = [
        {"name": "origin", "label": "From", "type": "text", "required": True, "autocomplete": "off"},
        {"name": "destination", "label": "To", "type": "text", "required": True, "autocomplete": "off"},
        {"name": "departDate", "label": "Depart date", "type": "date", "required": True},
        {"name": "returnDate", "label": "Return date", "type": "date", "required": payload.get("tripType") == "round_trip"},
        {"name": "adults", "label": "Adults", "type": "number", "required": True},
        {"name": "children", "label": "Children", "type": "number", "required": False},
        {"name": "infants", "label": "Infants", "type": "number", "required": False},
        {"name": "cabin", "label": "Cabin", "type": "select", "required": True},
    ]
    for field in fields:
        message = (errors.get(field["name"]) or [None])[0]
        field["id"] = f"flight-{field['name']}-field"
        field["errorId"] = f"flight-{field['name']}-error" if message else None
        field["error"] = message
        field["ariaInvalid"] = bool(message)
        field["ariaDescribedBy"] = field["errorId"]
    first_error_name = next(iter(errors), None)
    return {
        "title": "Search flights",
        "fields": fields,
        "errorSummary": f"Please correct {len(errors)} flight search field error{'s' if len(errors) != 1 else ''}." if errors else None,
        "focusTarget": f"flight-{first_error_name}-field" if first_error_name else "flight-origin-field",
        "statusRegion": {"id": "flight-search-status", "role": "status", "ariaLive": "polite"},
        "query": validation["query"],
    }


def search_results_component(payload: Mapping[str, Any]) -> dict[str, Any]:
    response = handle_flight_search(payload)
    if not response.body["success"]:
        return {
            "state": "error",
            "role": "alert",
            "message": response.body["error"]["message"],
            "retryAction": {"label": "Retry search", "href": "/search"},
        }
    data = response.body["data"]
    if data["empty"]:
        return {
            "state": "empty",
            "role": "status",
            "message": "No flights match this search. Try nearby airports or different dates.",
            "offers": [],
        }
    return {
        "state": "success",
        "role": "status",
        "heading": f"{len(data['offers'])} flights found",
        "sessionId": data["sessionId"],
        "offers": [offer_card_component(offer) for offer in data["offers"]],
    }


def offer_card_component(offer: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": offer["id"],
        "label": f"{offer['airline']} flight from {offer['departureAirport']} to {offer['arrivalAirport']}",
        "price": offer["price"],
        "status": "expired" if offer["isExpired"] else "available",
        "selectDisabled": bool(offer["isExpired"]),
        "selectLabel": "Offer expired" if offer["isExpired"] else f"Select {offer['airline']} flight",
        "ariaDescribedBy": f"{offer['id']}-price {offer['id']}-baggage {offer['id']}-status",
        "segments": [segment for itinerary in offer["itineraries"] for segment in itinerary["segments"]],
    }


def offer_detail_component(offer_id: str) -> dict[str, Any]:
    detail = build_offer_detail(offer_id)
    return {
        "heading": "Review flight details",
        "status": detail["status"],
        "canContinue": detail["canContinue"],
        "timeline": detail["timeline"],
        "fareSummary": detail["fareSummary"],
        "baggageSummary": detail["baggageSummary"],
        "requiredPassengerForms": detail["requiredPassengerForms"],
    }


def checkout_form_component(payload: Mapping[str, Any]) -> dict[str, Any]:
    response = handle_create_booking_draft(payload)
    errors = response.body.get("error", {}).get("fields", {}) if not response.body["success"] else {}
    return {
        "state": "validation_error" if errors else "ready",
        "errorSummary": f"Please correct {len(errors)} checkout field error{'s' if len(errors) != 1 else ''}." if errors else None,
        "focusTarget": _field_focus_target(next(iter(errors), "contact.email")),
        "fields": errors,
        "draft": response.body["data"] if response.body["success"] else None,
        "statusRegion": {"id": "checkout-status", "role": "status", "ariaLive": "polite"},
    }


def revalidation_component(scenario: str = "success") -> dict[str, Any]:
    repository, draft = _draft()
    review = revalidate_booking_draft(draft["id"], repository=repository, scenario=scenario)
    state = review["revalidation"]["status"]
    return {
        "state": state,
        "paymentAllowed": review["paymentAllowed"],
        "message": review["revalidation"]["message"],
        "currentTotal": review["currentTotal"],
        "latestTotal": review["revalidation"].get("latestTotal"),
        "actions": review["actions"],
        "role": "alert" if state in {"price_increased", "unavailable", "retryable_failure", "currency_mismatch", "material_change"} else "status",
    }


def payment_component(scenario: str = "success") -> dict[str, Any]:
    repository, draft = _validated_draft(scenario="price_change" if scenario == "price_changed_success" else "success")
    payment_payload = {
        "bookingId": draft["id"],
        "paymentToken": "tok_frontend_fixture_12345678",
        "idempotencyKey": f"idem-frontend-{scenario}",
        "amountCents": draft["total"]["amountCents"],
        "currency": draft["total"]["currency"],
        "scenario": "declined" if scenario == "failure" else "success",
    }
    response = handle_finalize_booking_payment(draft["id"], payment_payload, repository=repository)
    data = response.body["data"]
    return {
        "state": data["status"],
        "paymentStatus": data["payment"]["status"],
        "tokenization": {"mocked": True, "rawCardSubmitted": False},
        "polling": data["polling"],
        "message": _payment_message(data["status"]),
        "role": "alert" if data["status"] == "payment_declined" else "status",
        "confirmation": data,
    }


def confirmation_component(status: str = "confirmed") -> dict[str, Any]:
    if status == "pending":
        return payment_component("success")["confirmation"] | {"heading": "Booking pending", "statusLabel": "Ticketing pending"}

    class TicketedProvider:
        def getOrderStatus(self, order_id: str) -> dict[str, Any]:
            return {
                "id": order_id,
                "offerId": "ofb_flt_oneway",
                "provider": "deterministic_mock_air",
                "providerOrderId": "native-ticketed",
                "pricing": {"total": {"amount": 28600, "currency": "USD"}},
                "status": "ticketed",
                "ticketingDeadline": "2031-07-01T07:45:00Z",
            }

    repository, draft = _validated_draft()
    confirmation = finalize_booking_payment(
        draft["id"],
        {
            "bookingId": draft["id"],
            "paymentToken": "tok_frontend_fixture_12345678",
            "idempotencyKey": "idem-confirmed-0001",
            "amountCents": draft["total"]["amountCents"],
            "currency": draft["total"]["currency"],
        },
        repository=repository,
    )
    confirmed = poll_booking_finalization(confirmation["bookingId"], repository=repository, provider=TicketedProvider())
    return confirmed | {"heading": "Booking confirmed", "statusLabel": "Confirmed"}


def booking_history_component(bookings: list[Mapping[str, Any]] | None = None, *, forbidden_detail: bool = False) -> dict[str, Any]:
    if forbidden_detail:
        state = next(state for state in build_page_states("account_reservations") if state["status"] == "forbidden")
        return {"state": "forbidden", "detail": state, "role": "alert"}
    if not bookings:
        state = next(state for state in build_page_states("account_reservations") if state["status"] == "empty")
        return {"state": "empty", "heading": "No bookings yet", "message": state["message"], "items": [], "role": "status"}
    return {
        "state": "success",
        "heading": "Your bookings",
        "items": [
            {
                "bookingId": booking["bookingId"],
                "status": booking["status"],
                "statusLabel": booking["status"].replace("_", " ").title(),
                "href": f"/bookings/{booking['bookingId']}",
            }
            for booking in bookings
        ],
        "role": "status",
    }


def render_component_html(component: Mapping[str, Any]) -> str:
    """Minimal HTML renderer used by component tests for accessible status copy."""

    role = escape(str(component.get("role") or "status"))
    heading = escape(str(component.get("heading") or component.get("state") or "Status"))
    message = escape(str(component.get("message") or component.get("statusLabel") or ""))
    return f'<section role="{role}" aria-live="{"assertive" if role == "alert" else "polite"}"><h2>{heading}</h2><p>{message}</p></section>'


def _draft() -> tuple[InMemoryBookingDraftRepository, dict[str, Any]]:
    repository = InMemoryBookingDraftRepository()
    draft = create_booking_draft(
        {"offerId": "ofb_flt_oneway", "contact": VALID_CONTACT, "passengers": [VALID_ADULT_PASSENGER]},
        repository=repository,
    )
    return repository, draft


def _validated_draft(*, scenario: str = "success") -> tuple[InMemoryBookingDraftRepository, dict[str, Any]]:
    repository, draft = _draft()
    revalidate_booking_draft(draft["id"], repository=repository, scenario=scenario)
    if scenario == "price_change":
        accept_revalidated_price(draft["id"], repository=repository)
    saved = repository.get(draft["id"])
    assert saved is not None
    return repository, saved


def _field_focus_target(field: str) -> str:
    return field.replace(".", "-").replace("[", "-").replace("]", "") + "-field"


def _payment_message(status: str) -> str:
    return {
        "ticketing_pending": "Payment succeeded and ticketing is pending. We will keep checking the booking status.",
        "confirmed": "Payment succeeded and the booking is confirmed.",
        "payment_declined": "Payment failed. The booking is not confirmed and can be retried.",
    }.get(status, "Payment state changed.")
