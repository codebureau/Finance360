"""Holdings based: an Account's value is derived from its Transactions (ADR-0013 section 3).

``derive_value`` is pure. The value on a date is, for each Instrument held, units x the Price on or
before that date x the exact-pair FX rate to the Account currency, **rounded once per line**
(half to even, in integer arithmetic), plus the cash balance. The lines are returned and sum
exactly to the total.

It answers ``NoDerivedValue(reason)``, never a partial total, when a Stream has no Coverage
reaching the date, nothing has been recorded yet, a balance went negative, or a held Instrument has
no Price or no FX rate. A Price older than the staleness limit is **shown as stale, not refused**.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from finance360.behaviours.types import Behaviour
from finance360.core.derived import (
    DerivedLine,
    DerivedValue,
    DeriveInputs,
    NoDerivedValue,
    stream_issues,
)
from finance360.core.fx import FxPoint, NoRate, rate_on_or_before
from finance360.core.money import Money, convert, decimal_parts, exponent, round_div
from finance360.core.price import NoPrice, price_on_or_before
from finance360.core.transaction import derive


def _minor(*factors: Decimal, currency: str) -> int:
    """The exact product of ``factors`` (major units of ``currency``) in minor units, rounded once
    half to even. Integer arithmetic throughout, so nothing depends on the Decimal context."""
    coefficient, exp = 1, exponent(currency)
    for factor in factors:
        _, c, e = decimal_parts(factor)
        coefficient *= c
        exp += e
    return round_div(coefficient * 10**exp, 1) if exp >= 0 else round_div(coefficient, 10**-exp)


def derive_value(inputs: DeriveInputs, on: date) -> DerivedValue | NoDerivedValue:
    def name(instrument_id: int) -> str:
        return inputs.instrument_names.get(instrument_id, f"Instrument {instrument_id}")

    def no(reason: str) -> NoDerivedValue:
        return NoDerivedValue(on, reason)

    issues = stream_issues(inputs.coverage, on)
    if issues:
        return NoDerivedValue(on, " ".join(i.message for i in issues), issues)
    if not any(c.occurred_on <= on for c in inputs.changes):
        return no(f"Nothing is recorded on or before {on.isoformat()}.")
    held = derive(inputs.changes, on)
    if held.invalid:
        first = held.invalid[0]
        what = "cash" if first.instrument_id is None else name(first.instrument_id)
        return no(
            f"A balance went negative ({what}, {first.on.isoformat()}): "
            "possible missing Opening position."
        )

    currency = inputs.account_currency
    cutoff = on - timedelta(days=inputs.stale_days)
    lines: list[DerivedLine] = []
    for instrument_id in sorted(held.units):
        units = held.units[instrument_id]
        native = inputs.instrument_currencies.get(instrument_id)
        if native is None:
            return no(f"{name(instrument_id)} is unknown.")
        price = price_on_or_before(inputs.prices, instrument_id, on)
        if isinstance(price, NoPrice):
            return no(f"{name(instrument_id)} has no Price on or before {on.isoformat()}.")
        rate: FxPoint | None = None
        factor = Decimal(1)
        if native != currency:
            found = rate_on_or_before(inputs.fx, native, currency, on)
            if isinstance(found, NoRate):
                return no(f"No {native} to {currency} rate on or before {on.isoformat()}.")
            rate, factor = found, found.rate
        value = Money(_minor(units, price.value, factor, currency=currency), currency)
        lines.append(
            DerivedLine(
                "holding", instrument_id, native, units, price, rate, value, price.as_at < cutoff
            )
        )
    for cash_ccy in sorted(held.cash):
        cash = held.cash[cash_ccy]
        if cash.amount_minor == 0:
            continue
        cash_rate: FxPoint | None = None
        value = cash
        if cash_ccy != currency:
            found = rate_on_or_before(inputs.fx, cash_ccy, currency, on)
            if isinstance(found, NoRate):
                return no(f"No {cash_ccy} to {currency} rate on or before {on.isoformat()}.")
            cash_rate, value = found, convert(cash, found.rate, currency)
        lines.append(DerivedLine("cash", None, cash_ccy, None, None, cash_rate, value))

    total = Money.zero(currency)
    for line in lines:
        total += line.value
    return DerivedValue(on, total, tuple(lines))


HOLDINGS_BASED = Behaviour(key="holdings_based", name="Holdings based", derive=derive_value)

__all__ = ["HOLDINGS_BASED", "derive_value"]
