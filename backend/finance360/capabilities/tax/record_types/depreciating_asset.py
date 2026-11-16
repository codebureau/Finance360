"""The ``depreciating_asset`` Record type: a worksheet that feeds a Return label.

A depreciating asset is not a separate entry on the return. Each financial year it yields one
**Contribution** to a Return label total (``business_expenses`` for a sole trader, or the
work-related ``work_other``): that year's deduction. The record's ``from_on`` is the **start date**
(when the asset was first used or installed ready for use). Its ``to_on`` is optional: blank while
the asset is still used, otherwise the last day it counts (no balancing adjustment is worked out).

**Method: diminishing value only.** For each financial year that the asset is held:

    decline in value = opening adjustable value x (days held / 365) x (multiplier / effective life)
    deduction        = decline in value x work-use share

The first year opens at the cost and counts from the start date to 30 June, both days included.
Each later year opens at the previous year's closing adjustable value (opening less the decline in
value, never below nothing).

**Source and what is unchecked.** The ATO page "Uniform capital allowance system: calculate decline
in value" (and ITAA 1997 s 40-70) states the formula above. It was not opened (government pages
block automated reads); it was taken from a web search summary. To be confirmed by a person: that
the first day is counted, that 365 is the divisor in a leap year too (so a full leap year is
366/365, as secondary sources say), and that the Rule pack multiplier is the 200% for assets held
from 10 May 2006.

**Claim in full** deducts the work-use share of the cost in the financial year of the start date
and nothing later. There is no threshold test here: whether an asset is eligible depends on how it
is used, which the Guide tells the household to check.

**The multiplier comes only from the Rule pack** (``tax.depreciation.dv_percent``, a whole percent
such as 200), through the ``rules`` argument, and it is the figure of the financial year the asset
started in, kept for the asset's life. Without it the deduction cannot be worked out:
``missing_figures`` says so, and the register and the estimate surface that, never skipping the
asset quietly.

**Rounding.** The decline in value and the deduction are each one rounding per year, with
``core.money.scale`` (integer arithmetic). Cumulative deductions are held to the work-use share of
the cost, so rounding can never claim more than the asset is worth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

from finance360.capabilities.tax import labels
from finance360.capabilities.tax.input import (
    InputError,
    as_int,
    as_obj,
    as_text,
    money_in,
    reject_floats,
)
from finance360.capabilities.tax.record_types.base import (
    Contribution,
    Operand,
    Record,
    RecordRules,
    RecordType,
    Step,
    check_label,
)
from finance360.core.money import BASE_CURRENCY, Money, format_amount, scale
from finance360.core.rate import PPM, Rate, RateError
from finance360.core.rulepack import fy_label

KEY = "depreciating_asset"
#: The Rule pack key for the diminishing value multiplier, as a whole percent (200 is 200%).
DV_PERCENT_KEY = "tax.depreciation.dv_percent"
#: The Return labels an asset may feed. Each lists this type in ``labels.py``.
ALLOWED_LABELS = ("business_expenses", "work_other")

#: The divisor in the ATO formula. It is 365 in a leap year too.
DAYS_IN_YEAR = 365
MIN_LIFE = Fraction(1)
MAX_LIFE = Fraction(100)
LIFE_PLACES = 2


@dataclass(frozen=True)
class DepreciatingAsset:
    label: str
    cost: Money
    #: Years, as the household entered it (up to two decimal places).
    effective_life: Fraction
    #: The part of the use that is for work or business; all of it when not stated.
    work_use: Rate
    claim_in_full: bool = False


@dataclass(frozen=True)
class YearRow:
    """One financial year of an asset's schedule. Computed on demand, never stored."""

    financial_year: int
    #: Days held in the year (the formula's numerator; 0 for a claim in full).
    days: int
    opening: Money
    #: The fall in adjustable value (the whole asset, before the work-use share).
    decline: Money
    #: What counts toward the Return label: the decline in value times the work-use share.
    deduction: Money
    closing: Money


def financial_year_of(day: date) -> int:
    """The year it ends in: 1 July 2025 is FY 2026."""
    return day.year + 1 if day.month >= 7 else day.year


def life_text(life: Fraction) -> str:
    """``Fraction(9, 2)`` as ``"4.5"``, ``Fraction(4)`` as ``"4"``."""
    text = format(Decimal(life.numerator) / Decimal(life.denominator), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def parse_life(raw: object) -> Fraction:
    """Effective life in years from decimal text: 1 to 100, up to two decimal places."""
    text = as_text(raw, "effective_life_years").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise InputError("Enter the effective life in years, such as 4 or 2.5.") from None
    if not value.is_finite() or "e" in text.lower():
        raise InputError("Enter the effective life in years, such as 4 or 2.5.")
    if -value.as_tuple().exponent > LIFE_PLACES:  # type: ignore[operator]
        raise InputError(f"The effective life has at most {LIFE_PLACES} decimal places.")
    life = Fraction(value)
    if not MIN_LIFE <= life <= MAX_LIFE:
        raise InputError(f"The effective life is from {MIN_LIFE} to {MAX_LIFE} years.")
    return life


def parse(raw: object) -> DepreciatingAsset:
    reject_floats(raw)
    body = as_obj(raw, "A depreciating asset")
    label = as_text(body.get("label"), "label")
    if label not in ALLOWED_LABELS:
        raise InputError("A depreciating asset feeds business expenses or other work expenses.")
    check_label(label, KEY, (labels.MONEY,))
    cost = money_in(body.get("cost"), "cost")
    if cost.currency != BASE_CURRENCY:
        raise InputError(f"A depreciating asset is in {BASE_CURRENCY}; enter the cost converted.")
    if cost.amount_minor <= 0:
        raise InputError("The cost is more than 0.")
    share = body.get("work_use_ppm", PPM)
    try:
        work_use = Rate(as_int(share, "work_use_ppm"))
    except RateError as error:
        raise InputError(str(error)) from None
    if not 0 < work_use.ppm <= PPM:
        raise InputError("A work-use share is more than 0% and at most 100%.")
    claim = body.get("claim_in_full", False)
    if not isinstance(claim, bool):
        raise InputError("'claim_in_full' must be true or false.")
    return DepreciatingAsset(
        label, cost, parse_life(body.get("effective_life_years")), work_use, claim
    )


def to_payload(value: DepreciatingAsset) -> dict[str, Any]:
    return {
        "label": value.label,
        "cost": {"minor": value.cost.amount_minor, "ccy": value.cost.currency},
        "effective_life_years": life_text(value.effective_life),
        "work_use_ppm": value.work_use.ppm,
        "claim_in_full": value.claim_in_full,
    }


def last_year(record: Record) -> int | None:
    """The last financial year the asset counts in, or ``None`` while it has no end date."""
    return financial_year_of(record.to_on) if record.to_on is not None else None


def schedule(record: Record, dv_percent: int, through: int | None = None) -> tuple[YearRow, ...]:
    """The asset's years from its start, up to ``through`` (its end date's year, if sooner).

    Stops early when nothing is left to write down. The first row is the year of the start date.
    """
    asset: DepreciatingAsset = record.value
    first = financial_year_of(record.from_on)
    final = last_year(record)
    limit = first if through is None else through
    if final is not None:
        limit = min(limit, final)
    cap = scale(asset.cost, asset.work_use.ppm, PPM)  # the most the work-use share can ever claim
    zero = Money.zero(BASE_CURRENCY)
    if asset.claim_in_full:
        row = YearRow(first, 0, asset.cost, asset.cost, cap, zero)
        return (row,) if limit >= first else ()
    rows: list[YearRow] = []
    opening, claimed = asset.cost, zero
    life = asset.effective_life
    for year in range(first, limit + 1):
        if opening.amount_minor <= 0:
            break
        start = max(record.from_on, date(year - 1, 7, 1))
        end = min(record.to_on or date(year, 6, 30), date(year, 6, 30))
        days = (end - start).days + 1
        # decline = opening x days/365 x (percent/100) / life, one rounding
        denominator = DAYS_IN_YEAR * 100 * life.numerator
        numerator = days * dv_percent * life.denominator
        decline = scale(opening, numerator, denominator)
        decline = Money(min(decline.amount_minor, opening.amount_minor), opening.currency)
        deduction = scale(opening, numerator * asset.work_use.ppm, denominator * PPM)
        room = cap - claimed
        deduction = Money(
            max(0, min(deduction.amount_minor, room.amount_minor)), deduction.currency
        )
        closing = opening - decline
        rows.append(YearRow(year, days, opening, decline, deduction, closing))
        opening, claimed = closing, claimed + deduction
    return tuple(rows)


def full_schedule(record: Record, dv_percent: int) -> tuple[YearRow, ...]:
    """Every year the worksheet shows: the effective life's years and the year after (a part-year
    start), or to the end date's year if sooner."""
    asset: DepreciatingAsset = record.value
    first = financial_year_of(record.from_on)
    years = -(-asset.effective_life.numerator // asset.effective_life.denominator)
    return schedule(record, dv_percent, first + years)


def figures_read(record: Record) -> tuple[tuple[str, int], ...]:
    """The start year's multiplier; an asset claimed in full reads no figure."""
    asset: DepreciatingAsset = record.value
    if asset.claim_in_full:
        return ()
    return ((DV_PERCENT_KEY, financial_year_of(record.from_on)),)


def calculation(record: Record, rules: RecordRules) -> tuple[Step, ...]:
    """The schedule's working, year by year, as exact steps.

    Each year is ``opening x days / 365 x multiplier / effective life = decline``, rounded once;
    then, below 100% work use, ``decline x work-use share = deduction``. Claimed in full is
    ``cost x work-use share = deduction``. A step notes any limit that made the printed product
    differ from the figure counted.
    """
    asset: DepreciatingAsset = record.value
    percent = multiplier_for(record, rules)
    if percent is None and not asset.claim_in_full:
        return ()
    share = Operand(asset.work_use.to_percent(), "%", "work-use share", "×")
    if asset.claim_in_full:
        rows = schedule(record, 0)
        return tuple(
            Step(
                f"FY {fy_label(r.financial_year)}: claimed in full",
                (Operand(format_amount(asset.cost), None, "cost"), share),
                Operand(format_amount(r.deduction), None, "deduction"),
                "Claimed in full in the first year and nothing after. Check the asset is eligible.",
            )
            for r in rows
        )
    steps: list[Step] = []
    life = asset.effective_life
    for r in full_schedule(record, percent or 0):
        denominator = DAYS_IN_YEAR * 100 * life.numerator
        numerator = r.days * (percent or 0) * life.denominator
        worked = scale(r.opening, numerator, denominator)
        capped = worked.amount_minor > r.decline.amount_minor
        label = fy_label(r.financial_year)
        steps.append(
            Step(
                f"FY {label}: decline in value",
                (
                    Operand(format_amount(r.opening), None, "opening value"),
                    Operand(str(r.days), "days", "days held", "×"),
                    Operand(str(DAYS_IN_YEAR), None, "days in the year", "÷"),
                    Operand(str(percent), "%", "diminishing value multiplier", "×"),
                    Operand(life_text(life), "years", "effective life", "÷"),
                ),
                Operand(format_amount(r.decline), None, "decline in value"),
                "Limited to the opening value: an asset is never written down below nothing."
                if capped
                else "Rounded once, to the cent.",
            )
        )
        if asset.work_use.ppm == PPM:
            continue
        by_share = scale(r.decline, asset.work_use.ppm, PPM)
        note = None
        if r.deduction != by_share:
            unlimited = scale(r.opening, numerator * asset.work_use.ppm, denominator * PPM)
            note = (
                "Limited so the total claimed never exceeds the work-use share of the cost."
                if r.deduction != unlimited
                else "Worked from the unrounded decline and rounded once, so it can differ by "
                "a cent from this product."
            )
        steps.append(
            Step(
                f"FY {label}: deduction",
                (Operand(format_amount(r.decline), None, "decline in value"), share),
                Operand(format_amount(r.deduction), None, "deduction"),
                note or "Rounded once, to the cent.",
            )
        )
    return tuple(steps)


def multiplier_for(record: Record, rules: RecordRules) -> int | None:
    """The diminishing value percent for the asset: the figure of the year it started in."""
    return rules.figure(DV_PERCENT_KEY, financial_year_of(record.from_on))


def missing_figures(record: Record, rules: RecordRules) -> tuple[str, ...]:
    asset: DepreciatingAsset = record.value
    if asset.claim_in_full or multiplier_for(record, rules) is not None:
        return ()
    return (DV_PERCENT_KEY,)


def deduction_in(record: Record, financial_year: int, rules: RecordRules) -> Money | None:
    """The asset's deduction in ``financial_year``, or ``None`` when the rules lack its figure."""
    asset: DepreciatingAsset = record.value
    percent = multiplier_for(record, rules)
    if percent is None and not asset.claim_in_full:
        return None
    for row in schedule(record, percent or 0, financial_year):
        if row.financial_year == financial_year:
            return row.deduction
    return Money.zero(BASE_CURRENCY)


def contributions(
    record: Record, financial_year: int, rules: RecordRules
) -> tuple[Contribution, ...]:
    value: DepreciatingAsset = record.value
    deduction = deduction_in(record, financial_year, rules)
    if deduction is None or deduction.amount_minor == 0:
        return ()
    return (Contribution(record.id, value.label, deduction),)


TYPE = RecordType(
    KEY,
    "Depreciating asset",
    parse,
    to_payload,
    lambda v: v.label,
    contributions,
    missing_figures,
    figures_read,
    calculation,
)
