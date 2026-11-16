"""Life events: derived dates that become year indices (ADR-0017 sections 3 and 4).

A **Life event** in M3 is a ``(member, kind, date, source)`` value computed here, never a stored
row. Every Life event is a date, and one rule turns a date into a year index: **the first
projection year whose start is on or after the date, never below 0**. Year ``t`` starts on the
``t``-th anniversary of ``as_at``. The engine never sees a Life event: it sees the year indices
this produces (``Line.accessible_from_year``).

Pure functions over plain values: no database, no models, no Capability (extends the ADR-0015
forbidden contract in ``pyproject.toml``).

Leap days. A date on 29 February falls on 28 February in a year that has none, as everywhere else
(``input.anniversary``). Adding an age to a date of birth clamps the day to the end of the month,
which gives the same answer. The one place the year rule can differ from B1's
``retirement_age - age_now``: a Member born on 29 February, ``as_at`` on 28 February of a leap year
(before that year's birthday), and a retirement date of 28 February of a common year. They reach the
age on the very day a year starts, so the year rule retires them in that year, one earlier than B1
did. ``tests/test_life_events.py`` pins the cases.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from finance360.capabilities.projection.input import anniversary
from finance360.capabilities.projection.rules import RulesUsed
from finance360.core.rulepack import Age

RETIREMENT = "retirement"
PRESERVATION_AGE = "preservation_age"
UNRESTRICTED_SUPER_AGE = "unrestricted_super_age"
AGE_PENSION_AGE = "age_pension_age"
#: In the order they are listed when two fall on the same day.
KINDS = (RETIREMENT, PRESERVATION_AGE, UNRESTRICTED_SUPER_AGE, AGE_PENSION_AGE)

#: Where the retirement date comes from; the other kinds carry the layer of their rule value.
SOURCE_SCENARIO = "scenario"


@dataclass(frozen=True)
class LifeEvent:
    member_id: int
    kind: str
    on: date
    #: The Member's age on that date: ``67`` or ``66y6m``.
    age: Age
    #: ``scenario`` for retirement; ``rule_pack`` (or ``scenario`` / ``household`` for an override)
    #: for the Rule pack ages.
    source: str
    #: The projection year index the date turns into (``year_index``).
    year: int


def date_at_age(born_on: date, age: Age) -> date:
    """The date a person born on ``born_on`` reaches ``age`` (years and whole months)."""
    month_index = born_on.year * 12 + (born_on.month - 1) + age.total_months
    year, month0 = divmod(month_index, 12)
    month = month0 + 1
    return date(year, month, min(born_on.day, calendar.monthrange(year, month)[1]))


def year_index(on: date, as_at: date) -> int:
    """The first projection year starting on or after ``on``; 0 for a date already past."""
    t = max(0, on.year - as_at.year - 1)
    while anniversary(as_at, as_at.year + t) < on:
        t += 1
    return t


def resolve(
    members: Sequence[tuple[int, date]],
    retirement_ages: Mapping[int, int],
    rules: RulesUsed,
    as_at: date,
) -> tuple[LifeEvent, ...]:
    """Every Member's Life events in date order (ties: Member id, then ``KINDS`` order).

    ``members`` is ``(id, date of birth)``. ``rules`` already carries any ``rule.`` override
    (applied before this, ADR-0017 section 3).
    """
    born = dict(members)
    events: list[LifeEvent] = []
    for member_id, born_on in members:
        age = Age(retirement_ages[member_id])
        on = date_at_age(born_on, age)
        events.append(
            LifeEvent(member_id, RETIREMENT, on, age, SOURCE_SCENARIO, year_index(on, as_at))
        )
    for value in rules.values:
        if value.key not in KINDS:
            continue
        on = date_at_age(born[value.member_id], value.value)
        events.append(
            LifeEvent(
                value.member_id, value.key, on, value.value, value.layer, year_index(on, as_at)
            )
        )
    return tuple(sorted(events, key=lambda e: (e.on, e.member_id, KINDS.index(e.kind))))


def super_access_year(retirement_year: int, preservation_year: int, unrestricted_year: int) -> int:
    """ADR-0017 section 4: retired and past preservation age, or age 65, whichever comes first.

    Deliberately not "at preservation age" alone: retiring before it leaves a real gap.
    """
    return min(max(retirement_year, preservation_year), unrestricted_year)


def member_years(events: Sequence[LifeEvent]) -> dict[int, dict[str, int]]:
    """Each Member's year index per kind."""
    by_member: dict[int, dict[str, int]] = {}
    for e in events:
        by_member.setdefault(e.member_id, {})[e.kind] = e.year
    return by_member


def member_super_access_years(events: Sequence[LifeEvent]) -> dict[int, int]:
    """Each Member's ``superannuation`` access year from their resolved Life events."""
    return {
        member_id: super_access_year(y[RETIREMENT], y[PRESERVATION_AGE], y[UNRESTRICTED_SUPER_AGE])
        for member_id, y in member_years(events).items()
    }
