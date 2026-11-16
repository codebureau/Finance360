"""Income streams in a projection: the type registry and the per-year cash flows (ADR-0017
sections 2, 4 and 5).

Pure functions over plain values: no database, no models, no Capability (the import-linter contract
that keeps the engine pure covers this module). The engine never sees a stream: ``flows`` expands
each into one ``Inflow`` per year it is in force (half-open ``[t, t+1)``), which the engine already
understands, so there is no engine change.

* **The type registry** is code, like ``behaviour_key``. M3 registers ``salary`` only. The
  tax-relevant kind (``employment`` for a salary) and the indexation (``real_wage_growth``) belong
  to the type, not to a column, so a tax capability reads them here without a migration.
* **Take-home pay** credits the stream's ``credit_category`` (cash) each year in force, grown by the
  real wage growth in one ``scale`` from the stored base: year ``t`` is
  ``base * (1 + g)^t``, rounded once, never compounded year on year.
* **Employer super (SG)** is added to the owner's ``superannuation`` line for each year a stream
  whose type pays it is in force: ``gross * sg_rate * (1 - contributions_tax_rate)``, the gross
  grown the same way, **rounded once**. Not modelled: Division 293, concessional caps, the maximum
  contribution base, salary sacrifice, fund fees.
* **A stream's years** come from its start and end: a date or a Life event key of the owner. A date
  becomes a year index by the one rule of ``life_events.year_index`` (the first year starting on or
  after it), so a stream ending at ``retirement`` ends in the Member's retirement year.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from finance360.capabilities.projection import life_events
from finance360.capabilities.projection.input import Inflow

# The type registry is pure data in ``core.income_types`` (re-exported here) so that Tax can read
# it without importing projection (ADR-0019 section 1, the routine move it names).
from finance360.core.income_types import (
    BUSINESS as BUSINESS,
)
from finance360.core.income_types import (
    EMPLOYMENT as EMPLOYMENT,
)
from finance360.core.income_types import (
    GOVERNMENT as GOVERNMENT,
)
from finance360.core.income_types import (
    INVESTMENT as INVESTMENT,
)
from finance360.core.income_types import (
    REAL_WAGE_GROWTH as REAL_WAGE_GROWTH,
)
from finance360.core.income_types import (
    SALARY as SALARY,
)
from finance360.core.income_types import (
    TAX_KINDS as TAX_KINDS,
)
from finance360.core.income_types import (
    TYPES as TYPES,
)
from finance360.core.income_types import (
    StreamType as StreamType,
)
from finance360.core.money import Money, scale
from finance360.core.rate import PPM, Rate

# --- the type registry ---------------------------------------------------------------------

SUPERANNUATION = "superannuation"


#: The Life event keys a stream may start or end at: those of its owner (``life_events.KINDS``).
EVENT_KEYS = life_events.KINDS
#: What a new salary ends at when it states no end (ADR-0017 section 2).
DEFAULT_END_EVENT = life_events.RETIREMENT


class IncomeError(ValueError):
    """A stream or rate that cannot be turned into flows."""


# --- plain values --------------------------------------------------------------------------


@dataclass(frozen=True)
class Stream:
    """A stored stream as the projection sees it."""

    id: int
    name: str
    type_key: str
    member_id: int
    gross_annual: Money
    take_home_annual: Money
    credit_category: str
    start_on: date | None = None
    start_event: str | None = None
    end_on: date | None = None
    end_event: str | None = None


@dataclass(frozen=True)
class Span:
    """The years a stream is in force: ``[from_year, until_year)``; None runs to the horizon."""

    from_year: int
    until_year: int | None

    def in_force(self, t: int) -> bool:
        return self.from_year <= t and (self.until_year is None or t < self.until_year)


def _year_of(
    on: date | None, event: str | None, years: Mapping[str, int], as_at: date
) -> int | None:
    if on is not None:
        return life_events.year_index(on, as_at)
    if event is not None:
        return years[event]
    return None


def span_of(stream: Stream, owner_years: Mapping[str, int], as_at: date) -> Span:
    """The years the stream is in force. ``owner_years`` is the owner's year index per Life event
    kind. Start null means now (year 0); end null means the horizon."""
    start = _year_of(stream.start_on, stream.start_event, owner_years, as_at)
    until = _year_of(stream.end_on, stream.end_event, owner_years, as_at)
    return Span(0 if start is None else start, until)


# --- amounts -------------------------------------------------------------------------------


def grown(base: Money, growth: Rate, t: int) -> Money:
    """``base * (1 + growth)^t``, one ``scale`` from the stored base (rounding never compounds)."""
    return scale(base, (PPM + growth.ppm) ** t, PPM**t)


def super_contribution(gross: Money, growth: Rate, t: int, sg: Rate, tax: Rate) -> Money:
    """Year ``t``'s employer super after contributions tax: ``gross * sg * (1 - tax)``, with the
    gross grown to year ``t``, in one rounding. Both rates must be from 0 to 100%, so the result
    never exceeds the grown gross."""
    for name, rate in (("SG rate", sg), ("Contributions tax rate", tax)):
        if not 0 <= rate.ppm <= PPM:
            raise IncomeError(f"{name} must be from 0% to 100%.")
    return scale(
        gross,
        (PPM + growth.ppm) ** t * sg.ppm * (PPM - tax.ppm),
        PPM**t * PPM * PPM,
    )


# --- what a run records --------------------------------------------------------------------


@dataclass(frozen=True)
class StreamUsed:
    """A stream as a projection used it: the stored amounts and the years and growth applied."""

    stream: Stream
    span: Span
    #: The real wage growth applied (the type's indexation).
    growth: Rate


@dataclass(frozen=True)
class RateUsed:
    """A Rule pack rate that applied from projection year ``from_year`` (FY ``fy``) until it
    changed. ``layer`` is ``rule_pack``, or ``scenario`` / ``household`` for a ``rule.``
    override."""

    key: str
    from_year: int
    fy: int
    value: Rate
    layer: str
    #: Where the value is from: the pack's cited source, or the Assumption that overrides it.
    source: str


@dataclass(frozen=True)
class IncomeUsed:
    streams: tuple[StreamUsed, ...]
    rates: tuple[RateUsed, ...]


# --- flows ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RatesFor:
    """The SG rate and contributions tax rate in force in each projection year."""

    sg: Sequence[Rate]
    tax: Sequence[Rate]


def flows(
    stream: Stream,
    span: Span,
    growth: Rate,
    years: int,
    rates: RatesFor | None,
    take_home: Mapping[int, Money] | None = None,
) -> list[Inflow]:
    """One take-home ``Inflow`` per year in force on ``credit_category``, and (for a type that pays
    employer super, with ``rates``) one on the owner's ``superannuation`` line. Years from the end
    of the horizon on are not expanded.

    ``take_home`` (projection year to amount) replaces the stated take-home pay for each year it
    holds: the estimate from the tax engine when tax is switched on. A year it does
    not hold uses the stated amount grown as before."""
    kind = TYPES[stream.type_key]
    out: list[Inflow] = []
    for t in range(
        span.from_year, years if span.until_year is None else min(span.until_year, years)
    ):
        pay = take_home[t] if take_home is not None and t in take_home else None
        if pay is None:
            pay = grown(stream.take_home_annual, growth, t)
        if pay.amount_minor > 0:
            out.append(Inflow(stream.member_id, stream.credit_category, pay, t, t + 1))
        if kind.employer_super and rates is not None:
            contribution = super_contribution(
                stream.gross_annual, growth, t, rates.sg[t], rates.tax[t]
            )
            if contribution.amount_minor > 0:
                out.append(Inflow(stream.member_id, SUPERANNUATION, contribution, t, t + 1))
    return out
