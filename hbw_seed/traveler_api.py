"""Framework-neutral traveler API application for integration contract tests."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import parse_qs

from .auth import register_user, sign_in
from .flight_checkout import (
    BookingDraftRepository,
    CheckoutValidationError,
    InMemoryBookingDraftRepository,
    create_booking_draft,
    handle_create_booking_draft,
    handle_finalize_booking_payment,
    handle_offer_detail,
    handle_poll_booking_finalization,
    handle_revalidate_booking_draft,
)
from .flight_search import airport_suggestions, handle_flight_search
from .flights import FlightProvider
from .profiles import ProfileAuthorizationError, ProfileRepository
from .public_api import ApiResponse, error_response, success_response
from .sessions import getOptionalUser, require_user_response


@dataclass
class TravelerApiApplication:
    """Small API application boundary used by traveler journey integration tests."""

    database_path: str
    provider: FlightProvider | None = None
    booking_repository: InMemoryBookingDraftRepository | BookingDraftRepository = field(default_factory=InMemoryBookingDraftRepository)
    profile_repository: ProfileRepository | None = None

    def request(
        self,
        method: str,
        path: str,
        *,
        session_id: str | None = None,
        query_string: str = "",
        json: Mapping[str, Any] | None = None,
    ) -> ApiResponse:
        method = method.upper()
        payload = dict(json or {})
        query = {key: values[-1] for key, values in parse_qs(query_string, keep_blank_values=True).items()}

        if method == "POST" and path == "/api/auth/register":
            return register_user(self.database_path, payload)
        if method == "POST" and path == "/api/auth/login":
            return sign_in(self.database_path, payload)
        if method == "GET" and path == "/api/users/me":
            user = require_user_response(self.database_path, session_id)
            if isinstance(user, ApiResponse):
                return user
            return success_response({"user": user})
        if method == "GET" and path == "/api/airports":
            return success_response({"airports": airport_suggestions(query.get("q", ""))})
        if method == "POST" and path == "/api/flights/search":
            return handle_flight_search(payload, provider=self.provider)

        offer_match = re.fullmatch(r"/api/flights/offers/([^/]+)", path)
        if method == "GET" and offer_match:
            return handle_offer_detail(offer_match.group(1), provider=self.provider)

        if method == "POST" and path == "/api/bookings/drafts":
            user = require_user_response(self.database_path, session_id)
            if isinstance(user, ApiResponse):
                return user
            return handle_create_booking_draft(
                {**payload, "userId": user["id"]},
                provider=self.provider,
                repository=self.booking_repository,
                profile_repository=self.profile_repository,
            )

        passenger_match = re.fullmatch(r"/api/bookings/([^/]+)/passengers", path)
        if method == "POST" and passenger_match:
            user = require_user_response(self.database_path, session_id)
            if isinstance(user, ApiResponse):
                return user
            # Passenger submission is represented by the immutable snapshots created on the draft.
            return self._booking_detail(passenger_match.group(1), user["id"])

        revalidate_match = re.fullmatch(r"/api/bookings/([^/]+)/revalidate", path)
        if method == "POST" and revalidate_match:
            user = require_user_response(self.database_path, session_id)
            if isinstance(user, ApiResponse):
                return user
            ownership = self._require_booking_owner(revalidate_match.group(1), user["id"])
            if isinstance(ownership, ApiResponse):
                return ownership
            return handle_revalidate_booking_draft(
                revalidate_match.group(1),
                repository=self.booking_repository,
                provider=self.provider,
                scenario=str(payload.get("scenario") or "success"),
            )

        payment_match = re.fullmatch(r"/api/bookings/([^/]+)/payments", path)
        if method == "POST" and payment_match:
            user = require_user_response(self.database_path, session_id)
            if isinstance(user, ApiResponse):
                return user
            ownership = self._require_booking_owner(payment_match.group(1), user["id"])
            if isinstance(ownership, ApiResponse):
                return ownership
            return handle_finalize_booking_payment(
                payment_match.group(1),
                payload,
                repository=self.booking_repository,
                provider=self.provider,
            )

        finalize_match = re.fullmatch(r"/api/bookings/([^/]+)/finalize", path)
        if method == "POST" and finalize_match:
            user = require_user_response(self.database_path, session_id)
            if isinstance(user, ApiResponse):
                return user
            return self._booking_detail(finalize_match.group(1), user["id"])

        status_match = re.fullmatch(r"/api/bookings/([^/]+)/status", path)
        if method == "GET" and status_match:
            user = require_user_response(self.database_path, session_id)
            if isinstance(user, ApiResponse):
                return user
            ownership = self._require_booking_owner(status_match.group(1), user["id"])
            if isinstance(ownership, ApiResponse):
                return ownership
            return handle_poll_booking_finalization(status_match.group(1), repository=self.booking_repository, provider=self.provider)

        if method == "GET" and path == "/api/bookings":
            user = require_user_response(self.database_path, session_id)
            if isinstance(user, ApiResponse):
                return user
            bookings = [self._booking_summary(draft) for draft in self._all_drafts() if draft.get("userId") == user["id"]]
            return success_response({"bookings": bookings})

        detail_match = re.fullmatch(r"/api/bookings/([^/]+)", path)
        if method == "GET" and detail_match:
            user = require_user_response(self.database_path, session_id)
            if isinstance(user, ApiResponse):
                return user
            return self._booking_detail(detail_match.group(1), user["id"])

        profile_match = re.fullmatch(r"/api/passenger-profiles/([^/]+)", path)
        if method == "GET" and profile_match:
            user = require_user_response(self.database_path, session_id)
            if isinstance(user, ApiResponse):
                return user
            if self.profile_repository is None:
                return error_response(404, "not_found", "Passenger profile not found.")
            try:
                return success_response({"profile": self.profile_repository.get_passenger_profile(user["id"], profile_match.group(1))})
            except ProfileAuthorizationError:
                return error_response(403, "forbidden", "Passenger profile is not accessible.")
            except Exception:
                return error_response(404, "not_found", "Passenger profile not found.")

        return error_response(404, "not_found", "Endpoint not found.")

    def _require_booking_owner(self, draft_id: str, user_id: str) -> dict[str, Any] | ApiResponse:
        draft = self.booking_repository.get(draft_id)
        if draft is None:
            return error_response(404, "not_found", "Booking not found.")
        if draft.get("userId") != user_id:
            return error_response(403, "forbidden", "Booking is not accessible.")
        return draft

    def _booking_detail(self, draft_id: str, user_id: str) -> ApiResponse:
        draft = self._require_booking_owner(draft_id, user_id)
        if isinstance(draft, ApiResponse):
            return draft
        return success_response({"booking": self._public_booking(draft)})

    def _all_drafts(self) -> list[dict[str, Any]]:
        if isinstance(self.booking_repository, InMemoryBookingDraftRepository):
            return [self.booking_repository.get(draft_id) for draft_id in sorted(self.booking_repository.drafts)]
        return []

    def _booking_summary(self, draft: dict[str, Any]) -> dict[str, Any]:
        return {
            "bookingId": draft["id"],
            "offerId": draft["offerId"],
            "status": "confirmed" if draft.get("status") == "finalized" else draft.get("status"),
            "total": draft["total"],
            "passengerCount": len(draft.get("passengerSnapshots") or []),
        }

    def _public_booking(self, draft: dict[str, Any]) -> dict[str, Any]:
        return {
            **self._booking_summary(draft),
            "contact": {"email": draft["contact"]["email"]},
            "itineraries": draft["offerSnapshot"].get("itineraries", []),
            "passengers": [
                {
                    "name": passenger["legalName"]["fullName"],
                    "passengerType": passenger["passengerType"],
                    "document": _public_document(passenger.get("document")),
                }
                for passenger in draft.get("passengerSnapshots") or []
            ],
            "payment": _latest_payment(draft),
            "order": _public_order(draft.get("providerOrder")),
        }


def _latest_payment(draft: dict[str, Any]) -> dict[str, Any] | None:
    attempts = draft.get("paymentAttempts") or []
    if not attempts:
        return None
    attempt = attempts[-1]
    return {"id": attempt.get("id"), "status": attempt.get("status"), "amount": attempt.get("amount")}


def _public_order(order: dict[str, Any] | None) -> dict[str, Any] | None:
    if not order:
        return None
    return {"id": order.get("id"), "status": order.get("status"), "pricing": order.get("pricing")}


def _public_document(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if not document:
        return None
    return {
        "documentType": document.get("documentType"),
        "issuingCountry": document.get("issuingCountry"),
        "nationalityCountry": document.get("nationalityCountry"),
        "expiresOn": document.get("expiresOn"),
        "documentNumberLast4": document.get("documentNumberLast4"),
    }
