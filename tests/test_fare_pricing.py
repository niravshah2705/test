import unittest
from datetime import date
from decimal import Decimal

from backend.shared.fare_pricing import (
    BaseFare,
    Currency,
    Discount,
    FareClass,
    FareValidityWindow,
    Fee,
    InMemoryFarePricingRepository,
    PassengerType,
    PriceAdjustmentType,
    Tax,
    passenger_type_from_traveler_type,
)
from backend.shared.flight_catalog import CabinClass
from backend.shared.travelers import TravelerType


class FarePricingRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryFarePricingRepository()
        self.usd = self.repository.save_currency(Currency(code="usd", name="US Dollar", minor_units=2))
        self.eur = self.repository.save_currency(Currency(code="EUR", name="Euro", minor_units=2))
        self.economy = self.repository.save_fare_class(
            FareClass(
                code="y",
                cabin=CabinClass.ECONOMY,
                name="Economy Flex",
                fare_class_id="fare_class_y_economy",
                refundable=True,
                changeable=True,
            )
        )
        self.business = self.repository.save_fare_class(
            FareClass(
                code="j",
                cabin=CabinClass.BUSINESS,
                name="Business Flex",
                fare_class_id="fare_class_j_business",
                refundable=True,
                changeable=True,
            )
        )
        self.active_window = FareValidityWindow(
            travel_from=date(2026, 7, 1),
            travel_until=date(2026, 7, 31),
            sale_from=date(2026, 1, 1),
            sale_until=date(2026, 6, 30),
        )

    def test_repository_creates_and_retrieves_pricing_records(self):
        tax = self.repository.save_tax(
            Tax(
                code="us",
                name="US Transportation Tax",
                amount=Decimal("7.50"),
                currency_code="usd",
                validity=self.active_window,
                tax_id="tax_us_transport",
            )
        )
        fee = self.repository.save_fee(
            Fee(
                code="svc",
                name="Service Fee",
                amount=Decimal("12.00"),
                currency_code="USD",
                validity=self.active_window,
                fee_id="fee_service",
            )
        )
        discount = self.repository.save_discount(
            Discount(
                code="promo10",
                name="Promo 10",
                amount=Decimal("10"),
                currency_code="USD",
                validity=self.active_window,
                discount_id="discount_promo10",
                adjustment_type=PriceAdjustmentType.PERCENTAGE,
            )
        )
        base_fare = self.repository.save_base_fare(
            BaseFare(
                flight_id="schedule_aa100",
                fare_class_id=self.economy.fare_class_id,
                cabin=CabinClass.ECONOMY,
                passenger_type=PassengerType.ADULT,
                amount=Decimal("250.00"),
                currency_code="usd",
                validity=self.active_window,
                base_fare_id="base_fare_aa100_y_adult",
            )
        )

        self.assertEqual(self.repository.get_currency("USD"), self.usd)
        self.assertEqual(self.repository.get_fare_class("fare_class_y_economy"), self.economy)
        self.assertEqual(self.repository.find_fare_class("y", CabinClass.ECONOMY), self.economy)
        self.assertEqual(self.repository.get_base_fare("base_fare_aa100_y_adult"), base_fare)
        self.assertEqual(self.repository.get_tax("tax_us_transport"), tax)
        self.assertEqual(self.repository.get_fee("fee_service"), fee)
        self.assertEqual(self.repository.get_discount("discount_promo10"), discount)
        self.assertEqual(base_fare.to_dict()["currencyCode"], "USD")

    def test_active_fare_lookup_filters_by_flight_cabin_passenger_travel_sale_and_currency(self):
        active_adult_usd = self.repository.save_base_fare(
            BaseFare(
                flight_id="schedule_aa100",
                fare_class_id=self.economy.fare_class_id,
                cabin=CabinClass.ECONOMY,
                passenger_type=PassengerType.ADULT,
                amount=Decimal("250.00"),
                currency_code="USD",
                validity=self.active_window,
                base_fare_id="active_adult_usd",
                priority=10,
            )
        )
        self.repository.save_base_fare(
            BaseFare(
                flight_id="schedule_aa100",
                fare_class_id=self.economy.fare_class_id,
                cabin=CabinClass.ECONOMY,
                passenger_type=PassengerType.CHILD,
                amount=Decimal("200.00"),
                currency_code="USD",
                validity=self.active_window,
                base_fare_id="child_usd",
            )
        )
        self.repository.save_base_fare(
            BaseFare(
                flight_id="schedule_aa100",
                fare_class_id=self.business.fare_class_id,
                cabin=CabinClass.BUSINESS,
                passenger_type=PassengerType.ADULT,
                amount=Decimal("800.00"),
                currency_code="USD",
                validity=self.active_window,
                base_fare_id="business_adult_usd",
            )
        )
        self.repository.save_base_fare(
            BaseFare(
                flight_id="schedule_aa200",
                fare_class_id=self.economy.fare_class_id,
                cabin=CabinClass.ECONOMY,
                passenger_type=PassengerType.ADULT,
                amount=Decimal("175.00"),
                currency_code="USD",
                validity=self.active_window,
                base_fare_id="other_flight_adult_usd",
            )
        )
        self.repository.save_base_fare(
            BaseFare(
                flight_id="schedule_aa100",
                fare_class_id=self.economy.fare_class_id,
                cabin=CabinClass.ECONOMY,
                passenger_type=PassengerType.ADULT,
                amount=Decimal("230.00"),
                currency_code="EUR",
                validity=self.active_window,
                base_fare_id="active_adult_eur",
            )
        )

        fares = self.repository.find_active_base_fares(
            flight_id="schedule_aa100",
            cabin=CabinClass.ECONOMY,
            passenger_type=PassengerType.ADULT,
            travel_date=date(2026, 7, 15),
            sale_date=date(2026, 6, 1),
            currency_code="usd",
        )

        self.assertEqual(fares, (active_adult_usd,))

    def test_active_fare_lookup_excludes_expired_travel_and_sale_windows(self):
        expired_travel = FareValidityWindow(
            travel_from=date(2026, 6, 1),
            travel_until=date(2026, 6, 30),
            sale_from=date(2026, 1, 1),
            sale_until=date(2026, 6, 30),
        )
        expired_sale = FareValidityWindow(
            travel_from=date(2026, 7, 1),
            travel_until=date(2026, 7, 31),
            sale_from=date(2026, 1, 1),
            sale_until=date(2026, 5, 31),
        )
        for base_fare_id, validity in (("expired_travel", expired_travel), ("expired_sale", expired_sale)):
            self.repository.save_base_fare(
                BaseFare(
                    flight_id="schedule_aa100",
                    fare_class_id=self.economy.fare_class_id,
                    cabin=CabinClass.ECONOMY,
                    passenger_type=PassengerType.ADULT,
                    amount=Decimal("250.00"),
                    currency_code="USD",
                    validity=validity,
                    base_fare_id=base_fare_id,
                )
            )

        fares = self.repository.find_active_base_fares(
            flight_id="schedule_aa100",
            cabin=CabinClass.ECONOMY,
            passenger_type=PassengerType.ADULT,
            travel_date=date(2026, 7, 15),
            sale_date=date(2026, 6, 1),
            currency_code="USD",
        )

        self.assertEqual(fares, tuple())

    def test_currency_constraints_are_enforced_for_base_fares_and_adjustments(self):
        with self.assertRaises(ValueError):
            self.repository.save_base_fare(
                BaseFare(
                    flight_id="schedule_aa100",
                    fare_class_id=self.economy.fare_class_id,
                    cabin=CabinClass.ECONOMY,
                    passenger_type=PassengerType.ADULT,
                    amount=Decimal("250.00"),
                    currency_code="GBP",
                    validity=self.active_window,
                )
            )
        with self.assertRaises(ValueError):
            self.repository.save_tax(
                Tax(code="gb", name="UK Tax", amount=Decimal("15.00"), currency_code="GBP", validity=self.active_window)
            )
        with self.assertRaises(ValueError):
            Currency(code="US", name="Invalid")

    def test_fare_class_uniqueness_and_base_fare_parent_constraints_are_enforced(self):
        with self.assertRaises(ValueError):
            self.repository.save_fare_class(
                FareClass(code="Y", cabin=CabinClass.ECONOMY, name="Duplicate Economy", fare_class_id="duplicate")
            )
        with self.assertRaises(ValueError):
            self.repository.save_base_fare(
                BaseFare(
                    flight_id="schedule_aa100",
                    fare_class_id="missing_fare_class",
                    cabin=CabinClass.ECONOMY,
                    passenger_type=PassengerType.ADULT,
                    amount=Decimal("250.00"),
                    currency_code="USD",
                    validity=self.active_window,
                )
            )
        with self.assertRaises(ValueError):
            self.repository.save_base_fare(
                BaseFare(
                    flight_id="schedule_aa100",
                    fare_class_id=self.economy.fare_class_id,
                    cabin=CabinClass.BUSINESS,
                    passenger_type=PassengerType.ADULT,
                    amount=Decimal("250.00"),
                    currency_code="USD",
                    validity=self.active_window,
                )
            )

    def test_validity_windows_and_adjustment_percentages_are_constrained(self):
        with self.assertRaises(ValueError):
            FareValidityWindow(
                travel_from=date(2026, 8, 1),
                travel_until=date(2026, 7, 1),
                sale_from=date(2026, 1, 1),
                sale_until=date(2026, 6, 30),
            )
        with self.assertRaises(ValueError):
            Discount(
                code="bad",
                name="Invalid Discount",
                amount=Decimal("101"),
                currency_code="USD",
                validity=self.active_window,
                adjustment_type=PriceAdjustmentType.PERCENTAGE,
            )

    def test_traveler_type_maps_to_pricing_passenger_type(self):
        self.assertEqual(passenger_type_from_traveler_type(TravelerType.ADULT), PassengerType.ADULT)
        self.assertEqual(passenger_type_from_traveler_type(TravelerType.CHILD), PassengerType.CHILD)
        self.assertEqual(passenger_type_from_traveler_type(TravelerType.INFANT), PassengerType.INFANT)


if __name__ == "__main__":
    unittest.main()
