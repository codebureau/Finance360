"""The reconciliation tolerance (ADR-0030 section 6). Pure.

The Unallocated line is always shown; only a material gap is flagged. A gap of either sign within
``reconcile_tolerance`` is shown quietly.
"""

from __future__ import annotations

from finance360.core.money import Money, exponent, round_div

#: The tolerance is the larger of this share of the Account's value (1%) and a flat A$50.
RECONCILE_PERCENT = 1
RECONCILE_FLOOR_MAJOR = 50


def reconcile_tolerance(value: Money) -> Money:
    """The larger of 1% of ``value`` and 50 major units, in ``value``'s currency."""
    share = round_div(abs(value.amount_minor) * RECONCILE_PERCENT, 100)
    floor = RECONCILE_FLOOR_MAJOR * 10 ** exponent(value.currency)
    return Money(max(share, floor), value.currency)


def is_material(gap: Money, value: Money) -> bool:
    """True when ``gap`` (of either sign) is beyond the tolerance for an Account worth ``value``."""
    return abs(gap.amount_minor) > reconcile_tolerance(value).amount_minor
