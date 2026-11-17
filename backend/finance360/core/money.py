"""Exact money (ADR-0002, ADR-0009 section 1).

``Money`` is an integer count of minor units plus an ISO 4217 code. There is no floating point
anywhere in this module, and this module is the only place that rounds.

Rounding policy
---------------
Every operation that can produce a fraction of a minor unit (FX conversion, allocation) rounds
**once, at the end, in integer arithmetic, half to even** (``round_div``).

FX round-trip bound
-------------------
Convert ``x`` minor units of currency A into B at rate ``r`` (B per 1 A), then convert the result
back at the exact reciprocal ``1/r``. Let ``kf`` be the forward factor in minor units (B-minor per
A-minor, i.e. ``r`` adjusted for the two exponents) and ``kb = 1/kf``. The forward step is off by at
most 1/2 B-minor, which the return step scales by ``kb``; the return step adds at most 1/2
A-minor. So the there-and-back error, in A-minor units, is::

    |z - x| <= (1 + kb) / 2

For currencies of equal exponent and ``r >= 1`` (``kb <= 1``) that is at most one minor unit. If the
return rate is not the exact reciprocal, add ``|x| * |kf * kb' - 1|``. Hypothesis checks this bound.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

#: Base currency in M1 (ADR-0009): a constant, not a Household column.
AUD = "AUD"
BASE_CURRENCY = AUD

INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1

#: Minor-unit exponent per ISO 4217 code (digits after the decimal point). Static, small, extended
#: when a currency is needed. Unknown codes raise.
CURRENCY_EXPONENTS: Mapping[str, int] = {
    "AUD": 2,
    "BHD": 3,
    "CAD": 2,
    "CHF": 2,
    "CNY": 2,
    "EUR": 2,
    "GBP": 2,
    "HKD": 2,
    "IDR": 2,
    "INR": 2,
    "JPY": 0,
    "KRW": 0,
    "KWD": 3,
    "NZD": 2,
    "SGD": 2,
    "THB": 2,
    "USD": 2,
    "VND": 0,
}


class MoneyError(ValueError):
    """An invalid Money value or operation."""


class CurrencyMismatchError(MoneyError):
    """Two Money values in different currencies were combined. There is no implicit conversion."""


def exponent(currency: str) -> int:
    try:
        return CURRENCY_EXPONENTS[currency]
    except (KeyError, TypeError):
        raise MoneyError(f"Unknown currency code: {currency!r}") from None


def opaque(value: object) -> object:
    """Return ``value`` typed as ``object``, for runtime checks on annotated parameters."""
    return value


def _check_int(value: object, what: str) -> int:
    if isinstance(value, float):
        raise TypeError(f"{what} must be an integer, not a float: {value!r}")
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{what} must be an integer, got {type(value).__name__}")
    return value


def decimal_parts(value: Decimal) -> tuple[bool, int, int]:
    """A finite Decimal as ``(negative, coefficient, exponent)`` with value = c * 10**exponent."""
    sign, digits, exp = value.as_tuple()
    if not isinstance(exp, int):
        raise MoneyError(f"Expected a finite Decimal: {value}")
    return bool(sign), int("".join(map(str, digits)) or "0"), exp


def round_div(numerator: int, denominator: int) -> int:
    """``numerator / denominator`` rounded half to even, in pure integer arithmetic."""
    if denominator == 0:
        raise ZeroDivisionError("round_div by zero")
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    quotient, remainder = divmod(numerator, denominator)  # remainder in [0, denominator)
    twice = 2 * remainder
    if twice > denominator or (twice == denominator and quotient % 2 == 1):
        quotient += 1
    return quotient


@dataclass(frozen=True, slots=True)
class Money:
    """An immutable amount: integer minor units plus an ISO 4217 currency code."""

    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        _check_int(self.amount_minor, "Money amount")
        exponent(self.currency)
        if not INT64_MIN <= self.amount_minor <= INT64_MAX:
            raise MoneyError(
                f"Money amount does not fit a signed 64-bit integer: {self.amount_minor}"
            )

    # SQLAlchemy composite protocol: (amount_minor, currency) column order.
    def __composite_values__(self) -> tuple[int, str]:
        return (self.amount_minor, self.currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(0, currency)

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(f"Cannot combine {self.currency} with {other.currency}")

    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def __neg__(self) -> Money:
        return Money(-self.amount_minor, self.currency)

    def __mul__(self, factor: object) -> Money:
        """Multiply by an integer: exact. Anything else (float, Decimal) is refused."""
        if isinstance(factor, float):
            raise TypeError("Money can only be multiplied by an integer")
        if not isinstance(factor, int) or isinstance(factor, bool):
            return NotImplemented
        return Money(self.amount_minor * factor, self.currency)

    __rmul__ = __mul__

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.amount_minor < other.amount_minor

    def __le__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.amount_minor <= other.amount_minor

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.amount_minor > other.amount_minor

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return self.amount_minor >= other.amount_minor


def convert(money: Money, rate: Decimal, to_currency: str) -> Money:
    """Convert ``money`` using ``rate`` (units of ``to_currency`` per 1 unit of ``money.currency``).

    Pure. Multiplies exactly, adjusts for the two currencies' exponents, and rounds once (half to
    even). The rate must be a finite, positive ``Decimal``; floats raise. See the module docstring
    for the there-and-back error bound.
    """
    untyped_rate = opaque(rate)  # callers may not respect the annotation
    if not isinstance(untyped_rate, Decimal):
        raise TypeError("FX rate must be a Decimal, not a float or other type")
    if not rate.is_finite() or rate <= 0:
        raise MoneyError(f"FX rate must be finite and positive: {rate}")
    _, coefficient, exp = decimal_parts(rate)
    # rate = coefficient * 10**exp ; minor-unit factor = rate * 10**(e_to - e_from)
    shift = exp + exponent(to_currency) - exponent(money.currency)
    numerator = money.amount_minor * coefficient * (10**shift if shift > 0 else 1)
    denominator = 10**-shift if shift < 0 else 1
    return Money(round_div(numerator, denominator), to_currency)


def allocate(total: Money, weights: Mapping[int, int]) -> dict[int, Money]:
    """Split ``total`` in proportion to ``weights`` (keyed by Member id) so parts sum exactly.

    Largest-remainder method: each part gets its floored proportional share; the leftover minor
    units go one each to the largest fractional remainders, ties broken by ascending key.
    Weights are non-negative integers (e.g. basis points) with a positive sum; a zero weight
    never receives anything. A negative total is allocated by magnitude and negated, so the split
    is symmetric.
    """
    if not weights:
        raise MoneyError("Cannot allocate across no parties")
    for key, weight in weights.items():
        _check_int(key, "Allocation key")
        if _check_int(weight, "Allocation weight") < 0:
            raise MoneyError(f"Allocation weight must not be negative: {weight}")
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        raise MoneyError("Allocation weights must have a positive sum")

    magnitude = abs(total.amount_minor)
    shares: dict[int, int] = {}
    remainders: dict[int, int] = {}
    for key, weight in weights.items():
        shares[key], remainders[key] = divmod(magnitude * weight, weight_sum)
    leftover = magnitude - sum(shares.values())
    for key in sorted(weights, key=lambda k: (-remainders[k], k))[:leftover]:
        shares[key] += 1
    sign = -1 if total.amount_minor < 0 else 1
    return {key: Money(sign * shares[key], total.currency) for key in sorted(weights)}


def scale(money: Money, numerator: int, denominator: int) -> Money:
    """``money`` times ``numerator / denominator``, rounded once, half to even (ADR-0015 s4).

    Pure integer arithmetic: ``round_div(amount * numerator, denominator)``. The denominator must
    be positive. A result beyond int64 raises ``MoneyError`` and never wraps.
    """
    if _check_int(denominator, "Scale denominator") <= 0:
        raise MoneyError(f"Scale denominator must be positive: {denominator}")
    _check_int(numerator, "Scale numerator")
    return Money(round_div(money.amount_minor * numerator, denominator), money.currency)


# --- named ATO rounding steps (ADR-0019 section 3) ----------------------------------------------
#
# The Australian tax calculation rounds differently from the rest of the product: a rate applied to
# an amount, an apportionment and a tapering offset each round once, to the nearest cent, and
# taxable income drops its cents. A figure on an exact half cent goes to the even cent, as the
# notice of assessment does (issue #61: two Members on the same surcharge rate, one half cent
# settled up and the other down, both to the even cent). Each convention is a named step here, the
# only place tax rounds, so the proof against assessments can find and correct one in one place.


def ato_scale(money: Money, numerator: int, denominator: int) -> Money:
    """ATO step "rate or ratio of an amount": ``money * numerator / denominator`` rounded once to
    the nearest cent, an exact half going to the even cent. Used for a rate in parts per million
    and for apportioning."""
    if _check_int(denominator, "Scale denominator") <= 0:
        raise MoneyError(f"Scale denominator must be positive: {denominator}")
    _check_int(numerator, "Scale numerator")
    return Money(round_div(money.amount_minor * numerator, denominator), money.currency)


def ato_offset_reduced(
    maximum: Money, reductions: tuple[tuple[Money, int], ...], denominator: int
) -> Money:
    """ATO step "tapering offset": ``maximum`` less each ``(amount, numerator)`` reduction over
    ``denominator``, worked exactly and rounded once, a half going to the even cent. Rounding the
    result once, not each reduction, is what the notice does for the low income tax offset."""
    if _check_int(denominator, "Scale denominator") <= 0:
        raise MoneyError(f"Scale denominator must be positive: {denominator}")
    total = maximum.amount_minor * denominator
    for amount, numerator in reductions:
        total -= amount.amount_minor * _check_int(numerator, "Scale numerator")
    return Money(round_div(total, denominator), maximum.currency)


def ato_whole_dollars(money: Money) -> Money:
    """ATO step "taxable income in whole dollars": drop the cents (toward zero)."""
    unit = 10 ** exponent(money.currency)
    whole = abs(money.amount_minor) // unit * unit
    return Money(-whole if money.amount_minor < 0 else whole, money.currency)


_AMOUNT = re.compile(r"-?[0-9]+(\.[0-9]+)?")


def parse_amount(text: str, currency: str) -> Money:
    """Parse a plain decimal string such as ``"1234.56"`` or ``"-1,234.5"`` into ``Money``.

    Pure integer arithmetic. More decimal places than the currency's exponent are rejected, never
    rounded (so ``"12.345"`` is an error for AUD). Commas and surrounding spaces are ignored.
    """
    clean = text.strip().replace(",", "")
    if not _AMOUNT.fullmatch(clean):
        raise MoneyError(f"Not an amount: {text!r}")
    digits = exponent(currency)
    whole, _, frac = clean.partition(".")
    if len(frac) > digits:
        raise MoneyError(f"{currency} amounts have at most {digits} decimal places: {text!r}")
    negative = whole.startswith("-")
    minor = int(whole.lstrip("-") + frac.ljust(digits, "0"))
    return Money(-minor if negative else minor, currency)


def format_amount(money: Money) -> str:
    """The amount as a plain decimal string with the currency's decimal places: ``"-1234.50"``."""
    digits = exponent(money.currency)
    sign = "-" if money.amount_minor < 0 else ""
    whole, frac = divmod(abs(money.amount_minor), 10**digits)
    return f"{sign}{whole}.{frac:0{digits}d}" if digits else f"{sign}{whole}"
