"""The ``home_office_hours`` Record type: a monthly worksheet that feeds a Return label.

The household keeps its home office hours as a table with one row per month. There are two
**arrangements**:

* ``regular``: a settled pattern of working from home. A row holds the working days in the month,
  the leave or holiday days, and the office days (worked away from home):

      home days = working days - leave days - office days
      hours     = home days x hours per day

  The record also holds the ``hours_per_day`` and the pattern the rows were prefilled from, which is
  exactly one of ``home_weekdays`` (the Mon to Fri days usually worked at home) or
  ``office_days_per_month``. The pattern is stored for display and for editing; the rows are the
  working and are always editable, so the server never recomputes a row from the pattern.
* ``casual``: no pattern. A row holds the actual hours worked from home that month, and the record
  has no hours per day.

A payload stored before the arrangement existed has no ``arrangement`` key and reads as ``regular``
with no pattern; the next save asks for one.

The record's hours are the sum of the rows. The record yields one **Contribution** to a Return
label total (``business_expenses`` for a sole trader, or the work-related ``work_other``): the total
hours times the fixed rate per hour.

**Method: the fixed-rate method only.** There is no actual-cost method and no timesheet or diary:
the worksheet is the household's own monthly table. The hours are already the hours worked from
home, so no work-use share applies. Someone with only a total enters it as a ``line`` record.

**One financial year.** A record sits within one financial year (both dates in the same 1 July to
30 June year); a stretch across a year boundary is two records, one per year, and nothing is
apportioned between years. ``parse`` cannot see the dates, so the service refuses a record that
spans years or that has a month outside its dates. A record counts only in the year of ``from_on``.

**Exact numbers.** Days and hours per day are decimals with at most two decimal places, held as
hundredths. A row's hours are home days x hours per day, so they are exact in ten-thousandths of an
hour (hundredths x hundredths); the total is the exact sum of the rows. No float is used anywhere.

**The rate comes only from the Rule pack** (``tax.home_office.rate_per_hour``, money in AUD per
hour, held per financial year), through the ``rules`` argument, and it is the figure of the year the
record sits in. ``RecordRules`` hands a money figure over as whole cents. Without it the deduction
cannot be worked out: ``missing_figures`` says so, and the register and the estimate surface that,
never skipping the record quietly.

**Rounding.** The deduction is total hours x rate, rounded once per record with
``core.money.scale`` (integer arithmetic, half to even): ``rate_cents x ten-thousandths / 10000``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from finance360.capabilities.tax import labels
from finance360.capabilities.tax.input import (
    InputError,
    as_list,
    as_obj,
    as_text,
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
from finance360.core.rulepack import fy_label

KEY = "home_office_hours"
#: The Rule pack key for the fixed rate: money (AUD) per hour of home office use.
RATE_KEY = "tax.home_office.rate_per_hour"
#: The Return labels a worksheet may feed. Each lists this type in ``labels.py``.
ALLOWED_LABELS = ("business_expenses", "work_other")

PLACES = 2
HUNDREDTHS = 100
#: Row hours are home days x hours per day: hundredths of a day x hundredths of an hour.
TEN_THOUSANDTHS = HUNDREDTHS * HUNDREDTHS
MAX_DAYS = 31 * HUNDREDTHS
MAX_HOURS_PER_DAY = 24 * HUNDREDTHS
MAX_MONTHS = 12
#: The most hours of one month, in hundredths: 31 days of 24 hours.
MAX_MONTH_HOURS = 31 * 24 * HUNDREDTHS

REGULAR = "regular"
CASUAL = "casual"
ARRANGEMENTS = (REGULAR, CASUAL)
#: The weekday codes a regular arrangement may name, in week order.
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri")

_NUMBER = re.compile(r"([0-9]+)(?:\.([0-9]+))?")
_MONTH = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")


@dataclass(frozen=True)
class MonthRow:
    #: ``"2025-07"``.
    month: str
    #: Each in hundredths of a day (zero in a casual row).
    working: int = 0
    leave: int = 0
    office: int = 0
    #: A casual row: the actual hours worked from home, in hundredths of an hour.
    actual: int | None = None

    @property
    def home(self) -> int:
        """Working days less leave and office days, in hundredths of a day (never negative)."""
        return self.working - self.leave - self.office


@dataclass(frozen=True)
class HomeOfficeHours:
    label: str
    #: Hours worked per home day, in hundredths of an hour (``None`` for a casual record).
    hours_per_day: int | None
    months: tuple[MonthRow, ...]
    arrangement: str = REGULAR
    #: Regular: the weekdays usually worked at home, in week order. ``None`` with the other pattern.
    home_weekdays: tuple[str, ...] | None = None
    #: Regular: the office days a month, in hundredths of a day. ``None`` with the other pattern.
    office_days_per_month: int | None = None


def financial_year_of(day: date) -> int:
    """The year it ends in: 1 July 2025 is FY 2026."""
    return day.year + 1 if day.month >= 7 else day.year


def month_financial_year(month: str) -> int:
    year, number = int(month[:4]), int(month[5:])
    return year + 1 if number >= 7 else year


def hundredths_text(value: int) -> str:
    """``760`` as ``"7.60"``."""
    return f"{value // HUNDREDTHS}.{value % HUNDREDTHS:02d}"


def hours_text(ten_thousandths: int) -> str:
    """Hours held in ten-thousandths, exactly: two decimal places, more only when needed."""
    whole, part = divmod(ten_thousandths, TEN_THOUSANDTHS)
    digits = f"{part:04d}"
    digits = digits[:2] if digits.endswith("00") else digits.rstrip("0")
    return f"{whole}.{digits}"


def parse_decimal(raw: object, what: str, maximum: int) -> int:
    """A non-negative decimal of text with at most two decimal places, as hundredths."""
    text = as_text(raw, what).strip()
    found = _NUMBER.fullmatch(text)
    if found is None:
        raise InputError(f"Enter {what} as a number, such as 7.6 or 21.")
    whole, fraction = found.group(1), found.group(2) or ""
    if len(fraction) > PLACES:
        raise InputError(f"{what[0].upper()}{what[1:]} has at most {PLACES} decimal places.")
    value = int(whole) * HUNDREDTHS + int(fraction.ljust(PLACES, "0"))
    if value > maximum:
        raise InputError(f"{what[0].upper()}{what[1:]} is at most {hundredths_text(maximum)}.")
    return value


def month_name(month: str) -> str:
    names = "January February March April May June July August September October November December"
    return f"{names.split()[int(month[5:]) - 1]} {month[:4]}"


def parse_row(raw: object, arrangement: str = REGULAR) -> MonthRow:
    body = as_obj(raw, "A month")
    month = as_text(body.get("month"), "month").strip()
    if _MONTH.fullmatch(month) is None:
        raise InputError("Enter the month as a year and month, such as 2025-07.")
    if arrangement == CASUAL:
        if any(k in body for k in ("working_days", "leave_days", "office_days")):
            raise InputError("A casual month holds only the hours worked from home.")
        return MonthRow(month, actual=parse_decimal(body.get("hours"), "hours", MAX_MONTH_HOURS))
    if "hours" in body:
        raise InputError("A regular month holds days, not hours.")
    row = MonthRow(
        month,
        parse_decimal(body.get("working_days"), "working days", MAX_DAYS),
        parse_decimal(body.get("leave_days"), "leave days", MAX_DAYS),
        parse_decimal(body.get("office_days"), "office days", MAX_DAYS),
    )
    if row.home < 0:
        raise InputError(
            f"{month_name(month)}: leave and office days are more than the working days."
        )
    return row


def parse_weekdays(raw: object) -> tuple[str, ...]:
    """A non-empty, repeat-free subset of the five weekday codes, in week order."""
    codes = [as_text(c, "weekday") for c in as_list(raw, "home weekdays")]
    if not codes:
        raise InputError("Choose at least one weekday worked at home.")
    if any(c not in WEEKDAYS for c in codes):
        raise InputError(f"Weekdays are {', '.join(WEEKDAYS)}.")
    if len(set(codes)) != len(codes):
        raise InputError("Each weekday appears once.")
    return tuple(c for c in WEEKDAYS if c in codes)


def parse(raw: object) -> HomeOfficeHours:
    reject_floats(raw)
    body = as_obj(raw, "A home office worksheet")
    label = as_text(body.get("label"), "label")
    if label not in ALLOWED_LABELS:
        raise InputError("Home office hours feed business expenses or other work expenses.")
    check_label(label, KEY, (labels.MONEY,))
    # A payload stored before the arrangement existed has no key: it is regular, with no pattern.
    arrangement = as_text(body.get("arrangement", REGULAR), "arrangement")
    if arrangement not in ARRANGEMENTS:
        raise InputError("The arrangement is regular or casual.")
    per_day: int | None = None
    weekdays: tuple[str, ...] | None = None
    per_month: int | None = None
    if arrangement == REGULAR:
        per_day = parse_decimal(body.get("hours_per_day"), "hours per day", MAX_HOURS_PER_DAY)
        if per_day <= 0:
            raise InputError("The hours per day are more than 0.")
        if "home_weekdays" in body and "office_days_per_month" in body:
            raise InputError("Give the weekdays at home or the office days a month, not both.")
        if "home_weekdays" in body:
            weekdays = parse_weekdays(body["home_weekdays"])
        if "office_days_per_month" in body:
            per_month = parse_decimal(
                body["office_days_per_month"], "office days a month", MAX_DAYS
            )
    elif any(k in body for k in ("hours_per_day", "home_weekdays", "office_days_per_month")):
        raise InputError("A casual record has no hours per day and no weekday pattern.")
    rows = tuple(parse_row(r, arrangement) for r in as_list(body.get("months"), "months"))
    if not rows:
        raise InputError("Add at least one month.")
    if len(rows) > MAX_MONTHS:
        raise InputError(f"A worksheet has at most {MAX_MONTHS} months.")
    months = [r.month for r in rows]
    if len(set(months)) != len(months):
        raise InputError("Each month appears once.")
    return HomeOfficeHours(label, per_day, rows, arrangement, weekdays, per_month)


def to_payload(value: HomeOfficeHours) -> dict[str, Any]:
    if value.arrangement == CASUAL:
        return {
            "label": value.label,
            "arrangement": CASUAL,
            "months": [
                {"month": r.month, "hours": hundredths_text(r.actual or 0)} for r in value.months
            ],
        }
    payload: dict[str, Any] = {
        "label": value.label,
        "arrangement": REGULAR,
        "hours_per_day": hundredths_text(value.hours_per_day or 0),
    }
    if value.home_weekdays is not None:
        payload["home_weekdays"] = list(value.home_weekdays)
    if value.office_days_per_month is not None:
        payload["office_days_per_month"] = hundredths_text(value.office_days_per_month)
    payload["months"] = [
        {
            "month": r.month,
            "working_days": hundredths_text(r.working),
            "leave_days": hundredths_text(r.leave),
            "office_days": hundredths_text(r.office),
        }
        for r in value.months
    ]
    return payload


def row_hours(row: MonthRow, hours_per_day: int | None) -> int:
    """The row's hours in ten-thousandths, exact: home days x hours per day for a regular row, the
    hours entered (hundredths of an hour) for a casual one."""
    if row.actual is not None:
        return row.actual * HUNDREDTHS
    return row.home * (hours_per_day or 0)


def total_hours(value: HomeOfficeHours) -> int:
    """The worksheet's hours in ten-thousandths: the exact sum of its rows."""
    return sum(row_hours(r, value.hours_per_day) for r in value.months)


def rate_for(record: Record, rules: RecordRules) -> Money | None:
    """The fixed rate per hour: the figure of the year the record sits in, in AUD."""
    cents = rules.figure(RATE_KEY, financial_year_of(record.from_on))
    return Money(cents, BASE_CURRENCY) if cents is not None else None


def missing_figures(record: Record, rules: RecordRules) -> tuple[str, ...]:
    return () if rate_for(record, rules) is not None else (RATE_KEY,)


def deduction(value: HomeOfficeHours, rate: Money) -> Money:
    """Total hours x rate, one rounding: ``rate x ten-thousandths / 10000``."""
    return scale(rate, total_hours(value), TEN_THOUSANDTHS)


def figures_read(record: Record) -> tuple[tuple[str, int], ...]:
    return ((RATE_KEY, financial_year_of(record.from_on)),)


def calculation(record: Record, rules: RecordRules) -> tuple[Step, ...]:
    """The working, as exact steps: the hours, then (once the rate is held) the deduction.

    A regular record's hours are its home days times the hours per day, exactly (each row is that
    product, so the sum is too). A casual record's hours are the sum of the months entered.
    """
    sheet: HomeOfficeHours = record.value
    total = hours_text(total_hours(sheet))
    if sheet.arrangement == CASUAL:
        months = tuple(
            Operand(
                hundredths_text(r.actual or 0),
                None,
                month_name(r.month),
                None if i == 0 else "+",
            )
            for i, r in enumerate(sheet.months)
        )
        hours = Step("Hours worked from home", months, Operand(total, "hours", "sum of the months"))
    else:
        days = sum(r.home for r in sheet.months)
        hours = Step(
            "Hours worked from home",
            (
                Operand(hundredths_text(days), "home days", "working days less leave and office"),
                Operand(
                    hundredths_text(sheet.hours_per_day or 0), "hours a day", "hours per day", "×"
                ),
            ),
            Operand(total, "hours", "total hours"),
        )
    rate = rate_for(record, rules)
    if rate is None:
        return (hours,)
    label = f"rate for FY {fy_label(financial_year_of(record.from_on))}"
    return (
        hours,
        Step(
            "Deduction",
            (
                Operand(total, "hours", "total hours"),
                Operand(format_amount(rate), "an hour", label, "×"),
            ),
            Operand(format_amount(deduction(sheet, rate)), None, "deduction"),
            "Rounded once, to the cent, for the whole record (not month by month).",
        ),
    )


def contributions(
    record: Record, financial_year: int, rules: RecordRules
) -> tuple[Contribution, ...]:
    value: HomeOfficeHours = record.value
    rate = rate_for(record, rules)
    if rate is None or financial_year != financial_year_of(record.from_on):
        return ()
    amount = deduction(value, rate)
    if amount.amount_minor == 0:
        return ()
    return (Contribution(record.id, value.label, amount),)


TYPE = RecordType(
    KEY,
    "Home office hours",
    parse,
    to_payload,
    lambda v: v.label,
    contributions,
    missing_figures,
    figures_read,
    calculation,
)
