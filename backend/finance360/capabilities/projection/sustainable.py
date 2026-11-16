"""Sustainable retirement income (B2): a deterministic integer search over the engine.

The answer is the largest annual spending, in today's dollars, that the engine can pay in full in
every year to the horizon. Spending is the household's phases (ADR-0017 s6): the search scales every
phase anchored to the last retirement by one common factor, holding their ratios, and the answer is
the first such phase's amount. With one phase that is the constant amount from the last retirement,
as before. Phases from the projection start are held as they are. Everything else in the Projection
input stays fixed; its own outflows are replaced by the ones being searched. No engine change: this
module only calls ``project`` (ADR-0015 s7), so it needs no ``ENGINE_VERSION`` bump. Pure, no I/O.

How the search works, all in minor units (integers; the only rounding is the one ``scale``
of each other phase):

* The floor is 0. Spending nothing from the last retirement is always met, so 0 lasts, unless
  phases held from the projection start already fall short on their own: then every amount fails
  and the answer is 0.
* The ceiling is ``SEARCH_CEILING_MINOR`` (10**11 minor units, 1 billion dollars a year). It exists
  only to make the search finite. If the ceiling itself lasts, the result is the ceiling and
  ``capped`` is true: the real answer is at least that, with no upper limit found. This also covers
  spending that starts after the horizon, which can never fall short.
* The search doubles 1, 2, 4, ... until an amount fails (or the ceiling is reached), then bisects
  between the last amount that lasted and the first that failed. It ends with ``amount`` lasting
  and ``amount + 1`` not lasting, whether or not rounding makes "lasts" step unevenly, because
  that pair is kept as the invariant at every step. Doubling plus bisection is at most about 75
  engine runs.
* "Lasts" means the run has no shortfall in any year (``lasts_beyond_horizon``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from finance360.capabilities.projection import spending
from finance360.capabilities.projection.engine import project
from finance360.capabilities.projection.input import Outflow, ProjectionInput
from finance360.core.money import Money

#: The largest amount the search will try, in minor units (1 billion in a 2-decimal currency).
SEARCH_CEILING_MINOR = 10**11


@dataclass(frozen=True)
class SustainableSpending:
    #: The annual spending, in today's dollars.
    amount: Money
    #: True when ``amount`` is the search ceiling and it still lasts: the answer is "at least".
    capped: bool


def _lasts(
    base: ProjectionInput,
    phases: Sequence[spending.Phase],
    minor: int,
    last_retirement_year: int,
    fixed: Sequence[Outflow],
) -> bool:
    scaled = spending.scale_from_retirement(phases, minor, base.currency)
    outflows = (*spending.outflows(scaled, last_retirement_year), *fixed)
    return project(replace(base, outflows=outflows)).lasts_beyond_horizon


def sustainable_spending(
    base: ProjectionInput,
    phases: Sequence[spending.Phase],
    last_retirement_year: int,
    fixed_outflows: Sequence[Outflow] = (),
) -> SustainableSpending:
    """The largest first ``last_retirement`` phase that, with the others scaled with it, lasts.

    ``fixed_outflows`` are amounts that are not spending (Division 293 the household pays itself):
    they are held as they are beside the searched phases."""
    lasting = 0  # always true: nothing to spend, nothing to fall short of
    probe = 1
    while True:
        if _lasts(base, phases, probe, last_retirement_year, fixed_outflows):
            lasting = probe
            if probe >= SEARCH_CEILING_MINOR:
                return SustainableSpending(Money(probe, base.currency), True)
            probe = min(probe * 2, SEARCH_CEILING_MINOR)
        else:
            failing = probe
            break
    while failing - lasting > 1:
        middle = (lasting + failing) // 2
        if _lasts(base, phases, middle, last_retirement_year, fixed_outflows):
            lasting = middle
        else:
            failing = middle
    return SustainableSpending(Money(lasting, base.currency), False)
