import unittest
from dataclasses import replace
from datetime import datetime, timezone

from backend.shared.bookings import (
    BaggageSelection,
    BaggageSelectionType,
    BookingContactDetails,
    BookingItinerarySegment,
    BookingPassenger,
    BookingRecord,
    BookingStatus,
    BookingStatusHistoryEntry,
    InMemoryBookingRepository,
    PassengerType,
    SpecialRequest,
    SpecialRequestType,
)


class BookingRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryBookingRepository()
        self.created_at = datetime(2026, 7, 7, 17, 0, tzinfo=timezone.utc)
        self.segment = BookingItinerarySegment(
            segment_id="segment_jfk_lax",
            sequence=1,
            origin_airport_code="jfk",
            destination_airport_code="lax",
            departure_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
            arrival_at=datetime(2026, 8, 1, 18, 0, tzinfo=timezone.utc),
            marketing_airline_code="aa",
            operating_airline_code="aa",
            flight_number="100",
            cabin_class="economy",
            provider_segment_id="provider_segment_1",
        )
        self.passenger = BookingPassenger(
            passenger_id="passenger_adult",
            passenger_type=PassengerType.ADULT,
            given_name=" Nirav ",
            family_name=" Shah ",
            traveler_profile_id="traveler_123",
            loyalty_programs={"aa": " 12345 "},
        )

    def make_booking(self, **overrides):
        values = {
            "booking_id": "booking_123",
            "booking_reference": " abc123 ",
            "customer_identifier": "customer_123",
            "contact_details": BookingContactDetails(
                email=" Traveler@Example.COM ",
                phone_number="+1 212 555 0199",
                given_name="Nirav",
                family_name="Shah",
            ),
            "passengers": (self.passenger,),
            "itinerary_segments": (self.segment,),
            "baggage_selections": (
                BaggageSelection(
                    baggage_id="baggage_checked",
                    passenger_id="passenger_adult",
                    segment_id="segment_jfk_lax",
                    selection_type=BaggageSelectionType.CHECKED,
                    quantity=1,
                    weight_kg=23,
                ),
            ),
            "special_requests": (
                SpecialRequest(
                    request_id="request_meal",
                    passenger_id="passenger_adult",
                    segment_id="segment_jfk_lax",
                    request_type=SpecialRequestType.MEAL,
                    code="vgml",
                    description="Vegetarian meal",
                ),
            ),
            "status_history": (
                BookingStatusHistoryEntry(
                    history_id="history_pending",
                    status=BookingStatus.PENDING,
                    changed_at=self.created_at,
                    actor="booking-service",
                ),
            ),
            "created_at": self.created_at,
            "updated_at": self.created_at,
        }
        values.update(overrides)
        return BookingRecord(**values)

    def test_repository_creates_booking_records_with_contact_baggage_and_requests(self):
        booking = self.repository.save(self.make_booking())

        stored = self.repository.get("booking_123")

        self.assertEqual(stored, booking)
        self.assertEqual(booking.booking_reference, "ABC123")
        self.assertEqual(booking.contact_details.email, "traveler@example.com")
        self.assertEqual(booking.passengers[0].given_name, "Nirav")
        self.assertEqual(booking.passengers[0].loyalty_programs, {"AA": "12345"})
        self.assertEqual(booking.baggage_selections[0].selection_type, BaggageSelectionType.CHECKED)
        self.assertEqual(booking.special_requests[0].code, "VGML")
        self.assertEqual(booking.status, BookingStatus.PENDING)
        self.assertEqual(booking.to_dict()["createdAt"], "2026-07-07T17:00:00Z")

    def test_itinerary_segments_are_associated_and_ordered_on_booking(self):
        second_segment = BookingItinerarySegment(
            segment_id="segment_lax_sfo",
            sequence=2,
            origin_airport_code="LAX",
            destination_airport_code="SFO",
            departure_at=datetime(2026, 8, 2, 16, 0, tzinfo=timezone.utc),
            arrival_at=datetime(2026, 8, 2, 17, 30, tzinfo=timezone.utc),
            marketing_airline_code="AA",
            flight_number="200",
        )
        booking = self.repository.save(self.make_booking(itinerary_segments=(second_segment, self.segment)))

        self.assertEqual([segment.segment_id for segment in booking.itinerary_segments], ["segment_jfk_lax", "segment_lax_sfo"])
        self.assertEqual(booking.itinerary_segments[0].origin_airport_code, "JFK")
        self.assertEqual(booking.itinerary_segments[0].destination_airport_code, "LAX")
        self.assertEqual(booking.itinerary_segments[0].flight_designator, "AA100")
        self.assertEqual(booking.to_dict()["itinerarySegments"][1]["flightDesignator"], "AA200")

    def test_passenger_validation_rejects_missing_duplicate_and_invalid_references(self):
        with self.assertRaises(ValueError):
            self.make_booking(passengers=())
        with self.assertRaises(ValueError):
            BookingPassenger(passenger_id="bad", given_name=" ", family_name="Traveler")
        with self.assertRaises(ValueError):
            self.make_booking(passengers=(self.passenger, replace(self.passenger)))
        with self.assertRaises(ValueError):
            self.make_booking(
                baggage_selections=(
                    BaggageSelection(
                        baggage_id="bad_baggage",
                        passenger_id="missing_passenger",
                        selection_type=BaggageSelectionType.CHECKED,
                    ),
                )
            )
        with self.assertRaises(ValueError):
            self.make_booking(
                special_requests=(
                    SpecialRequest(
                        request_id="bad_request",
                        passenger_id="passenger_adult",
                        segment_id="missing_segment",
                        request_type=SpecialRequestType.ASSISTANCE,
                        code="WCHR",
                    ),
                )
            )

    def test_status_history_is_append_only(self):
        booking = self.repository.save(self.make_booking())
        confirmed_at = datetime(2026, 7, 7, 17, 5, tzinfo=timezone.utc)
        confirmed = self.repository.append_status(
            booking.booking_id,
            BookingStatusHistoryEntry(
                history_id="history_confirmed",
                status=BookingStatus.CONFIRMED,
                changed_at=confirmed_at,
                reason="Provider confirmed",
            ),
        )

        self.assertEqual([entry.status for entry in confirmed.status_history], [BookingStatus.PENDING, BookingStatus.CONFIRMED])
        self.assertEqual(self.repository.get(booking.booking_id).status, BookingStatus.CONFIRMED)
        with self.assertRaises(ValueError):
            self.repository.save(replace(confirmed, status_history=confirmed.status_history[:1]))
        with self.assertRaises(ValueError):
            self.repository.save(
                replace(
                    confirmed,
                    status_history=(
                        BookingStatusHistoryEntry(
                            history_id="history_rewritten",
                            status=BookingStatus.CANCELLED,
                            changed_at=self.created_at,
                        ),
                    ),
                )
            )
        with self.assertRaises(ValueError):
            self.repository.append_status(
                booking.booking_id,
                BookingStatusHistoryEntry(
                    history_id="history_backdated",
                    status=BookingStatus.CANCELLED,
                    changed_at=datetime(2026, 7, 7, 16, 59, tzinfo=timezone.utc),
                ),
            )

    def test_lookup_by_booking_reference_and_customer_identifier(self):
        first = self.repository.save(self.make_booking())
        second = self.repository.save(
            self.make_booking(
                booking_id="booking_456",
                booking_reference="def456",
                customer_identifier="customer_123",
                passengers=(replace(self.passenger, passenger_id="passenger_child", passenger_type=PassengerType.CHILD),),
                baggage_selections=(),
                special_requests=(),
            )
        )
        self.repository.save(
            self.make_booking(
                booking_id="booking_789",
                booking_reference="ghi789",
                customer_identifier="customer_other",
                baggage_selections=(),
                special_requests=(),
            )
        )

        self.assertEqual(self.repository.find_by_reference("abc123"), first)
        self.assertEqual(self.repository.find_by_reference(" DEF456 "), second)
        self.assertEqual(
            [booking.booking_reference for booking in self.repository.find_by_customer_identifier("customer_123")],
            ["ABC123", "DEF456"],
        )
        self.assertEqual(self.repository.find_by_customer_identifier("missing_customer"), tuple())
        with self.assertRaises(ValueError):
            self.repository.save(self.make_booking(booking_id="booking_duplicate", booking_reference="ABC123"))


if __name__ == "__main__":
    unittest.main()
