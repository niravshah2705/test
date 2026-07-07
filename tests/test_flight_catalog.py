import unittest
from datetime import date, datetime, time, timezone

from backend.shared.flight_catalog import (
    Aircraft,
    Airline,
    Airport,
    CabinClass,
    FlightInstance,
    FlightSchedule,
    InMemoryFlightCatalogRepository,
    OperatingStatus,
    Route,
)


class FlightCatalogRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryFlightCatalogRepository()
        self.jfk = self.repository.save_airport(
            Airport(code="jfk", name="John F. Kennedy International", city="New York", country_code="us")
        )
        self.lax = self.repository.save_airport(
            Airport(code="LAX", name="Los Angeles International", city="Los Angeles", country_code="US")
        )
        self.airline = self.repository.save_airline(Airline(code="aa", name="American Airlines", airline_id="airline_aa"))
        self.aircraft = self.repository.save_aircraft(
            Aircraft(
                model_code="32B",
                manufacturer="Airbus",
                model_name="A321neo",
                seat_capacity=196,
                aircraft_id="aircraft_a321neo",
            )
        )
        self.route = self.repository.save_route(
            Route(origin_airport_code="jfk", destination_airport_code="lax", route_id="route_jfk_lax", distance_km=3974)
        )
        self.schedule = self.repository.save_schedule(
            FlightSchedule(
                airline_code="aa",
                flight_number="100",
                route_id=self.route.route_id,
                aircraft_id=self.aircraft.aircraft_id,
                departure_time=time(8, 30),
                arrival_time=time(11, 45),
                effective_from=date(2026, 1, 1),
                effective_until=date(2026, 12, 31),
                operating_days=(0, 1, 2, 3, 4),
                cabin_classes=(CabinClass.ECONOMY, CabinClass.BUSINESS),
                schedule_id="schedule_aa100",
            )
        )

    def test_repository_creates_and_retrieves_catalog_records(self):
        instance = self.repository.save_flight_instance(
            FlightInstance(
                schedule_id=self.schedule.schedule_id,
                service_date=date(2026, 7, 6),
                instance_id="instance_aa100_20260706",
            )
        )

        self.assertEqual(self.repository.get_airport("JFK"), self.jfk)
        self.assertEqual(self.repository.get_airport("jfk").code, "JFK")
        self.assertEqual(self.repository.get_airline("AA"), self.airline)
        self.assertEqual(self.repository.get_aircraft("aircraft_a321neo"), self.aircraft)
        self.assertEqual(self.repository.get_route("route_jfk_lax"), self.route)
        self.assertEqual(self.repository.find_route("JFK", "LAX"), self.route)
        self.assertEqual(self.repository.get_schedule("schedule_aa100"), self.schedule)
        self.assertEqual(self.repository.get_flight_instance("instance_aa100_20260706"), instance)
        self.assertEqual(instance.status, OperatingStatus.SCHEDULED)

    def test_repository_queries_schedules_by_route_and_service_date(self):
        saturday_schedule = FlightSchedule(
            airline_code="AA",
            flight_number="200",
            route_id=self.route.route_id,
            departure_time=time(16, 0),
            arrival_time=time(19, 15),
            effective_from=date(2026, 7, 1),
            effective_until=date(2026, 7, 31),
            operating_days=(5,),
            schedule_id="schedule_aa200",
        )
        self.repository.save_schedule(saturday_schedule)

        all_route_schedules = self.repository.find_schedules_for_route("jfk", "lax")
        monday_schedules = self.repository.find_schedules_for_route_date("JFK", "LAX", date(2026, 7, 6))
        saturday_schedules = self.repository.find_schedules_for_route_date("JFK", "LAX", date(2026, 7, 4))
        out_of_effective_range = self.repository.find_schedules_for_route_date("JFK", "LAX", date(2027, 7, 3))

        self.assertEqual([schedule.schedule_id for schedule in all_route_schedules], ["schedule_aa100", "schedule_aa200"])
        self.assertEqual([schedule.schedule_id for schedule in monday_schedules], ["schedule_aa100"])
        self.assertEqual([schedule.schedule_id for schedule in saturday_schedules], ["schedule_aa200"])
        self.assertEqual(out_of_effective_range, tuple())
        self.assertEqual(self.repository.find_schedules_for_route("LAX", "JFK"), tuple())

    def test_repository_updates_flight_instance_operating_status(self):
        self.repository.save_flight_instance(
            FlightInstance(
                schedule_id=self.schedule.schedule_id,
                service_date=date(2026, 7, 6),
                instance_id="instance_aa100_20260706",
            )
        )
        updated_at = datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc)

        updated = self.repository.update_flight_status(
            self.schedule.schedule_id,
            date(2026, 7, 6),
            OperatingStatus.DELAYED,
            updated_at=updated_at,
        )

        self.assertEqual(updated.status, OperatingStatus.DELAYED)
        self.assertEqual(updated.status_updated_at, updated_at)
        self.assertEqual(
            self.repository.find_flight_instance(self.schedule.schedule_id, date(2026, 7, 6)).status,
            OperatingStatus.DELAYED,
        )

    def test_airport_codes_are_constrained_to_three_letters(self):
        self.assertEqual(Airport(code=" sfo ", name="San Francisco", city="San Francisco", country_code="US").code, "SFO")

        for invalid_code in ("SF", "SFO1", "12A", ""):
            with self.subTest(invalid_code=invalid_code):
                with self.assertRaises(ValueError):
                    Airport(code=invalid_code, name="Invalid", city="Nowhere", country_code="US")

    def test_route_uniqueness_is_enforced_for_directional_airport_pairs(self):
        with self.assertRaises(ValueError):
            self.repository.save_route(
                Route(origin_airport_code="JFK", destination_airport_code="LAX", route_id="route_duplicate")
            )

        reverse = self.repository.save_route(
            Route(origin_airport_code="LAX", destination_airport_code="JFK", route_id="route_lax_jfk")
        )
        self.assertEqual(self.repository.find_route("LAX", "JFK"), reverse)

    def test_flight_instance_identity_is_schedule_and_service_date(self):
        first = self.repository.save_flight_instance(
            FlightInstance(
                schedule_id=self.schedule.schedule_id,
                service_date=date(2026, 7, 6),
                instance_id="instance_first",
            )
        )

        with self.assertRaises(ValueError):
            self.repository.save_flight_instance(
                FlightInstance(
                    schedule_id=self.schedule.schedule_id,
                    service_date=date(2026, 7, 6),
                    instance_id="instance_duplicate",
                )
            )

        replacement = self.repository.save_flight_instance(
            FlightInstance(
                schedule_id=self.schedule.schedule_id,
                service_date=date(2026, 7, 6),
                status=OperatingStatus.CANCELLED,
                instance_id="instance_first",
            )
        )
        self.assertEqual(first.identity, ("schedule_aa100", date(2026, 7, 6)))
        self.assertEqual(self.repository.find_flight_instance("schedule_aa100", date(2026, 7, 6)), replacement)
        self.assertEqual(replacement.status, OperatingStatus.CANCELLED)

    def test_schedules_and_instances_must_reference_existing_parent_records(self):
        with self.assertRaises(ValueError):
            self.repository.save_schedule(
                FlightSchedule(
                    airline_code="AA",
                    flight_number="999",
                    route_id="missing_route",
                    departure_time=time(1, 0),
                    arrival_time=time(3, 0),
                    effective_from=date(2026, 1, 1),
                    effective_until=date(2026, 1, 31),
                    operating_days=(0,),
                )
            )
        with self.assertRaises(ValueError):
            self.repository.save_flight_instance(
                FlightInstance(schedule_id="missing_schedule", service_date=date(2026, 7, 6))
            )


if __name__ == "__main__":
    unittest.main()
