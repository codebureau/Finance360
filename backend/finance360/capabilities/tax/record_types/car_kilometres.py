"""The ``car_kilometres`` Record type: a schedule of work trips that feeds a Return label.

The household keeps its work kilometres as a list of **trips**. Each trip has a description (where
to or from, and the purpose), the kilometres of one trip, how many **times** it was made (default 1)
and, optionally, the date. The record's kilometres are the sum of kilometres x times over the trips.

The record yields one **Contribution** to a Return label total (``business_expenses`` for a sole
trader, or the work-related car expenses label ``work_car``): the kilometres counted times the Rule
pack's rate per kilometre, where the kilometres counted are the total limited to the Rule pack's
limit per car:

    counted km = min(total km, limit km)
    deduction  = counted km x rate per km

**Method: cents per kilometre only.** There is no logbook method, no actual-cost method and no trip
tracking. The kilometres are already work kilometres, so no work-use share applies.

**One record per Member per financial year.** The limit is per car and this household has one car a
person, so a Member's trips for a year all go in one record. A record sits within one financial
year (both dates in the same 1 July to 30 June year) and counts only in the year of ``from_on``.
``parse`` cannot see the dates or the other records, so the service refuses a record that spans
years, a trip dated outside the record, and a second record for the same Member and year.

**Exact numbers.** Kilometres are decimals with at most one decimal place, held as tenths of a
kilometre. The total is the exact sum of kilometres x times in tenths. No float is used anywhere.

**The rate and the limit come only from the Rule pack** (``tax.car.rate_per_km``, money in AUD per
kilometre, and ``tax.car.max_km``, a whole number of kilometres, each held per financial year),
through the ``rules`` argument, and they are the figures of the year the record sits in.
``RecordRules`` hands a money figure over as whole cents. Without either the deduction cannot be
worked out: ``missing_figures`` says so, and the register and the estimate surface that, never
skipping the record quietly.

**Rounding.** The deduction is rounded once per record with ``core.money.scale`` (integer
arithmetic, half to even): ``rate_cents x counted tenths / 10``.
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

KEY = "car_kilometres"
#: The Rule pack key for the rate: money (AUD) per kilometre.
RATE_KEY = "tax.car.rate_per_km"
#: The Rule pack key for the most kilometres counted per car in a year (a whole number of km).
LIMIT_KEY = "tax.car.max_km"
#: The Return labels a record may feed. Each lists this type in ``labels.py``.
ALLOWED_LABELS = ("business_expenses", "work_car")

TENTHS = 10
MAX_TRIPS = 500
#: A trip's kilometres, in tenths: 100,000 km is far beyond any single trip.
MAX_TRIP_TENTHS = 100_000 * TENTHS
MAX_TIMES = 10_000
DESCRIPTION_MAX = 200
#: Up to this many trips are listed one by one in the working; more are summarised.
SHOWN_TRIPS = 8

_KM = re.compile(r"([0-9]+)(?:\.([0-9]))?")


@dataclass(frozen=True)
class Trip:
    description: str
    #: Kilometres of one trip, in tenths of a kilometre (more than 0).
    tenths: int
    #: How many times it was made (1 or more).
    times: int = 1
    on: date | None = None

    @property
    def total(self) -> int:
        """This row's kilometres in tenths: one trip's kilometres times the times."""
        return self.tenths * self.times


@dataclass(frozen=True)
class CarKilometres:
    label: str
    trips: tuple[Trip, ...]


def financial_year_of(day: date) -> int:
    """The year it ends in: 1 July 2025 is FY 2026."""
    return day.year + 1 if day.month >= 7 else day.year


def km_text(tenths: int) -> str:
    """``420`` as ``"42.0"``: one decimal place, exactly."""
    return f"{tenths // TENTHS}.{tenths % TENTHS}"


def parse_description(raw: object) -> str:
    text = as_text(raw, "description").strip()
    if not text:
        raise InputError("Describe the trip: where to or from, and the purpose.")
    if len(text) > DESCRIPTION_MAX:
        raise InputError(f"The description is at most {DESCRIPTION_MAX} characters.")
    return text


def parse_km(raw: object) -> int:
    """Kilometres of one trip as text with at most one decimal place, as tenths. More than 0."""
    text = as_text(raw, "kilometres").strip()
    found = _KM.fullmatch(text)
    if found is None:
        raise InputError(
            "Enter the kilometres of one trip as a number with at most 1 decimal place, "
            "such as 42 or 42.5."
        )
    value = int(found.group(1)) * TENTHS + int(found.group(2) or "0")
    if value <= 0:
        raise InputError("The kilometres are more than 0.")
    if value > MAX_TRIP_TENTHS:
        raise InputError(f"The kilometres of one trip are at most {km_text(MAX_TRIP_TENTHS)}.")
    return value


def parse_times(raw: object) -> int:
    """How many times, a whole number from 1: an integer, or text of digits. Blank is once."""
    if isinstance(raw, bool):
        raise InputError("Times is a whole number, 1 or more.")
    if isinstance(raw, str):
        text = raw.strip()
        if text == "":
            return 1
        if re.fullmatch(r"[0-9]+", text) is None:
            raise InputError("Times is a whole number, 1 or more.")
        raw = int(text)
    if not isinstance(raw, int):
        raise InputError("Times is a whole number, 1 or more.")
    if raw < 1:
        raise InputError("Times is a whole number, 1 or more.")
    if raw > MAX_TIMES:
        raise InputError(f"Times is at most {MAX_TIMES}.")
    return raw


def parse_on(raw: object) -> date | None:
    """The optional date of a trip: an ISO date, or blank for none."""
    if raw is None:
        return None
    text = as_text(raw, "date").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise InputError("Enter the date as a year, month and day, such as 2025-08-14.") from None


def parse_trip(raw: object) -> Trip:
    body = as_obj(raw, "A trip")
    return Trip(
        parse_description(body.get("description")),
        parse_km(body.get("km")),
        parse_times(body.get("times", 1)),
        parse_on(body.get("on")),
    )


def parse(raw: object) -> CarKilometres:
    reject_floats(raw)
    body = as_obj(raw, "A car kilometres record")
    label = as_text(body.get("label"), "label")
    if label not in ALLOWED_LABELS:
        raise InputError("Car kilometres feed business expenses or work-related car expenses.")
    check_label(label, KEY, (labels.MONEY,))
    trips = tuple(parse_trip(t) for t in as_list(body.get("trips"), "trips"))
    if not trips:
        raise InputError("Add at least one trip.")
    if len(trips) > MAX_TRIPS:
        raise InputError(f"A record has at most {MAX_TRIPS} trips.")
    return CarKilometres(label, trips)


def to_payload(value: CarKilometres) -> dict[str, Any]:
    trips: list[dict[str, Any]] = []
    for t in value.trips:
        row: dict[str, Any] = {
            "description": t.description,
            "km": km_text(t.tenths),
            "times": t.times,
        }
        if t.on is not None:
            row["on"] = t.on.isoformat()
        trips.append(row)
    return {"label": value.label, "trips": trips}


def total_tenths(value: CarKilometres) -> int:
    """The record's kilometres in tenths: the exact sum of kilometres x times over the trips."""
    return sum(t.total for t in value.trips)


def limit_for(record: Record, rules: RecordRules) -> int | None:
    """The most kilometres counted in the year the record sits in, in whole kilometres."""
    return rules.figure(LIMIT_KEY, financial_year_of(record.from_on))


def rate_for(record: Record, rules: RecordRules) -> Money | None:
    """The rate per kilometre: the figure of the year the record sits in, in AUD."""
    cents = rules.figure(RATE_KEY, financial_year_of(record.from_on))
    return Money(cents, BASE_CURRENCY) if cents is not None else None


def counted_tenths(value: CarKilometres, limit_km: int) -> int:
    """The kilometres that count, in tenths: the total, limited to ``limit_km``."""
    return min(total_tenths(value), limit_km * TENTHS)


def missing_figures(record: Record, rules: RecordRules) -> tuple[str, ...]:
    held = (
        (RATE_KEY, rate_for(record, rules) is not None),
        (LIMIT_KEY, limit_for(record, rules) is not None),
    )
    return tuple(key for key, present in held if not present)


def deduction(value: CarKilometres, rate: Money, limit_km: int) -> Money:
    """Kilometres counted x rate, one rounding: ``rate x counted tenths / 10``."""
    return scale(rate, counted_tenths(value, limit_km), TENTHS)


def figures_read(record: Record) -> tuple[tuple[str, int], ...]:
    year = financial_year_of(record.from_on)
    return ((RATE_KEY, year), (LIMIT_KEY, year))


def _trip_terms(trips: tuple[Trip, ...]) -> tuple[Operand, ...]:
    """One term per trip: its kilometres, then ``× times`` when it was made more than once."""
    terms: list[Operand] = []
    for i, trip in enumerate(trips):
        join = None if i == 0 else "+"
        terms.append(Operand(km_text(trip.tenths), "km", trip.description, join))
        if trip.times > 1:
            terms.append(Operand(str(trip.times), None, "times", "×"))
    return tuple(terms)


def calculation(record: Record, rules: RecordRules) -> tuple[Step, ...]:
    """The working, as exact steps: the kilometres, the limit if it bites, then the deduction.

    A step is given as soon as what it needs is held: the kilometres need nothing, the limit needs
    its figure, and the deduction needs both figures.
    """
    value: CarKilometres = record.value
    total = km_text(total_tenths(value))
    count = len(value.trips)
    if count <= SHOWN_TRIPS:
        operands = _trip_terms(value.trips)
    else:
        operands = (Operand(total, "km", f"sum of {count} trips"),)
    steps: list[Step] = [
        Step("Kilometres driven", operands, Operand(total, "km", "total kilometres"))
    ]
    limit = limit_for(record, rules)
    year = fy_label(financial_year_of(record.from_on))
    counted = total
    if limit is not None:
        counted = km_text(counted_tenths(value, limit))
        if total_tenths(value) > limit * TENTHS:
            steps.append(
                Step(
                    f"Limited to the {limit:,} km limit",
                    (Operand(total, "km", "total kilometres"),),
                    Operand(counted, "km", f"kilometres counted (limit for FY {year})"),
                    "The limit is for one car in one year: kilometres above it do not count.",
                )
            )
    rate = rate_for(record, rules)
    if rate is None or limit is None:
        return tuple(steps)
    steps.append(
        Step(
            "Deduction",
            (
                Operand(counted, "km", "kilometres counted"),
                Operand(format_amount(rate), "a km", f"rate for FY {year}", "×"),
            ),
            Operand(format_amount(deduction(value, rate, limit)), None, "deduction"),
            "Rounded once, to the cent, for the whole record (not trip by trip).",
        )
    )
    return tuple(steps)


def contributions(
    record: Record, financial_year: int, rules: RecordRules
) -> tuple[Contribution, ...]:
    value: CarKilometres = record.value
    rate = rate_for(record, rules)
    limit = limit_for(record, rules)
    if rate is None or limit is None or financial_year != financial_year_of(record.from_on):
        return ()
    amount = deduction(value, rate, limit)
    if amount.amount_minor == 0:
        return ()
    return (Contribution(record.id, value.label, amount),)


TYPE = RecordType(
    KEY,
    "Car kilometres",
    parse,
    to_payload,
    lambda v: v.label,
    contributions,
    missing_figures,
    figures_read,
    calculation,
)
