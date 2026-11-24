"""The reconciliation tolerance (ADR-0030 section 6). Pure.

The Unallocated line is always shown; only a material gap is flagged. A gap of either sign within
``reconcile_tolerance`` is shown quietly. A super Account's option mix that is older than its
Valuation is judged against the wider ``stale_tolerance`` instead: a year's returns and
contributions explain such a gap.
"""

from __future__ import annotations

from finance360.core.money import Money, exponent, round_div

#: The tolerance is the larger of this share of the Account's value (1%) and a flat A$50.
RECONCILE_PERCENT = 1
RECONCILE_FLOOR_MAJOR = 50
#: A stale option mix (the Valuation is dated after the Super statement) is flagged only beyond
#: this share of the Account's value.
STALE_PERCENT = 15


def reconcile_tolerance(value: Money) -> Money:
    """The larger of 1% of ``value`` and 50 major units, in ``value``'s currency."""
    share = round_div(abs(value.amount_minor) * RECONCILE_PERCENT, 100)
    floor = RECONCILE_FLOOR_MAJOR * 10 ** exponent(value.currency)
    return Money(max(share, floor), value.currency)


def is_material(gap: Money, value: Money) -> bool:
    """True when ``gap`` (of either sign) is beyond the tolerance for an Account worth ``value``."""
    return abs(gap.amount_minor) > reconcile_tolerance(value).amount_minor


def stale_tolerance(value: Money) -> Money:
    """15% of ``value``, in ``value``'s currency (the stale-option-mix tolerance)."""
    return Money(round_div(abs(value.amount_minor) * STALE_PERCENT, 100), value.currency)


def is_stale_material(gap: Money, value: Money) -> bool:
    """True when ``gap`` (of either sign) is beyond the stale tolerance for an Account worth
    ``value``: "the option mix may have changed"."""
    return abs(gap.amount_minor) > stale_tolerance(value).amount_minor
