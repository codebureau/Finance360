"""Spending phases: the household's spending plan as a list (ADR-0017 section 6).

A **spending phase** is a level of annual spending, in today's dollars, that starts at the
projection start (``start``) or a whole number of years after the last Member retires
(``last_retirement``) and runs until the next phase starts; the last runs to the horizon.
Anchoring to retirement is the point: "active years, then slower years" moves when a Scenario
retires earlier or later.

Pure functions over plain values: no database, no models, no Capability (the import-linter contract
that keeps the engine pure covers this module). The engine never sees a phase: ``outflows`` turns
the list into the ``Outflow`` entries it already understands, so there is no engine change.

* Phases are sorted by their resolved start year. When two resolve to the same year the later anchor
  (``last_retirement`` after ``start``) wins and the other is empty.
* A phase with nothing before it means nothing is spent before the first phase.
* A Scenario's list replaces the Household default list as a whole (``resolve``), never a phase at a
  time, with ``drawdown_order`` (ADR-0016 section 3) as the precedent.
* ``scale_from_retirement`` is what the sustainable-income search varies: every ``last_retirement``
  phase by one common factor, holding their ratios.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from finance360.capabilities.projection.input import Outflow
from finance360.core.errors import FieldError
from finance360.core.money import BASE_CURRENCY, Money, MoneyError, parse_amount, scale

START = "start"
LAST_RETIREMENT = "last_retirement"
#: In precedence order: on a same-year clash the later anchor wins.
ANCHORS = (START, LAST_RETIREMENT)
ANCHOR_LABEL = {START: "the projection start", LAST_RETIREMENT: "the last retirement"}
#: The most whole years after an anchor (the horizon is at most this far on).
MAX_OFFSET = 120

LAYER_SCENARIO = "scenario"
LAYER_HOUSEHOLD = "household"

FIELD = "spending_phases"


@dataclass(frozen=True)
class Phase:
    """One stored phase: ``annual_amount`` a year from ``offset_years`` after ``anchor``."""

    anchor: str
    offset_years: int
    annual_amount: Money


@dataclass(frozen=True)
class Scheduled:
    """A phase placed on the timeline. ``until_year`` None means it runs to the horizon; a phase
    whose ``until_year`` equals its ``from_year`` is empty (another phase starts the same year)."""

    phase: Phase
    from_year: int
    until_year: int | None

    @property
    def empty(self) -> bool:
        return self.until_year is not None and self.until_year <= self.from_year


@dataclass(frozen=True)
class Resolved:
    phases: tuple[Phase, ...]
    #: ``scenario`` if the Scenario states its own list, else ``household``.
    layer: str


def start_year(phase: Phase, last_retirement_year: int) -> int:
    """The projection year the phase begins: its offset from the start or the last retirement."""
    base = last_retirement_year if phase.anchor == LAST_RETIREMENT else 0
    return base + phase.offset_years


def ordered(phases: Sequence[Phase]) -> tuple[Phase, ...]:
    """The list in a fixed order (by anchor, then offset) so equal lists compare and store equal."""
    return tuple(sorted(phases, key=lambda p: (ANCHORS.index(p.anchor), p.offset_years)))


def schedule(phases: Sequence[Phase], last_retirement_year: int) -> tuple[Scheduled, ...]:
    """Each phase with the year it starts and the year the next one starts (None for the last)."""
    keyed = sorted(
        ((start_year(p, last_retirement_year), ANCHORS.index(p.anchor), p) for p in phases),
        key=lambda k: (k[0], k[1], k[2].offset_years),
    )
    out: list[Scheduled] = []
    for i, (year, _, phase) in enumerate(keyed):
        until = keyed[i + 1][0] if i + 1 < len(keyed) else None
        out.append(Scheduled(phase, year, until))
    return tuple(out)


def outflows(phases: Sequence[Phase], last_retirement_year: int) -> tuple[Outflow, ...]:
    """One ``Outflow`` per phase that spends something over at least one year.

    The ranges are disjoint by construction: each ends where the next begins.
    """
    return tuple(
        Outflow(s.phase.annual_amount, s.from_year, s.until_year)
        for s in schedule(phases, last_retirement_year)
        if not s.empty and s.phase.annual_amount.amount_minor > 0
    )


def scale_from_retirement(phases: Sequence[Phase], first_minor: int, currency: str) -> list[Phase]:
    """The list with every ``last_retirement`` phase scaled so the first one is ``first_minor``.

    The common factor is ``first_minor / (the first such phase's amount)``, applied to each with one
    rounding, so ratios hold and each amount is non-decreasing in ``first_minor`` (which keeps the
    sustainable-income search monotone). ``start`` phases are held. With no ``last_retirement``
    phase there is nothing to scale, so one is added at offset 0: the search then asks how much
    could be spent from the last retirement beside the fixed phases. If the first phase is a stored
    0 (only a converted 0 ``annual_spending`` can be), it becomes ``first_minor`` and the rest are
    held, as no ratio exists.
    """
    retire = sorted(
        (p for p in phases if p.anchor == LAST_RETIREMENT), key=lambda p: p.offset_years
    )
    held = [p for p in phases if p.anchor != LAST_RETIREMENT]
    if not retire:
        return [*held, Phase(LAST_RETIREMENT, 0, Money(first_minor, currency))]
    base = retire[0].annual_amount.amount_minor
    scaled = [
        replace(
            p,
            annual_amount=(
                Money(first_minor, currency)
                if i == 0
                else scale(p.annual_amount, first_minor, base)
                if base > 0
                else p.annual_amount
            ),
        )
        for i, p in enumerate(retire)
    ]
    return [*held, *scaled]


def pick(default: Sequence[Phase], override: Sequence[Phase]) -> Resolved | None:
    """The Scenario's own list if it has one, else the Household default list; None if both are
    empty (spending is unset, which is an error, never a silent zero)."""
    if override:
        return Resolved(ordered(override), LAYER_SCENARIO)
    if default:
        return Resolved(ordered(default), LAYER_HOUSEHOLD)
    return None


def parse(entries: Sequence[tuple[str, int, str]]) -> tuple[list[Phase], list[FieldError]]:
    """Validate a whole list as the screen or API states it.

    Each entry is ``(anchor, offset_years, amount text)``.

    Every problem is collected against its own field (``spending_phases.<i>.<field>``): a known
    anchor, a whole offset from 0 to ``MAX_OFFSET``, an amount above 0, and no duplicate
    ``(anchor, offset_years)`` in the list. The phases are returned in the order given.
    """
    errors: list[FieldError] = []
    phases: list[Phase] = []
    seen: set[tuple[str, int]] = set()
    for i, (anchor, offset, text) in enumerate(entries):
        where = f"{FIELD}.{i}"
        placed = True  # a known anchor and a usable offset: the phase has a place in the list
        if anchor not in ANCHORS:
            errors.append(
                FieldError(f"{where}.anchor", "Choose the projection start or the last retirement.")
            )
            placed = False
        if not 0 <= offset <= MAX_OFFSET:
            errors.append(
                FieldError(
                    f"{where}.offset_years",
                    f"Enter a whole number of years from 0 to {MAX_OFFSET}.",
                )
            )
            placed = False
        if placed:
            if (anchor, offset) in seen:
                errors.append(
                    FieldError(
                        f"{where}.offset_years",
                        "Another phase already starts at this point. Each phase needs its own.",
                    )
                )
            seen.add((anchor, offset))
        try:
            amount = parse_amount(text, BASE_CURRENCY)
        except MoneyError as error:
            errors.append(FieldError(f"{where}.annual_amount", str(error)))
            continue
        if amount.amount_minor <= 0:
            errors.append(FieldError(f"{where}.annual_amount", "Enter an amount above 0."))
            continue
        if placed:
            phases.append(Phase(anchor, offset, amount))
    return phases, errors
