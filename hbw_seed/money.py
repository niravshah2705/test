"""Integer minor-unit money utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SUPPORTED_CURRENCIES = {"USD": 2}


class MoneyValidationError(ValueError):
    """Raised when money inputs are invalid or incompatible."""


@dataclass(frozen=True, order=True)
class Money:
    amount_minor: int
    currency: str = "USD"

    def __post_init__(self) -> None:
        _validate_minor_units(self.amount_minor, "amount_minor")
        _currency_exponent(self.currency)

    @property
    def amount_cents(self) -> int:
        if self.currency != "USD":
            raise MoneyValidationError("amount_cents is only available for USD.")
        return self.amount_minor

    def add(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def subtract(self, other: "Money") -> "Money":
        self._assert_same_currency(other)
        amount = self.amount_minor - other.amount_minor
        if amount < 0:
            raise MoneyValidationError("money amount cannot be negative.")
        return Money(amount, self.currency)

    def multiply(self, quantity: int) -> "Money":
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise MoneyValidationError("quantity must be an integer.")
        if quantity < 0:
            raise MoneyValidationError("quantity must be non-negative.")
        return Money(self.amount_minor * quantity, self.currency)

    def compare(self, other: "Money") -> int:
        self._assert_same_currency(other)
        return (self.amount_minor > other.amount_minor) - (self.amount_minor < other.amount_minor)

    def format(self) -> str:
        return format_money(self.amount_minor, self.currency)["formatted"]

    def to_payload(self) -> dict[str, Any]:
        return format_money(self.amount_minor, self.currency)

    def _assert_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise MoneyValidationError("money currency mismatch.")


def money(amount_minor: int, currency: str = "USD") -> Money:
    return Money(amount_minor=amount_minor, currency=currency)


def add_money(*amounts: Money) -> Money:
    if not amounts:
        raise MoneyValidationError("at least one money amount is required.")
    total = amounts[0]
    for amount in amounts[1:]:
        total = total.add(amount)
    return total


def subtract_money(left: Money, right: Money) -> Money:
    return left.subtract(right)


def multiply_money(amount: Money, quantity: int) -> Money:
    return amount.multiply(quantity)


def compare_money(left: Money, right: Money) -> int:
    return left.compare(right)


def format_money(amount_minor: int, currency: str = "USD") -> dict[str, Any]:
    _validate_minor_units(amount_minor, "amount_minor")
    exponent = _currency_exponent(currency)
    unit = 10**exponent
    major_units, minor_units = divmod(amount_minor, unit)
    return {
        "amountCents": amount_minor if currency == "USD" else amount_minor,
        "currency": currency,
        "formatted": f"{currency} {major_units}.{minor_units:0{exponent}d}",
    }


def _validate_minor_units(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyValidationError(f"{field} must be an integer minor-unit amount.")
    if value < 0:
        raise MoneyValidationError(f"{field} must be non-negative.")


def _currency_exponent(currency: str) -> int:
    if not isinstance(currency, str) or currency != currency.upper() or len(currency) != 3:
        raise MoneyValidationError("currency must be a supported three-letter uppercase code.")
    try:
        return SUPPORTED_CURRENCIES[currency]
    except KeyError as exc:
        raise MoneyValidationError(f"Unsupported currency: {currency}.") from exc
