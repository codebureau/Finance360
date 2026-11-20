"""Break-even search for a Tax what-if (ADR-0019 section 6). Pure: no I/O, no clock, no database.

The question: at what amount of **one** override (one Member's total for one label, set absolutely)
is the Household's net outcome at least the base case? The caller supplies ``net_at``, a pure
function from an amount in minor units to the Household net outcome in minor units, and the
``target`` (the base case's net outcome). Everything here is whole integers: no float.

How it searches (the same technique as the sustainable-income bisection, ADR-0015):

1. **Scan** a fixed grid of ``GRID_POINTS + 1`` amounts spread evenly from ``lower`` to ``upper``,
   recording whether the condition ``net >= target`` holds at each.
2. Take the **first** adjacent pair where the condition changes (the lowest crossing) and **bisect**
   between them in minor units until the two are one minor unit apart. When the condition starts to
   hold as the amount rises, the answer is the *smallest* amount that holds; when it stops holding,
   the *largest*.
3. **Verify** it: the found amount holds and the neighbour on the other side does not (both are
   evaluated afresh and reported, so the household sees one minor unit either side).

**Monotonicity is not guaranteed**, so the answer is *a* crossing, and the result says so when:

* the condition changes more than once across the grid (the amounts that satisfy it are not one
  range), or the net outcome rises and also falls across the grid (``reversal``); or
* the net outcome jumps at the crossing itself by more than ``CLIFF_MINOR`` for one minor unit of
  the amount (``cliff``): a step such as a levy surcharge tier or a HELP repayment threshold. The
  engine's own whole-dollar rounding moves a total by under a dollar for one cent, so a larger move
  is a step in the rules, and another amount nearby may behave differently.

**Search bounds** (decided here; ADR-0019 leaves them to this slice): ``lower`` is zero (a
Label total is never negative) and ``upper`` is ``DEFAULT_UPPER_MINOR``, AUD 10,000,000.00. That is
far above any one Label total a household has, and costs about 30 bisection steps plus the grid.
When the condition holds at no grid amount, or at every one, the result is ``none`` or
``always``: there is no crossing in range to report. A crossing narrower than one grid step (about
AUD 39,000 at the default bounds) in an otherwise flat range can be missed; that is the stated
limit of the grid.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

#: Grid intervals between ``lower`` and ``upper`` (so ``GRID_POINTS + 1`` amounts are tried).
GRID_POINTS = 256
#: The default upper bound: AUD 10,000,000.00 in minor units.
DEFAULT_UPPER_MINOR = 1_000_000_000
#: One minor unit moving the net outcome by more than this is a step in the rules (a whole dollar).
CLIFF_MINOR = 100

FOUND = "found"
#: The condition holds at every amount tried: nothing to find in range.
ALWAYS = "always"
#: The condition holds at none of them: no crossing in range.
NONE = "none"

SMALLEST = "smallest"
LARGEST = "largest"

REVERSAL = "reversal"
CLIFF = "cliff"
MANY_CROSSINGS = "many_crossings"


class BreakEvenError(ValueError):
    """The search was asked for something that cannot be searched."""


@dataclass(frozen=True)
class Probe:
    """The net outcome at one amount, and whether it meets the target."""

    amount_minor: int
    net_minor: int
    holds: bool


@dataclass(frozen=True)
class BreakEven:
    status: str
    target_minor: int
    lower_minor: int
    upper_minor: int
    #: The crossing: ``smallest`` (the condition starts to hold as the amount rises) or
    #: ``largest`` (it stops holding). ``None`` unless ``status`` is ``found``.
    direction: str | None = None
    #: The found amount and the amounts one minor unit either side (``None`` outside the bounds).
    below: Probe | None = None
    at: Probe | None = None
    above: Probe | None = None
    #: True when the found amount holds and its neighbour on the far side does not.
    verified: bool = False
    #: ``reversal``, ``cliff`` and ``many_crossings``: why the range is not monotone.
    reasons: tuple[str, ...] = ()

    @property
    def non_monotone(self) -> bool:
        return bool(self.reasons)

    @property
    def found_minor(self) -> int | None:
        return self.at.amount_minor if self.at is not None else None


def _grid(lower: int, upper: int) -> list[int]:
    return sorted({lower + (upper - lower) * i // GRID_POINTS for i in range(GRID_POINTS + 1)})


def search(
    net_at: Callable[[int], int],
    target_minor: int,
    *,
    lower_minor: int = 0,
    upper_minor: int = DEFAULT_UPPER_MINOR,
) -> BreakEven:
    """Find an amount in ``[lower_minor, upper_minor]`` where ``net_at(amount) >= target_minor``
    starts or stops holding. ``net_at`` must be pure (same amount, same answer)."""
    if lower_minor < 0 or upper_minor <= lower_minor:
        raise BreakEvenError("The search range must be from 0 upward, with an upper bound.")

    cache: dict[int, Probe] = {}

    def probe(amount: int) -> Probe:
        held = cache.get(amount)
        if held is None:
            net = net_at(amount)
            held = cache[amount] = Probe(amount, net, net >= target_minor)
        return held

    points = [probe(a) for a in _grid(lower_minor, upper_minor)]
    flips = [i for i in range(len(points) - 1) if points[i].holds != points[i + 1].holds]
    if not flips:
        return BreakEven(
            ALWAYS if points[0].holds else NONE, target_minor, lower_minor, upper_minor
        )

    # Bisect the lowest crossing: ``a`` and ``b`` always differ in whether the condition holds.
    a, b = points[flips[0]], points[flips[0] + 1]
    rising = b.holds  # holds as the amount rises: the answer is the smallest amount that holds
    lo, hi = a.amount_minor, b.amount_minor
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if probe(mid).holds == a.holds:
            lo = mid
        else:
            hi = mid
    found = hi if rising else lo

    at = probe(found)
    inside = probe(found - 1 if rising else found + 1)  # the neighbour that must not hold
    verified = at.holds and not inside.holds
    below = probe(found - 1) if found - 1 >= lower_minor else None
    above = probe(found + 1) if found + 1 <= upper_minor else None

    reasons: list[str] = []
    nets = [p.net_minor for p in points]
    rises = any(y > x for x, y in zip(nets, nets[1:], strict=False))
    falls = any(y < x for x, y in zip(nets, nets[1:], strict=False))
    if len(flips) > 1:
        reasons.append(MANY_CROSSINGS)
    if rises and falls:
        reasons.append(REVERSAL)
    step = abs(at.net_minor - inside.net_minor)
    if step > CLIFF_MINOR:
        reasons.append(CLIFF)

    return BreakEven(
        FOUND,
        target_minor,
        lower_minor,
        upper_minor,
        direction=SMALLEST if rising else LARGEST,
        below=below,
        at=at,
        above=above,
        verified=verified,
        reasons=tuple(reasons),
    )
