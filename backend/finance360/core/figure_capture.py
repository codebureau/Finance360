"""Validate a Rule figure a person typed (or, later, an AI proposed): the one validator (ADR-0022).

Pure and jurisdiction-agnostic. A ``FigureSpec`` (``core.rulepack``) declares, per key, the unit
and the plausible range. This module parses the canonical figure text (``"123.45"`` dollars,
``"16.5%"``, brackets as ``"over:rate,over:rate"``), checks unit and range, formats a value back to
that text and flags a large year-on-year change. The maintainer's script (``rulepacks/capture``)
and the app's capture service (``core.rulepack_store``) both validate through it: one validator.
It holds no figure and does no I/O.
"""

from __future__ import annotations

import re

from finance360.core.money import Money, MoneyError, format_amount, parse_amount
from finance360.core.rate import PPM, Rate, RateError
from finance360.core.rulepack import (
    BRACKETS,
    INT,
    MONEY,
    RATE,
    Bracket,
    FigureSpec,
    Value,
)

CURRENCY = "AUD"
#: A unit a person may have typed beside a money figure, mapped to the unit it means.
_UNIT_WORDS: dict[str, str] = {
    "pa": "year",
    "p.a.": "year",
    "/yr": "year",
    "/year": "year",
    "year": "year",
    "yearly": "year",
    "annual": "year",
    "annually": "year",
    "per year": "year",
    "pf": "fortnight",
    "/fn": "fortnight",
    "fortnight": "fortnight",
    "per fortnight": "fortnight",
    "pw": "week",
    "/wk": "week",
    "week": "week",
    "weekly": "week",
    "per week": "week",
    "pm": "month",
    "/mth": "month",
    "month": "month",
    "monthly": "month",
    "per month": "month",
    "/hr": "hour",
    "/hour": "hour",
    "hour": "hour",
    "hourly": "hour",
    "per hour": "hour",
    "/km": "km",
    "/kilometre": "km",
    "km": "km",
    "kilometre": "km",
    "per km": "km",
    "per kilometre": "km",
}


class FigureError(ValueError):
    """What a person typed is not a valid figure for the key (the message says why)."""


# --- parse and format ----------------------------------------------------------------------


def _money(spec: FigureSpec, text: str) -> Money:
    clean = text.strip().lower()
    if "%" in clean:
        raise FigureError("That is a percentage; this figure is an amount in dollars.")
    clean = clean.removeprefix("$").strip()
    unit = spec.unit
    for word in sorted(_UNIT_WORDS, key=len, reverse=True):
        if clean.endswith(word) and clean != word:
            stated = _UNIT_WORDS[word]
            if unit is not None and stated != unit:
                raise FigureError(f"That is a per-{stated} figure; this one is per {unit}.")
            clean = clean[: -len(word)].strip()
            break
    try:
        return parse_amount(clean.removeprefix("$"), CURRENCY)
    except MoneyError:
        raise FigureError(
            "Enter a dollar amount such as 1234.50 (no letters or symbols)."
        ) from None


def _rate(text: str) -> Rate:
    clean = text.strip()
    if "$" in clean:
        raise FigureError("That is a dollar amount; this figure is a percentage.")
    try:
        return Rate.from_percent(clean.removesuffix("%").strip())
    except (RateError, TypeError):
        raise FigureError(
            "Enter a percentage such as 16 or 16.5 (at most 4 decimal places)."
        ) from None


def _check_money_range(money: Money, low: int, high: int, what: str) -> None:
    if not low <= money.amount_minor <= high:
        raise FigureError(
            f"{what} {format_amount(money)} is outside the plausible range "
            f"{format_amount(Money(low, CURRENCY))} to {format_amount(Money(high, CURRENCY))}."
        )


def _check_rate_range(rate: Rate, low: int, high: int) -> None:
    if not low <= rate.ppm <= high:
        raise FigureError(
            f"{rate.to_percent()}% is outside the plausible range "
            f"{Rate(low).to_percent()}% to {Rate(high).to_percent()}%."
        )


def parse_figure(spec: FigureSpec, text: str) -> Value:
    """Parse and validate a typed figure. Raises ``FigureError`` naming the problem."""
    if spec.kind == MONEY:
        money = _money(spec, text)
        _check_money_range(money, spec.low, spec.high, "")
        return money
    if spec.kind == RATE:
        rate = _rate(text)
        _check_rate_range(rate, spec.low, spec.high)
        return rate
    if spec.kind == INT:
        clean = text.strip()
        if not (clean.isascii() and clean.lstrip("-").isdigit()):
            raise FigureError("Enter a whole number.")
        number = int(clean)
        if not spec.low <= number <= spec.high:
            raise FigureError(f"{number} is outside the plausible range {spec.low} to {spec.high}.")
        return number
    return _brackets(spec, text)


def _brackets(spec: FigureSpec, text: str) -> tuple[Bracket, ...]:
    tokens = [t for t in re.split(r"[,;\n]", text) if t.strip()]
    if not tokens:
        raise FigureError("Enter each band as over:rate, separated by commas (0:0,20000:15).")
    bands: list[Bracket] = []
    for number, token in enumerate(tokens, start=1):
        over_text, colon, rate_text = token.partition(":")
        if not colon:
            raise FigureError(f"Band {number} ({token.strip()!r}) needs the form over:rate.")
        over = _money(spec, over_text)
        rate = _rate(rate_text)
        _check_money_range(over, spec.low, spec.high, f"Band {number} threshold")
        if not spec.rate_low <= rate.ppm <= spec.rate_high:
            raise FigureError(
                f"Band {number} rate {rate.to_percent()}% is outside the plausible range "
                f"{Rate(spec.rate_low).to_percent()}% to {Rate(spec.rate_high).to_percent()}%."
            )
        bands.append(Bracket(over, rate))
    if bands[0].threshold.amount_minor != 0:
        raise FigureError("The first band starts at 0.")
    for earlier, later in zip(bands, bands[1:], strict=False):
        if later.threshold <= earlier.threshold:
            raise FigureError("Band thresholds must ascend, each above the one before.")
    return tuple(bands)


def format_figure(spec: FigureSpec, value: Value) -> str:
    """The canonical text of a value; ``parse_figure(spec, format_figure(spec, v)) == v``."""
    if isinstance(value, Money):
        return format_amount(value)
    if isinstance(value, Rate):
        return f"{value.to_percent()}%"
    if isinstance(value, tuple):
        return ",".join(f"{format_amount(b.threshold)}:{b.rate.to_percent()}%" for b in value)
    return str(value)


# --- year-on-year change -------------------------------------------------------------------


def _jumped(previous: int, new: int, limit_ppm: int) -> bool:
    """Whether ``new`` differs from ``previous`` by more than ``limit_ppm`` of it (integers only).
    Any move off or onto zero counts."""
    if previous == new:
        return False
    if previous == 0:
        return True
    return abs(new - previous) * PPM > limit_ppm * abs(previous)


def large_change(spec: FigureSpec, previous: Value, new: Value) -> str | None:
    """A message if ``new`` is a large change from ``previous`` (the earlier year's figure)."""
    if isinstance(previous, tuple) and isinstance(new, tuple):
        if len(previous) != len(new):
            return f"The number of bands changed from {len(previous)} to {len(new)}."
        moved = any(
            _jumped(p.threshold.amount_minor, n.threshold.amount_minor, spec.max_change_ppm)
            or _jumped(p.rate.ppm, n.rate.ppm, spec.max_change_ppm)
            for p, n in zip(previous, new, strict=True)
        )
    elif isinstance(previous, Money) and isinstance(new, Money):
        moved = _jumped(previous.amount_minor, new.amount_minor, spec.max_change_ppm)
    elif isinstance(previous, Rate) and isinstance(new, Rate):
        moved = _jumped(previous.ppm, new.ppm, spec.max_change_ppm)
    elif isinstance(previous, int) and isinstance(new, int):
        moved = _jumped(previous, new, spec.max_change_ppm)
    else:
        return None
    if not moved:
        return None
    limit = Rate(spec.max_change_ppm).to_percent()
    return (
        f"Changed by more than {limit}% from {format_figure(spec, previous)} "
        f"to {format_figure(spec, new)}."
    )


def range_text(spec: FigureSpec) -> str:
    if spec.kind == MONEY:
        low, high = (format_amount(Money(n, CURRENCY)) for n in (spec.low, spec.high))
        return f"${low} to ${high}" + (f" per {spec.unit}" if spec.unit else "")
    if spec.kind == RATE:
        return f"{Rate(spec.low).to_percent()}% to {Rate(spec.high).to_percent()}%"
    if spec.kind == BRACKETS:
        low, high = (format_amount(Money(n, CURRENCY)) for n in (spec.low, spec.high))
        return (
            f"each threshold ${low} to ${high} per {spec.unit or 'year'}, "
            f"each rate {Rate(spec.rate_low).to_percent()}% to {Rate(spec.rate_high).to_percent()}%"
        )
    return f"{spec.low} to {spec.high}"


def entry_hint(spec: FigureSpec) -> str:
    if spec.kind == MONEY:
        return "dollars" + (f" per {spec.unit}" if spec.unit else "") + ", e.g. 1234.50"
    if spec.kind == RATE:
        return "a percentage, e.g. 16.5"
    if spec.kind == BRACKETS:
        return "bands as over:rate, comma separated, first over 0, e.g. 0:0,20000:15"
    return "a whole number"
