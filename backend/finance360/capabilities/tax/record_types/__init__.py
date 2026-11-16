"""The Record type registry (ADR-0019 section 2). Pure: no database, no clock, no Rule pack.

A **Record type** parses the payload of a Tax record to a frozen value and yields its
**Contributions**: ``(record_id, label, value)``, rounded once per record with
``core.money.scale``. A **Label total** is the exact sum of the Contributions to one label, for one
Member and one financial year. It is computed on demand and never stored, and because every
Contribution carries its ``record_id`` a total always drills down to its records.

Six types exist: ``line`` (one money amount against one label, with an optional work-use share),
``fact`` (a flag or a number of days), ``income_statement`` (an employer's figures, which feed
several labels, return and memo), ``depreciating_asset`` (a worksheet whose deduction in each
financial year feeds one label), ``home_office_hours`` (a schedule of hours whose deduction, at a
Rule pack rate per hour, feeds one label) and ``car_kilometres`` (a schedule of trips whose
kilometres, up to a Rule pack limit and at a Rule pack rate per kilometre, feed one label). There is
no framework beyond what these need. They are code, not a plug-in tree, because only tax uses them.
A Contribution is for one financial year and takes the ``rules`` argument (``RecordRules``, the Rule
pack figures the service resolved): only the asset and the two schedules read it, for the
diminishing value multiplier, the rate per hour, and the rate and limit per kilometre.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from finance360.capabilities.tax import labels
from finance360.capabilities.tax.input import Total
from finance360.capabilities.tax.record_types import (
    car_kilometres,
    depreciating_asset,
    fact,
    home_office_hours,
    income_statement,
    line,
)
from finance360.capabilities.tax.record_types.base import (
    NO_RULES,
    Contribution,
    Record,
    RecordRules,
    RecordType,
    Step,
)
from finance360.core.money import BASE_CURRENCY, Money

LINE = line.KEY
FACT = fact.KEY
INCOME_STATEMENT = income_statement.KEY
DEPRECIATING_ASSET = depreciating_asset.KEY
HOME_OFFICE_HOURS = home_office_hours.KEY
CAR_KILOMETRES = car_kilometres.KEY

TYPES: dict[str, RecordType] = {
    t.key: t
    for t in (
        line.TYPE,
        fact.TYPE,
        income_statement.TYPE,
        depreciating_asset.TYPE,
        home_office_hours.TYPE,
        car_kilometres.TYPE,
    )
}

#: A record with no end date that has no end: the far future, so it overlaps every later year.
OPEN_END = date(9999, 12, 31)


@dataclass(frozen=True)
class LabelTotal:
    member_id: int | None
    label: str
    total: Total
    contributions: tuple[Contribution, ...]


def span(record: Record) -> tuple[date, date]:
    """The days a record covers: ``from_on`` to ``to_on``, or just ``from_on`` when it has no end.

    A line or a fact is dated. A depreciating asset with no end date is still held, so its span has
    no end and it overlaps every year from its start.
    """
    if record.record_type == DEPRECIATING_ASSET and record.to_on is None:
        return record.from_on, OPEN_END
    return record.from_on, record.to_on or record.from_on


def financial_year_span(financial_year: int) -> tuple[date, date]:
    """1 July of the year before to 30 June of ``financial_year``."""
    return date(financial_year - 1, 7, 1), date(financial_year, 6, 30)


def overlaps(record: Record, financial_year: int) -> bool:
    """Whether the record's span touches the year (ends on or after 1 July, starts by 30 June)."""
    start, end = span(record)
    year_start, year_end = financial_year_span(financial_year)
    return start <= year_end and end >= year_start


def contributions_of(
    record: Record, financial_year: int | None = None, rules: RecordRules = NO_RULES
) -> tuple[Contribution, ...]:
    """What the record adds to its label(s) in ``financial_year`` (its first year when omitted)."""
    record_type = TYPES.get(record.record_type)
    if record_type is None:
        return ()
    year = financial_year if financial_year is not None else _first_year(record)
    return record_type.contributions(record, year, rules)


def label_of(record: Record) -> str:
    return TYPES[record.record_type].label_of(record.value)


def missing_figures(record: Record, rules: RecordRules) -> tuple[str, ...]:
    """The Rule pack keys the record needs for ``rules`` and does not have."""
    record_type = TYPES.get(record.record_type)
    return record_type.missing_figures(record, rules) if record_type is not None else ()


def figures_read(record: Record) -> tuple[tuple[str, int], ...]:
    """The Rule pack figures the record reads, as ``(key, financial year)``, held or not."""
    record_type = TYPES.get(record.record_type)
    return record_type.figures_read(record) if record_type is not None else ()


def calculation_of(record: Record, rules: RecordRules) -> tuple[Step, ...]:
    """The record's working as exact steps (none for a type with no calculation)."""
    record_type = TYPES.get(record.record_type)
    return record_type.calculation(record, rules) if record_type is not None else ()


def _first_year(record: Record) -> int:
    return depreciating_asset.financial_year_of(record.from_on)


def unavailable(
    records: Sequence[Record], financial_year: int, rules: RecordRules = NO_RULES
) -> tuple[tuple[Record, tuple[str, ...]], ...]:
    """The records of the year that cannot be counted because a Rule pack figure is missing."""
    found = ((r, missing_figures(r, rules)) for r in records if overlaps(r, financial_year))
    return tuple((record, keys) for record, keys in found if keys)


def label_totals(
    records: Sequence[Record], financial_year: int, rules: RecordRules = NO_RULES
) -> dict[tuple[int | None, str], LabelTotal]:
    """The Label totals of ``records`` for one financial year, keyed by ``(Member, label)``.

    Only records whose span overlaps the year count. A line, fact or statement counts whole (never
    apportioned across years); an asset counts the deduction its schedule gives that year. Money
    and days add up exactly; a flag is set when any record sets it. Contributions are in record
    order, so equal input gives equal output. An asset whose Rule pack figure is missing adds
    nothing here: ``unavailable`` names it, and the caller must say so.
    """
    grouped: dict[tuple[int | None, str], list[Contribution]] = {}
    for record in records:
        if not overlaps(record, financial_year):
            continue
        for contribution in contributions_of(record, financial_year, rules):
            grouped.setdefault((record.member_id, contribution.label), []).append(contribution)
    return {
        key: LabelTotal(key[0], key[1], _sum(key[1], items), tuple(items))
        for key, items in grouped.items()
    }


def _sum(label_key: str, items: Sequence[Contribution]) -> Total:
    kind = labels.LABELS[label_key].kind
    if kind == labels.MONEY:
        total = Money.zero(BASE_CURRENCY)
        for item in items:
            if isinstance(item.value, Money):
                total = total + item.value
        return total
    if kind == labels.DAYS:
        return sum(item.value for item in items if isinstance(item.value, int))
    return any(item.value is True for item in items)
